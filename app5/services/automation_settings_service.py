"""
Live, DB-backed override for whether the daily autosend cycle
(services/autosend_service.py) is on — lets it be flipped from the
Settings page without restarting the backend, unlike the .env
AUTOSEND_ENABLED flag, which only takes effect at process startup.
"""
from __future__ import annotations

from config import settings
from database.models import AppSetting

_AUTOSEND_ENABLED_KEY = "autosend_enabled"


def get_autosend_enabled(db) -> bool:
    """DB value if ever set (via the Settings toggle), else the .env default."""
    row = db.query(AppSetting).filter(AppSetting.key == _AUTOSEND_ENABLED_KEY).first()
    if row and row.value is not None:
        return row.value == "true"
    return settings.autosend_enabled


def set_autosend_enabled(db, enabled: bool) -> None:
    row = db.query(AppSetting).filter(AppSetting.key == _AUTOSEND_ENABLED_KEY).first()
    value = "true" if enabled else "false"
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=_AUTOSEND_ENABLED_KEY, value=value))


_WEEKLY_AUTOSEND_ENABLED_KEY = "weekly_autosend_enabled"


def get_weekly_autosend_enabled(db) -> bool:
    """Off unless explicitly turned on (no .env default — this is a DB-only
    toggle, added 2026-08-24 per explicit instruction: Weekly reports now
    auto-SEND every Monday except the first Monday of the month, which
    needs its own not-yet-built template)."""
    row = db.query(AppSetting).filter(AppSetting.key == _WEEKLY_AUTOSEND_ENABLED_KEY).first()
    return row.value == "true" if row and row.value is not None else False


def set_weekly_autosend_enabled(db, enabled: bool) -> None:
    row = db.query(AppSetting).filter(AppSetting.key == _WEEKLY_AUTOSEND_ENABLED_KEY).first()
    value = "true" if enabled else "false"
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=_WEEKLY_AUTOSEND_ENABLED_KEY, value=value))


_INCOMING_SYNC_ENABLED_KEY = "incoming_sync_enabled"


def get_incoming_sync_enabled(db) -> bool:
    """Off by default — the incoming-mail poller only runs once explicitly enabled."""
    row = db.query(AppSetting).filter(AppSetting.key == _INCOMING_SYNC_ENABLED_KEY).first()
    return row.value == "true" if row and row.value is not None else False


def set_incoming_sync_enabled(db, enabled: bool) -> None:
    row = db.query(AppSetting).filter(AppSetting.key == _INCOMING_SYNC_ENABLED_KEY).first()
    value = "true" if enabled else "false"
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=_INCOMING_SYNC_ENABLED_KEY, value=value))


# ---------------------------------------------------------------------
# Limit-approval forward drafting (supervised trial).
#
# Two keys, not one. The "since" timestamp is the important half: without
# it, switching this on would immediately draft forwards for the ENTIRE
# historical backlog (83 open requests at the time this was added) and
# bury the real, current ones in the Drafts folder. Stamping the moment it
# was enabled means only mail that arrives AFTER that gets drafted.
# ---------------------------------------------------------------------
# ---------------------------------------------------------------------
# Weekdays the daily autosend must NOT run.
#
# Python's weekday() convention: Monday=0 ... Sunday=6. Stored as a
# comma-separated list so it stays inspectable in the DB and can be changed
# without a deploy. Default: Monday, because the daily report covers the
# previous day and Monday's would cover Sunday (no business activity).
# ---------------------------------------------------------------------
_AUTOSEND_SKIP_WEEKDAYS_KEY = "autosend_skip_weekdays"
_DEFAULT_SKIP_WEEKDAYS = "0"  # Monday

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def get_autosend_skip_weekdays(db) -> list[int]:
    row = db.query(AppSetting).filter(AppSetting.key == _AUTOSEND_SKIP_WEEKDAYS_KEY).first()
    raw = row.value if row and row.value is not None else _DEFAULT_SKIP_WEEKDAYS
    out = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit() and 0 <= int(part) <= 6:
            out.append(int(part))
    return sorted(set(out))


