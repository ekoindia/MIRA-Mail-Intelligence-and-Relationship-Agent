from __future__ import annotations

import io

import pandas as pd
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel

from api.auth import get_current_user
from database.db import get_db
from database.org_models import OrgLevel
from services.org_service import (
    OrgUploadError,
    add_org_unit,
    delete_org_unit,
    get_org_directory,
    import_org_units_auto,
    list_org_units,
    update_org_unit,
)

router = APIRouter(prefix="/api/org-units", tags=["org-units"])


def _unit_to_dict(u) -> dict:
    return {
        "id": u.id, "level": u.level.value, "unitCode": u.unit_code, "unitName": u.unit_name,
        "email": u.email, "ccEmails": u.cc_emails, "parent": u.parent.unit_name if u.parent else None,
        "region": u.region, "isActive": u.is_active,
    }


class OrgUnitIn(BaseModel):
    level: str
    unitName: str
    email: str
    ccEmails: str | None = None


class OrgUnitUpdateIn(BaseModel):
    unitName: str | None = None
    email: str | None = None
    ccEmails: str | None = None


@router.post("")
def create_unit(body: OrgUnitIn, user: dict = Depends(get_current_user)):
    try:
        level = OrgLevel(body.level)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid level '{body.level}'.")
    with get_db() as db:
        try:
            unit = add_org_unit(
                db, level, body.unitName, body.email, added_by=user["username"], cc_emails=body.ccEmails,
            )
        except OrgUploadError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return _unit_to_dict(unit)


@router.put("/{unit_id}")
def update_unit(unit_id: int, body: OrgUnitUpdateIn, user: dict = Depends(get_current_user)):
    with get_db() as db:
        try:
            unit = update_org_unit(
                db, unit_id, unit_name=body.unitName, email=body.email, updated_by=user["username"],
                cc_emails=body.ccEmails,
            )
        except OrgUploadError as exc:
            raise HTTPException(status_code=400 if "not found" not in str(exc).lower() else 404, detail=str(exc))
        return _unit_to_dict(unit)


@router.delete("/{unit_id}")
def delete_unit(unit_id: int, user: dict = Depends(get_current_user)):
    with get_db() as db:
        try:
            delete_org_unit(db, unit_id, deleted_by=user["username"])
        except OrgUploadError as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        return {"deleted": True}


@router.get("")
def list_units(level: str | None = None, user: dict = Depends(get_current_user)):
    with get_db() as db:
        lvl = OrgLevel(level) if level else None
        units = list_org_units(db, level=lvl)
        return [_unit_to_dict(u) for u in units]


@router.get("/directory")
def directory(user: dict = Depends(get_current_user)):
    with get_db() as db:
        return get_org_directory(db)


@router.post("/upload")
async def upload(level: str, file: UploadFile = File(...), user: dict = Depends(get_current_user)):
    try:
        lvl = OrgLevel(level)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid level '{level}'.")

    content = await file.read()
    try:
        df = pd.read_excel(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=400, detail=f"Couldn't read the file as an Excel sheet: {exc}")

    with get_db() as db:
        try:
            result = import_org_units_auto(db, lvl, df, user["username"])
        except OrgUploadError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
    return result
