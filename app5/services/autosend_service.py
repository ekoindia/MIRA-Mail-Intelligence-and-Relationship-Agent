"""
Daily "fetch, verify freshness, then send" automation for the calling-sheet
driven reports.

Reuses services/report_send_service.send_report_now — the exact same
function the manual Send / Send All / Test Draft buttons call — so a
scheduled run always goes through identical recipient-resolution,
segmented-override, and Draft Only / Send Directly logic. This module only
adds the *timing and freshness gate* on top.

Cycle (all times are server-local, matching the other pollers in
services/scheduler_service.py):

1. At settings.autosend_fetch_time (default 10:15), read the live Calling
   Sheet and hash its raw content.
2. Compare against the hash captured the last time the sheet was confirmed
   fresh. If identical, today's data hasn't landed yet (e.g. an SBI-side
   outage) — wait settings.autosend_recheck_minutes (default 60, i.e.
   hourly) and check again. This repeats for as long as it takes; there's
   no cap, since a day where the sheet never gets updated should just
   never auto-send rather than fire with duplicate data.
3. Once the hash differs from the stored baseline, that becomes the new
   baseline (persisted so tomorrow's comparison is against *today's* data,
   not the fetch attempt an hour ago). If it's not yet
   settings.autosend_send_time (default 10:30), wait for it — no need to
   keep re-checking once freshness for today is already confirmed.
4. Once both "fresh" and "at/after send time" are true, draft/send every
   automated Daily-frequency report (Weekly/Monthly stay manual-only —
   this cycle never fires them) and record today as done, so a later
   poller tick this same day is a no-op.

Off by default (settings.autosend_enabled) — see config.py for why. Can be
turned on/off live from the Settings page without a restart; see
services/automation_settings_service.get_autosend_enabled, which this
checks on every tick in preference to the static .env value.

The freshness check itself (steps 1-3 below) lives in
services/calling_sheet_freshness_service.py, shared with every manual
Draft/Send trigger too (see services/report_send_service.send_report_now)
— this module only owns the *timing* around it: when to first check, how
often to retry, and when to fire the actual send once fresh.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from config import settings
from database.db import get_db
from database.models import AppSetting, ReportMaster
from database.org_models import OrgLevel
from services.automation_settings_service import (
    WEEKDAY_NAMES,
    get_autosend_enabled,
    get_autosend_skip_weekdays,
)
from services.calling_sheet_freshness_service import check_freshness, is_confirmed_fresh_today
from services.report_send_service import send_report_now
from utils.logger import get_logger

logger = get_logger(__name__)

# Real users.id — distribution_jobs.created_by is a genuine FK, so this
# can't be a synthetic placeholder id. Seeded once (users.username =
# "autosend_scheduler", is_active=False so it can never actually log in).
_SYSTEM_USER = {"id": 2, "username": "autosend_scheduler"}

# SBI Kiosk — Onboarding & Growth (Daily) is a Daily ReportMaster like any
# other, but its aggregator (team_calling_summary_service.
# get_sbi_kiosk_growth_context) is only registered in combined_digest_
# service._UNIT_AGGREGATORS, not report_aggregation_service.AGGREGATORS —
# send_report_now would find no aggregator for it and draft an empty email
# with unrendered template variables. Routed separately below instead, via
# the same combined_digest_service.send_combined_digest path the mapping
# page already uses for its manual trigger. Enabled 2026-08-28 per explicit
# instruction ("kal se... wahi same logic jo daily report ka tha") — see
# [[project_sbi_kiosk_growth_report]].
_SBI_KIOSK_REPORT_PREFIX = "SBI Kiosk"

_KEY_LAST_CHECK_AT = "autosend_last_check_at"
_KEY_LAST_SENT_DATE = "autosend_last_sent_date"


def _get_setting(db, key: str) -> str | None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else None


def _set_setting(db, key: str, value: str) -> None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def _parse_hhmm(value: str) -> tuple[int, int]:
    hh, mm = value.split(":")
    return int(hh), int(mm)


def check_and_run_daily_autosend() -> None:
    now = datetime.now()
    today_str = now.date().isoformat()
    fetch_h, fetch_m = _parse_hhmm(settings.autosend_fetch_time)
    send_h, send_m = _parse_hhmm(settings.autosend_send_time)
    fetch_due_at = now.replace(hour=fetch_h, minute=fetch_m, second=0, microsecond=0)
    send_due_at = now.replace(hour=send_h, minute=send_m, second=0, microsecond=0)

    with get_db() as db:
        # DB-backed override (Settings page toggle) takes precedence over the
        # .env default — this is what actually lets the toggle be flipped
        # live without restarting the backend.
        if not get_autosend_enabled(db):
            return

        # Standing "don't run today" rule (default: Monday — the daily report
        # covers the previous day, and Monday's would cover Sunday). Checked
        # before any freshness/state bookkeeping so a skipped day leaves no
        # trace and tomorrow behaves completely normally.
        skip_days = get_autosend_skip_weekdays(db)
        if now.weekday() in skip_days:
            logger.info(
                "Autosend: skipping — %s is a configured no-send day.",
                WEEKDAY_NAMES[now.weekday()],
            )
            return

        if _get_setting(db, _KEY_LAST_SENT_DATE) == today_str:
            return  # already sent today

        if now < fetch_due_at:
            return  # not yet fetch time

        fresh_confirmed_today = is_confirmed_fresh_today(db)

        if not fresh_confirmed_today:
            last_check_raw = _get_setting(db, _KEY_LAST_CHECK_AT)
            last_check_at = datetime.fromisoformat(last_check_raw) if last_check_raw else None
            recheck_due = (
                last_check_at is None
                or last_check_at.date().isoformat() != today_str
                or now >= last_check_at + timedelta(minutes=settings.autosend_recheck_minutes)
            )
            if not recheck_due:
                return

            _set_setting(db, _KEY_LAST_CHECK_AT, now.isoformat())
            fresh_confirmed_today, reason = check_freshness(db)
            if not fresh_confirmed_today:
                logger.info("Autosend: %s Rechecking in %s minutes.", reason, settings.autosend_recheck_minutes)
                db.flush()
                return
            logger.info("Autosend: %s", reason)

        if now < send_due_at:
            return  # fresh, but still waiting for the scheduled send window

        # Daily only: this cycle's hourly-recheck-until-fresh behavior only
        # makes sense for reports that run every day. Weekly/Monthly reports
        # stay on their existing manual Scheduler-page trigger — firing them
        # from this same daily loop would draft them every single day
        # instead of on their real cadence.
        reports = (
            db.query(ReportMaster)
            .filter(
                ReportMaster.org_levels.isnot(None), ReportMaster.org_levels != "",
                ReportMaster.frequency == "Daily",
                # SBI Kiosk routes through combined_digest_service below
                # instead — see _SBI_KIOSK_REPORT_PREFIX above.
                ~ReportMaster.report_name.like(f"{_SBI_KIOSK_REPORT_PREFIX}%"),
            )
            .order_by(ReportMaster.report_name)
            .all()
        )
        sent, skipped, failed = 0, 0, 0
        for rm in reports:
            try:
                # force_draft=False: this cycle now sends for real whenever
                # a report's own delivery_mode says "send" — draft-only is
                # reserved for the manual "Draft Only" action, not applied
                # blanket here anymore. Per-report delivery_mode on the
                # Reports page is the only thing that decides draft vs send
                # for an unattended run.
                send_report_now(db, rm, _SYSTEM_USER, force_draft=False)
                sent += 1
            except ValueError as exc:
                skipped += 1
                logger.info("Autosend: skipped '%s' — %s", rm.report_name, exc)
            except Exception as exc:  # noqa: BLE001
                failed += 1
                logger.exception("Autosend: send failed for '%s': %s", rm.report_name, exc)

        _set_setting(db, _KEY_LAST_SENT_DATE, today_str)
        db.flush()
        logger.info(
            "Autosend: daily run complete for %s — sent=%s skipped=%s failed=%s",
            today_str, sent, skipped, failed,
        )


# ---------------------------------------------------------------------------
# SBI Kiosk — Onboarding & Growth (Daily) — a separate poller, not folded
# into check_and_run_daily_autosend above, because it has TWO independent
# sources to wait on (the Calling Sheet for Calling/WhatsApp/Voice/Camp, the
# Circle 1A85 Admin Dashboard for the Growth section — see
# services/circle_dashboard_freshness_service.py), and its own recipient
# (sbikiosk@eko.co.in) — a stall on either source, or on this report's own
# recheck state, must never block or be blocked by the other Daily reports'
# shared _KEY_LAST_SENT_DATE/_KEY_LAST_CHECK_AT flags above. Same fetch/
# freshness-recheck/send timing shape as check_and_run_daily_autosend,
# against its own state keys. Enabled 2026-08-28 per explicit instruction
# ("kal se... wahi same logic jo daily report ka tha", then "growth k liye
# bhi freshness check kar lena daily") — see
# [[project_sbi_kiosk_growth_report]].
# ---------------------------------------------------------------------------

_SBI_KEY_LAST_CHECK_AT = "sbikiosk_autosend_last_check_at"
_SBI_KEY_LAST_SENT_DATE = "sbikiosk_autosend_last_sent_date"


def check_and_run_sbi_kiosk_growth_autosend() -> None:
    now = datetime.now()
    today_str = now.date().isoformat()
    fetch_h, fetch_m = _parse_hhmm(settings.autosend_fetch_time)
    send_h, send_m = _parse_hhmm(settings.autosend_send_time)
    fetch_due_at = now.replace(hour=fetch_h, minute=fetch_m, second=0, microsecond=0)
    send_due_at = now.replace(hour=send_h, minute=send_m, second=0, microsecond=0)

    with get_db() as db:
        if not get_autosend_enabled(db):
            return

        skip_days = get_autosend_skip_weekdays(db)
        if now.weekday() in skip_days:
            return  # same no-send-day rule as the main Daily cycle, logged there already

        if _get_setting(db, _SBI_KEY_LAST_SENT_DATE) == today_str:
            return  # already sent today

        if now < fetch_due_at:
            return  # not yet fetch time

        # BOTH sources must be confirmed fresh today — Calling Sheet (shared
        # gate, same one every other Daily report uses) for the Calling/
        # WhatsApp/Voice/Camp tables, and the Circle dashboard (this
        # report's own gate) for the Growth section. Either one still
        # showing yesterday's data holds the whole email back and retries
        # on the same recheck cadence as the main cycle.
        from services.circle_dashboard_freshness_service import (
            check_freshness as check_dashboard_freshness,
            is_confirmed_fresh_today as dashboard_confirmed_fresh_today,
        )

        sheet_fresh_today = is_confirmed_fresh_today(db)
        dashboard_fresh_today = dashboard_confirmed_fresh_today(db)

        if not (sheet_fresh_today and dashboard_fresh_today):
            last_check_raw = _get_setting(db, _SBI_KEY_LAST_CHECK_AT)
            last_check_at = datetime.fromisoformat(last_check_raw) if last_check_raw else None
            recheck_due = (
                last_check_at is None
                or last_check_at.date().isoformat() != today_str
                or now >= last_check_at + timedelta(minutes=settings.autosend_recheck_minutes)
            )
            if not recheck_due:
                return

            _set_setting(db, _SBI_KEY_LAST_CHECK_AT, now.isoformat())

            if not sheet_fresh_today:
                sheet_fresh_today, reason = check_freshness(db)
                if not sheet_fresh_today:
                    logger.info(
                        "SBI Kiosk autosend: %s Rechecking in %s minutes.",
                        reason, settings.autosend_recheck_minutes,
                    )
                    db.flush()
                    return

            if not dashboard_fresh_today:
                dashboard_fresh_today, reason = check_dashboard_freshness(db)
                if not dashboard_fresh_today:
                    logger.info(
                        "SBI Kiosk autosend: %s Rechecking in %s minutes.",
                        reason, settings.autosend_recheck_minutes,
                    )
                    db.flush()
                    return

            logger.info("SBI Kiosk autosend: both Calling Sheet and Circle dashboard confirmed fresh for today.")

        if now < send_due_at:
            return  # fresh, but still waiting for the scheduled send window

        from services.combined_digest_service import send_combined_digest

        try:
            result = send_combined_digest(db, "Daily", OrgLevel.INTERNAL, _SYSTEM_USER, force_draft=False)
            logger.info("SBI Kiosk autosend: sent — %s", result)
        except ValueError as exc:
            logger.info("SBI Kiosk autosend: skipped — %s", exc)
        except Exception:  # noqa: BLE001
            logger.exception("SBI Kiosk autosend: send failed.")

        _set_setting(db, _SBI_KEY_LAST_SENT_DATE, today_str)
        db.flush()
