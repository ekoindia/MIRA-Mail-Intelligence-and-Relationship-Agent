from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import get_current_user
from config import settings
from database.db import get_db
from services.automation_settings_service import (
    WEEKDAY_NAMES,
    get_autosend_enabled,
    get_autosend_skip_weekdays,
    set_autosend_enabled,
    set_autosend_skip_weekdays,
)

router = APIRouter(prefix="/api/automation", tags=["automation"])


@router.get("/status")
def get_status(user: dict = Depends(get_current_user)):
    from datetime import datetime

    with get_db() as db:
        skip = get_autosend_skip_weekdays(db)
        return {
            "autosendEnabled": get_autosend_enabled(db),
            "fetchTime": settings.autosend_fetch_time,
            "sendTime": settings.autosend_send_time,
            "skipWeekdays": skip,
            "skipWeekdayNames": [WEEKDAY_NAMES[d] for d in skip],
            # Surfaced so the UI can say "not sending today" instead of the
            # user wondering why nothing went out.
            "skippedToday": datetime.now().weekday() in skip,
        }


class ToggleIn(BaseModel):
    enabled: bool


@router.patch("/autosend")
def set_autosend(body: ToggleIn, user: dict = Depends(get_current_user)):
    with get_db() as db:
        set_autosend_enabled(db, body.enabled)
    return {"autosendEnabled": body.enabled}


class SkipWeekdaysIn(BaseModel):
    days: list[int]


@router.patch("/skip-weekdays")
def set_skip_weekdays(body: SkipWeekdaysIn, user: dict = Depends(get_current_user)):
    with get_db() as db:
        set_autosend_skip_weekdays(db, body.days)
        skip = get_autosend_skip_weekdays(db)
    return {"skipWeekdays": skip, "skipWeekdayNames": [WEEKDAY_NAMES[d] for d in skip]}
