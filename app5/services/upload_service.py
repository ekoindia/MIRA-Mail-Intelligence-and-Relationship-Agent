"""Handles master-data uploads (Branch/LHO) and report file uploads."""
from __future__ import annotations

import uuid
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from config import BASE_DIR, settings
from database.models import Branch, LHO, ReportMaster, ReportUpload
from services.audit_service import log_action
from utils.validators import (
    is_valid_email,
    sanitize_filename,
    validate_master_excel_columns,
    validate_report_file,
)

BRANCH_REQUIRED_COLS = ["Branch Code", "Branch Name", "Branch Email", "LHO"]
LHO_OPTIONAL_EMAIL_COL = "LHO Email"


class UploadError(Exception):
    pass


def import_branch_lho_master(db: Session, dataframe: pd.DataFrame, uploaded_by: str) -> dict:
    """
    Import Branch/LHO master data from a DataFrame with columns:
    Branch Code, Branch Name, Branch Email, LHO, LHO Email

    Upserts LHOs first (by name), then upserts Branches (by code), linking to LHO.
    Returns a summary dict with counts and row-level errors.
    """
    ok, msg = validate_master_excel_columns(list(dataframe.columns), BRANCH_REQUIRED_COLS)
    if not ok:
        raise UploadError(msg)

    dataframe = dataframe.rename(columns={c: c.strip() for c in dataframe.columns})

    errors: list[str] = []
    branches_created, branches_updated = 0, 0
    lhos_created, lhos_updated = 0, 0
    lho_cache: dict[str, LHO] = {}

    for idx, row in dataframe.iterrows():
        row_num = idx + 2  # account for header row
        try:
            branch_code = str(row["Branch Code"]).strip()
            branch_name = str(row["Branch Name"]).strip()
            branch_email = str(row["Branch Email"]).strip()
            lho_name = str(row["LHO"]).strip() if pd.notna(row.get("LHO")) else ""
            lho_email = (
                str(row[LHO_OPTIONAL_EMAIL_COL]).strip()
                if LHO_OPTIONAL_EMAIL_COL in dataframe.columns and pd.notna(row.get(LHO_OPTIONAL_EMAIL_COL))
                else ""
            )
            region = str(row["Region"]).strip() if "Region" in dataframe.columns and pd.notna(row.get("Region")) else None

            if not branch_code or not branch_name:
                errors.append(f"Row {row_num}: Branch Code/Name is required.")
                continue
            if not is_valid_email(branch_email):
                errors.append(f"Row {row_num}: Invalid branch email '{branch_email}'.")
                continue

            lho_obj = None
            if lho_name:
                lho_obj = lho_cache.get(lho_name) or db.query(LHO).filter(LHO.lho_name == lho_name).first()
                if lho_obj:
                    if lho_email and is_valid_email(lho_email):
                        lho_obj.lho_email = lho_email
                    lho_obj.region = region or lho_obj.region
                    lhos_updated += 1
                else:
                    if lho_email and not is_valid_email(lho_email):
                        errors.append(f"Row {row_num}: Invalid LHO email '{lho_email}'.")
                        lho_email = ""
                    lho_obj = LHO(
                        lho_name=lho_name,
                        lho_email=lho_email or branch_email,
                        region=region,
                    )
                    db.add(lho_obj)
                    db.flush()
                    lhos_created += 1
                lho_cache[lho_name] = lho_obj

            existing = db.query(Branch).filter(Branch.branch_code == branch_code).first()
            if existing:
                existing.branch_name = branch_name
                existing.branch_email = branch_email
                existing.region = region or existing.region
                existing.lho = lho_obj
                branches_updated += 1
            else:
                db.add(
                    Branch(
                        branch_code=branch_code,
                        branch_name=branch_name,
                        branch_email=branch_email,
                        region=region,
                        lho=lho_obj,
                    )
                )
                branches_created += 1

        except Exception as exc:  # noqa: BLE001
            errors.append(f"Row {row_num}: {exc}")

    db.flush()
    log_action(
        db, "UPLOAD_MASTER", username=uploaded_by, entity_type="BranchLHOMaster",
        details=f"branches+{branches_created}/~{branches_updated}, lhos+{lhos_created}/~{lhos_updated}, "
                f"errors={len(errors)}",
    )

    return {
        "branches_created": branches_created,
        "branches_updated": branches_updated,
        "lhos_created": lhos_created,
        "lhos_updated": lhos_updated,
        "errors": errors,
    }


def save_report_file(
    db: Session,
    report_master_id: int,
    uploaded_file,
    uploaded_by_id: int,
    uploaded_by_username: str,
) -> ReportUpload:
    """Validate and persist an uploaded report file (PDF/XLSX) to disk + DB."""
    file_bytes = uploaded_file.getbuffer()
    size = file_bytes.nbytes
    ok, msg = validate_report_file(uploaded_file.name, size)
    if not ok:
        raise UploadError(msg)

    report_master = db.query(ReportMaster).get(report_master_id)
    if not report_master:
        raise UploadError("Report type not found.")

    safe_name = sanitize_filename(uploaded_file.name)
    unique_name = f"{uuid.uuid4().hex[:8]}_{safe_name}"
    dest_dir = BASE_DIR / settings.upload_dir
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / unique_name

    with open(dest_path, "wb") as f:
        f.write(file_bytes)

    ext = Path(safe_name).suffix.lower().lstrip(".")
    upload = ReportUpload(
        report_master_id=report_master_id,
        file_name=uploaded_file.name,
        stored_path=str(dest_path),
        file_type=ext,
        file_size_bytes=size,
        uploaded_by=uploaded_by_id,
    )
    db.add(upload)
    db.flush()

    log_action(
        db, "UPLOAD_REPORT", user_id=uploaded_by_id, username=uploaded_by_username,
        entity_type="ReportUpload", entity_id=upload.id,
        details=f"{uploaded_file.name} ({size} bytes) for report '{report_master.report_name}'",
    )
    return upload
