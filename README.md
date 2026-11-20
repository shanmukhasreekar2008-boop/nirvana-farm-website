# Nirvana local website

The website uses a local Flask API and MySQL database for customer accounts, pickup requests, and farm stock. It listens only on this computer (`127.0.0.1`) and is not configured for public hosting.

## Set up MySQL

1. Install and start MySQL Server, then open MySQL Workbench and connect as an administrator.
2. Open and execute `schema.sql` in Workbench. It creates the `nirvana` database and its tables, including the payment fields.
3. Create a separate application account. Replace the example password with a strong password of your own:

   ```sql
   CREATE USER 'nirvana_app'@'127.0.0.1' IDENTIFIED BY 'choose-a-local-password';
   GRANT SELECT, INSERT, UPDATE, DELETE ON nirvana.* TO 'nirvana_app'@'127.0.0.1';
   ```

4. In PowerShell, copy the environment example and set the same password in `.env`:

   ```powershell
   Copy-Item .env.example .env
   ```

   Edit `.env` and replace `MYSQL_PASSWORD=replace-with-a-local-password`. `.env` is ignored by Git and must not be shared.

### Existing database

If you already created the earlier Nirvana database, back it up and execute `payment_migration.sql` once in MySQL Workbench while connected as a database administrator. Do not run it on a fresh database or run it a second time; the application account does not have permission to change the schema.

## Install and run

From the project folder in PowerShell:

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py create-admin
.\.venv\Scripts\python.exe server.py
```

The admin command prompts for the first admin name, email, and password without echoing the password. Open <http://127.0.0.1:5000> and sign in with that account to enter the admin-only Farm Desk. Admins can update stock, sale units, selling prices, and optional per-unit product costs. A product cannot be ordered until its price and sale unit are set. Customers can create accounts on the same site, see current stock, and pay for a pickup order through PhonePe hosted checkout. Payment is verified server-side; a successful payment return is not itself treated as proof of payment.

## PhonePe setup

1. Complete PhonePe Business/PG onboarding and obtain the API client ID, secret, and version from its merchant dashboard. Configure the settlement account there; this application cannot select or change where PhonePe settles funds.
2. Put those values in the local `.env` file. Start with `PHONEPE_ENV=SANDBOX`; sandbox payments are for testing and do not transfer real money.
3. Before taking real payments, switch to PhonePe production credentials and `PHONEPE_ENV=PRODUCTION`, set `PUBLIC_BASE_URL` to the site's public HTTPS address, and complete production onboarding. Do not expose the Flask development server to the internet.

PhonePe secrets must remain in `.env`, not in chat, source control, or screenshots. The admin dashboard reports verified paid revenue and recorded product costs; missing cost entries are counted separately. These figures do not include labor or other operating expenses and are not net profit.

## Early-stage limitations

This is a local development setup, not a production deployment. A public GitHub repository shares the source code but does not run the website; GitHub Pages cannot host this Flask/MySQL backend. Public launch needs a Python-capable host, production MySQL, HTTPS, and additional operational protections. It does not include email verification, password recovery, or delivery workflows. Do not expose the Flask development server to the public internet.
