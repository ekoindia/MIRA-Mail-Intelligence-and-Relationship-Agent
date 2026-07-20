from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_user
from database.db import get_db
from database.models import ReportMaster
from database.report_source_models import AuthType, HttpMethod, ReportSource, SourceType
from services.audit_service import log_action
from services.report_source_service import fetch_any_report, list_recent_runs
from services.sheet_source_service import extract_spreadsheet_id

router = APIRouter(prefix="/api/sources", tags=["sources"])


class SourceIn(BaseModel):
    name: str
    reportId: int
    sourceType: str = "Google Sheet"

    # REST fields
    baseUrl: str | None = None
    httpMethod: str = "GET"
    endpointPath: str | None = None
    authType: str = "None"
    authHeaderName: str | None = None
    authSecret: str | None = None
    filenameTemplate: str | None = None

    # Google Sheet fields
    googleSheetUrl: str | None = None
    googleSheetTab: str | None = None


def _source_to_dict(s: ReportSource) -> dict:
    return {
        "id": s.id, "name": s.name, "reportId": s.report_master_id,
        "reportName": s.report_master.report_name,
        "sourceType": s.source_type.value,
        "baseUrl": s.base_url, "httpMethod": s.http_method.value, "endpointPath": s.endpoint_path_template,
        "authType": s.auth_type.value, "filenameTemplate": s.filename_template,
        "googleSheetId": s.google_sheet_id, "googleSheetTab": s.google_sheet_tab,
        "isActive": s.is_active,
    }


@router.get("")
def list_sources(user: dict = Depends(get_current_user)):
    with get_db() as db:
        sources = db.query(ReportSource).order_by(ReportSource.name).all()
        return [_source_to_dict(s) for s in sources]


@router.post("")
def create_source(body: SourceIn, user: dict = Depends(get_current_user)):
    with get_db() as db:
        if db.query(ReportSource).filter(ReportSource.name == body.name.strip()).first():
            raise HTTPException(status_code=400, detail="A source with this name already exists.")
        if not db.query(ReportMaster).get(body.reportId):
            raise HTTPException(status_code=400, detail="Report not found.")

        source_type = SourceType(body.sourceType)
        kwargs = dict(
            name=body.name.strip(), report_master_id=body.reportId, source_type=source_type,
            created_by=user["id"],
        )
        if source_type == SourceType.GOOGLE_SHEET:
            if not body.googleSheetUrl:
                raise HTTPException(status_code=400, detail="Google Sheet URL/ID is required.")
            kwargs.update(
                google_sheet_id=extract_spreadsheet_id(body.googleSheetUrl),
                google_sheet_tab=body.googleSheetTab or "Sheet1",
                filename_template=body.filenameTemplate or "{name}_{date}.xlsx",
            )
        else:
            if not body.baseUrl or not body.filenameTemplate:
                raise HTTPException(status_code=400, detail="Base URL and filename template are required.")
            kwargs.update(
                base_url=body.baseUrl, http_method=HttpMethod(body.httpMethod),
                endpoint_path_template=body.endpointPath, auth_type=AuthType(body.authType),
                auth_header_name=body.authHeaderName, auth_secret=body.authSecret,
                filename_template=body.filenameTemplate,
            )

        source = ReportSource(**kwargs)
        db.add(source)
        db.flush()
        log_action(db, "CREATE_REPORT_SOURCE", user_id=user["id"], username=user["username"],
                   entity_type="ReportSource", entity_id=source.id, details=body.name)
        return _source_to_dict(source)


@router.post("/{source_id}/test-fetch")
def test_fetch(source_id: int, user: dict = Depends(get_current_user)):
    with get_db() as db:
        source = db.query(ReportSource).get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found.")
        result = fetch_any_report(db, source, triggered_by="manual")
    return {
        "success": result["success"],
        "error": result["error"],
        "fileName": result["run"].resolved_filename,
    }


@router.get("/{source_id}/runs")
def runs(source_id: int, user: dict = Depends(get_current_user)):
    with get_db() as db:
        recent = list_recent_runs(db, source_id, limit=10)
        return [
            {
                "id": r.id, "status": r.status.value, "runAt": r.run_at.isoformat(),
                "triggeredBy": r.triggered_by, "fileName": r.resolved_filename, "error": r.error,
            }
            for r in recent
        ]


@router.patch("/{source_id}/toggle")
def toggle_active(source_id: int, user: dict = Depends(get_current_user)):
    with get_db() as db:
        source = db.query(ReportSource).get(source_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found.")
        source.is_active = not source.is_active
        return {"isActive": source.is_active}
