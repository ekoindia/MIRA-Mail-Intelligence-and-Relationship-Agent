"""
Weekly "auto-send every Monday" automation — added 2026-08-24 per explicit
instruction. Mirrors services/autosend_service.py's daily fetch/freshness/
send cycle, but for Weekly reports, gated to Mondays only.

The FIRST Monday of each month used to be skipped entirely (paused,
fully manual) — per explicit instruction 2026-08-27 it now sends like
every other Monday, but with no week-over-week growth comparison: last
Monday was a different month, so there's nothing honest to compare
against. combined_digest_service.py handles this by passing
is_first_monday=True into growth_service.apply_growth(), which hides the
whole {{#if Has_Growth_Comparison}} block in the template rather than
showing a comparison figure — see growth_service.is_first_monday_of_month
(moved there from this file, since it's now growth-comparison logic, not
send-gating logic) and apply_growth's own docstring. Automation resumes
the normal comparison template on its own the following Monday — no
separate template swap needed, since it's the same template either way,
just with that one block conditionally shown or hidden.

Reuses services/combined_digest_service.send_combined_digest — the same
function the manual Scheduler-page "Send by frequency" button calls for
Weekly — so a scheduled run goes through identical recipient-resolution,
growth/snapshot, and Draft Only / Send Directly logic. This module only
adds the *timing/day gate* on top, exactly like autosend_service.py does
for Daily.

REAL SEND, not draft — per explicit instruction. This required two changes
beyond just wiring up a scheduler job:
  1. Every Weekly ReportMaster's delivery_mode was changed from 'draft' to
     'send' (see set_weekly_delivery_mode_to_send below / the one-off script
     that ran it) — force_draft=False alone does nothing if the underlying
     report is still configured to draft.
  2. bypass_rbo_safety_net=True is passed to send_combined_digest — the
     RBO/AO force-draft override that's existed since Weekly was built is
     explicitly lifted for this automated path only (confirmed with the
     user: RBO should fully auto-send too, not stay draft-only). The manual
     Scheduler button still keeps that safety net by default.

Off by default (services.automation_settings_service.get_weekly_autosend_
enabled) — must be explicitly turned on.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from config import settings
from database.db import get_db
from database.models import AppSetting
from database.org_models import OrgLevel
from services.automation_settings_service import (
    WEEKDAY_NAMES,
    get_weekly_autosend_enabled,
)
from services.calling_sheet_freshness_service import check_freshness, is_confirmed_fresh_today
from services.combined_digest_service import automated_reports_for_level, send_combined_digest
from services.snapshot_service import capture_weekly_baseline_snapshot, get_snapshot
from utils.logger import get_logger

logger = get_logger(__name__)

# Same synthetic system user as daily autosend (services/autosend_service.py)
# — distribution_jobs.created_by is a real FK, so this can't be a made-up id.
_SYSTEM_USER = {"id": 2, "username": "autosend_scheduler"}

_KEY_LAST_CHECK_AT = "weekly_autosend_last_check_at"
_KEY_LAST_SENT_MONDAY = "weekly_autosend_last_sent_monday"

_LEVELS = [OrgLevel.RBO, OrgLevel.LHO, OrgLevel.CORP, OrgLevel.BRANCH]


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


def run_weekly_send_now(db, today) -> dict:
    """Actually send every level's Weekly digest for real. Shared by the
    scheduler cycle below and by a one-off manual trigger (e.g. the
    2026-08-24 same-day send once data was updated) so both paths behave
    identically. Returns a per-level summary; never raises — a level with
    nothing to send (ValueError from send_combined_digest, e.g. no
    automated reports, no recipients) is recorded as skipped, not fatal to
    the other levels."""
    results = []
    for level in _LEVELS:
        reports = automated_reports_for_level(db, "Weekly", level)
        if not reports:
            results.append({"level": level.value, "status": "skipped", "reason": "no automated reports"})
            continue
        try:
            out = send_combined_digest(
                db, "Weekly", level, _SYSTEM_USER, force_draft=False, bypass_rbo_safety_net=True,
            )
            results.append({"level": level.value, "status": "sent", **out})
        except ValueError as exc:
            results.append({"level": level.value, "status": "skipped", "reason": str(exc)})
        except Exception as exc:  # noqa: BLE001
            logger.exception("Weekly autosend: level %s failed.", level.value)
            results.append({"level": level.value, "status": "failed", "reason": str(exc)})

    _set_setting(db, _KEY_LAST_SENT_MONDAY, today.isoformat())
    db.flush()
    logger.info("Weekly autosend: run complete for %s — %s", today, results)
    return {"date": today.isoformat(), "levels": results}


def check_and_run_weekly_autosend() -> None:
    now = datetime.now()
    today = now.date()

    with get_db() as db:
        if not get_weekly_autosend_enabled(db):
            return

        if now.weekday() != 0:
            return  # not Monday

        # First Monday of the month used to return here (fully manual,
        # paused). It now sends like any other Monday — the no-growth-
        # comparison behavior for that week lives in
        # combined_digest_service.py -> growth_service.apply_growth
        # (is_first_monday_of_month, still checked there), not here.

        if _get_setting(db, _KEY_LAST_SENT_MONDAY) == today.isoformat():
            return  # already sent this Monday

        fetch_h, fetch_m = _parse_hhmm(settings.autosend_fetch_time)
        send_h, send_m = _parse_hhmm(settings.autosend_send_time)
        fetch_due_at = now.replace(hour=fetch_h, minute=fetch_m, second=0, microsecond=0)
        send_due_at = now.replace(hour=send_h, minute=send_m, second=0, microsecond=0)

        if now < fetch_due_at:
            return  # not yet fetch time

        fresh_confirmed_today = is_confirmed_fresh_today(db)
        if not fresh_confirmed_today:
            last_check_raw = _get_setting(db, _KEY_LAST_CHECK_AT)
            last_check_at = datetime.fromisoformat(last_check_raw) if last_check_raw else None
            recheck_due = (
                last_check_at is None
                or last_check_at.date().isoformat() != today.isoformat()
                or now >= last_check_at + timedelta(minutes=settings.autosend_recheck_minutes)
            )
            if not recheck_due:
                return

            _set_setting(db, _KEY_LAST_CHECK_AT, now.isoformat())
            fresh_confirmed_today, reason = check_freshness(db)
            if not fresh_confirmed_today:
                logger.info("Weekly autosend: %s Rechecking in %s minutes.", reason, settings.autosend_recheck_minutes)
                db.flush()
                return
            logger.info("Weekly autosend: %s", reason)

        if now < send_due_at:
            return  # fresh, but still waiting for the scheduled send window

        run_weekly_send_now(db, today)


_KEY_LAST_BASELINE_MONDAY = "weekly_baseline_last_captured_monday"


def check_and_run_weekly_baseline_capture() -> None:
    """
    Keeps a fresh WeeklyReportSnapshot baseline flowing every Monday even
    when weekly_autosend is off (or a real send otherwise doesn't happen
    that day) — see snapshot_service.capture_weekly_baseline_snapshot's
    docstring for the incident that prompted this (2026-08-31 ->
    2026-09-22, three Mondays with weekly_autosend off left no baseline
    at all, so the growth comparison on the next real send got rejected
    outright by growth_service's 10-day max-gap check). Deliberately
    independent of get_weekly_autosend_enabled — a fresh baseline is
    worth keeping regardless of whether real mail goes out that day.
    """
    now = datetime.now()
    today = now.date()

    with get_db() as db:
        if now.weekday() != 0:
            return  # not Monday

        if _get_setting(db, _KEY_LAST_BASELINE_MONDAY) == today.isoformat():
            return  # already handled this Monday

        # Same send-time gate as the real cycle, and deliberately checked
        # AFTER it: if weekly_autosend is on and already ran today,
        # run_weekly_send_now's own real snapshot (via save_drafted_report_
        # snapshot) will already exist below, and real numbers always beat
        # a synthetic capture.
        send_h, send_m = _parse_hhmm(settings.autosend_send_time)
        send_due_at = now.replace(hour=send_h, minute=send_m, second=0, microsecond=0)
        if now < send_due_at:
            return

        if get_snapshot(db, today):
            _set_setting(db, _KEY_LAST_BASELINE_MONDAY, today.isoformat())
            db.flush()
            return

        if not is_confirmed_fresh_today(db):
            # Don't invent a baseline off a stale/unrefreshed sheet — leave
            # _KEY_LAST_BASELINE_MONDAY unset so a later poll today retries.
            return

        try:
            count = capture_weekly_baseline_snapshot(db, today)
            logger.info(
                "Weekly baseline: captured synthetic snapshot for %s (%d rows) — no real weekly send today.",
                today, count,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Weekly baseline capture failed for %s.", today)
            return  # leave unset so a later poll today retries

        _set_setting(db, _KEY_LAST_BASELINE_MONDAY, today.isoformat())
        db.flush()
