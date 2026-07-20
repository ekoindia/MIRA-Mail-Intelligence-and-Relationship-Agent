"""
Automated "fetch the source, then distribute" schedules.

Fetch and send are independent, separately-timed steps — e.g. refresh the
report at 11:40 and send at 12:00 — not one combined action. Mirrors
services/scheduler_service.py (same ScheduleFrequency, same
next_run_datetime helper) but the source of the file is a ReportSource
fetch instead of a manually uploaded ReportUpload, and recipients are
resolved against the org hierarchy (database/org_models.py) instead of
Branch/LHO.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from database.db import get_db
from database.models import ReportUpload
from database.org_models import OrgLevel
from database.report_source_models import AutoDistributionSchedule, ReportSource
from services.audit_service import log_action
from services.distribution_service import ResolvedRecipient, create_distribution_job
from services.email_service import run_distribution_job
from services.recipient_resolution_service import resolve_recipients_for_levels
from services.report_source_service import fetch_any_report
from services.segmented_distribution_service import apply_segmented_overrides
from utils.helpers import next_run_datetime
from utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_FETCH_TIME = "11:40"
DEFAULT_SEND_TIME = "12:00"


def create_auto_schedule(
    db: Session,
    name: str,
    report_source_id: int,
    template_id: int | None,
    org_levels: list[OrgLevel],
    org_unit_ids: list[int] | None,
    frequency: str,
    send_time: str,
    day_of_week: int | None,
    day_of_month: int | None,
    created_by_id: int,
    created_by_username: str,
    fetch_time: str | None = None,
) -> AutoDistributionSchedule:
    """
    org_levels: one or more org levels this report goes out to in the SAME
    send (e.g. ["LHO", "Corporate Center"]) — most reports in the
    distribution matrix target several levels at once, not just one.

    fetch_time: when the source is refreshed, independent of send_time
    (defaults to DEFAULT_FETCH_TIME, i.e. 11:40, so a 12:00 send always has
    a same-day refresh behind it without fetching and sending at once).
    """
    fetch_time = fetch_time or DEFAULT_FETCH_TIME
    next_send = next_run_datetime(frequency, send_time, day_of_week, day_of_month)
    next_fetch = next_run_datetime(frequency, fetch_time, day_of_week, day_of_month)
    schedule = AutoDistributionSchedule(
        name=name,
        report_source_id=report_source_id,
        template_id=template_id,
        org_level=",".join(l.value for l in org_levels),
        org_unit_ids=",".join(str(i) for i in org_unit_ids) if org_unit_ids else None,
        frequency=frequency,
        run_time=send_time,
        fetch_time=fetch_time,
        day_of_week=day_of_week,
        day_of_month=day_of_month,
        is_active=True,
        next_run_at=next_send,
        next_fetch_at=next_fetch,
        created_by=created_by_id,
    )
    db.add(schedule)
    db.flush()
    log_action(
        db, "CREATE_AUTO_DISTRIBUTION_SCHEDULE", user_id=created_by_id, username=created_by_username,
        entity_type="AutoDistributionSchedule", entity_id=schedule.id,
        details=f"{name} - {frequency} - fetch {fetch_time}, send {send_time} - levels={schedule.org_level}",
    )
    return schedule


def _resolved_recipients_for(db: Session, org_level_csv: str, org_unit_ids_csv: str | None) -> list[ResolvedRecipient]:
    """
    LHO/Branch recipients come straight from the Calling Sheet's own mail-ID
    columns (see recipient_resolution_service); RBO/AO/Corporate Center have
    no matching column in the sheet and still use the manually-configured
    recipients from Settings. org_unit_ids (restricting to specific units)
    only applies to the manually-configured levels — the sheet-derived ones
    always resolve every unit currently present in the sheet.
    """
    unit_ids = {int(x) for x in org_unit_ids_csv.split(",") if x.strip()} if org_unit_ids_csv else None
    levels = [OrgLevel(v.strip()) for v in org_level_csv.split(",") if v.strip()]
    raw = resolve_recipients_for_levels(db, levels)
    return [
        ResolvedRecipient(
            name=r.name, email=r.email, recipient_type=r.level,
            lho_name=r.name if r.level == OrgLevel.LHO.value else None,
            cc_emails=r.cc_emails,
        )
        for r in raw
        if unit_ids is None or r.source != "org" or r.unit_id in unit_ids
    ]


def _run_fetch(db: Session, schedule: AutoDistributionSchedule, now: datetime) -> None:
    source = db.query(ReportSource).get(schedule.report_source_id)
    if not source or not source.is_active:
        logger.warning("Auto-schedule '%s' due to fetch but source is missing/inactive - skipping.", schedule.name)
    else:
        fetch_result = fetch_any_report(db, source, now=now, triggered_by="schedule")
        if not fetch_result["success"]:
            logger.error("Auto-schedule '%s' fetch failed: %s", schedule.name, fetch_result["error"])
        else:
            logger.info("Auto-schedule '%s' fetch completed: upload_id=%s", schedule.name, fetch_result["upload"].id)

    schedule.last_fetch_at = now
    schedule.next_fetch_at = next_run_datetime(
        schedule.frequency.value, schedule.fetch_time or DEFAULT_FETCH_TIME,
        schedule.day_of_week, schedule.day_of_month, from_dt=now,
    )


def _run_send(db: Session, schedule: AutoDistributionSchedule, now: datetime) -> None:
    source = db.query(ReportSource).get(schedule.report_source_id)
    latest_upload = (
        db.query(ReportUpload)
        .filter(ReportUpload.report_master_id == source.report_master_id)
        .order_by(ReportUpload.uploaded_at.desc())
        .first()
        if source else None
    )

    if not latest_upload:
        logger.warning("Auto-schedule '%s' due to send but no report has been fetched yet - skipping.", schedule.name)
    else:
        recipients = _resolved_recipients_for(db, schedule.org_level, schedule.org_unit_ids)
        if not recipients:
            logger.warning(
                "Auto-schedule '%s' has a report ready but resolved 0 recipients for level=%s - skipping send.",
                schedule.name, schedule.org_level,
            )
        else:
            job = create_distribution_job(
                db,
                upload_id=latest_upload.id,
                template_id=schedule.template_id,
                recipients=recipients,
                created_by_id=schedule.created_by or 0,
                created_by_username="auto_distribution_scheduler",
                is_scheduled_run=True,
            )
            if source.report_master:
                apply_segmented_overrides(db, job, source.report_master.report_name)
            run_distribution_job(db, job.id)
            logger.info("Auto-distribution send '%s' completed: job_id=%s", schedule.name, job.id)

    schedule.last_run_at = now
    schedule.next_run_at = next_run_datetime(
        schedule.frequency.value, schedule.run_time, schedule.day_of_week,
        schedule.day_of_month, from_dt=now,
    )


def check_and_run_due_auto_schedules() -> None:
    """
    Poller: for each active schedule, fire the fetch step when next_fetch_at
    has passed and — independently — fire the send step when next_run_at
    (the send time) has passed. A schedule created before fetch/send were
    split has next_fetch_at unset, so it fetches immediately before it
    sends, same as before.
    """
    with get_db() as db:
        now = datetime.now()
        due = db.query(AutoDistributionSchedule).filter(
            AutoDistributionSchedule.is_active.is_(True)
        ).filter(
            (AutoDistributionSchedule.next_fetch_at.is_(None))
            | (AutoDistributionSchedule.next_fetch_at <= now)
            | (AutoDistributionSchedule.next_run_at <= now)
        ).all()

        for schedule in due:
            try:
                if schedule.next_fetch_at is None or schedule.next_fetch_at <= now:
                    _run_fetch(db, schedule, now)
                if schedule.next_run_at <= now:
                    _run_send(db, schedule, now)
            except Exception as exc:  # noqa: BLE001
                logger.exception("Auto-distribution run '%s' failed: %s", schedule.name, exc)


def list_auto_schedules(db: Session) -> list[AutoDistributionSchedule]:
    return db.query(AutoDistributionSchedule).order_by(AutoDistributionSchedule.next_run_at).all()


def set_auto_schedule_active(db: Session, schedule_id: int, is_active: bool) -> None:
    schedule = db.query(AutoDistributionSchedule).get(schedule_id)
    if schedule:
        schedule.is_active = is_active
