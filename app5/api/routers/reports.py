from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.auth import get_current_user
from database.db import get_db
from database.models import EmailLog, EmailTemplate, ReportMaster, ReportUpload
from database.org_models import OrgLevel
from services.distribution_service import ResolvedRecipient, create_distribution_job
from services.email_service import run_distribution_job
from services.recipient_resolution_service import resolve_recipient_by_ref, resolve_recipients_for_levels
from services.report_aggregation_service import AGGREGATORS
from services.segmented_distribution_service import apply_segmented_overrides
from utils.helpers import format_bytes
from utils.validators import is_valid_email

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _report_to_dict(r: ReportMaster) -> dict:
    return {
        "id": r.id,
        "reportName": r.report_name,
        "description": r.description,
        "frequency": r.frequency,
        "orgLevels": r.org_levels.split(",") if r.org_levels else [],
        "isActive": r.is_active,
        "templateId": r.default_template_id,
        "templateName": r.default_template.name if r.default_template else None,
        # "draft" | "send" | None (None = falls back to the account-wide
        # Gmail Connection default in Settings).
        "deliveryMode": r.delivery_mode or "draft",
    }


class DeliveryModeIn(BaseModel):
    mode: str  # "draft" | "send"


@router.patch("/{report_id}/delivery-mode")
def set_delivery_mode(report_id: int, body: DeliveryModeIn, user: dict = Depends(get_current_user)):
    if body.mode not in ("draft", "send"):
        raise HTTPException(status_code=400, detail="mode must be 'draft' or 'send'.")
    with get_db() as db:
        rm = db.query(ReportMaster).get(report_id)
        if not rm:
            raise HTTPException(status_code=404, detail="Report not found.")
        rm.delivery_mode = body.mode
        db.flush()
        return _report_to_dict(rm)


@router.get("/{report_id}/recipients")
def report_recipients(report_id: int, user: dict = Depends(get_current_user)):
    """
    Real recipients currently configured for this report's org level(s) —
    powers the Test Send picker on the Reports page. LHO/Branch come
    straight from the Calling Sheet's own mail-ID columns; RBO/AO/Corporate
    Center come from the manually-configured list on the Settings page
    (see services/recipient_resolution_service.py).
    """
    with get_db() as db:
        rm = db.query(ReportMaster).get(report_id)
        if not rm:
            raise HTTPException(status_code=404, detail="Report not found.")
        levels = [OrgLevel(v.strip()) for v in (rm.org_levels or "").split(",") if v.strip()]
        if not levels:
            return []
        recipients = resolve_recipients_for_levels(db, levels)
        return [
            {
                "source": r.source, "unitId": r.unit_id, "name": r.name, "level": r.level, "email": r.email,
                "ccEmails": r.cc_emails,
            }
            for r in recipients
        ]


class TestSendIn(BaseModel):
    source: str  # "sheet" | "org"
    level: str
    unitId: int | None = None  # required when source == "org"
    name: str | None = None  # required when source == "sheet"
    overrideEmail: str | None = None  # if set, redirect delivery here but keep using the real recipient's data


@router.post("/{report_id}/test-send")
def test_send(report_id: int, body: TestSendIn, user: dict = Depends(get_current_user)):
    """
    One-off send/draft for a single recipient, right now — doesn't wait for
    the schedule. Reuses the exact same job-creation, segmentation, and
    send-mode logic as a real scheduled run (see auto_distribution_service),
    so it's a true test of the live path, not a separate mock.

    If overrideEmail is set, the email is redirected there but still built
    from the real recipient's data (recipient_name/level are unchanged,
    only the destination address is swapped) — lets you preview exactly
    what a real recipient would receive without it landing in their inbox.
    """
    with get_db() as db:
        rm = db.query(ReportMaster).get(report_id)
        if not rm:
            raise HTTPException(status_code=404, detail="Report not found.")

        recipient_ref = resolve_recipient_by_ref(
            db, body.source, body.level, unit_id=body.unitId, name=body.name,
        )
        if not recipient_ref:
            raise HTTPException(status_code=404, detail="Recipient not found.")

        is_test_redirect = bool((body.overrideEmail or "").strip())
        destination = (body.overrideEmail or "").strip() or recipient_ref.email
        if not is_valid_email(destination):
            raise HTTPException(status_code=400, detail=f"Invalid email address '{destination}'.")
        # A test redirect must never also CC the real recipient's real CC
        # list — that would defeat the entire point of testing against a
        # safe address instead of the live one.
        cc_emails = None if is_test_redirect else recipient_ref.cc_emails

        latest_upload = (
            db.query(ReportUpload)
            .filter(ReportUpload.report_master_id == report_id)
            .order_by(ReportUpload.uploaded_at.desc())
            .first()
        )
        if not latest_upload:
            raise HTTPException(
                status_code=400,
                detail="No data fetched yet for this report — run Test Fetch on the Scheduler page first.",
            )

        recipient = ResolvedRecipient(
            name=recipient_ref.name, email=destination, recipient_type=recipient_ref.level,
            lho_name=recipient_ref.name if recipient_ref.level == OrgLevel.LHO.value else None,
            cc_emails=cc_emails,
        )
        job = create_distribution_job(
            db, upload_id=latest_upload.id, template_id=rm.default_template_id,
            recipients=[recipient], created_by_id=user["id"], created_by_username=user["username"],
            is_scheduled_run=False,
        )
        if rm.report_name in AGGREGATORS:
            apply_segmented_overrides(db, job, rm.report_name)
        run_distribution_job(db, job.id)

        log = db.query(EmailLog).filter(EmailLog.job_id == job.id).first()
        return {
            "jobId": job.id, "sentTo": log.recipient_email, "ccTo": log.cc_emails,
            "status": log.status.value, "sentVia": log.sent_via, "error": log.last_error,
        }


@router.get("")
def list_reports(user: dict = Depends(get_current_user)):
    with get_db() as db:
        reports = db.query(ReportMaster).order_by(ReportMaster.report_name).all()
        return [_report_to_dict(r) for r in reports]


@router.get("/{report_id}/files")
def list_files(report_id: int, user: dict = Depends(get_current_user)):
    with get_db() as db:
        rm = db.query(ReportMaster).get(report_id)
        if not rm:
            raise HTTPException(status_code=404, detail="Report not found.")
        uploads = (
            db.query(ReportUpload)
            .filter(ReportUpload.report_master_id == report_id)
            .order_by(ReportUpload.uploaded_at.desc())
            .all()
        )
        return [
            {
                "id": u.id, "fileName": u.file_name, "fileType": u.file_type,
                "sizeLabel": format_bytes(u.file_size_bytes), "uploadedAt": u.uploaded_at.isoformat(),
            }
            for u in uploads
        ]


@router.get("/files/{upload_id}/download")
def download_file(upload_id: int, user: dict = Depends(get_current_user)):
    with get_db() as db:
        u = db.query(ReportUpload).get(upload_id)
        if not u:
            raise HTTPException(status_code=404, detail="File not found.")
        return FileResponse(u.stored_path, filename=u.file_name)
