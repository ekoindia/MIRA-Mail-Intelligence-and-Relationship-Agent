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
