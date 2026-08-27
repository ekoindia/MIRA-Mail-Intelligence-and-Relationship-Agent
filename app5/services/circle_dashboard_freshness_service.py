"""
Same "has the source actually refreshed since it was last confirmed fresh"
gate as calling_sheet_freshness_service.py, but for the Circle 1A85 Admin
Dashboard that feeds the SBI Kiosk Growth report's Growth section (Focus
Products / DFS / GTV / Loan Lead) — see circle_dashboard_service.py.

Why a separate module rather than reusing calling_sheet_freshness_service:
the Growth section's source is a DIFFERENT system (an unauthenticated
internal admin dashboard, not the Google Sheet), regenerated on its own
schedule — it can go stale independently of the Calling Sheet, so it needs
its own hash/baseline, not a shared one. Added 2026-08-27 per explicit
instruction ("growth k liye bhi freshness check kar lena daily") — see
[[project_sbi_kiosk_growth_report]].
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from database.models import AppSetting
from services.circle_dashboard_service import fetch_dashboard_soup
from utils.logger import get_logger

logger = get_logger(__name__)

_KEY_BASELINE_HASH = "circle_dashboard_baseline_hash"
_KEY_BASELINE_DATE = "circle_dashboard_baseline_date"


def _get_setting(db, key: str) -> str | None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    return row.value if row else None


def _set_setting(db, key: str, value: str) -> None:
    row = db.query(AppSetting).filter(AppSetting.key == key).first()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=key, value=value))


def _hash_dashboard() -> str | None:
    """Hash of the dashboard's raw HTML text — the rawest possible signal
    of "has anything at all changed", same principle as
    calling_sheet_freshness_service._hash_calling_sheet. Returns None if
    the dashboard couldn't be fetched at all (network/HTTP failure) —
    distinct from "fetched but unchanged", so callers can tell "can't
    check" from "checked, not fresh yet"."""
    soup = fetch_dashboard_soup()
    if soup is None:
        return None
    return hashlib.sha256(str(soup).encode("utf-8")).hexdigest()


def is_confirmed_fresh_today(db) -> bool:
    """Cheap read-only check — no fetch. True once something has already
    confirmed freshness today."""
    today_str = datetime.now().date().isoformat()
    return _get_setting(db, _KEY_BASELINE_DATE) == today_str


def check_freshness(db) -> tuple[bool, str]:
    """
    Returns (is_fresh, reason). Same contract as
    calling_sheet_freshness_service.check_freshness: if already confirmed
    fresh today, returns True with no fetch; otherwise fetches and compares
    against the last confirmed-fresh baseline hash. Unchanged (or
    unreachable) -> False, don't draft/send yet. Changed -> new baseline,
    True.
    """
    if is_confirmed_fresh_today(db):
        return True, "Circle dashboard already confirmed fresh for today."

    try:
        new_hash = _hash_dashboard()
    except Exception as exc:  # noqa: BLE001
        logger.error("Could not fetch Circle dashboard to verify freshness: %s", exc)
        return False, f"Could not fetch the Circle dashboard to verify it's been updated: {exc}"

    if new_hash is None:
        return False, "Circle dashboard could not be reached to verify it's been updated."

    old_hash = _get_setting(db, _KEY_BASELINE_HASH)
    if new_hash == old_hash:
        return False, (
            "Circle dashboard hasn't been updated since it was last confirmed fresh "
            "(same data as before) — refusing to draft/send to avoid duplicating "
            "yesterday's Growth numbers."
        )

    today_str = datetime.now().date().isoformat()
    _set_setting(db, _KEY_BASELINE_HASH, new_hash)
    _set_setting(db, _KEY_BASELINE_DATE, today_str)
    db.flush()
    return True, "Circle dashboard confirmed refreshed for today."
