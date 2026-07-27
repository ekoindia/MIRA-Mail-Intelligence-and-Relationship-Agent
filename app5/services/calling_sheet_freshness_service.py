"""
Shared "has the calling sheet actually been refreshed since it was last
confirmed fresh" gate — checked before ANY draft/send, whether triggered
by the automatic daily autosend cycle or a manual button (Scheduler's
Draft/Send-by-frequency, or anything else that calls
services.report_send_service.send_report_now).

Why this exists as its own module rather than living only in
services/autosend_service.py: that module's freshness check previously
only ran as part of the unattended 10:15/10:30 cycle — a manual "Draft"
click bypassed it entirely and could draft/send the exact same data twice
in a row (e.g. during an SBI-side outage that leaves the calling sheet
showing yesterday's snapshot). This module is the single source of truth
both paths now share, so the guarantee is universal, not cycle-specific.
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from database.models import AppSetting
from services.calling_sheet_service import fetch_calling_sheet_raw
from utils.logger import get_logger

logger = get_logger(__name__)

# Deliberately the same keys services/autosend_service.py already used for
# this, so existing state carries over with no migration needed.
_KEY_BASELINE_HASH = "autosend_baseline_hash"
_KEY_BASELINE_DATE = "autosend_baseline_date"


def _get_setting(db, key: str) -> str | None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else None


def _set_setting(db, key: str, value: str) -> None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def _hash_calling_sheet() -> str:
    """Hash of the live sheet's raw cell values — deliberately the rawest
    possible signal of "has anything at all changed", not any one column,
    so a refresh is detected even if it only touches a column no report
    currently reads."""
    values = fetch_calling_sheet_raw()
    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


def is_confirmed_fresh_today(db) -> bool:
    """Cheap read-only check — no fetch. True once something (autosend or
    a manual trigger) has already confirmed freshness today."""
    today_str = datetime.now().date().isoformat()
    return _get_setting(db, _KEY_BASELINE_DATE) == today_str


def check_freshness(db) -> tuple[bool, str]:
    """
    Returns (is_fresh, reason).

    If already confirmed fresh today, returns True immediately with no
    fetch. Otherwise fetches the live sheet and compares its hash against
    the last confirmed-fresh snapshot: if unchanged, the sheet's underlying
    source hasn't actually refreshed yet (e.g. an SBI-side outage keeps
    yesterday's data in place) — returns False, and callers must not
    draft/send. If changed, that becomes the new baseline (persisted, so
    later calls today short-circuit True) and returns True.
    """
    if is_confirmed_fresh_today(db):
        return True, "Calling sheet already confirmed fresh for today."

    try:
        new_hash = _hash_calling_sheet()
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not fetch calling sheet to verify freshness: %s", exc)
        return False, f"Could not fetch the calling sheet to verify it's been updated: {exc}"

    old_hash = _get_setting(db, _KEY_BASELINE_HASH)
    if new_hash == old_hash:
        return False, (
            "Calling sheet hasn't been updated since it was last confirmed fresh "
            "(same data as before) — refusing to draft/send to avoid duplicating "
            "yesterday's report."
        )

    today_str = datetime.now().date().isoformat()
    _set_setting(db, _KEY_BASELINE_HASH, new_hash)
    _set_setting(db, _KEY_BASELINE_DATE, today_str)
    db.flush()
    return True, "Calling sheet confirmed refreshed for today."
