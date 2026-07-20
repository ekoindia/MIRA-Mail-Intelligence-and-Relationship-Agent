# 📊 Weekly Report Distribution Dashboard

An enterprise-grade Streamlit application for uploading weekly reports (PDF/Excel)
and automatically distributing them to Branches and LHOs via email, based on
configurable recipient mappings.

---

## ✨ Features

- **Secure login** with Admin/Operator roles (bcrypt-hashed passwords)
- **Branch/LHO Master** — bulk Excel upload (Branch Code, Branch Name, Branch Email, LHO, LHO Email)
- **Report Master** — configure report types with recipient rules (Branch / LHO / Both)
- **Multi-file upload** for PDF/XLSX reports with size and type validation
- **Automatic recipient mapping**, with manual override by Branch, LHO, or Region
- **Distribution Preview** — see report, recipients, counts, attachment before sending
- **Email Template Builder** with variables: `{{Recipient_Name}}`, `{{Branch_Name}}`, `{{LHO_Name}}`, `{{Report_Name}}`, `{{Date}}`
- **Email delivery** via Microsoft Graph API (primary), automatic SMTP fallback
- **Batch sending**, retry logic, live progress bar, per-recipient delivery tracking
- **Scheduler** — daily/weekly/monthly recurring distribution with next/last run tracking
- **Dashboard** — KPIs and charts for reports uploaded, emails sent/failed, success rate
- **Delivery Logs** — filter by date, report, status, branch, LHO; CSV export
- **Failed Email Management** — retry selected or retry all
- **Full Audit Log** — logins, uploads, sends, edits (Admin only)
- **Settings** — email config status, batch size/retries, theme, user management
- **Dark/Light mode**, responsive KPI-card UI

---

## 🏗️ Architecture

```
weekly_report_dashboard/
├── app.py                     # Entry point: login + landing dashboard
├── config.py                  # Environment-driven settings
├── database/
│   ├── db.py                  # Engine/session, Postgres→SQLite fallback, init_db()
│   └── models.py               # SQLAlchemy ORM models (full schema)
├── services/                  # Business logic (REST-style service layer)
│   ├── auth_service.py         # Login, user management
│   ├── upload_service.py       # Master data + report file uploads
│   ├── distribution_service.py # Recipient resolution + job creation
│   ├── email_service.py        # Graph API / SMTP sending, batching, retry
│   ├── scheduler_service.py    # Recurring schedule logic
│   └── audit_service.py        # Audit trail writer/reader
├── pages/                     # Streamlit multipage UI
│   ├── 1_Dashboard.py
│   ├── 2_Upload_Reports.py
│   ├── 3_Branch_LHO_Master.py
│   ├── 4_Report_Master.py
│   ├── 5_Distribution.py
│   ├── 6_Email_Templates.py
│   ├── 7_Delivery_Logs.py
│   ├── 8_Failed_Emails.py
│   ├── 9_Scheduler.py
│   ├── 10_Audit_Logs.py
│   └── 11_Settings.py
├── utils/
│   ├── security.py             # bcrypt hashing
│   ├── validators.py           # File/email/master-data validation
│   ├── helpers.py              # Template rendering, date math
│   ├── logger.py                # Rotating file + console logging
│   └── ui.py                    # Shared Streamlit UI components
├── static/style.css            # Enterprise UI styling
├── scheduler_worker.py         # Standalone always-on scheduler process
├── requirements.txt
└── .env.example
```

**Design principles:** modular service layer (business logic never lives in
page files), SQLAlchemy ORM with typed models, environment-based config,
structured error handling + validation at every entry point, and a rotating
file logger for production diagnostics.

---

## 🚀 Local Setup

### 1. Clone and install dependencies

```bash
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env`:
- Set `DB_ENGINE=postgres` and fill in `POSTGRES_*` values, **or** leave it as
  `DB_ENGINE=sqlite` for a zero-setup local database (`data/app.db`).
- If PostgreSQL is unreachable at startup, the app **automatically falls back
  to SQLite** — it will never fail to boot because of a DB outage.
