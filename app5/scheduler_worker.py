"""
Standalone scheduler worker.

Run this as a separate always-on process (e.g. via systemd, supervisord,
or a Docker sidecar) so scheduled report distributions still fire even
when no one has the Streamlit UI open:

    python scheduler_worker.py

It reuses the exact same `check_and_run_due_schedules` logic as the
in-app scheduler, polling the database every minute.
"""
from __future__ import annotations

import time

from apscheduler.schedulers.blocking import BlockingScheduler

from database.db import init_db
from services.auto_distribution_service import check_and_run_due_auto_schedules
from services.scheduler_service import check_and_run_due_schedules
from utils.logger import get_logger, setup_logging

setup_logging()
logger = get_logger("scheduler_worker")


def main() -> None:
    init_db()
    logger.info("Scheduler worker starting. Polling every 60 seconds...")

    scheduler = BlockingScheduler(timezone="Asia/Kolkata")
    scheduler.add_job(check_and_run_due_schedules, "interval", minutes=1, id="schedule_poller")
    scheduler.add_job(
        check_and_run_due_auto_schedules, "interval", minutes=1, id="auto_distribution_poller"
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler worker stopped.")


if __name__ == "__main__":
    main()
