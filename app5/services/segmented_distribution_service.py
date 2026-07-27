"""
Per-recipient "segmented" context for reports backed by the Calling Sheet.

Each EmailLog row gets its own computed metrics (target/achievement/top
performers/etc., see report_aggregation_service.py) written to
context_override_json and substituted straight into that recipient's email
body via {{Variable}} tags — see services/email_service.run_distribution_job.
No attachment is generated: the filled-in template body IS the report, per
explicit instruction not to produce an Excel file for these emails.
attachment_override_path is set to "" (an explicit "no attachment" marker,
distinct from NULL which means "use the job's shared upload") so
run_distribution_job never falls back to attaching the raw fetched sheet.

No-op for any report not in report_aggregation_service.AGGREGATORS (e.g.
REST-API-backed reports, or the Calling-Sheet reports with known data
gaps) — those keep working exactly as before, off the one shared upload.
"""
from __future__ import annotations

import json

from sqlalchemy.orm import Session

from database.models import DistributionJob
from services.calling_sheet_service import load_calling_sheet
from services.report_aggregation_service import AGGREGATORS, filter_for_recipient
from utils.logger import get_logger

logger = get_logger(__name__)

# The 4 scheme "achievement today" fields shown in the Daily RBO Update
# email (see AGGREGATORS["Account Opening (Daily)"] = aggregate_account_
# opening_and_sss). When an RBO has zero activity across ALL FOUR today —
# not just PMJDY — the daily email would just be an empty table, so that
# RBO is skipped entirely (no draft, no send) per explicit instruction.
_DAILY_FTD_KEYS = ("AO_PMJDY_FTD_Achievement", "SSS_APY_FTD", "SSS_PMSBY_FTD", "SSS_PMJJBY_FTD")


def _all_daily_schemes_zero(context: dict) -> bool:
    values = [context.get(k) for k in _DAILY_FTD_KEYS if k in context]
    return len(values) == len(_DAILY_FTD_KEYS) and all(v == 0 for v in values)


def drop_zero_activity_daily_recipients(db: Session, job: DistributionJob) -> int:
    """
    Remove (not just skip) any EmailLog row whose per-recipient context
    shows zero activity across all 4 daily schemes — must be called AFTER
    apply_segmented_overrides has populated context_override_json. Returns
    the number of recipients removed. Deleting the row here (rather than
    marking it skipped) means run_distribution_job never sees it, so no
    draft or send is created for that RBO at all.
    """
    removed = 0
    for log_row in list(job.email_logs):
        if not log_row.context_override_json:
            continue
        context = json.loads(log_row.context_override_json)
        if _all_daily_schemes_zero(context):
            logger.info(
                "Daily digest: skipping %s <%s> — zero activity across all 4 schemes today.",
                log_row.recipient_name, log_row.recipient_email,
            )
            db.delete(log_row)
            removed += 1

    if removed:
        job.total_recipients = max(job.total_recipients - removed, 0)
        db.flush()
    return removed


def apply_segmented_overrides(db: Session, job: DistributionJob, report_name: str) -> bool:
    """
    Populate context_override_json (and suppress the attachment) on every
    EmailLog row of `job`, for report types backed by the Calling Sheet.

    Returns True if segmentation was applied, False if this report isn't
    one of the aggregatable Calling-Sheet reports (caller keeps the job's
    default shared-attachment behavior in that case).
    """
    aggregator = AGGREGATORS.get(report_name)
    if aggregator is None:
        return False

    df = load_calling_sheet()

    for log_row in job.email_logs:
        recipient_df = filter_for_recipient(
            df, log_row.recipient_type, log_row.recipient_name, log_row.recipient_email,
        )
        if recipient_df.empty:
            logger.warning(
                "Segmented distribution: no Calling Sheet rows for %s '%s' (report=%s).",
                log_row.recipient_type, log_row.recipient_name, report_name,
            )

        context = aggregator(recipient_df)
        log_row.attachment_override_path = ""  # explicit "no attachment", not "unset"
        log_row.context_override_json = json.dumps(context)

    db.flush()
    logger.info(
        "Segmented distribution applied: job_id=%s report=%s recipients=%d",
        job.id, report_name, len(job.email_logs),
    )
    return True
