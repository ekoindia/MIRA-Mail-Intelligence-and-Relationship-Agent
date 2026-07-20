from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_user
from database.db import get_db
from database.models import EmailTemplate, ReportMaster
from services.audit_service import log_action
from utils.helpers import SUPPORTED_TEMPLATE_VARS, render_template

router = APIRouter(prefix="/api/templates", tags=["templates"])


class TemplateIn(BaseModel):
    name: str
    subject: str
    bodyHtml: str
    isDefault: bool = False
    reportIds: list[int] = []


class PreviewIn(BaseModel):
    subject: str
    bodyHtml: str


def _template_to_dict(t: EmailTemplate, mapped: list[str]) -> dict:
    return {
        "id": t.id, "name": t.name, "subject": t.subject, "bodyHtml": t.body_html,
        "isDefault": t.is_default, "updatedAt": t.updated_at.isoformat(),
        "mappedReports": mapped,
    }


def _reports_by_template(db) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for r in db.query(ReportMaster).all():
        if r.default_template_id:
            out.setdefault(r.default_template_id, []).append(r.report_name)
    return out


@router.get("/variables")
def variables(user: dict = Depends(get_current_user)):
    return SUPPORTED_TEMPLATE_VARS


@router.get("")
def list_templates(user: dict = Depends(get_current_user)):
    with get_db() as db:
        templates = db.query(EmailTemplate).order_by(EmailTemplate.name).all()
        mapping = _reports_by_template(db)
        return [_template_to_dict(t, mapping.get(t.id, [])) for t in templates]


@router.post("/preview")
def preview(body: PreviewIn, user: dict = Depends(get_current_user)):
    sample = {
        "Recipient_Name": "Rohit Sharma", "Branch_Name": "Connaught Place Branch",
        "RBO_Name": "Lucknow RBO", "AO_Name": "Lucknow AO", "LHO_Name": "Delhi LHO",
        "Corp_Name": "Corporate Center", "Report_Name": "Weekly Sales Report", "Date": "13-Jul-2026",
        "Week_Number": "29", "Week_Start": "13", "Week_End": "19 Jul 2026", "Month_Year": "July 2026",
    }
    return {"subject": render_template(body.subject, sample), "bodyHtml": render_template(body.bodyHtml, sample)}


@router.post("")
def create_template(body: TemplateIn, user: dict = Depends(get_current_user)):
    if not body.name.strip() or not body.subject.strip():
        raise HTTPException(status_code=400, detail="Name and subject are required.")
    with get_db() as db:
        if db.query(EmailTemplate).filter(EmailTemplate.name == body.name.strip()).first():
            raise HTTPException(status_code=400, detail="A template with this name already exists.")
        if body.isDefault:
            db.query(EmailTemplate).update({EmailTemplate.is_default: False})
        record = EmailTemplate(
            name=body.name.strip(), subject=body.subject, body_html=body.bodyHtml,
            is_default=body.isDefault, created_by=user["id"],
        )
        db.add(record)
        db.flush()
        for rid in body.reportIds:
            rm = db.query(ReportMaster).get(rid)
            if rm:
                rm.default_template_id = record.id
        log_action(db, "CREATE_TEMPLATE", user_id=user["id"], username=user["username"],
                   entity_type="EmailTemplate", entity_id=record.id, details=body.name)
        return _template_to_dict(record, body.reportIds and [
            db.query(ReportMaster).get(rid).report_name for rid in body.reportIds if db.query(ReportMaster).get(rid)
        ] or [])


@router.put("/{template_id}")
def update_template(template_id: int, body: TemplateIn, user: dict = Depends(get_current_user)):
    if not body.name.strip() or not body.subject.strip():
        raise HTTPException(status_code=400, detail="Name and subject are required.")
    with get_db() as db:
        record = db.query(EmailTemplate).get(template_id)
        if not record:
            raise HTTPException(status_code=404, detail="Template not found.")
        if body.isDefault:
            db.query(EmailTemplate).update({EmailTemplate.is_default: False})

        record.name, record.subject, record.body_html, record.is_default = (
            body.name.strip(), body.subject, body.bodyHtml, body.isDefault,
        )

        mapping = _reports_by_template(db)
        previously_mapped_ids = {
            r.id for r in db.query(ReportMaster).filter(ReportMaster.default_template_id == template_id).all()
        }
        selected_ids = set(body.reportIds)
        for rid in previously_mapped_ids - selected_ids:
            rm = db.query(ReportMaster).get(rid)
            if rm:
                rm.default_template_id = None
        for rid in selected_ids:
            rm = db.query(ReportMaster).get(rid)
            if rm:
                rm.default_template_id = record.id

        log_action(db, "EDIT_TEMPLATE", user_id=user["id"], username=user["username"],
                   entity_type="EmailTemplate", entity_id=record.id, details=body.name)

        mapped_names = [db.query(ReportMaster).get(rid).report_name for rid in selected_ids
                        if db.query(ReportMaster).get(rid)]
        return _template_to_dict(record, mapped_names)


@router.delete("/{template_id}")
def delete_template(template_id: int, user: dict = Depends(get_current_user)):
    with get_db() as db:
        record = db.query(EmailTemplate).get(template_id)
        if not record:
            raise HTTPException(status_code=404, detail="Template not found.")
        db.query(ReportMaster).filter(ReportMaster.default_template_id == template_id).update(
            {ReportMaster.default_template_id: None}
        )
        name = record.name
        db.delete(record)
        log_action(db, "DELETE_TEMPLATE", user_id=user["id"], username=user["username"],
                   entity_type="EmailTemplate", entity_id=template_id, details=name)
    return {"deleted": True}
