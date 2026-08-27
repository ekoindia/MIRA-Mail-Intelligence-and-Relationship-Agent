# MIRA — Reporting Console

A full-stack platform that automates recurring business reporting end-to-end: it pulls live figures from Google Sheets, computes per-recipient breakdowns, renders them into branded email templates, and delivers them through Gmail on a schedule — with a companion inbox-intelligence layer that classifies incoming mail, tracks reply/turnaround patterns, and surfaces self-service fixes for its own data-quality issues.

Built as a **FastAPI + React/TypeScript** rewrite of an earlier Streamlit prototype, currently running as an internal tool for a banking-correspondent network's operations reporting.

---

## What it does

**Outbound reporting**
- Reads a live Google Sheet as the single source of truth — columns are resolved by header name every run, not by hardcoded position, so the pipeline survives upstream structural changes (inserted columns, month rollovers).
- Aggregates figures per recipient, per organizational level (Branch → RBO → LHO → Corporate Center), and per report frequency (Daily / Weekly / Monthly).
- Combines every report mapped to a recipient into a single digest email, with conditional `{{#if}}` sections so an empty report unit is omitted rather than sent with placeholder data.
- Computes week-over-week / month-over-month growth against a stored snapshot baseline, so every recipient sees trend, not just a raw number.
- A freshness gate hashes the source sheet before every send — if nothing changed upstream, nothing goes out, preventing stale re-sends during an upstream outage.
- Daily send cycle runs unattended on a schedule; Weekly/Monthly are deliberately manual triggers. Draft-only vs. auto-send is configured per report.

**Inbound intelligence**
- A separate, strictly read/classify pipeline over the same mailbox: ingests incoming mail, classifies intent, extracts actionable tasks into a work queue, and detects recurring subject patterns to score how much of the inbox could be handled automatically.
- Auto-acknowledgement drafting for well-understood intents, always as a *draft* — this subsystem has never been given send authority.
- A self-scanning "Suggestions" feed that flags its own data-quality issues (missed classifications, stale mappings) with one-click, scoped auto-fixes — it is explicitly barred from ever touching outbound mail.

---

## Architecture

```
├── app5/                        # Backend — FastAPI
│   ├── api/
│   │   ├── main.py              # App entrypoint, router registration, lifespan (init_db, scheduler)
│   │   ├── auth.py              # JWT auth dependency
│   │   └── routers/             # One router per feature area
│   ├── database/                # SQLAlchemy models (report config, org hierarchy, inbox tables, snapshots)
│   ├── services/                # All business logic — sheet parsing, aggregation, rendering, Gmail, scheduling
│   └── utils/                   # Template rendering, date math, security, logging
└── web/                         # Frontend — React 19 + TypeScript + Vite + Tailwind v4
    └── src/
        ├── pages/                # Dashboard, Incoming, Reports, Templates, Scheduler, Suggestions, Delivery/Audit Logs, Settings
        └── components/           # Shared UI library + feature components
```

**API surface** (`app5/api/routers/`): `auth`, `dashboard`, `org_units`, `reports`, `templates`, `sources`, `schedules`, `logs`, `gmail`, `tracking`, `automation`, `incoming`, `suggestions`.

Two pipelines share only the Gmail client: the **reporting pipeline** (sheet → aggregate → render → send) is the original product; the **incoming-mail subsystem** is an independently-built read/classify layer over the same inbox, with its own database tables and its own, much narrower, permissions.

Full architectural notes, domain model, and known gotchas live in [`app5/README.md`](app5/README.md).

---

## Tech stack

| Layer | Stack |
|---|---|
| Backend | FastAPI, SQLAlchemy 2.0, APScheduler, PostgreSQL with automatic SQLite fallback |
| Frontend | React 19, TypeScript, Vite, Tailwind CSS v4, TanStack Query, Recharts |
| Integrations | Gmail API, Google Sheets (via `requests`/`gspread`-style read), OAuth 2.0 |
| Auth | JWT sessions, bcrypt password hashing |

---

## Running locally

**Requirements**: Python 3.11+, Node 18+, a Google Cloud OAuth client (Gmail + Sheets scopes).

```bash
# Backend
cd app5
python -m venv venv
venv/Scripts/pip install -r requirements.txt   # venv/bin/pip on macOS/Linux
cp .env.example .env                           # fill in real values — see below
venv/Scripts/python.exe -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

```bash
# Frontend
cd web
npm install
npm run dev        # http://localhost:5173, proxies API calls to :8000
```

Drop your Gmail OAuth client as `app5/credentials.json` (never committed — see `.gitignore`), then connect the account once from the app's Settings page. A default admin account is seeded on first run from `DEFAULT_ADMIN_USERNAME` / `DEFAULT_ADMIN_PASSWORD` in `.env` — change it immediately in a real deployment.

---

## Project status

Actively developed internal tool. This repository contains application code only — no production data, credentials, or the connected mailbox's content are included or ever committed (see `.gitignore` at the root and inside `app5/`).

## License

[MIT](LICENSE)
