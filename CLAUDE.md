# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Backend** (`app5/`, FastAPI):
```bash
cd app5
venv/Scripts/python.exe -m uvicorn api.main:app --host 0.0.0.0 --port 8000
```
Run **without** `--reload` — WatchFiles-based auto-reload has been observed to silently miss rapid successive edits on this machine. After any backend Python change, kill the process and start it fresh before trusting a live HTTP-level test; don't assume `--reload` is even active. There is no automated test suite for the backend.

**Frontend** (`web/`, React + Vite + TypeScript + Tailwind v4):
```bash
cd web
npm install
npm run dev      # http://localhost:5173, proxies API calls to :8000
npm run build     # tsc -b && vite build
npm run lint      # oxlint
```
There is no frontend test suite either — verify UI changes by running the dev server and checking in a browser. `npx tsc --noEmit -p tsconfig.app.json` is the fastest way to catch a broken JSX edit (a dropped `)}` during a section reorder) before reloading anything.

**Verification habits that have repeatedly paid off here**, in the absence of tests:
- Before bulk-applying anything to real recipients, render/preview **one sample** and read it. This has caught real mistakes more than once.
- After rendering a changed template, assert that **no literal `{{...}}` remains** in the output.
- When slicing rendered HTML to check a specific section, confirm your delimiter is actually unique — a marker word appearing earlier in prose makes the slice silently wrong, and the resulting "it didn't render" conclusion false.
- After any state-changing run, **query the DB for the real row counts** rather than trusting the run's own summary output.

**Environment**: copy `app5/.env.example` → `app5/.env`. `DB_ENGINE` falls back to SQLite automatically if Postgres is unreachable — the app never fails to boot on a DB outage. Gmail is connected via interactive OAuth from the Settings page in the UI (`GMAIL_CREDENTIALS_PATH`/`GMAIL_TOKEN_PATH`), not via env-only setup.

This is a real git repo (`git log` shows an initial commit + one prior commit) with uncommitted local changes as of writing — check `git status` before assuming a clean tree.

## Architecture

This is an internal report-automation platform for Eko Bharat Ventures' SBI Kiosk Banking / BC network. It reads live data from a Google Sheets "Calling Sheet," computes per-recipient figures, and drafts/sends one combined email per recipient per report frequency via a connected Gmail account.

**Domain model**: recipients resolve to one of 5 org levels — Branch → RBO → LHO → Corporate Center, plus AO (`database/org_models.py::OrgLevel`). Schemes: PMJDY (Account Opening), APY/PMSBY/PMJJBY (Social Security Scheme). Reports are `ReportMaster` rows tagged with `frequency` (Daily/Weekly/Monthly) and `org_levels` (comma-separated levels they apply to). Several reports are configured but intentionally **paused** (Re-KYC & Inoperative Accounts, CSP Physical Camp, Server Issue, Inputs for Month-on-Month Growth) because the Calling Sheet has no honest data source for them yet — they must stay excluded from emails rather than sent with fabricated numbers; see `report_aggregation_service.NOT_YET_AUTOMATED_REPORTS`.

**The Calling Sheet is the single live data source for everything** (`services/calling_sheet_service.py`). Every column is resolved by header name/pattern on every call — never by hardcoded position — because the sheet's structure changes over time (month rollover on labels like "MTD Achievement July'26", inserted columns). Any new aggregator must follow this same pattern.

**Combined-digest architecture** (`services/combined_digest_service.py`) is the core of the send pipeline: one email per recipient per (frequency, org level), combining every automated report mapped to that level via `_UNIT_AGGREGATORS` (report name → aggregator function). When 2+ reports combine into one email, each unit's context keys get a name prefix (`LL_`, `INACT_`, `DFS_`, `INCOME_`) to avoid key collisions between units; a level with only one report (e.g. Weekly Branch → Loan Lead Generation only) keeps unprefixed keys.

