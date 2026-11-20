from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from getpass import getpass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

import mysql.connector
from dotenv import load_dotenv
from flask import Flask, abort, jsonify, make_response, request, send_from_directory
from phonepe.sdk.pg.env import Env
from phonepe.sdk.pg.common.models.request.refund_request import RefundRequest
from phonepe.sdk.pg.payments.v2.models.request.standard_checkout_pay_request import (
    StandardCheckoutPayRequest,
)
from phonepe.sdk.pg.payments.v2.standard_checkout_client import StandardCheckoutClient


APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")

app = Flask(__name__, static_folder=None)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024

SESSION_COOKIE = "nirvana_session"
SESSION_TTL = timedelta(days=7)
PASSWORD_ITERATIONS = 600_000
PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 128
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PRODUCT_SLUGS = {"garden-tomatoes", "leafy-greens", "root-vegetables"}
MAX_UNIT_PRICE_PAISE = 100_000_000
ASSETS = {"styles.css", "script.js"}


class ApiError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class DatabaseUnavailable(Exception):
    pass


def phonepe_settings() -> dict[str, Any]:
    client_id = os.environ.get("PHONEPE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("PHONEPE_CLIENT_SECRET", "").strip()
    version_text = os.environ.get("PHONEPE_CLIENT_VERSION", "").strip()
    environment = os.environ.get("PHONEPE_ENV", "SANDBOX").strip().upper()
    public_base_url = os.environ.get("PUBLIC_BASE_URL", "http://127.0.0.1:5000").strip().rstrip("/")
    if not client_id or not client_secret or not version_text:
        raise ApiError(503, "Online payment is not set up yet. Please contact the farm.")
    try:
        client_version = int(version_text)
    except ValueError as error:
        raise ApiError(503, "PhonePe client version must be a positive number.") from error
    if client_version < 1:
        raise ApiError(503, "PhonePe client version must be a positive number.")
    if environment not in {"SANDBOX", "PRODUCTION"}:
        raise ApiError(503, "PHONEPE_ENV must be SANDBOX or PRODUCTION.")
    parsed_base_url = urlsplit(public_base_url)
    local_http = parsed_base_url.scheme == "http" and parsed_base_url.hostname in {
        "localhost",
        "127.0.0.1",
    }
    if parsed_base_url.scheme != "https" and not local_http:
        raise ApiError(503, "PUBLIC_BASE_URL must use HTTPS outside local development.")
    if environment == "PRODUCTION" and parsed_base_url.scheme != "https":
        raise ApiError(503, "Production PhonePe payments require a public HTTPS site URL.")
    if not parsed_base_url.netloc:
        raise ApiError(503, "PUBLIC_BASE_URL must be a valid site URL.")
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "client_version": client_version,
        "environment": environment,
        "public_base_url": public_base_url,
    }


def phonepe_client(settings: dict[str, Any] | None = None) -> StandardCheckoutClient:
    config = settings or phonepe_settings()
    environment = Env.SANDBOX if config["environment"] == "SANDBOX" else Env.PRODUCTION
    return StandardCheckoutClient.get_instance(
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        client_version=config["client_version"],
        env=environment,
    )


def validate_price_paise(value: Any, label: str, *, allow_zero: bool = False) -> int | None:
    if value is None:
        return None
    minimum = 0 if allow_zero else 1
    if type(value) is not int or not minimum <= value <= MAX_UNIT_PRICE_PAISE:
        lower_bound = "zero" if allow_zero else "at least one paise"
        raise ApiError(400, f"{label} must be {lower_bound} and no more than ₹1,000,000, or blank.")
    return value


def phonepe_state(response: Any) -> str:
    state = getattr(response, "state", None)
    if state is None:
        raise ApiError(502, "PhonePe returned an incomplete payment status.")
    return str(getattr(state, "name", state)).upper().rsplit(".", 1)[-1]


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def open_database() -> mysql.connector.MySQLConnection:
    password = os.environ.get("MYSQL_PASSWORD", "")
    if not password:
        raise DatabaseUnavailable("Copy .env.example to .env and set MYSQL_PASSWORD.")
    try:
        port = int(os.environ.get("MYSQL_PORT", "3306"))
    except ValueError as error:
        raise DatabaseUnavailable("MYSQL_PORT must be a number between 1 and 65535.") from error
    if not 1 <= port <= 65535:
        raise DatabaseUnavailable("MYSQL_PORT must be a number between 1 and 65535.")
    try:
        return mysql.connector.connect(
            host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
            port=port,
            user=os.environ.get("MYSQL_USER", "nirvana_app"),
            password=password,
            database=os.environ.get("MYSQL_DATABASE", "nirvana"),
            connection_timeout=4,
            charset="utf8mb4",
            collation="utf8mb4_unicode_ci",
            autocommit=False,
        )
    except mysql.connector.Error as error:
        raise DatabaseUnavailable("Cannot connect to local MySQL. Check that it is running and .env is correct.") from error


