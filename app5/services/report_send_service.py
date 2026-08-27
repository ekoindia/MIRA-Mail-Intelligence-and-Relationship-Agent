"""
Single entry point for "fetch this report's latest calling-sheet data and
send/draft it to its real recipients right now" — shared by:
  * api/routers/reports.py (the Send / Send All / Test Draft buttons)
  * services/autosend_service.py (the daily automatic fetch-then-send cycle)

Kept in services/ rather than the router so the scheduler can call it
without importing from api/ (services must not depend on the API layer).
"""
from __future__ import annotations

from database.models import EmailLog, EmailStatus, ReportMaster, ReportUpload
from database.org_models import OrgLevel
from database.report_source_models import ReportSource
from services.calling_sheet_freshness_service import check_freshness
from services.distribution_service import ResolvedRecipient, create_distribution_job
from services.email_service import run_distribution_job
from services.recipient_resolution_service import resolve_recipients_for_levels
from services.report_aggregation_service import AGGREGATORS, MERGED_INTO_OTHER_REPORT, NOT_YET_AUTOMATED_REPORTS
from services.report_source_service import fetch_any_report
from services.segmented_distribution_service import apply_segmented_overrides, drop_zero_activity_daily_recipients


def send_report_now(db, rm: ReportMaster, user: dict, force_draft: bool = False) -> dict:
    """
    Fetch the latest calling-sheet data (if a source is connected) and
    send/draft one report to every real recipient right now. Raises
    ValueError with a human-readable reason for anything that stops the
    send (no source, no data, no recipients) — callers decide whether that's
    a hard failure (single-report send) or a skip (send-all sweep).

    force_draft=True always creates Gmail drafts, ignoring the report's
    stored Draft Only / Send Directly setting — used by the per-report
    "Test (Draft)" button, which must never actually send real mail.
    """
    if rm.report_name in NOT_YET_AUTOMATED_REPORTS:
        # Never send these, even if a stale/demo ReportUpload happens to
        # exist from earlier testing — their template still has unfilled
        # "[ ]" placeholders, so an email would go out broken.
        raise ValueError("Not automated yet — this report's template isn't filled in from real data.")

    if rm.report_name in MERGED_INTO_OTHER_REPORT:
        raise ValueError(
            f"Sent together with {MERGED_INTO_OTHER_REPORT[rm.report_name]} — not sent as its own email."
        )

    # Universal gate, regardless of what triggered this call (manual Draft/
    # Send-by-frequency button, or the automatic daily cycle): never draft
    # or send if the calling sheet's underlying source hasn't actually
    # refreshed since it was last confirmed fresh (e.g. an SBI-side outage
    # leaves it showing yesterday's snapshot) — that would just duplicate
    # the last real report with the same numbers.
    is_fresh, freshness_reason = check_freshness(db)
    if not is_fresh:
        raise ValueError(freshness_reason)

    source = (
        db.query(ReportSource)
        .filter(ReportSource.report_master_id == rm.id, ReportSource.is_active.is_(True))
        .first()
    )
    if source:
        fetch_result = fetch_any_report(db, source, triggered_by="manual")
        if not fetch_result["success"]:
            raise ValueError(f"Fetch failed: {fetch_result['error']}")

    latest_upload = (
        db.query(ReportUpload)
        .filter(ReportUpload.report_master_id == rm.id)
        .order_by(ReportUpload.uploaded_at.desc())
        .first()
    )
    if not latest_upload:
        raise ValueError("No data available — connect a source on the Scheduler page first.")

    levels = [OrgLevel(v.strip()) for v in (rm.org_levels or "").split(",") if v.strip()]
    if not levels:
        raise ValueError("No configured recipient level(s).")

    refs = resolve_recipients_for_levels(db, levels)
    if not refs:
        raise ValueError("No recipients resolved yet.")

    recipients = [
        ResolvedRecipient(
            name=r.name, email=r.email, recipient_type=r.level,
            lho_name=r.name if r.level == OrgLevel.LHO.value else None,
            cc_emails=r.cc_emails,
        )
        for r in refs
    ]

    # force_draft is caller-controlled only: the manual per-report "Draft
    # Only" action passes force_draft=True; everything else (Send-by-
    # frequency "send" mode, the daily autosend cycle) passes False and
    # gets whatever rm.delivery_mode says, for every recipient level
    # including RBO/AO — no blanket override here anymore.

    job = create_distribution_job(
        db, upload_id=latest_upload.id, template_id=rm.default_template_id,
        recipients=recipients, created_by_id=user["id"], created_by_username=user["username"],
        is_scheduled_run=False,
    )
    if rm.report_name in AGGREGATORS:
        apply_segmented_overrides(db, job, rm.report_name)
        if rm.frequency == "Daily":
            # An RBO with zero new activity across all 4 schemes today would
            # just get an empty table — skip it entirely rather than draft/
            # send a report with nothing in it.
            drop_zero_activity_daily_recipients(db, job)

    if job.total_recipients == 0:
        raise ValueError("Every recipient had zero activity across all 4 schemes today — nothing to send.")

    run_distribution_job(db, job.id, force_draft=force_draft)

    logs = db.query(EmailLog).filter(EmailLog.job_id == job.id).all()
    return {
        "reportId": rm.id, "reportName": rm.report_name, "jobId": job.id,
        "recipientCount": len(logs),
        "sent": sum(1 for l in logs if l.status == EmailStatus.SENT),
        "failed": sum(1 for l in logs if l.status == EmailStatus.FAILED),
        "deliveryMode": "draft" if force_draft else (rm.delivery_mode or "draft"),
    }