**Template resolution** (`resolve_digest_template_id` in the same file) has two paths: a level with exactly one automated report reuses that report's own `ReportMaster.default_template_id` FK directly; a level with 2+ reports looks up a template by exact **name** (`MULTI_REPORT_DIGEST_TEMPLATE_NAMES`, e.g. `"Weekly RBO Update"`). **This FK can silently go NULL** if a template's report mapping is edited via a script/API call that doesn't carefully preserve `reportIds` for every affected template (it happened twice in one session, for two different templates) — always spot-check `report_masters.default_template_id` after any bulk template edit, via `api/routers/templates.py`'s `_reports_by_template` or a direct query.

**Conditional template sections** (`{{#if Flag}}...{{/if}}`, implemented in `utils/helpers.py::render_template`) let a whole section (e.g. Loan Lead Generation) disappear for a recipient with nothing to report, while every variable inside stays individually visible/editable in the Templates UI rather than being collapsed into an opaque server-rendered HTML blob. The semantics differ per report and are load-bearing, not cosmetic: Loan Lead Generation skips when a recipient generated zero leads that period (a real "0" there is meaningfully skippable); Inactive CSPs shows a real "0 inactive" as legitimate, reportable data and only skips when the recipient has no CSPs in scope at all.

**Freshness gate** (`services/calling_sheet_freshness_service.py`) hashes the sheet's raw content before any draft/send and compares it to the last confirmed-fresh snapshot; if unchanged, the run is skipped rather than re-sending the same numbers (e.g. during an upstream data outage).

**Automation scope**: three independent autosend cycles exist, each its own `AppSetting` toggle (`services/automation_settings_service.py`):
- **Daily** (`services/autosend_service.py::check_and_run_daily_autosend`) — `autosend_enabled`, currently **ON**. Polls every minute, fetches at a configured time, rechecks hourly until fresh, then drafts/sends.
- **SBI Kiosk Growth Report** (`services/autosend_service.py::check_and_run_sbi_kiosk_growth_autosend`) — separate isolated "Internal" OrgLevel, dual freshness gate (Calling Sheet + Circle Dashboard), currently **ON**, sends daily alongside the Daily cycle.
- **Weekly** (`services/weekly_autosend_service.py::check_and_run_weekly_autosend`) — `weekly_autosend_enabled`, currently **OFF**. The mechanism exists and is scheduled (see Scheduler jobs below), but stays off until explicitly turned on — the last real weekly send was **2026-08-31**, so whenever it's next enabled, the first run's growth comparison will span that whole gap, not a normal single week (see Weekly growth comparison section).

Monthly has no autosend cycle at all — always a manual trigger from the Scheduler page. Draft-vs-send is controlled per report (`ReportMaster.delivery_mode`).

The **force-draft override differs by path**, and the difference is deliberate: `combined_digest_service.send_combined_digest` still forces draft-only whenever RBO/AO recipients are in the run, but `report_send_service.send_report_now` does **not** — there `force_draft` is caller-controlled only (the manual per-report "Draft Only" button passes `True`; the autosend cycle passes `False` and honours `rm.delivery_mode` for every level). Don't "restore" the blanket override in `report_send_service`; it was removed on purpose.

**Daily autosend runs every day, including Monday** (`autosend_skip_weekdays` in `services/automation_settings_service.py`, currently empty — `[]`). It originally skipped Monday on the theory that the weekly report covered that day instead; per explicit instruction on 2026-08-19 that skip was removed so Monday now gets both the daily send and (when manually triggered) the weekly one. The mechanism is still there — `get_autosend_skip_weekdays`/`set_autosend_skip_weekdays` — if a skip day is ever wanted again; the skip check runs **before** any freshness check or state bookkeeping, so a skipped day doesn't consume or advance that day's run state.

**Scheduler jobs** (`services/scheduler_service.py::get_scheduler`), 7 in total: `schedule_poller` (1 min), `auto_distribution_poller` (1 min), `daily_autosend_poller` (1 min), `sbi_kiosk_growth_autosend_poller` (1 min), `weekly_autosend_poller` (1 min), `suggestions_poller` (30 min), `incoming_sync_poller` (1 min). Each mailbox-touching job is gated on an `AppSetting` toggle and no-ops when off. New toggles follow the same key/value pattern in `services/automation_settings_service.py` — current keys: `autosend_enabled`, `weekly_autosend_enabled`, `incoming_sync_enabled`, `autosend_skip_weekdays`, `limit_forward_drafts_enabled`, `limit_forward_drafts_since`, `incoming_ack_enabled`, `incoming_ack_since`. Because the 1-minute pollers are always running, any long-held write transaction will hit `database is locked` — and a full paced batch email send (`email_service.run_distribution_job`) runs as **one long-held SQLite transaction for the whole job**, so other connections (including a fresh `sqlite3.connect()` for a manual check) won't see any of that job's rows as committed until the entire batch — every recipient, with pacing sleeps between batches — finishes. This is cosmetic (not data loss) but has caused real confusion twice: a job that looks "missing" mid-run, and a recipient who clicks their own report's card link within the first few minutes of receiving it and hits "This link is no longer valid" because their own `EmailLog` row isn't committed yet. Not yet fixed — see Known issues below.