@contextmanager
def database() -> Iterator[mysql.connector.MySQLConnection]:
    connection = open_database()
    try:
        yield connection
    finally:
        connection.close()


@contextmanager
def transaction() -> Iterator[mysql.connector.MySQLConnection]:
    with database() as connection:
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def make_password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    salt_text = base64.urlsafe_b64encode(salt).decode("ascii").rstrip("=")
    derived_text = base64.urlsafe_b64encode(derived).decode("ascii").rstrip("=")
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt_text}${derived_text}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iteration_text, salt_text, expected_text = stored_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iteration_text)
        if not PASSWORD_ITERATIONS <= iterations <= 2_000_000:
            return False
        salt = base64.urlsafe_b64decode(salt_text + "=" * (-len(salt_text) % 4))
        expected = base64.urlsafe_b64decode(expected_text + "=" * (-len(expected_text) % 4))
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


DUMMY_PASSWORD_HASH = make_password_hash(secrets.token_urlsafe(24))


def normalize_email(value: Any) -> str:
    if not isinstance(value, str):
        raise ApiError(400, "Enter a valid email address.")
    email = value.strip().lower()
    if len(email) > 254 or not EMAIL_PATTERN.fullmatch(email):
        raise ApiError(400, "Enter a valid email address.")
    return email


def validate_password(value: Any) -> str:
    if not isinstance(value, str) or not PASSWORD_MIN_LENGTH <= len(value) <= PASSWORD_MAX_LENGTH:
        raise ApiError(400, f"Password must be {PASSWORD_MIN_LENGTH}-{PASSWORD_MAX_LENGTH} characters.")
    return value


def request_json() -> dict[str, Any]:
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ApiError(400, "Send a JSON request.")
    return payload


def session_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("ascii")).hexdigest()


