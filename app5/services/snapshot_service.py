"""
Period-over-period snapshot storage for the growth report due
2026-08-03 (template not provided yet). Copies the exact computed data
that went into an already-drafted weekly report run (EmailLog.
context_override_json, per recipient) into a dedicated table, so it's
preserved even if those DistributionJob/EmailLog rows are ever cleaned
up — the actual numbers that were drafted, not a fresh re-fetch of the
live Calling Sheet (which may have already changed since the draft ran).
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy.orm import Session

from database.models import DistributionJob, EmailLog
from database.snapshot_models import WeeklyReportSnapshot
from utils.logger import get_logger

logger = get_logger(__name__)


def save_drafted_report_snapshot(db: Session, job_ids: list[int], report_date: date) -> int:
    """
    Copy every EmailLog row belonging to `job_ids` (a completed weekly
    draft run) into WeeklyReportSnapshot, tagged with `report_date`.
    Skips any log row with no context_override_json (nothing computed to
    snapshot). Returns the number of rows saved.

    Idempotency is scoped to the JOBS being saved, not to the whole date.
    That distinction matters: the weekly run calls this once per org level
    (RBO, then LHO, then Corporate Center, then Branch), so clearing the
    entire date each time made every level wipe the previous one — a real
    run on 2026-08-17 left a single Corporate-Center row where 39 were
    expected, which would silently have become next week's baseline.
    Re-running the same job still replaces just that job's rows.
    """
    db.query(WeeklyReportSnapshot).filter(
        WeeklyReportSnapshot.report_date == report_date,
        WeeklyReportSnapshot.source_job_id.in_(job_ids),
    ).delete(synchronize_session=False)

    logs = (
        db.query(EmailLog)
        .filter(EmailLog.job_id.in_(job_ids), EmailLog.context_override_json.isnot(None))
        .all()
    )

    count = 0
    for log_row in logs:
        db.add(WeeklyReportSnapshot(
            report_date=report_date,
            level=log_row.recipient_type,
            recipient_name=log_row.recipient_name,
            recipient_email=log_row.recipient_email,
            source_job_id=log_row.job_id,
            context_json=log_row.context_override_json,
        ))
        count += 1

    db.flush()
    logger.info("Saved weekly report snapshot for %s: %d recipient rows from jobs %s.", report_date, count, job_ids)
    return count


def capture_weekly_baseline_snapshot(db: Session, report_date: date) -> int:
    """
    Build and store this week's WeeklyReportSnapshot rows WITHOUT sending
    (or drafting) anything — for every level/recipient that would have
    gotten a real Weekly digest, using the exact same context-building
    logic as send_combined_digest (services/combined_digest_service.py),
    just stopping short of create_distribution_job/run_distribution_job.

    Why this exists: save_drafted_report_snapshot only ever ran AFTER a
    real weekly send, so any week weekly_autosend was turned off (or a
    send otherwise didn't happen) left a hole in the snapshot history —
    the next real send's growth comparison would then be measured against
    a stale multi-week-old baseline instead of "last week", and could get
    rejected outright by growth_service's _MAX_COMPARISON_GAP_DAYS check.
    This keeps a fresh baseline flowing every week regardless of whether
    real mail ever went out that week, per explicit instruction after
    exactly that gap happened (2026-08-31 -> 2026-09-22, three weeks off).

    Rows are written with source_job_id=None (no real DistributionJob
    backs them) so they're clearly distinguishable from a real send's
    snapshot rows if that ever matters. Growth is deliberately NOT applied
    here — this call IS next week's baseline, not a report being compared
    against one.

    Idempotent per report_date: replaces any synthetic (source_job_id is
    NULL) rows already stored for this date, same re-run safety as
    save_drafted_report_snapshot. Never touches real-send rows (those keep
    their own source_job_id) — if a real send already happened today, the
    caller should skip calling this entirely (see
    weekly_autosend_service.check_and_run_weekly_baseline_capture), since
    real numbers are strictly better than a synthetic capture.
    """
    # Imported lazily: combined_digest_service imports save_drafted_report_
    # snapshot from this module at module load time, so importing from it
    # up top here would be a circular import.
    from database.org_models import OrgLevel
    from services.calling_sheet_service import load_calling_sheet
    from services.combined_digest_service import (
        _LOAN_LEAD_REPORT,
        _combined_context,
        _has_any_activity,
        automated_reports_for_level,
    )
    from services.recipient_resolution_service import resolve_recipients_for_levels
    from services.report_aggregation_service import (
        build_csp_income_breakdown,
        build_csp_metric_snapshot,
        build_inactive_csp_breakdown,
        build_loan_lead_csp_snapshot,
        filter_for_recipient,
    )

    db.query(WeeklyReportSnapshot).filter(
        WeeklyReportSnapshot.report_date == report_date,
        WeeklyReportSnapshot.source_job_id.is_(None),
    ).delete(synchronize_session=False)

    df = load_calling_sheet()
    count = 0
    for level in (OrgLevel.RBO, OrgLevel.LHO, OrgLevel.CORP, OrgLevel.BRANCH):
        reports = automated_reports_for_level(db, "Weekly", level)
        if not reports:
            continue
        refs = resolve_recipients_for_levels(db, [level])
        if not refs:
            continue

        has_ao_report = any(r.report_name == "Account Opening (Weekly)" for r in reports)
        has_ll_report = any(r.report_name == "Loan Lead Generation (Weekly)" for r in reports)
        has_income_report = any(r.report_name == "CSP Income Impact (Monthly)" for r in reports)
        has_inactive_report = any(r.report_name == "Inactive CSPs (Weekly)" for r in reports)
        loan_lead_only = len(reports) == 1 and reports[0].report_name == _LOAN_LEAD_REPORT

        for ref in refs:
            recipient_df = filter_for_recipient(df, ref.level, ref.name, ref.email)
            context = _combined_context(reports, recipient_df)
            if (loan_lead_only and not context.get("Has_Leads")) or not _has_any_activity(context):
                continue  # matches send_combined_digest's own skip — keeps the recipient set comparable

            if has_ao_report or has_ll_report:
                snapshot = build_csp_metric_snapshot(recipient_df) if has_ao_report else {}
                if has_ll_report:
                    snapshot["LL"] = build_loan_lead_csp_snapshot(recipient_df)
                context["_csp_snapshot"] = snapshot
            if has_income_report:
                context["_income_snapshot"] = build_csp_income_breakdown(recipient_df)
            if has_inactive_report:
                context["_inactive_snapshot"] = build_inactive_csp_breakdown(recipient_df)

            db.add(WeeklyReportSnapshot(
                report_date=report_date,
                level=ref.level,
                recipient_name=ref.name,
                recipient_email=ref.email,
                source_job_id=None,
                context_json=json.dumps(context),
            ))
            count += 1

    db.flush()
    logger.info("Captured synthetic weekly baseline snapshot for %s: %d recipient row(s).", report_date, count)
    return count


def get_snapshot(db: Session, report_date: date) -> list[WeeklyReportSnapshot]:
    return (
        db.query(WeeklyReportSnapshot)
        .filter(WeeklyReportSnapshot.report_date == report_date)
        .all()
    )


def list_snapshot_dates(db: Session) -> list[date]:
    rows = (
        db.query(WeeklyReportSnapshot.report_date)
        .distinct()
        .order_by(WeeklyReportSnapshot.report_date)
        .all()
    )
    return [r[0] for r in rows]