def set_autosend_skip_weekdays(db, days: list[int]) -> None:
    clean = sorted({d for d in days if isinstance(d, int) and 0 <= d <= 6})
    value = ",".join(str(d) for d in clean)
    row = db.query(AppSetting).filter(AppSetting.key == _AUTOSEND_SKIP_WEEKDAYS_KEY).first()
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=_AUTOSEND_SKIP_WEEKDAYS_KEY, value=value))


_LIMIT_FORWARD_ENABLED_KEY = "limit_forward_drafts_enabled"
_LIMIT_FORWARD_SINCE_KEY = "limit_forward_drafts_since"


def get_limit_forward_enabled(db) -> bool:
    """Off by default. When on, this actually SENDS the limit-approval
    forward to Priyanshu — see services/limit_forward_service.py. (Key name
    kept as "..._drafts_enabled" from its earlier draft-only phase; renaming
    the stored AppSetting key isn't worth a migration for an internal id.)
    """
    row = db.query(AppSetting).filter(AppSetting.key == _LIMIT_FORWARD_ENABLED_KEY).first()
    return row.value == "true" if row and row.value is not None else False


def get_limit_forward_since(db):
    """ISO timestamp of when forwarding was last switched on, or None."""
    from datetime import datetime

    row = db.query(AppSetting).filter(AppSetting.key == _LIMIT_FORWARD_SINCE_KEY).first()
    if not row or not row.value:
        return None
    try:
        return datetime.fromisoformat(row.value)
    except ValueError:
        return None


def set_limit_forward_enabled(db, enabled: bool) -> None:
    from datetime import datetime

    row = db.query(AppSetting).filter(AppSetting.key == _LIMIT_FORWARD_ENABLED_KEY).first()
    value = "true" if enabled else "false"
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=_LIMIT_FORWARD_ENABLED_KEY, value=value))

    if enabled:
        # Re-stamp on every enable, so turning it off and on again doesn't
        # suddenly sweep up everything that arrived while it was off.
        stamp = datetime.utcnow().isoformat()
        srow = db.query(AppSetting).filter(AppSetting.key == _LIMIT_FORWARD_SINCE_KEY).first()
        if srow:
            srow.value = stamp
        else:
            db.add(AppSetting(key=_LIMIT_FORWARD_SINCE_KEY, value=stamp))


_INCOMING_ACK_ENABLED_KEY = "incoming_ack_drafts_enabled"
_INCOMING_ACK_SINCE_KEY = "incoming_ack_drafts_since"


def get_incoming_ack_enabled(db) -> bool:
    """Off by default. When on, drafts (never sends) a generic 'received,
    noted' acknowledgment for incoming SBI-domain mail in the periodic
    status-push categories (SBI Data / Status Push, Report Submission /
    Status, Micro ATM Report, BC-CSP Agreement & PVR Pendency Report) — see
    services/incoming_ack_service.py. DRAFT ONLY, mirrors the limit-forward
    trial pattern above."""
    row = db.query(AppSetting).filter(AppSetting.key == _INCOMING_ACK_ENABLED_KEY).first()
    return row.value == "true" if row and row.value is not None else False


def get_incoming_ack_since(db):
    from datetime import datetime

    row = db.query(AppSetting).filter(AppSetting.key == _INCOMING_ACK_SINCE_KEY).first()
    if not row or not row.value:
        return None
    try:
        return datetime.fromisoformat(row.value)
    except ValueError:
        return None


def set_incoming_ack_enabled(db, enabled: bool) -> None:
    from datetime import datetime

    row = db.query(AppSetting).filter(AppSetting.key == _INCOMING_ACK_ENABLED_KEY).first()
    value = "true" if enabled else "false"
    if row:
        row.value = value
    else:
        db.add(AppSetting(key=_INCOMING_ACK_ENABLED_KEY, value=value))

    if enabled:
        stamp = datetime.utcnow().isoformat()
        srow = db.query(AppSetting).filter(AppSetting.key == _INCOMING_ACK_SINCE_KEY).first()
        if srow:
            srow.value = stamp
        else:
            db.add(AppSetting(key=_INCOMING_ACK_SINCE_KEY, value=stamp))