def current_session() -> dict[str, Any] | None:
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token or len(raw_token) > 100 or not raw_token.isascii():
        return None
    token_digest = session_hash(raw_token)
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT s.token_hash, s.csrf_token, s.user_id, s.expires_at,
                       u.display_name, u.email, u.role
                FROM web_sessions AS s
                LEFT JOIN users AS u ON u.id = s.user_id
                WHERE s.token_hash = %s
                """,
                (token_digest,),
            )
            row = cursor.fetchone()
            if not row:
                return None
            if row["expires_at"] <= utc_now():
                cursor.execute("DELETE FROM web_sessions WHERE token_hash = %s", (token_digest,))
                connection.commit()
                return None
            user = None
            if row["user_id"] is not None:
                user = {
                    "id": row["user_id"],
                    "name": row["display_name"],
                    "email": row["email"],
                    "role": row["role"],
                }
            return {
                "token_hash": row["token_hash"],
                "csrf_token": row["csrf_token"],
                "user_id": row["user_id"],
                "user": user,
            }
        finally:
            cursor.close()


def require_csrf() -> dict[str, Any]:
    session = current_session()
    if not session:
        raise ApiError(401, "Your session expired. Refresh the page and try again.")
    submitted = request.headers.get("X-CSRF-Token", "")
    if not hmac.compare_digest(session["csrf_token"], submitted):
        raise ApiError(403, "Refresh the page and try again.")
    return session


def issue_session(user_id: int | None, previous_hash: str | None = None) -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(32)
    digest = session_hash(raw_token)
    csrf_token = secrets.token_urlsafe(32)
    expires_at = utc_now() + SESSION_TTL
    with transaction() as connection:
        cursor = connection.cursor()
        try:
            if previous_hash:
                cursor.execute("DELETE FROM web_sessions WHERE token_hash = %s", (previous_hash,))
            cursor.execute(
                """
                INSERT INTO web_sessions (token_hash, csrf_token, user_id, expires_at)
                VALUES (%s, %s, %s, %s)
                """,
                (digest, csrf_token, user_id, expires_at),
            )
        finally:
            cursor.close()
    return raw_token, csrf_token


def attach_session_cookie(response: Any, raw_token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        secure=request.is_secure,
        samesite="Lax",
        path="/",
    )


def clear_session_cookie(response: Any) -> None:
    response.delete_cookie(SESSION_COOKIE, httponly=True, secure=request.is_secure, samesite="Lax", path="/")


def require_admin() -> dict[str, Any]:
    session = require_csrf()
    if not session["user"] or session["user"]["role"] != "admin":
        raise ApiError(403, "Farm desk access is restricted to admin accounts.")
    return session


def validate_customer(name_value: Any, email_value: Any) -> tuple[str, str]:
    if not isinstance(name_value, str):
        raise ApiError(400, "Enter your name.")
    name = name_value.strip()
    if not 1 <= len(name) <= 80:
        raise ApiError(400, "Name must be between 1 and 80 characters.")
    return name, normalize_email(email_value)


def set_no_store(response: Any) -> Any:
    response.headers["Cache-Control"] = "no-store"
    return response


@app.after_request
def security_headers(response: Any) -> Any:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "object-src 'none'; "
        "base-uri 'self'; "
        "frame-ancestors 'none'"
    )
    if request.is_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.errorhandler(ApiError)
def handle_api_error(error: ApiError) -> Any:
    response = jsonify({"error": str(error)})
    response.status_code = error.status_code
    return set_no_store(response)


@app.errorhandler(DatabaseUnavailable)
def handle_database_unavailable(error: DatabaseUnavailable) -> Any:
    app.logger.warning("MySQL is unavailable: %s", error)
    response = jsonify({"error": str(error)})
    response.status_code = 503
    return set_no_store(response)


@app.errorhandler(mysql.connector.Error)
def handle_mysql_error(error: mysql.connector.Error) -> Any:
    app.logger.exception("MySQL request failed.")
    response = jsonify({"error": "The database request failed. Check the local MySQL setup and try again."})
    response.status_code = 503
    return set_no_store(response)


@app.errorhandler(413)
def handle_request_too_large(_: Any) -> Any:
    return set_no_store(jsonify({"error": "Request is too large."})), 413


@app.get("/")
def home() -> Any:
    return send_from_directory(APP_DIR, "index.html")


@app.get("/<path:asset>")
def static_asset(asset: str) -> Any:
    if asset not in ASSETS:
        abort(404)
    return send_from_directory(APP_DIR, asset)


@app.get("/api/session")
def api_session() -> Any:
    session = current_session()
    if session:
        return jsonify({"csrfToken": session["csrf_token"], "user": session["user"]})
    raw_token, csrf_token = issue_session(None)
    response = make_response(jsonify({"csrfToken": csrf_token, "user": None}))
    attach_session_cookie(response, raw_token)
    return response


@app.post("/api/auth/signup")
def api_signup() -> Any:
    anonymous_session = require_csrf()
    if anonymous_session["user_id"] is not None:
        raise ApiError(409, "You are already signed in.")
    payload = request_json()
    name, email = validate_customer(payload.get("name"), payload.get("email"))
    password = validate_password(payload.get("password"))
    password_hash = make_password_hash(password)
    try:
        with transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    "INSERT INTO users (display_name, email, password_hash, role) VALUES (%s, %s, %s, 'customer')",
                    (name, email, password_hash),
                )
                user_id = int(cursor.lastrowid)
            finally:
                cursor.close()
    except mysql.connector.IntegrityError as error:
        if error.errno == 1062:
            raise ApiError(409, "An account with that email already exists.") from error
        raise
    raw_token, csrf_token = issue_session(user_id, anonymous_session["token_hash"])
    response = make_response(
        jsonify(
            {
                "csrfToken": csrf_token,
                "user": {"id": user_id, "name": name, "email": email, "role": "customer"},
            }
        ),
        201,
    )
    attach_session_cookie(response, raw_token)
    return response


@app.post("/api/auth/login")
def api_login() -> Any:
    anonymous_session = require_csrf()
    payload = request_json()
    email = normalize_email(payload.get("email"))
    password = validate_password(payload.get("password"))
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT id, display_name, email, password_hash, role FROM users WHERE email = %s",
                (email,),
            )
            user = cursor.fetchone()
        finally:
            cursor.close()
    password_hash = user["password_hash"] if user else DUMMY_PASSWORD_HASH
    valid_password = verify_password(password, password_hash)
    if not user or not valid_password:
        raise ApiError(401, "Email or password is incorrect.")
    raw_token, csrf_token = issue_session(int(user["id"]), anonymous_session["token_hash"])
    response = make_response(
        jsonify(
            {
                "csrfToken": csrf_token,
                "user": {
                    "id": user["id"],
                    "name": user["display_name"],
                    "email": user["email"],
                    "role": user["role"],
                },
            }
        )
    )
    attach_session_cookie(response, raw_token)
    return response


@app.delete("/api/session")
def api_logout() -> Any:
    session = require_csrf()
    with transaction() as connection:
        cursor = connection.cursor()
        try:
            cursor.execute("DELETE FROM web_sessions WHERE token_hash = %s", (session["token_hash"],))
        finally:
            cursor.close()
    response = make_response(jsonify({"user": None}))
    clear_session_cookie(response)
    return response


@app.get("/api/products")
def api_products() -> Any:
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT slug, name, on_hand, reserved_quantity, unit_label, unit_price_paise,
                       on_hand - reserved_quantity AS available
                FROM product_inventory
                ORDER BY display_order, name
                """
            )
            products = cursor.fetchall()
        finally:
            cursor.close()
    return jsonify({"products": products})