- Fill in either `MS_GRAPH_*` (recommended for enterprise/M365 tenants) or
  `SMTP_*` credentials. If Graph is disabled or a Graph call fails, the app
  automatically falls back to SMTP.
- Set `DEFAULT_ADMIN_USERNAME` / `DEFAULT_ADMIN_PASSWORD` — a default Admin
  user is seeded automatically on first run if no users exist yet.

### 3. Run the app

```bash
streamlit run app.py
```

Open `http://localhost:8501` and log in with your default admin credentials.

### 4. (Recommended for production) Run the scheduler worker

The in-app scheduler only fires while the Streamlit process is alive. For
reliable recurring sends, also run the standalone worker:

```bash
python scheduler_worker.py
```

---

## 🔑 Microsoft Graph API Setup

1. Register an app in **Azure AD → App Registrations**.
2. Grant **Application permission** `Mail.Send` (admin consent required).
3. Create a client secret.
4. Set in `.env`:
   ```
   MS_GRAPH_ENABLED=true
   MS_GRAPH_TENANT_ID=<tenant-id>
   MS_GRAPH_CLIENT_ID=<app-client-id>
   MS_GRAPH_CLIENT_SECRET=<client-secret>
   MS_GRAPH_SENDER_EMAIL=reports@yourcompany.com
   ```
5. The sender mailbox (`MS_GRAPH_SENDER_EMAIL`) must be a real, licensed
   mailbox the app has permission to send as.

If Graph is not configured, set `MS_GRAPH_ENABLED=false` and fill in
`SMTP_*` instead — SMTP works standalone too.

---

## 🗄️ Database Notes

- **PostgreSQL** is the recommended production database — set `DB_ENGINE=postgres`.
- Tables are created automatically on first run via `init_db()` (no manual
  migrations needed for a fresh install). For schema changes on an existing
  database, introduce **Alembic** migrations before altering `database/models.py`
  in production.
- **SQLite fallback** (`data/app.db`) is used automatically if Postgres is
  unset or unreachable — ideal for local development or demos.

---

## 🖥️ Server Deployment

### Option A — systemd (Linux)

```ini
# /etc/systemd/system/report-dashboard.service
[Unit]
Description=Weekly Report Distribution Dashboard
After=network.target

[Service]
WorkingDirectory=/opt/weekly_report_dashboard
ExecStart=/opt/weekly_report_dashboard/venv/bin/streamlit run app.py --server.port 8501 --server.address 0.0.0.0
Restart=always
User=www-data
EnvironmentFile=/opt/weekly_report_dashboard/.env

[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/report-scheduler.service
[Unit]
Description=Report Distribution Scheduler Worker
After=network.target

[Service]
WorkingDirectory=/opt/weekly_report_dashboard
ExecStart=/opt/weekly_report_dashboard/venv/bin/python scheduler_worker.py
Restart=always
User=www-data
EnvironmentFile=/opt/weekly_report_dashboard/.env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now report-dashboard report-scheduler
```

Put Nginx in front for TLS termination and reverse-proxy to `127.0.0.1:8501`.

### Option B — Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8501
CMD ["streamlit", "run", "app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

Run the scheduler as a second container/service from the same image with
`CMD ["python", "scheduler_worker.py"]`, sharing the same `.env` and pointing
both at the same PostgreSQL instance.

### Option C — Streamlit Community Cloud / PaaS

Works out of the box with `DB_ENGINE=sqlite` for small deployments, but note
platform ephemeral storage may reset uploaded files/SQLite data on redeploy —
use a managed PostgreSQL (e.g. Supabase, RDS, Azure Database for PostgreSQL)
for anything persistent.

---

## 🔐 Security Notes

- Passwords are hashed with bcrypt (12 rounds); never stored in plaintext.
- Set a strong, random `SECRET_KEY` in production.
- Restrict `MASTER_UPLOAD_DIR` / `UPLOAD_DIR` permissions on the host.
- Rotate the Graph API client secret and SMTP password periodically.
- Audit logs are append-only from the UI — there is no in-app delete function.

---

## 📄 License

Internal enterprise tool — adapt freely for your organization.
