"""
At-most-once guard for real outbound mail.

A send run keeps its EmailLog/job rows in one long DB transaction, so if the
final commit fails (e.g. "database is locked") the rows are rolled back while
the mail has already left Gmail. The next poller tick then sees nothing sent
and sends everything again — on 2026-11-04 that repeated every few minutes
for an hour. These markers live in small JSON files outside the database, are
written the moment a mail actually goes out, and survive any rollback.
"""
from __future__ import annotations

import json
import os
import threading
from contextvars import ContextVar
from datetime import date
from pathlib import Path

from config import BASE_DIR

_DIR = BASE_DIR / "data" / "send_guard"
_lock = threading.Lock()

# Set by the autosend cycles around each report they fire, so only unattended
# runs are guarded; manual Scheduler-page sends and test drafts are unaffected.
autosend_report: ContextVar[str | None] = ContextVar("autosend_report", default=None)


def _path(kind: str, day: date) -> Path:
    return _DIR / f"{kind}_{day.isoformat()}.json"


def _read(kind: str, day: date) -> set[str]:
    p = _path(kind, day)
    if not p.exists():
        return set()
    return set(json.loads(p.read_text(encoding="utf-8")))


def _add(kind: str, day: date, item: str) -> None:
    with _lock:
        items = _read(kind, day)
        if item in items:
            return
        items.add(item)
        _DIR.mkdir(parents=True, exist_ok=True)
        tmp = _path(kind, day).with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(items)), encoding="utf-8")
        os.replace(tmp, _path(kind, day))


def mark_autosend_mail_sent() -> None:
    """Record that the autosend report currently running has sent real mail today."""
    name = autosend_report.get()
    if name:
        _add("autosend_started", date.today(), name)


def autosend_reports_started_today() -> set[str]:
    return _read("autosend_started", date.today())


def mark_forwarded(incoming_id: int) -> None:
    _add("forwarded", date.today(), str(incoming_id))


def forwarded_recently(days: int = 3) -> set[str]:
    from datetime import timedelta

    out: set[str] = set()
    for i in range(days):
        out |= _read("forwarded", date.today() - timedelta(days=i))
    return out