def release_order_reservations(cursor: Any, order_id: int) -> None:
    cursor.execute(
        "SELECT product_slug, quantity FROM pickup_order_items WHERE order_id = %s",
        (order_id,),
    )
    for item in cursor.fetchall():
        cursor.execute(
            """
            UPDATE product_inventory
            SET reserved_quantity = reserved_quantity - %s
            WHERE slug = %s AND reserved_quantity >= %s
            """,
            (item["quantity"], item["product_slug"], item["quantity"]),
        )
        if cursor.rowcount != 1:
            raise ApiError(409, "Inventory changed unexpectedly. Check the stock records.")


def fail_initiating_payment(order_id: int) -> None:
    with transaction() as connection:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT status, payment_status
                FROM pickup_orders WHERE id = %s FOR UPDATE
                """,
                (order_id,),
            )
            order = cursor.fetchone()
            if not order or order["payment_status"] != "initiating":
                return
            release_order_reservations(cursor, order_id)
            cursor.execute(
                """
                UPDATE pickup_orders
                SET status = 'cancelled', payment_status = 'failed'
                WHERE id = %s AND payment_status = 'initiating'
                """,
                (order_id,),
            )
        finally:
            cursor.close()


@app.post("/api/orders")
def api_create_order() -> Any:
    session = require_csrf()
    if not session["user"] or session["user"]["role"] != "customer":
        raise ApiError(401, "Sign in with a customer account before placing a pickup order.")
    settings = phonepe_settings()
    payload = request_json()
    customer_name = session["user"]["name"]
    customer_email = session["user"]["email"]
    note = payload.get("note", "")
    if not isinstance(note, str) or len(note.strip()) > 1000:
        raise ApiError(400, "Pickup note must be 1,000 characters or fewer.")
    submitted_items = payload.get("items")
    if not isinstance(submitted_items, list) or not 1 <= len(submitted_items) <= len(PRODUCT_SLUGS):
        raise ApiError(400, "Choose at least one product.")
    items: dict[str, int] = {}
    for item in submitted_items:
        if not isinstance(item, dict):
            raise ApiError(400, "Invalid product request.")
        slug = item.get("product")
        quantity = item.get("quantity")
        if not isinstance(slug, str) or slug not in PRODUCT_SLUGS or type(quantity) is not int or not 1 <= quantity <= 99 or slug in items:
            raise ApiError(400, "Invalid product or quantity.")
        items[slug] = quantity

    order_id = 0
    with transaction() as connection:
        cursor = connection.cursor(dictionary=True)
        try:
            products: dict[str, dict[str, Any]] = {}
            for slug in sorted(items):
                cursor.execute(
                    """
                    SELECT slug, name, on_hand, reserved_quantity, unit_label,
                           unit_price_paise, unit_cost_paise
                    FROM product_inventory WHERE slug = %s FOR UPDATE
                    """,
                    (slug,),
                )
                product = cursor.fetchone()
                if not product or product["on_hand"] - product["reserved_quantity"] < items[slug]:
                    raise ApiError(409, "One or more items no longer have enough stock.")
                if product["unit_price_paise"] is None or product["unit_price_paise"] < 1:
                    raise ApiError(409, f"The farm has not set a price for {product['name']} yet.")
                if not product["unit_label"]:
                    raise ApiError(409, f"The farm has not set a sale unit for {product['name']} yet.")
                products[slug] = product
            total_amount_paise = sum(
                int(products[slug]["unit_price_paise"]) * quantity
                for slug, quantity in items.items()
            )
            cursor.execute(
                """
                INSERT INTO pickup_orders (
                    user_id, customer_name, customer_email, note, status, payment_status,
                    total_amount_paise, created_at
                )
                VALUES (%s, %s, %s, %s, 'pending', 'initiating', %s, %s)
                """,
                (
                    session["user_id"],
                    customer_name,
                    customer_email,
                    note.strip(),
                    total_amount_paise,
                    utc_now(),
                ),
            )
            order_id = int(cursor.lastrowid)
            merchant_order_id = f"NIRVANA-{order_id}-{secrets.token_hex(6)}"
            cursor.execute(
                "UPDATE pickup_orders SET merchant_order_id = %s WHERE id = %s",
                (merchant_order_id, order_id),
            )
            for slug, quantity in items.items():
                product = products[slug]
                cursor.execute(
                    """
                    INSERT INTO pickup_order_items (
                        order_id, product_slug, product_name, quantity, unit_label,
                        unit_price_paise, unit_cost_paise
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        order_id,
                        slug,
                        product["name"],
                        quantity,
                        product["unit_label"],
                        product["unit_price_paise"],
                        product["unit_cost_paise"],
                    ),
                )
                cursor.execute(
                    """
                    UPDATE product_inventory
                    SET reserved_quantity = reserved_quantity + %s
                    WHERE slug = %s AND on_hand - reserved_quantity >= %s
                    """,
                    (quantity, slug, quantity),
                )
                if cursor.rowcount != 1:
                    raise ApiError(409, "Stock changed while creating the request. Please try again.")
        finally:
            cursor.close()
    try:
        client = phonepe_client(settings)
        pay_request = StandardCheckoutPayRequest.build_request(
            merchant_order_id=merchant_order_id,
            amount=total_amount_paise,
            redirect_url=(
                f"{settings['public_base_url']}/?payment=phonepe&order={order_id}"
            ),
            expire_after=900,
        )
        payment = client.pay(pay_request)
        checkout_url = payment.redirect_url
        if not checkout_url:
            raise RuntimeError("PhonePe did not return a checkout URL.")
        with transaction() as connection:
            cursor = connection.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE pickup_orders
                    SET payment_status = 'pending', phonepe_order_id = %s
                    WHERE id = %s AND payment_status = 'initiating'
                    """,
                    (payment.order_id, order_id),
                )
                if cursor.rowcount != 1:
                    raise ApiError(409, "The order changed while starting payment. Contact the farm.")
            finally:
                cursor.close()
    except Exception as error:
        app.logger.exception("PhonePe checkout could not be started.")
        fail_initiating_payment(order_id)
        if isinstance(error, ApiError) and error.status_code == 409:
            raise
        raise ApiError(
            502,
            "PhonePe could not start this payment. The order was not confirmed; please try again.",
        ) from error
    return jsonify(
        {
            "id": order_id,
            "status": "pending",
            "paymentStatus": "pending",
            "totalAmountPaise": total_amount_paise,
            "checkoutUrl": checkout_url,
        }
    ), 201


@app.get("/api/admin/orders")
def api_admin_orders() -> Any:
    require_admin()
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT o.id, o.customer_name, o.customer_email, o.note, o.status,
                       o.payment_status, o.total_amount_paise, o.created_at,
                       i.product_slug, i.product_name, i.quantity,
                       i.unit_label, i.unit_price_paise
                FROM pickup_orders AS o
                LEFT JOIN pickup_order_items AS i ON i.order_id = o.id
                ORDER BY o.created_at DESC, o.id DESC
                """
            )
            rows = cursor.fetchall()
        finally:
            cursor.close()
    orders: dict[int, dict[str, Any]] = {}
    for row in rows:
        order_id = int(row["id"])
        if order_id not in orders:
            orders[order_id] = {
                "id": order_id,
                "name": row["customer_name"],
                "email": row["customer_email"],
                "note": row["note"],
                "status": row["status"],
                "paymentStatus": row["payment_status"],
                "totalAmountPaise": int(row["total_amount_paise"] or 0),
                "createdAt": row["created_at"].replace(tzinfo=timezone.utc).isoformat(),
                "items": [],
            }
        if row["product_slug"] is not None:
            orders[order_id]["items"].append(
                {
                    "product": row["product_name"],
                    "quantity": row["quantity"],
                    "unitLabel": row["unit_label"],
                    "unitPricePaise": int(row["unit_price_paise"] or 0),
                }
            )
    return jsonify({"orders": list(orders.values())})


