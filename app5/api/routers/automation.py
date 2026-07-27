from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from api.auth import get_current_user
from config import settings
from database.db import get_db
from services.automation_settings_service import get_autosend_enabled, set_autosend_enabled

router = APIRouter(prefix="/api/automation", tags=["automation"])


@router.get("/status")
def get_status(user: dict = Depends(get_current_user)):
    with get_db() as db:
        return {
            "autosendEnabled": get_autosend_enabled(db),
            "fetchTime": settings.autosend_fetch_time,
            "sendTime": settings.autosend_send_time,
        }


class ToggleIn(BaseModel):
    enabled: bool


@router.patch("/autosend")
def set_autosend(body: ToggleIn, user: dict = Depends(get_current_user)):
    with get_db() as db:
        set_autosend_enabled(db, body.enabled)
    return {"autosendEnabled": body.enabled}