**Gmail integration split**: `services/gmail_auth.py` + `services/gmail_service.py` own the connected account's OAuth client and can both draft (`create_outbound_draft`) and actually send (`send_message`), and can fetch the account's real configured signature (`get_default_signature`) for appending to outgoing mail. If working via an external Gmail MCP-style connector instead, note that such connectors are typically draft-only with no send capability — check before assuming a "send" action is possible through one.

## Weekly growth comparison

**Cadence**: the weekly report goes out **every Monday**, draft-only, comparing each recipient against their own numbers from the previous week. Column data always comes from the **live Calling Sheet**, re-fetched at run time; the snapshot is only ever the comparison baseline, never the source of the current week's figures.

**Snapshot infrastructure** (`database/snapshot_models.py` + `services/snapshot_service.py`) captures the *actual drafted* email content per recipient (`EmailLog.context_override_json`), not a fresh re-fetch of the live sheet, since the sheet may have already changed by the time a comparison is needed. `save_drafted_report_snapshot` is called from `combined_digest_service` after `run_distribution_job`, wrapped in try/except so a snapshot failure can never fail the report itself.

Its idempotent delete is scoped to `source_job_id.in_(job_ids)` — **not to the whole `report_date`**. The weekly run calls it once per org level (RBO → LHO → Corporate Center → Branch), so a date-wide delete made every level wipe the previous one; a real run left 1 row where 39 were expected, which would silently have become the next week's baseline. Keep the delete job-scoped.

**Growth calculation** (`services/growth_service.py`): `GROWTH_METRICS` maps each output variable to `(underlying_metric_key, higher_is_better)`. `apply_growth()` only computes a variable when its **underlying metric is present in the context** — so a level whose email doesn't contain that report simply has no growth line, rather than a misleading zero. Inverted metrics (e.g. inactive CSPs) render a decrease as an improvement: `-40.0% (20 -> 12) - improvement`. A baseline older than `_MAX_COMPARISON_GAP_DAYS` (10) is rejected and the line falls back to `NO_DATA` rather than comparing against a stale fortnight. `load_previous_contexts` orders by `source_job_id, id` so a date with duplicate same-day runs resolves deterministically.

Growth is injected **only for Weekly** runs, before the context is stored — so the snapshot records the raw metrics, not the derived percentages.

**Nesting constraint**: `{{#if}}` blocks support only **one level of nesting** (the regex in `render_template` is non-greedy). A growth box placed inside another conditional leaves a literal `{{#if Has_Growth_Comparison}}` in the rendered email. Always assert on leftover `{{...}}` after rendering a changed template.

## Email templates

All 7 templates are **rows in the `email_templates` table**, not files on disk — there is no template directory to edit. Change them via `api/routers/templates.py` (or the Templates UI); one-off bulk rewrites have been done with throwaway scripts that POST through the same API.