@app.get("/api/admin/summary")
def api_admin_summary() -> Any:
    require_admin()
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT COUNT(*) AS total,
                       COALESCE(SUM(status = 'pending'), 0) AS pending,
                       COALESCE(SUM(status = 'confirmed'), 0) AS confirmed,
                       COALESCE(SUM(status = 'collected'), 0) AS collected,
                       COALESCE(SUM(status = 'cancelled'), 0) AS cancelled,
                       COALESCE(SUM(created_at >= UTC_TIMESTAMP() - INTERVAL 7 DAY), 0) AS last_seven_days
                FROM pickup_orders
                """
            )
            order_stats = {
                key: int(value or 0)
                for key, value in cursor.fetchone().items()
            }
            cursor.execute(
                """
                SELECT COUNT(*) AS product_count,
                       COALESCE(SUM(on_hand), 0) AS on_hand,
                       COALESCE(SUM(on_hand - reserved_quantity), 0) AS available,
                       COALESCE(SUM(reserved_quantity), 0) AS reserved,
                       COALESCE(SUM(on_hand - reserved_quantity > 0), 0) AS products_in_stock
                FROM product_inventory
                """
            )
            inventory_stats = {
                key: int(value or 0)
                for key, value in cursor.fetchone().items()
            }
            cursor.execute(
                """
                SELECT i.product_slug, MAX(i.product_name) AS name, SUM(i.quantity) AS quantity
                FROM pickup_order_items AS i
                JOIN pickup_orders AS o ON o.id = i.order_id
                WHERE o.status != 'cancelled'
                GROUP BY i.product_slug
                ORDER BY quantity DESC, name
                LIMIT 3
                """
            )
            top_products = [
                {**product, "quantity": int(product["quantity"] or 0)}
                for product in cursor.fetchall()
            ]
            cursor.execute(
                """
                SELECT
                    COALESCE(SUM(
                        CASE WHEN o.payment_status = 'paid' AND o.status != 'cancelled'
                        THEN o.total_amount_paise ELSE 0 END
                    ), 0) AS revenue_paise,
                    COALESCE(SUM(
                        CASE WHEN o.payment_status IN ('refund_due', 'refunding')
                        THEN o.total_amount_paise ELSE 0 END
                    ), 0) AS refund_due_paise
                FROM pickup_orders AS o
                """
            )
            payment_stats = {
                key: int(value or 0)
                for key, value in cursor.fetchone().items()
            }
            cursor.execute(
                """
                SELECT
                    COALESCE(SUM(
                        CASE WHEN o.payment_status = 'paid' AND o.status != 'cancelled'
                             AND i.unit_cost_paise IS NOT NULL
                        THEN i.unit_cost_paise * i.quantity ELSE 0 END
                    ), 0) AS recorded_cost_paise,
                    COALESCE(SUM(
                        CASE WHEN o.payment_status = 'paid' AND o.status != 'cancelled'
                             AND i.unit_cost_paise IS NULL
                        THEN 1 ELSE 0 END
                    ), 0) AS missing_cost_lines
                FROM pickup_orders AS o
                JOIN pickup_order_items AS i ON i.order_id = o.id
                """
            )
            cost_stats = {
                key: int(value or 0)
                for key, value in cursor.fetchone().items()
            }
        finally:
            cursor.close()
    return jsonify(
        {
            "orders": order_stats,
            "inventory": inventory_stats,
            "topProducts": top_products,
            "payments": {**payment_stats, **cost_stats},
        }
    )


@app.get("/api/orders/<int:order_id>/payment")
def api_order_payment(order_id: int) -> Any:
    session = current_session()
    if not session or not session["user"]:
        raise ApiError(401, "Sign in to check this order's payment.")
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT id, user_id, status, payment_status, total_amount_paise,
                       merchant_order_id
                FROM pickup_orders WHERE id = %s
                """,
                (order_id,),
            )
            order = cursor.fetchone()
        finally:
            cursor.close()
    if not order or (
        session["user"]["role"] != "admin"
        and int(order["user_id"] or 0) != int(session["user_id"])
    ):
        raise ApiError(404, "Pickup order not found.")

    payment_status = order["payment_status"]
    order_status = order["status"]
    if payment_status in {"initiating", "pending"}:
        if not order["merchant_order_id"]:
            raise ApiError(409, "This order is missing its PhonePe reference. Contact the farm.")
        try:
            status_response = phonepe_client().get_order_status(
                merchant_order_id=order["merchant_order_id"]
            )
            provider_state = phonepe_state(status_response)
        except Exception as error:
            app.logger.exception("PhonePe payment status could not be verified.")
            raise ApiError(502, "PhonePe could not verify the payment yet. Please try again.") from error
        if provider_state not in {"COMPLETED", "FAILED", "PENDING", "INITIATED", "IN_PROGRESS"}:
            app.logger.error("PhonePe returned an unexpected payment state: %s", provider_state)
            raise ApiError(502, "PhonePe returned an unexpected payment status. Contact the farm.")

        with transaction() as connection:
            cursor = connection.cursor(dictionary=True)
            try:
                cursor.execute(
                    """
                    SELECT status, payment_status
                    FROM pickup_orders WHERE id = %s FOR UPDATE
                    """,
                    (order_id,),
                )
                current = cursor.fetchone()
                if current and current["payment_status"] in {"initiating", "pending"}:
                    if provider_state == "COMPLETED":
                        payment_status = (
                            "refund_due" if current["status"] == "cancelled" else "paid"
                        )
                        cursor.execute(
                            """
                            UPDATE pickup_orders
                            SET payment_status = %s, paid_at = COALESCE(paid_at, %s)
                            WHERE id = %s
                            """,
                            (payment_status, utc_now(), order_id),
                        )
                    elif provider_state == "FAILED":
                        if current["status"] in {"pending", "confirmed"}:
                            release_order_reservations(cursor, order_id)
                            order_status = "cancelled"
                        payment_status = "failed"
                        cursor.execute(
                            """
                            UPDATE pickup_orders
                            SET status = %s, payment_status = 'failed'
                            WHERE id = %s
                            """,
                            (order_status, order_id),
                        )
                    else:
                        payment_status = "pending"
            finally:
                cursor.close()

    return jsonify(
        {
            "id": order_id,
            "status": order_status,
            "paymentStatus": payment_status,
            "totalAmountPaise": int(order["total_amount_paise"] or 0),
        }
    )


