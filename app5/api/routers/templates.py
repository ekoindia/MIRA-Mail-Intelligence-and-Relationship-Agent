from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_user
from database.db import get_db
from database.models import EmailTemplate, ReportMaster
from services.audit_service import log_action
from services.combined_digest_service import (
    ALL_LEVELS,
    automated_reports_for_level,
    display_report_list,
    reports_for_level,
    resolve_digest_template_id,
)
from utils.helpers import SUPPORTED_TEMPLATE_VARS, TEMPLATE_IF_PATTERN, render_email_body, render_template

router = APIRouter(prefix="/api/templates", tags=["templates"])

_FREQUENCIES = ("Daily", "Weekly", "Monthly")


class TemplateIn(BaseModel):
    name: str
    subject: str
    bodyHtml: str
    isDefault: bool = False
    reportIds: list[int] = []


class PreviewIn(BaseModel):
    subject: str
    bodyHtml: str


def _template_to_dict(t: EmailTemplate, mapped: list[ReportMaster], is_managed: bool) -> dict:
    return {
        "id": t.id, "name": t.name, "subject": t.subject, "bodyHtml": t.body_html,
        "isDefault": t.is_default, "updatedAt": t.updated_at.isoformat(),
        # Raw report names/ids, unmerged and unfiltered — deliberately NOT
        # the display-merged, filtered list /api/reports returns (that one
        # folds Social Security Scheme (Daily) into Account Opening (Daily)'s
        # display name and drops it from the list entirely, which used to
        # make name-based matching here silently lose 2 of a template's 3
        # mapped reports). mappedReportIds is what the Templates page's
        # "Applies to Reports" picker should actually use.
        "mappedReports": [r.report_name for r in mapped],
        "mappedReportIds": [r.id for r in mapped],
        # Same list collapsed for display: automation status per entry so
        # the UI can badge not-yet-automated reports as paused, and a
        # report merged into another's email (Social Security Scheme into
        # Account Opening) folded into one "X & Y" entry instead of
        # showing as its own separate, misleadingly-paused-looking item.
        "mappedReportDetails": display_report_list(mapped),
        # Every template actually resolved by the Reports Mapping page for
        # some (frequency, level) — whether via the single-report shortcut
        # (Daily RBO Update, Weekly Branch Update) or the multi-report name
        # lookup (everything else) — gets its "applies to" list from that
        # mapping, not from manually toggling chips here. Reassigning
        # default_template_id on these wouldn't do anything real (the
        # single-report case would just silently break, since that's
        # exactly the FK the mapping itself reads), so the frontend shows
        # all of these as read-only.
        "isDigestManaged": is_managed,
    }


def _reports_by_template(db) -> tuple[dict[int, list[ReportMaster]], set[int]]:
    """
    For every (frequency, level) with at least one automated report,
    resolve which template actually renders that combined email (same
    logic /api/reports/mapping uses) and attach the FULL configured report
    list for that level (automated + not-yet-automated) to it.

    Returns (template_id -> mapped reports, set of template ids that are
    mapping-managed this way). Any template not in that set falls back to
    the plain ReportMaster.default_template_id-based lookup — reserved for
    genuinely custom templates not wired into the Reports mapping at all.
    """
    managed: dict[int, list[ReportMaster]] = {}
    managed_ids: set[int] = set()
    for frequency in _FREQUENCIES:
        for level in ALL_LEVELS:
            automated = automated_reports_for_level(db, frequency, level)
            if not automated:
                continue
            try:
                template_id = resolve_digest_template_id(db, frequency, level, automated)
            except ValueError:
                continue
            managed_ids.add(template_id)
            seen_ids = {r.id for r in managed.get(template_id, [])}
            for r in reports_for_level(db, frequency, level):
                if r.id not in seen_ids:
                    managed.setdefault(template_id, []).append(r)
                    seen_ids.add(r.id)

    out: dict[int, list[ReportMaster]] = dict(managed)
    for r in db.query(ReportMaster).all():
        if r.default_template_id and r.default_template_id not in managed_ids:
            out.setdefault(r.default_template_id, []).append(r)
    return out, managed_ids


@router.get("/variables")
def variables(user: dict = Depends(get_current_user)):
    return SUPPORTED_TEMPLATE_VARS


@router.get("/report-options")
def report_options(user: dict = Depends(get_current_user)):
    """Every ReportMaster row, raw name and id, unmerged/unfiltered — for the
    Templates page's report picker. /api/reports is display-oriented (merges
    Account Opening + Social Security Scheme into one line, hides the merged
    row entirely) which makes it unsuitable for template-to-report mapping."""
    with get_db() as db:
        reports = db.query(ReportMaster).order_by(ReportMaster.report_name).all()
        return [{"id": r.id, "reportName": r.report_name, "frequency": r.frequency} for r in reports]


@router.get("")
def list_templates(user: dict = Depends(get_current_user)):
    with get_db() as db:
        templates = db.query(EmailTemplate).order_by(EmailTemplate.name).all()
        mapping, managed_ids = _reports_by_template(db)
        return [_template_to_dict(t, mapping.get(t.id, []), t.id in managed_ids) for t in templates]


@router.post("/preview")
def preview(body: PreviewIn, user: dict = Depends(get_current_user)):
    sample = {
        "Recipient_Name": "Rohit Sharma", "Branch_Name": "Connaught Place Branch",
        "RBO_Name": "Lucknow RBO", "AO_Name": "Lucknow AO", "LHO_Name": "Delhi LHO",
        "Corp_Name": "Corporate Center", "Report_Name": "Weekly Sales Report", "Date": "13-Jul-2026",
        "Week_Number": "29", "Week_Start": "13", "Week_End": "19 Jul 2026", "Month_Year": "July 2026",
    }
    # Preview is cosmetic only and never fetches real sheet data — but a
    # {{#if Flag}}...{{/if}} conditional section (e.g. Loan Lead Generation,
    # which real sends omit for a recipient with zero leads) would
    # otherwise vanish here too, since Flag is never in this sample dict.
    # Force every #if flag actually referenced in this template to true so
    # its content renders here as raw {{Variable}} placeholders, same as
    # everywhere else in this preview, instead of being silently hidden.
    for flag, _body in TEMPLATE_IF_PATTERN.findall(body.subject + body.bodyHtml):
        sample[flag] = True
    return {"subject": render_template(body.subject, sample), "bodyHtml": render_email_body(body.bodyHtml, sample)}


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
        mapping, managed_ids = _reports_by_template(db)
        return _template_to_dict(record, mapping.get(record.id, []), record.id in managed_ids)


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

        mapping, managed_ids = _reports_by_template(db)
        return _template_to_dict(record, mapping.get(record.id, []), record.id in managed_ids)


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
