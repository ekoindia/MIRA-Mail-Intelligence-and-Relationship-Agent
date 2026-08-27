# Reporting Console — Eko Bharat Ventures

An internal report-automation platform for Eko's SBI Kiosk Banking / BC (Business Correspondent) network. It reads live data from a Google Sheets "Calling Sheet," computes per-recipient figures (Account Opening, Social Security Scheme, Loan Lead Generation, Inactive CSPs, CSP Income Impact, DFS Incentive Slab), and drafts or sends one combined email per recipient per report frequency via Gmail.

This replaces an earlier Streamlit prototype — the app is now a **FastAPI backend + React/TypeScript frontend**, not Streamlit. If you find references to `streamlit run app.py` anywhere, they're stale.

---

## Architecture

```
weekly_report_dashboard_v4_automated/
├── app5/                        # Backend (FastAPI)
│   ├── api/
│   │   ├── main.py              # App entrypoint, router registration, lifespan (init_db, scheduler)
│   │   ├── auth.py               # JWT auth dependency
│   │   └── routers/              # One router per feature area (see below)
│   ├── database/
│   │   ├── db.py                 # Engine/session, Postgres→SQLite fallback, WAL + busy_timeout, init_db()
│   │   ├── models.py             # Core ORM models (users, templates, report_masters, distribution_jobs, email_logs, ...)
│   │   ├── org_models.py         # Org hierarchy (OrgUnit, OrgLevel enum: Branch/RBO/AO/LHO/Corporate Center)
│   │   ├── report_source_models.py  # ReportSource / ReportSourceRun / AutoDistributionSchedule
│   │   ├── incoming_models.py    # Inbound/sent Gmail tables: IncomingEmail, SentEmail, ExtractedTask, reply templates
│   │   ├── snapshot_models.py    # WeeklyReportSnapshot — period-over-period baseline for the growth report
│   │   └── suggestion_models.py  # Suggestion rows for the self-scanning agent feed
│   ├── services/                 # All business logic lives here, never in routers
│   └── utils/                    # Template rendering, date math, security, logging
└── web/                          # Frontend (React + Vite + TypeScript + Tailwind v4)
    └── src/
        ├── pages/                # Dashboard, Incoming, Reports, Templates, Scheduler, Suggestions, DeliveryLogs, AuditLogs, Settings, Login
        └── components/ui.tsx     # Shared component library (Card, Button, Badge/status-chip, Table, Toggle, ...)
```

**API routers** (`app5/api/routers/`): `auth`, `dashboard`, `org_units`, `reports` (report mapping + send-by-frequency), `templates`, `sources` (report source connectors), `schedules`, `logs` (delivery/audit), `gmail` (OAuth connect + settings), `tracking` (open-tracking pixel), `automation` (autosend toggles + skip-weekdays), `incoming` (inbox analytics, triage, work queue, sent-mail scan), `suggestions` (agent feed).

**Two pipelines, not one.** The report pipeline (Calling Sheet → aggregators → combined digest → outbound mail) is the original product. The **incoming-mail subsystem** (`services/incoming_service.py`, `sent_mail_service.py`, `limit_forward_service.py` + `web/src/pages/Incoming.tsx`) is a separate read/classify pipeline over the same connected mailbox. They share only the Gmail client. Everything the incoming side does that touches Gmail is **draft-only or read-only** — it has never been given send authority.

---

## Domain model

- **Org levels**: Branch → RBO → LHO → Corporate Center, plus AO. Every recipient resolves to exactly one of these (`database/org_models.py::OrgLevel`).
- **Schemes**: PMJDY (Account Opening), APY / PMSBY / PMJJBY (Social Security Scheme).
- **Reports** (`ReportMaster` rows), by frequency:
  - **Daily / RBO**: Account Opening & SSS (combined), Re-KYC & Inoperative Accounts *(paused)*.
  - **Weekly / RBO**: Account Opening & SSS, Loan Lead Generation, Re-KYC *(paused)*.
  - **Weekly / LHO**: Account Opening & SSS, Inactive CSPs, Loan Lead Generation, CSP Physical Camp *(paused)*, Server Issue *(paused)*.
  - **Weekly / Corporate Center**: Inactive CSPs, Loan Lead Generation, CSP Physical Camp *(paused)*, Server Issue *(paused)*.
  - **Weekly / Branch**: Loan Lead Generation only.
  - **Monthly / LHO & Corporate Center**: CSP Income Impact, DFS Incentive Slab, Inputs for Month-on-Month Growth *(paused)*.
  - *(paused)* means the report is configured but has no honest data source in the Calling Sheet yet (`report_aggregation_service.NOT_YET_AUTOMATED_REPORTS`) — it's deliberately excluded from emails rather than sent with fabricated data.