@app.post("/api/admin/orders/<int:order_id>/refund")
def api_refund_order(order_id: int) -> Any:
    require_admin()
    with transaction() as connection:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT payment_status, total_amount_paise, merchant_order_id,
                       merchant_refund_id
                FROM pickup_orders WHERE id = %s FOR UPDATE
                """,
                (order_id,),
            )
            order = cursor.fetchone()
            if not order:
                raise ApiError(404, "Pickup order not found.")
            if order["payment_status"] not in {"refund_due", "refunding"}:
                raise ApiError(409, "This order does not have a refund to process.")
            if not order["merchant_order_id"] or not order["total_amount_paise"]:
                raise ApiError(409, "This order is missing its PhonePe payment details.")
            merchant_refund_id = order["merchant_refund_id"]
            if not merchant_refund_id:
                merchant_refund_id = f"NIRVANA-R-{order_id}-{secrets.token_hex(6)}"
                cursor.execute(
                    "UPDATE pickup_orders SET merchant_refund_id = %s WHERE id = %s",
                    (merchant_refund_id, order_id),
                )
            should_create_refund = order["payment_status"] == "refund_due"
        finally:
            cursor.close()

    try:
        client = phonepe_client()
        if should_create_refund:
            refund_response = client.refund(
                RefundRequest.build_refund_request(
                    merchant_refund_id=merchant_refund_id,
                    amount=int(order["total_amount_paise"]),
                    original_merchant_order_id=order["merchant_order_id"],
                )
            )
        else:
            refund_response = client.get_refund_status(merchant_refund_id)
        refund_state = phonepe_state(refund_response)
    except Exception as error:
        app.logger.exception("PhonePe refund could not be verified.")
        raise ApiError(502, "PhonePe could not confirm the refund. Check its dashboard before retrying.") from error

    if refund_state not in {"COMPLETED", "FAILED", "PENDING", "INITIATED", "IN_PROGRESS"}:
        app.logger.error("PhonePe returned an unexpected refund state: %s", refund_state)
        raise ApiError(502, "PhonePe returned an unexpected refund status. Check its dashboard.")
    payment_status = (
        "refunded"
        if refund_state == "COMPLETED"
        else "refund_due"
        if refund_state == "FAILED"
        else "refunding"
    )
    with transaction() as connection:
        cursor = connection.cursor()
        try:
            cursor.execute(
                """
                UPDATE pickup_orders
                SET payment_status = %s,
                    phonepe_refund_id = %s,
                    refunded_at = CASE WHEN %s = 'refunded' THEN COALESCE(refunded_at, %s) ELSE refunded_at END
                WHERE id = %s AND payment_status IN ('refund_due', 'refunding')
                """,
                (
                    payment_status,
                    getattr(refund_response, "refund_id", None),
                    payment_status,
                    utc_now(),
                    order_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ApiError(409, "The refund state changed. Reload the admin dashboard.")
        finally:
            cursor.close()
    return jsonify({"id": order_id, "paymentStatus": payment_status})


@app.patch("/api/admin/orders/<int:order_id>")
def api_update_order(order_id: int) -> Any:
    require_admin()
    payload = request_json()
    new_status = payload.get("status")
    if not isinstance(new_status, str) or new_status not in {"confirmed", "collected", "cancelled"}:
        raise ApiError(400, "Choose confirmed, collected, or cancelled.")
    with transaction() as connection:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT status, payment_status FROM pickup_orders WHERE id = %s FOR UPDATE",
                (order_id,),
            )
            order = cursor.fetchone()
            if not order:
                raise ApiError(404, "Pickup request not found.")
            current_status = order["status"]
            current_payment_status = order["payment_status"]
            transitions = {
                "pending": {"confirmed", "cancelled"},
                "confirmed": {"collected", "cancelled"},
            }
            if new_status == current_status:
                return jsonify({"id": order_id, "status": current_status})
            if new_status not in transitions.get(current_status, set()):
                raise ApiError(409, "This pickup request can no longer be changed.")
            if new_status in {"confirmed", "collected"} and current_payment_status != "paid":
                raise ApiError(409, "A pickup can only be confirmed after PhonePe verifies payment.")
            if new_status in {"collected", "cancelled"}:
                if new_status == "cancelled":
                    release_order_reservations(cursor, order_id)
                else:
                    cursor.execute(
                        "SELECT product_slug, quantity FROM pickup_order_items WHERE order_id = %s",
                        (order_id,),
                    )
                    for item in cursor.fetchall():
                        cursor.execute(
                            """
                            UPDATE product_inventory
                            SET reserved_quantity = reserved_quantity - %s,
                                on_hand = on_hand - %s
                            WHERE slug = %s AND reserved_quantity >= %s AND on_hand >= %s
                            """,
                            (
                                item["quantity"],
                                item["quantity"],
                                item["product_slug"],
                                item["quantity"],
                                item["quantity"],
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise ApiError(409, "Inventory changed unexpectedly. Check the stock records.")
            payment_status = (
                "refund_due"
                if new_status == "cancelled" and current_payment_status == "paid"
                else current_payment_status
            )
            cursor.execute(
                "UPDATE pickup_orders SET status = %s, payment_status = %s WHERE id = %s",
                (new_status, payment_status, order_id),
            )
        finally:
            cursor.close()
    return jsonify({"id": order_id, "status": new_status, "paymentStatus": payment_status})


@app.get("/api/admin/inventory")
def api_admin_inventory() -> Any:
    require_admin()
    with database() as connection:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                """
                SELECT slug, name, on_hand, reserved_quantity, unit_label,
                       unit_price_paise, unit_cost_paise,
                       on_hand - reserved_quantity AS available
                FROM product_inventory
                ORDER BY display_order, name
                """
            )
            inventory = cursor.fetchall()
        finally:
            cursor.close()
    return jsonify({"inventory": inventory})


@app.put("/api/admin/inventory/<slug>")
def api_update_inventory(slug: str) -> Any:
    require_admin()
    if slug not in PRODUCT_SLUGS:
        raise ApiError(404, "Product not found.")
    payload = request_json()
    on_hand = payload.get("onHand")
    if type(on_hand) is not int or not 0 <= on_hand <= 100_000:
        raise ApiError(400, "Stock must be a whole number between 0 and 100,000.")
    if "unitLabel" not in payload or "unitPricePaise" not in payload or "unitCostPaise" not in payload:
        raise ApiError(400, "Send the sale unit, product price, and unit cost; optional values may be blank.")
    unit_label = payload["unitLabel"]
    if unit_label is not None:
        if not isinstance(unit_label, str) or len(unit_label.strip()) > 24:
            raise ApiError(400, "Sale unit must be 24 characters or fewer, or blank.")
        unit_label = unit_label.strip() or None
    unit_price_paise = validate_price_paise(payload["unitPricePaise"], "Product price")
    unit_cost_paise = validate_price_paise(
        payload["unitCostPaise"], "Product cost", allow_zero=True
    )
    with transaction() as connection:
        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT reserved_quantity FROM product_inventory WHERE slug = %s FOR UPDATE",
                (slug,),
            )
            product = cursor.fetchone()
            if not product:
                raise ApiError(404, "Product not found.")
            if on_hand < product["reserved_quantity"]:
                raise ApiError(409, "Stock cannot be lower than the amount reserved for pickup.")
            cursor.execute(
                """
                UPDATE product_inventory
                SET on_hand = %s, unit_label = %s,
                    unit_price_paise = %s, unit_cost_paise = %s
                WHERE slug = %s
                """,
                (on_hand, unit_label, unit_price_paise, unit_cost_paise, slug),
            )
        finally:
            cursor.close()
    return jsonify(
        {
            "slug": slug,
            "onHand": on_hand,
            "unitLabel": unit_label,
            "unitPricePaise": unit_price_paise,
            "unitCostPaise": unit_cost_paise,
        }
    )


def create_admin_account() -> None:
    print("Create the first Nirvana admin account. The password will not be echoed.")
    name = input("Admin name: ").strip()
    if not 1 <= len(name) <= 80:
        raise SystemExit("Admin name must be between 1 and 80 characters.")
    try:
        email = normalize_email(input("Admin email: "))
        password = validate_password(getpass("Admin password (12+ characters): "))
    except ApiError as error:
        raise SystemExit(str(error)) from error
    confirmation = getpass("Confirm password: ")
    if not hmac.compare_digest(password, confirmation):
        raise SystemExit("Passwords did not match.")
    with transaction() as connection:
        cursor = connection.cursor()
        try:
            cursor.execute(
                "INSERT INTO users (display_name, email, password_hash, role) VALUES (%s, %s, %s, 'admin')",
                (name, email, make_password_hash(password)),
            )
        except mysql.connector.IntegrityError as error:
            if error.errno == 1062:
                raise SystemExit("That email already has an account; existing accounts are not promoted automatically.") from error
            raise
        finally:
            cursor.close()
    print("Admin account created. Sign in on the Nirvana website.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "create-admin":
        create_admin_account()
    else:
        app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")), debug=False)
