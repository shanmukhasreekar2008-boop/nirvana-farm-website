import os
import unittest
from unittest.mock import patch

import server


class PasswordHashTests(unittest.TestCase):
    def test_password_hash_is_salted_and_verifies(self):
        password = "a-unique-nirvana-test-password"
        first_hash = server.make_password_hash(password)
        second_hash = server.make_password_hash(password)

        self.assertNotEqual(first_hash, second_hash)
        self.assertTrue(server.verify_password(password, first_hash))
        self.assertFalse(server.verify_password("a-different-password", first_hash))

    def test_malformed_hash_fails_closed(self):
        self.assertFalse(server.verify_password("any-password", "not-a-valid-hash"))


class PhonePeConfigurationTests(unittest.TestCase):
    def test_checkout_is_disabled_without_phonepe_credentials(self):
        environment = {
            "PHONEPE_CLIENT_ID": "",
            "PHONEPE_CLIENT_SECRET": "",
            "PHONEPE_CLIENT_VERSION": "",
        }
        with patch.dict(os.environ, environment):
            with self.assertRaises(server.ApiError) as error:
                server.phonepe_settings()

        self.assertEqual(error.exception.status_code, 503)

    def test_sandbox_accepts_local_development_url(self):
        environment = {
            "PHONEPE_CLIENT_ID": "sandbox-client",
            "PHONEPE_CLIENT_SECRET": "sandbox-secret",
            "PHONEPE_CLIENT_VERSION": "1",
            "PHONEPE_ENV": "SANDBOX",
            "PUBLIC_BASE_URL": "http://127.0.0.1:5000",
        }
        with patch.dict(os.environ, environment):
            settings = server.phonepe_settings()

        self.assertEqual(settings["environment"], "SANDBOX")
        self.assertEqual(settings["public_base_url"], "http://127.0.0.1:5000")

    def test_production_requires_https(self):
        environment = {
            "PHONEPE_CLIENT_ID": "production-client",
            "PHONEPE_CLIENT_SECRET": "production-secret",
            "PHONEPE_CLIENT_VERSION": "1",
            "PHONEPE_ENV": "PRODUCTION",
            "PUBLIC_BASE_URL": "http://127.0.0.1:5000",
        }
        with patch.dict(os.environ, environment):
            with self.assertRaises(server.ApiError) as error:
                server.phonepe_settings()

        self.assertEqual(error.exception.status_code, 503)
        self.assertIn("HTTPS", str(error.exception))


class PriceValidationTests(unittest.TestCase):
    def test_accepts_price_and_optional_zero_cost(self):
        self.assertEqual(server.validate_price_paise(1, "Selling price"), 1)
        self.assertEqual(
            server.validate_price_paise(0, "Unit cost", allow_zero=True), 0
        )
        self.assertIsNone(server.validate_price_paise(None, "Selling price"))

    def test_rejects_zero_selling_price_and_values_above_limit(self):
        for value in (0, 100_000_001, True):
            with self.subTest(value=value):
                with self.assertRaises(server.ApiError):
                    server.validate_price_paise(value, "Selling price")


class BackendSmokeTests(unittest.TestCase):
    def setUp(self):
        self.client = server.app.test_client()

    def test_site_assets_are_served(self):
        responses = [self.client.get(path) for path in ("/", "/script.js", "/styles.css")]
        try:
            self.assertTrue(all(response.status_code == 200 for response in responses))
        finally:
            for response in responses:
                response.close()

    def test_public_page_no_longer_loads_the_3d_farm_map(self):
        page = self.client.get("/")
        script = self.client.get("/script.js")
        try:
            self.assertNotIn(b"farm-map", page.data)
            self.assertNotIn(b"initializeFarmWalk", script.data)
            self.assertNotIn(b"THREE", script.data)
        finally:
            page.close()
            script.close()

    @patch.dict(os.environ, {"MYSQL_PASSWORD": ""})
    def test_api_reports_missing_mysql_configuration(self):
        response = self.client.get("/api/products")

        self.assertEqual(response.status_code, 503)
        self.assertIn(".env", response.get_json()["error"])

    def test_admin_summary_requires_a_csrf_protected_session(self):
        response = self.client.get("/api/admin/summary")

        self.assertEqual(response.status_code, 401)
        self.assertIn("session", response.get_json()["error"].lower())