Every template shares **one design**: the Daily-report card style (table layout, inline styles only — no flex/grid, since email clients don't support them). If you add or rebuild a template, match that design rather than inventing a new one. Templates carry **no "This is an automated report from the Reporting Console." footer** — it was removed deliberately; don't reintroduce it.

Two recurring hazards when scripting template edits: (1) a template body contains literal `%` characters, so `%`-style string formatting crashes — use concatenation or `.replace()`; (2) editing a template's report mapping without carefully preserving `reportIds` silently NULLs `report_masters.default_template_id` (see the Template resolution note above).

## Clickable report-detail cards

Every metric card in a sent email (PMJDY/APY/PMSBY/PMJJBY, Loan Lead, Inactive CSPs, CSP Income) links to a **public, no-login detail page** — `web/src/pages/ReportDetail.tsx`, served at `{PUBLIC_BASE_URL}/report-detail?token=...&metric=...&mode=...`. The token is the recipient's own `EmailLog.tracking_token`; it identifies which email's data to show and grants no write access. Backend routes are `api/routers/report_detail.py`, all under `/api/public/report-detail/{token}` (no auth dependency, same shape as `api/routers/tracking.py`'s open-pixel endpoint):
- `GET /{token}?metric=X` — current-value breakdown (`services/report_detail_service.py::get_metric_breakdown`)
- `GET /{token}/growth?metric=X` — week-over-week per-CSP growth for Weekly digests (`services/report_growth_service.py`)
- `GET /{token}/income` — CSP Income Impact (`services/report_income_service.py`)
- `GET /{token}/inactive` — Inactive CSPs / Circle Spread (`services/report_inactive_service.py`)

**Two data sources, by design.** Daily emails set `EmailLog.csp_breakdown_json` at send time (`report_aggregation_service.build_csp_metric_breakdown`, active-CSPs-only, carries target + FTD). Weekly emails don't set that column — they instead stash a `_csp_snapshot` (every CSP, including zero-activity ones, for the growth comparison) inside `EmailLog.context_override_json`, which `report_detail_service.get_metric_breakdown` falls back to when `csp_breakdown_json` is absent, filtered to `mtd > 0` rows. That fallback path has **no target/FTD** (the snapshot never carries those), so the response includes `has_target`/`has_ftd` flags the frontend uses to omit those tiles entirely rather than show a fabricated `0`.

**`Card_Mode` ("growth" vs "current")**: on the first Monday of the month, `services/growth_service.py::is_first_monday_of_month` suppresses the week-over-week comparison (`Has_Growth_Comparison=False`) since there's no prior-week baseline that month — but the card should still open and show current-period data, just without the comparison. `email_service.py` computes `Card_Mode = "growth" if Has_Growth_Comparison else "current"` per recipient and templates use `&mode={{Card_Mode}}` in card hrefs (never a hardcoded `&mode=growth`) so `ReportDetail.tsx` renders the right one of its three modes (`current` / `growth` / plus the pre-existing `inactive` view).

**Template engine hard limit** (`utils/helpers.py::TEMPLATE_IF_PATTERN`): `{{#if Flag}}...{{/if}}` supports only **one non-nested level** (see Nesting constraint above) — this bit real production emails once when a bar-chart addition nested one `{{#if}}` inside another and leaked literal `{{#if}}` text into sent mail. Fixed by pre-computing combined booleans in Python (`Show_Growth_Hint`, `Show_LL_Growth_Hint`, `Card_Mode`) instead of nesting conditionals in template HTML — do the same for any new combined condition.

## Incoming mail subsystem

A second, largely independent pipeline reads the connected account's **inbox** (the report pipeline above only writes outbound mail). Frontend is `web/src/pages/Incoming.tsx`; API is `api/routers/incoming.py`; the `incoming_sync_poller` scheduler job is toggle-gated and off by default.

**Triage classification** (`services/incoming_service.py`): `TRIAGE_RULES` is an **ordered, first-match-wins** keyword list producing a tier (task/info/noise/other) and an intent. Two hard-won rules govern edits to it:
- Match against the **subject plus only the first ~600 chars of body** (`_TRIAGE_BODY_CHARS`). Quoted reply chains below that point cause false matches.
- **Narrow rules must sit above broad ones.** Over-broad keywords have mis-tagged real work three separate times (`"csp code"` matched "TOTAL CSP CODE" inside routine status reports; `"meeting"` swallowed genuine SBI work). When adding a keyword, check how many already-replied-to messages it would capture — a high count means it's too broad.

`backfill_triage()` reclassifies locally with no Gmail calls (~2,500 rows in under 10s), so re-running after a rule change is cheap.

**Work queue**: `extract_task_identifiers` pulls ticket/CSP identifiers from the **subject only**; ticket `\b(\d{6})\b` takes precedence over CSP code `\b(1A\d{6})\b`. `sync_extracted_tasks()` is **insert-only** — it never reopens or closes an existing task, so re-syncing can't resurrect resolved work.

**Recipient kind**: `classify_recipient_kind` must match the login address *and* every team alias in the `GMAIL_ACCOUNT_ALIASES` env list. Missing an alias silently dumps that mail into an "unknown" bucket excluded from analysis — this hid 98% of one bucket until found.

**Timestamps**: always prefer Gmail's `internalDate` (epoch ms) over the `Date` header, and always emit ISO strings with a trailing `Z` (`_utc_iso`). Both halves matter: dropping the offset server-side *and* emitting naive ISO (which JS parses as local time) each independently shift displayed times by hours.

**Outgoing mail scan** (`services/sent_mail_service.py`): classifies the Sent folder into performance-based / report-distribution / issue-related, weighting subject matches over body (`_SUBJECT_WEIGHT = 3`, `_BODY_WEIGHT = 1`), and tracks per-category reply rates. A lopsided category distribution is often genuine (a real bulk campaign), so verify against raw subjects before "fixing" the classifier.

**Limit-approval forwarding** (`services/limit_forward_service.py`): **DRAFT ONLY.** Recreates the human workflow discovered by reading ~150 real bodies — a colleague's limit request is forwarded to a fixed approver with the requester Cc'd, and the approver's "Approved." reply auto-closes the ticket. `_is_from_forward_target()` is the hard loop guard; `close_tickets_from_approvals()` reads only above the quoted chain via `_body_head()`. This must not be switched to real sending without an explicit decision from the user.

**Open-rate stats** use a 2-day lookback on both numerator and denominator, so mail sent yesterday and opened today is counted. A same-day-only filter loses that entirely. (Open tracking and every clickable report-detail card need `PUBLIC_BASE_URL` set correctly to work against external recipients — it is intentionally blank in the local `app5/.env` for dev; the live rack server's value is the one that matters and must always match whatever port `/mira/` is actually reachable on externally. See Deployment below — this has broken in production before and is worth checking whenever cards stop opening.)

## Suggestions feed

(`database/suggestion_models.py`, `services/suggestion_service.py`, `api/routers/suggestions.py`, `web/src/pages/Suggestions.tsx`): a self-scanning feed that surfaces operational problems — currently a broken digest-template link (detected via the real `resolve_digest_template_id`, so zero false positives) and same-day duplicate draft batches — as `Suggestion` rows, on a 30-minute scheduler job (`suggestions_poller`) plus a manual "Rescan now" button. Detection is always read-only; a suggestion only gets a one-click "Approve" auto-fix (`can_auto_fix=True`) when the remediation is unambiguous and safe — currently just re-linking a report's `default_template_id` to the one matching `EmailTemplate` found by name convention. Anything ambiguous, or anything that would touch Gmail (e.g. cleaning up duplicate drafts), is surfaced for manual review only, never auto-applied. Approve/Dismiss always writes to `AuditLog`. **This system must stay scoped to internal app config/data fixes — never extend it to draft or send email autonomously.**

The feed is split into **Outgoing** and **Incoming** tabs (filtered by `category_prefix`), and lives **only** on the Suggestions page — deliberately not embedded in the Incoming dashboard. New UI belongs where its concept already lives, not on whichever page is currently under discussion.

Two constraints for writing suggestion **executors**:
- An executor that calls a reclassifier must **commit its own write in a separate `get_db()` block first**. Calling it inside the still-uncommitted outer session makes it read stale data and silently report "reclassified 0".
- Any bulk update loop must **commit per row**. A single transaction spanning ~300 updates collides with the 1-minute pollers and throws `database is locked`. The same per-row pattern applies to any loop making external API calls, which additionally preserves partial progress.

## Deployment (live instance)

This is a real, actively-used production system — the account it sends from is a real SBI-facing mailbox, not a sandbox. The **rack server at `122.176.147.78`** is the live sending instance (there is no separate staging environment); a local dev run never sends real mail unless Gmail is explicitly connected to it.

**Process management**: `mira-backend` runs under **systemd**, not pm2 — `mira-backend.service` (`ExecStart=.../venv/bin/uvicorn api.main:app --host 127.0.0.1 --port 8600`, `Restart=always`, enabled for boot). **Never manage it with pm2** — doing so once caused a severe crash-loop (pm2 and systemd fighting over the same port). Restart with `systemctl restart mira-backend` (needs root — the repo's `ssh_run.py` helper takes a `--sudo` flag for this). A separate, unrelated project (`wa-issue-fetcher`) on the same shared box does use pm2 — that's fine and expected, it's a different process on a different port.

**Frontend**: a static Vite build (`web/dist/`, built with `base=/mira/`) served directly by nginx via an `alias` + `try_files` block, not a dev server. Rebuild with `npm run build` on the server after a frontend change; no backend restart needed for frontend-only changes.

**Nginx / networking — check this first whenever "the site is down" or "cards don't open"**: this is a **shared rack server** hosting several unrelated projects (`csp-dashboard`, `wa-voxo-*`, `orders-kiosk`, etc.), and `/mira/` has at different times been reachable on **port 8080** (via a `location /mira/` block inside the `csp-dashboard` nginx site) and on **port 8601** (via its own dedicated `mira-8601` site). Which port is actually externally reachable has changed at least once due to nginx/network changes made for an unrelated project on the same box — `curl` from **inside** the server (`127.0.0.1:PORT`) can return `200` while the same path from the **real external IP** returns `404`, because something at the network layer outside nginx's own config was intercepting that port. Always verify with `curl http://122.176.147.78:PORT/mira/` (the real external IP, not `127.0.0.1`) before trusting a "200 = working" result. Whichever port is confirmed live, `PUBLIC_BASE_URL` in the **server's** `app5/.env` must match it exactly (scheme + IP + port + `/mira`, no trailing slash) — every clickable report-detail card link and the open-tracking pixel are built from this value at send time and get baked verbatim into the email; **a link already sent can never be fixed retroactively**, only future sends pick up a corrected value after a backend restart.

**Two git remotes exist and both must be pushed** on every change: `origin` (`ekoindia/MIRA-Mail-Intelligence-and-Relationship-Agent`, the originally requested repo) and `server-origin` (`prachi-2909/MIRA_Email_Automation`, what the server's own clone actually tracks). `git push origin main && git push server-origin main` is the standard pattern — pushing to only one silently leaves the other behind with no error. The server's own remote is just `origin` pointing at the `prachi-2909` fork (see `git remote -v` on the box).

**What lives outside git entirely** (both `.env` and `*.db*` are gitignored by design): the server's `app5/.env` (real credentials, `PUBLIC_BASE_URL`) and `app5/data/app.db` (email template bodies, all report config, all send history) are **not version-controlled** — they're the live, stateful parts of the deployment. Template body edits made via direct SQL or the Templates API on the server exist **only in that one SQLite file** until someone deliberately exports/syncs them elsewhere. Before assuming local and server have diverged, compare directly (e.g. hash `email_templates.subject + body_html` per row) rather than relying on git status, which will show both as clean even when their live content differs.

## Known issues / current state (as of 2026-09-17)

- **A recipient's card can 404 "link no longer valid" within the first few minutes of a send** — root cause is the long-held single-transaction batch send described under Scheduler jobs above. Diagnosed, not yet fixed; a real occurrence was confirmed to resolve itself once that day's batch job fully committed.
- **One recipient address bounced permanently**: `mgrfi.nashikrural@sbi.co.in` (RBO 2, Nashik) — SBI's mail server reports "Recipient Unknown." Needs a corrected address from the business side; the app can't route around a wrong address on its own, and resending to the same address will just bounce again.
- **Weekly autosend is off, pending explicit go-ahead** — last real weekly send was 2026-08-31. When it's turned on, expect the first growth comparison to span that whole gap (see Automation scope above), not a normal single week.
- Something **external to MIRA's own code** has been observed trashing-and-permanently-deleting some "Daily Progress Report" Sent-folder emails from the connected mailbox shortly after send (confirmed via DB/audit-log cross-check that they were genuinely sent) — a full codebase grep found zero trash/delete calls anywhere in this app. Likely a Gmail filter or third-party OAuth grant on the account; not something fixable from inside this codebase.
