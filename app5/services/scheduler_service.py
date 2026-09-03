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

        from services.autosend_service import (
            check_and_run_daily_autosend,
            check_and_run_sbi_kiosk_growth_autosend,
        )

        _scheduler.add_job(
            check_and_run_daily_autosend, "interval", minutes=1,
            id="daily_autosend_poller", replace_existing=True,
        )
        _scheduler.add_job(
            check_and_run_sbi_kiosk_growth_autosend, "interval", minutes=1,
            id="sbi_kiosk_growth_autosend_poller", replace_existing=True,
        )

        from services.weekly_autosend_service import check_and_run_weekly_autosend

        _scheduler.add_job(
            check_and_run_weekly_autosend, "interval", minutes=1,
            id="weekly_autosend_poller", replace_existing=True,
        )

        from services.suggestion_service import run_scheduled_suggestion_scan

        _scheduler.add_job(
            run_scheduled_suggestion_scan, "interval", minutes=30,
            id="suggestions_poller", replace_existing=True,
        )

        _scheduler.add_job(
            check_and_run_incoming_sync, "interval", minutes=1,
            id="incoming_sync_poller", replace_existing=True,
        )
        logger.info(
            "Scheduler started with 1-minute pollers (manual + auto-distribution + daily autosend "
            "+ weekly autosend), a 30-minute suggestions poller, a 1-minute incoming-mail sync "
            "poller (off by default)."
        )
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


def check_and_run_incoming_sync() -> None:
    """Poller: executes limit forward cycle, ticket auto-closing, and ack drafting
    (each governed by its own independent toggle / safety rules), then runs
    full inbox ingest and sent-mail scanning if incoming_sync_enabled is on."""
    # Always run ticket auto-closing and limit forward / ack cycles first.
    # These functions check their own toggles (e.g. get_limit_forward_enabled)
    # and safely no-op when disabled.
    _run_limit_forward_cycle()
    _run_incoming_ack_cycle()

    from services.automation_settings_service import get_incoming_sync_enabled
    from services.incoming_service import (
        backfill_recipient_kind, backfill_triage, ingest_new_messages,
        recheck_pending_replies, sync_extracted_tasks,
    )
    from services.sent_mail_service import backfill_sent_reply_status, scan_sent_mail

    with get_db() as db:
        if not get_incoming_sync_enabled(db):
            return

    try:
        summary = ingest_new_messages(create_drafts=False)
        replies_updated = recheck_pending_replies()
        recipient_kind_backfilled = backfill_recipient_kind()
        triage_backfilled = backfill_triage()
        # Insert-only (never reopens/closes an existing task) — this is what
        # actually populates the Work Queue. Was missing from this poller
        # (only POST /api/incoming/sync's manual button ran it), so the
        # Work Queue silently went stale for anyone who never clicked
        # "Sync now" even though ingest + forwarding kept running fine.
        tasks_extracted = sync_extracted_tasks()
        logger.info(
            "Incoming sync poller: %s, replies_updated=%d, recipient_kind_backfilled=%d, "
            "triage_backfilled=%d, tasks_created=%d",
            summary, replies_updated, recipient_kind_backfilled, triage_backfilled,
            tasks_extracted["created"],
        )
    except Exception:  # noqa: BLE001
        logger.exception("Incoming sync poller failed.")

    try:
        sent_summary = scan_sent_mail()
        sent_reply_backfilled = backfill_sent_reply_status()
        logger.info("Sent-mail sync poller: %s, reply_backfilled=%d", sent_summary, sent_reply_backfilled)
    except Exception:  # noqa: BLE001
        logger.exception("Sent-mail sync poller failed.")


def _run_incoming_ack_cycle() -> None:
    """DRAFTS (never sends) a generic acknowledgment for incoming SBI-domain
    status-push mail — see services/incoming_ack_service.py. Added
    2026-08-25 per explicit instruction, same supervised-trial shape as
    _run_limit_forward_cycle: its own toggle, bounded by a 'since' stamp so
    a first run after enabling can't sweep up the historical backlog.
    """
    from services.automation_settings_service import (
        get_incoming_ack_enabled,
        get_incoming_ack_since,
    )
    from services.incoming_ack_service import create_ack_drafts

    with get_db() as db:
        if not get_incoming_ack_enabled(db):
            return
        since = get_incoming_ack_since(db)

    if since is None:
        logger.warning("Incoming ack drafting enabled but no 'since' stamp — skipping to avoid backlog sweep.")
        return

    try:
        summary = create_ack_drafts(max_items=25, since=since)
        if summary.get("drafted"):
            logger.info("Incoming ack drafts created (DRAFT only, nothing sent): %s", summary)
    except Exception:  # noqa: BLE001
        logger.exception("Incoming ack drafting failed.")


def _run_limit_forward_cycle() -> None:
    """SENDS the limit-approval forward directly to Priyanshu for
    newly-arrived requests (switched over from a supervised draft-only trial
    that ran since 2026-08-14, per explicit instruction after that trial was
    reviewed), and closes tickets Priyanshu has approved.

    Gated by its own toggle, separate from the sync toggle, and bounded by
    the "since" stamp so it can never sweep up the historical backlog.
    Closing tickets is safe to run regardless of that toggle — it only
    updates this app's own records and sends nothing.
    """
    from services.automation_settings_service import (
        get_limit_forward_enabled,
        get_limit_forward_since,
    )
    from services.limit_forward_service import (
        close_tickets_from_approvals,
        create_forward_sends,
    )

    try:
        close_tickets_from_approvals()
    except Exception:  # noqa: BLE001
        logger.exception("Ticket auto-close from approvals failed.")

    with get_db() as db:
        if not get_limit_forward_enabled(db):
            return
        since = get_limit_forward_since(db)

    if since is None:
        logger.warning("Limit forward sending enabled but no 'since' stamp — skipping to avoid backlog sweep.")
        return

    try:
        summary = create_forward_sends(max_items=25, since=since)
        if summary.get("sent"):
            logger.info("Limit forward emails SENT to Priyanshu: %s", summary)
    except Exception:  # noqa: BLE001
        logger.exception("Limit forward sending failed.")


def list_schedules(db: Session) -> list[ScheduleConfig]:
    return db.query(ScheduleConfig).order_by(ScheduleConfig.next_run_at).all()


def set_schedule_active(db: Session, schedule_id: int, is_active: bool) -> None:
    schedule = db.query(ScheduleConfig).get(schedule_id)
    if schedule:
        schedule.is_active = is_active
