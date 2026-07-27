from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from api.auth import get_current_user
from database.db import get_db
from database.models import EmailTemplate, ReportMaster, ReportUpload
from services.combined_digest_service import (
    ALL_LEVELS,
    automated_reports_for_level,
    display_report_list,
    is_automated,
    reports_for_level,
    resolve_digest_template_id,
    send_combined_digest,
)
from services.report_aggregation_service import MERGED_INTO_OTHER_REPORT
from services.report_send_service import send_report_now
from utils.helpers import format_bytes

router = APIRouter(prefix="/api/reports", tags=["reports"])

# Reverse of MERGED_INTO_OTHER_REPORT: target report name -> the report
# name(s) folded into its email. Used purely for display — the Report
# Mapping table shows one combined line ("Account Opening (Daily) &
# Social Security Scheme (Daily)") instead of two rows, one of which would
# otherwise show as a broken "sent together with X" test result.
_MERGE_DISPLAY_NAMES: dict[str, list[str]] = {}
for _merged_name, _target_name in MERGED_INTO_OTHER_REPORT.items():
    _MERGE_DISPLAY_NAMES.setdefault(_target_name, []).append(_merged_name)


def _report_to_dict(r: ReportMaster) -> dict:
    merged_in = _MERGE_DISPLAY_NAMES.get(r.report_name)
    display_name = " & ".join([r.report_name, *merged_in]) if merged_in else r.report_name
    return {
        "id": r.id,
        "reportName": display_name,
        "description": r.description,
        "frequency": r.frequency,
        "orgLevels": r.org_levels.split(",") if r.org_levels else [],
        "isActive": r.is_active,
        "templateId": r.default_template_id,
        "templateName": r.default_template.name if r.default_template else None,
        # "draft" | "send" | None (None = falls back to the account-wide
        # Gmail Connection default in Settings). Governs what the automatic
        # daily autosend cycle does — see Settings page.
        "deliveryMode": r.delivery_mode or "draft",
    }


class DeliveryModeIn(BaseModel):
    mode: str  # "draft" | "send"


@router.patch("/delivery-mode")
def set_delivery_mode_all(body: DeliveryModeIn, user: dict = Depends(get_current_user)):
    """One global Draft Only / Send Directly setting applied to every report at
    once — what the automatic daily autosend cycle does (set from Settings)."""
    if body.mode not in ("draft", "send"):
        raise HTTPException(status_code=400, detail="mode must be 'draft' or 'send'.")
    with get_db() as db:
        db.query(ReportMaster).update({ReportMaster.delivery_mode: body.mode})
        db.flush()
    return {"deliveryMode": body.mode}


class SendByFrequencyIn(BaseModel):
    frequency: str  # "Daily" | "Weekly" | "Monthly"
    mode: str  # "draft" | "send"


@router.post("/send-by-frequency")
def send_by_frequency(body: SendByFrequencyIn, user: dict = Depends(get_current_user)):
    """
    Manually trigger everything for one frequency right now, explicitly as
    drafts or as real sends — the Scheduler page's per-frequency buttons.

    Daily keeps the original per-report path (send_report_now): Account
    Opening (Daily) already covers Social Security Scheme via
    MERGED_INTO_OTHER_REPORT, so Daily already sends one combined email per
    RBO with no further change needed.

    Weekly and Monthly go through services.combined_digest_service instead:
    one combined email per RECIPIENT LEVEL (RBO/LHO/Corporate Center/
    Branch), covering every automated report mapped to that level — per
    explicit instruction that all reports assigned to a level for a given
    frequency should arrive as a single email, not one per report.
    """
    if body.mode not in ("draft", "send"):
        raise HTTPException(status_code=400, detail="mode must be 'draft' or 'send'.")
    if body.frequency not in ("Daily", "Weekly", "Monthly"):
        raise HTTPException(status_code=400, detail="frequency must be 'Daily', 'Weekly', or 'Monthly'.")

    with get_db() as db:
        if body.frequency == "Daily":
            reports = (
                db.query(ReportMaster)
                .filter(
                    ReportMaster.frequency == body.frequency,
                    ReportMaster.org_levels.isnot(None),
                    ReportMaster.org_levels != "",
                )
                .order_by(ReportMaster.report_name)
                .all()
            )
            results = []
            for rm in reports:
                try:
                    results.append({**send_report_now(db, rm, user, force_draft=(body.mode == "draft")), "skipped": False})
                except ValueError as exc:
                    results.append({
                        "reportId": rm.id, "reportName": rm.report_name, "skipped": True, "reason": str(exc),
                    })
            return {"results": results}

        results = []
        for level in ALL_LEVELS:
            reports = automated_reports_for_level(db, body.frequency, level)
            if not reports:
                continue  # this level has nothing automated for this frequency — no email, no skip entry
            try:
                digest = send_combined_digest(db, body.frequency, level, user, force_draft=(body.mode == "draft"))
                results.append({**digest, "skipped": False})
            except ValueError as exc:
                results.append({
                    "level": level.value, "reportNames": [r.report_name for r in reports],
                    "skipped": True, "reason": str(exc),
                })
        return {"results": results}


@router.get("/mapping")
def report_mapping(user: dict = Depends(get_current_user)):
    """
    The Reports page's mapping table: one row per (frequency, org level),
    listing every report that goes out in that level's single combined
    email and the template that renders it. Not-yet-automated reports are
    still listed (so the mapping reflects the full configured intent, not
    just what currently has real data) but flagged so the UI can badge
    them as paused — see combined_digest_service.is_automated.
    """
    with get_db() as db:
        rows = []
        for frequency in ("Daily", "Weekly", "Monthly"):
            for level in ALL_LEVELS:
                reports = reports_for_level(db, frequency, level)
                if not reports:
                    continue
                automated = [r for r in reports if is_automated(r.report_name)]
                template_name, template_id = None, None
                if automated:
                    try:
                        template_id = resolve_digest_template_id(db, frequency, level, automated)
                        template = db.query(EmailTemplate).get(template_id)
                        template_name = template.name if template else None
                    except ValueError:
                        pass
                rows.append({
                    "frequency": frequency,
                    "level": level.value,
                    # Collapsed for display: Social Security Scheme folds
                    # into Account Opening's entry instead of showing
                    # separately as if it were paused — it's fully covered
                    # by Account Opening's own combined aggregator.
                    "reports": display_report_list(reports),
                    "templateId": template_id,
                    "templateName": template_name,
                })
        return rows


@router.get("")
def list_reports(user: dict = Depends(get_current_user)):
    with get_db() as db:
        reports = db.query(ReportMaster).order_by(ReportMaster.report_name).all()
        # Reports merged into another report's email don't get their own row.
        reports = [r for r in reports if r.report_name not in MERGED_INTO_OTHER_REPORT]
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