- **The Calling Sheet** (`services/calling_sheet_service.py`) is the single live data source for everything. Columns are resolved **by header name/pattern every call**, never by hardcoded position — the sheet's structure changes over time (month-rollover, inserted columns).

---

## How a report email actually gets built

**Combined-digest architecture** (`services/combined_digest_service.py`): one email per recipient per (frequency, org level), combining every automated report mapped to that level. `_UNIT_AGGREGATORS` maps a report name to its aggregator function; when 2+ reports combine into one email, each unit's context keys get a name prefix (`LL_`, `INACT_`, `DFS_`, `INCOME_`) to avoid collisions — a level with only one report (e.g. Weekly Branch) keeps unprefixed keys.

**Template resolution** (`resolve_digest_template_id`): a level with exactly one automated report reuses that report's own `ReportMaster.default_template_id` FK directly. A level with 2+ reports looks up a template by exact **name** (`MULTI_REPORT_DIGEST_TEMPLATE_NAMES`, e.g. `"Weekly RBO Update"`). ⚠️ **Known gotcha**: editing a template's report list via the Templates page's save flow can silently clear another template's `default_template_id` FK if not careful — always spot-check `report_masters.default_template_id` after any bulk template script.

**Conditional sections** (`utils/helpers.py`): templates support `{{#if Flag}}...{{/if}}` blocks so a whole section (e.g. Loan Lead Generation) is omitted for a recipient with nothing to report, while every variable inside stays individually visible/editable in the Templates page — not collapsed into an opaque server-rendered blob. Loan Lead Generation skips when the recipient generated zero leads that period (a real "0" is meaningfully skippable there); Inactive CSPs shows a real "0 inactive" as legitimate data and only skips when the recipient has literally no CSPs in scope.

**Freshness gate** (`services/calling_sheet_freshness_service.py`): before any draft/send, the sheet's raw content is hashed and compared against the last confirmed-fresh snapshot — if unchanged, nothing is drafted (prevents re-sending yesterday's numbers during an upstream outage).

---

## Automation

Only the **Daily** cycle auto-fires: `services/autosend_service.py` polls every minute, fetches at `AUTOSEND_FETCH_TIME` (rechecking hourly until the sheet is confirmed fresh), then drafts/sends once fresh at/after `AUTOSEND_SEND_TIME`. Toggle in Settings/Scheduler; off by default.

The Daily cycle runs **every day, including Monday** (`autosend_skip_weekdays` — empty by default now; was `"0"`/Monday until 2026-08-19, removed per explicit instruction so Monday gets both the daily send and the weekly one). The skip mechanism itself is still there and is checked before any freshness/state bookkeeping so a skipped day leaves the run state untouched, if a skip day is ever configured again.

**Weekly and Monthly are 100% manual** — someone clicks "Draft Weekly" / "Draft Monthly" on the Scheduler page. No cron-like schedule exists for them. In practice the weekly report is drafted **every Monday**, and every Weekly `ReportMaster` is `delivery_mode='draft'`; sending weekly would require a deliberate change.

Draft-vs-send is controlled per report (`ReportMaster.delivery_mode`). The RBO/AO draft-only safety net applies in `combined_digest_service.send_combined_digest` only — it was intentionally removed from `report_send_service.send_report_now`, where `force_draft` is now caller-controlled (manual "Draft Only" button passes `True`, autosend passes `False`).

**Weekly growth comparison**: after each weekly run, `services/snapshot_service.py` copies the drafted per-recipient context into `WeeklyReportSnapshot`; the next Monday, `services/growth_service.py` reads it back as a baseline and injects per-recipient week-over-week %. The current week's *numbers* always come from the live Calling Sheet — the snapshot is only ever the comparison baseline.

---

## Running locally

**Requirements**: Python 3.11+, Node 18+, a Google Cloud OAuth client (Gmail + Sheets scopes) for the connected account, `credentials.json` in `app5/`.

```bash
# Backend
cd app5
venv/Scripts/python.exe -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```

