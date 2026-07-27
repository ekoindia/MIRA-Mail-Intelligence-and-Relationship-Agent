"""
Recurring distribution schedules.

Uses APScheduler's BackgroundScheduler, running inside the Streamlit
process. For production-grade reliability with multiple worker processes,
run `scheduler_worker.py` as a separate always-on process instead (see
README) -- the logic here is identical either way since it reads/writes
the same database.
"""
from __future__ import annotations

from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy.orm import Session

from database.db import get_db
from database.models import JobStatus, ReportUpload, ScheduleConfig
from services.audit_service import log_action
from services.distribution_service import create_distribution_job, resolve_recipients
from services.email_service import run_distribution_job
from utils.helpers import next_run_datetime
from utils.logger import get_logger

logger = get_logger(__name__)

_scheduler: BackgroundScheduler | None = None


def get_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is None:
        from services.auto_distribution_service import check_and_run_due_auto_schedules

        _scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
        _scheduler.start()
        _scheduler.add_job(
            check_and_run_due_schedules, "interval", minutes=1, id="schedule_poller", replace_existing=True
        )
        _scheduler.add_job(
            check_and_run_due_auto_schedules, "interval", minutes=1,
            id="auto_distribution_poller", replace_existing=True,
        )

        from services.autosend_service import check_and_run_daily_autosend

        _scheduler.add_job(
            check_and_run_daily_autosend, "interval", minutes=1,
            id="daily_autosend_poller", replace_existing=True,
        )
        logger.info("Scheduler started with 1-minute pollers (manual + auto-distribution + daily autosend).")
    return _scheduler


def create_schedule(
    db: Session,
    name: str,
    report_master_id: int,
    frequency: str,
    run_time: str,
    day_of_week: int | None,
    day_of_month: int | None,
    created_by_id: int,
    created_by_username: str,
) -> ScheduleConfig:
    next_run = next_run_datetime(frequency, run_time, day_of_week, day_of_month)
    schedule = ScheduleConfig(
        name=name,
        report_master_id=report_master_id,
        frequency=frequency,
        run_time=run_time,
        day_of_week=day_of_week,
        day_of_month=day_of_month,
        is_active=True,
        next_run_at=next_run,
        created_by=created_by_id,
    )
    db.add(schedule)
    db.flush()
    log_action(
        db, "CREATE_SCHEDULE", user_id=created_by_id, username=created_by_username,
        entity_type="ScheduleConfig", entity_id=schedule.id,
        details=f"{name} - {frequency} at {run_time}",
    )
    return schedule


def check_and_run_due_schedules() -> None:
    """Poller: find schedules whose next_run_at has passed and execute them.

    Sends the most recently uploaded report file for the schedule's
    report type, to the report's configured recipient list.
    """
    with get_db() as db:
        now = datetime.now()
        due = db.query(ScheduleConfig).filter(
            ScheduleConfig.is_active.is_(True), ScheduleConfig.next_run_at <= now
        ).all()

        for schedule in due:
            try:
                latest_upload = (
                    db.query(ReportUpload)
                    .filter(ReportUpload.report_master_id == schedule.report_master_id)
                    .order_by(ReportUpload.uploaded_at.desc())
                    .first()
                )
                if not latest_upload:
                    logger.warning("Schedule '%s' due but no report uploaded yet - skipping.", schedule.name)
                else:
                    recipients = resolve_recipients(db, schedule.report_master.recipient_type.value)
                    job = create_distribution_job(
                        db,
                        upload_id=latest_upload.id,
                        template_id=schedule.report_master.default_template_id,
                        recipients=recipients,
                        created_by_id=schedule.created_by or 0,
                        created_by_username="scheduler",
                        is_scheduled_run=True,
                    )
                    run_distribution_job(db, job.id)
                    logger.info("Scheduled run '%s' completed: job_id=%s", schedule.name, job.id)

                schedule.last_run_at = now
                schedule.next_run_at = next_run_datetime(
                    schedule.frequency.value, schedule.run_time, schedule.day_of_week,
                    schedule.day_of_month, from_dt=now,
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception("Scheduled run '%s' failed: %s", schedule.name, exc)


def list_schedules(db: Session) -> list[ScheduleConfig]:
    return db.query(ScheduleConfig).order_by(ScheduleConfig.next_run_at).all()


def set_schedule_active(db: Session, schedule_id: int, is_active: bool) -> None:
    schedule = db.query(ScheduleConfig).get(schedule_id)
    if schedule:
        schedule.is_active = is_active
