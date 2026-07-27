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