⚠️ Run **without** `--reload` — WatchFiles-based auto-reload has been observed to silently miss rapid successive edits on this machine. Every backend Python change needs a full kill + restart of the process, not just a file save, before you trust a live HTTP-level test.

```bash
# Frontend
cd web
npm install
npm run dev        # http://localhost:5173, proxies API calls to :8000
```

Copy `app5/.env.example` → `app5/.env` and fill in real values. Key settings: `DB_ENGINE` (falls back to SQLite automatically if Postgres is unreachable — never fails to boot on a DB outage), `GMAIL_CREDENTIALS_PATH`/`GMAIL_TOKEN_PATH` (connect once via Settings page in the UI, interactive OAuth), `AUTOSEND_*`, `EMAIL_BATCH_SIZE`/`EMAIL_PACE_*` (real-send pacing to avoid looking like an automated burst to Gmail — drafts skip this pacing entirely, only real sends are throttled).

Default admin login is seeded on first run from `DEFAULT_ADMIN_USERNAME`/`DEFAULT_ADMIN_PASSWORD`.

---

## Known gotchas

- **Template FK can silently go NULL.** A template's report mapping is either FK-based (single report) or name-based (multi-report digest). Editing templates via a script that doesn't carefully preserve `reportIds` for every affected template can clear another template's FK — always verify `report_masters.default_template_id` is still set after any bulk template edit.
- **uvicorn `--reload` is unreliable here** — see above. Always restart clean.
- **The Gmail MCP-style draft tools some agents may reach for cannot send** — only this app's own `services/gmail_service.py` (`send_message`, `get_default_signature`, `create_outbound_draft`) can actually send or fetch the connected account's real signature.
- **No same-day dedup guard on manual draft buttons** — repeated clicks of "Draft Daily"/"Draft Weekly" will re-draft everyone currently in scope; the freshness gate only prevents re-drafting *stale* data, not accidental double-clicks on fresh data. This also produces duplicate snapshot rows for that date, which is why `growth_service.load_previous_contexts` orders deterministically.
- **`{{#if}}` supports only one level of nesting.** The matching regex is non-greedy, so a conditional inside a conditional leaves the literal `{{#if ...}}` text in the rendered email. Always check for leftover `{{...}}` after editing a template.
- **Templates contain literal `%` characters** — `%`-style string formatting in any template-rewriting script will crash. Use concatenation or `.replace()`.
- **Long write transactions hit `database is locked`.** The 1-minute pollers are always running, so any bulk update loop must commit per row rather than wrapping hundreds of updates in one transaction. The same applies to loops making external API calls, where per-row commits also preserve partial progress.
- **Reclassifiers read stale data inside an uncommitted session.** If a suggestion executor writes then calls a reclassifier, commit the write in its own `get_db()` block first — otherwise the reclassifier silently reports "0 changed".
- **Triage keyword rules are ordered, first-match-wins, and easy to over-broaden.** Narrow/specific rules must sit above broad ones, and matching reads only the subject plus the first ~600 chars of body (quoted reply chains below that cause false matches). Over-broad keywords have mis-tagged real work three separate times.
- **Gmail timestamps**: prefer `internalDate` over the `Date` header, and always emit ISO strings with a trailing `Z`. Emitting naive ISO makes the browser parse it as local time and shift displayed times by hours.
- **Team aliases must be listed in `GMAIL_ACCOUNT_ALIASES`** — mail sent to an unlisted alias falls into an "unknown" bucket that's excluded from analysis, silently.
- **Open-rate stats need a 2-day lookback**, otherwise mail sent yesterday and opened today never counts. Open tracking against external recipients also needs `PUBLIC_BASE_URL` set (currently empty).

---

## Security notes

- Passwords are bcrypt-hashed. Set a strong, random `SECRET_KEY` in production.
- The connected Gmail account has real send authority — treat `credentials.json` / `data/gmail_token.json` as sensitive.
- Audit logs (`services/audit_service.py`) are append-only from the UI.
- **The incoming-mail subsystem has no send authority and must keep it that way.** Limit-approval forwarding (`services/limit_forward_service.py`) creates Gmail **drafts only**; `_is_from_forward_target()` is a hard loop guard against forwarding the approver's own mail back. The Suggestions agent is scoped to internal app config/data fixes and must never be extended to draft or send email autonomously.
- Reply-template auto-reply (drafting replies to inbound mail) is **not enabled**. Before it ever is, the empty "Delivery Status Notification (Failure)" reply template must be deleted or deactivated.
