"""
Google Sheets report source — the "calling sheet" that's updated daily.

Reads a tab from a Google Sheet (via the same OAuth connection used for
Gmail, extended with the spreadsheets.readonly scope — see
services/gmail_auth.SCOPES), converts it to an .xlsx, and registers it as
a normal ReportUpload, exactly like services/report_source_service.py does
for REST APIs. This lets the rest of the distribution pipeline (recipient
resolution, templates, batch send) stay unmodified regardless of which
kind of source a report is configured with.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

from config import BASE_DIR
from database.models import ReportUpload
from database.report_source_models import ReportSource, ReportSourceRun, RunStatus
from services.gmail_auth import get_credentials
from services.report_source_service import resolve_placeholders
from utils.logger import get_logger
from utils.validators import sanitize_filename

logger = get_logger(__name__)

REPORTS_AUTO_DIR = BASE_DIR / "uploads" / "reports_auto"
REPORTS_AUTO_DIR.mkdir(parents=True, exist_ok=True)

_SHEET_URL_ID_PATTERN = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")


class SheetSourceError(Exception):
    pass


def extract_spreadsheet_id(url_or_id: str) -> str:
    """Accepts either a full Google Sheets URL or a bare spreadsheet id."""
    url_or_id = (url_or_id or "").strip()
    match = _SHEET_URL_ID_PATTERN.search(url_or_id)
    if match:
        return match.group(1)
    return url_or_id  # assume it's already a bare id


def _get_sheets_service():
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:  # noqa: BLE001
        raise SheetSourceError("google-api-python-client not installed.") from exc

    creds = get_credentials(interactive=False)
    if creds is None:
        raise SheetSourceError(
            "Gmail/Google account not connected. Connect it in Settings first — "
            "the same connection is used to read the Google Sheet."
        )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def fetch_sheet_report(
    db: Session,
    source: ReportSource,
    now: datetime | None = None,
    triggered_by: str = "manual",
) -> dict:
    """
    Read `source.google_sheet_tab` from `source.google_sheet_id`, save it as
    an .xlsx, and register it as a ReportUpload. Mirrors
    report_source_service.fetch_report()'s return shape:
    {"success": bool, "upload": ReportUpload | None, "error": str | None, "run": ReportSourceRun}
    """
    now = now or datetime.now()
    filename = sanitize_filename(resolve_placeholders(source.filename_template or "{name}_{date}.xlsx", now))
    filename = filename.replace("{name}", source.name.replace(" ", "_"))
    if not Path(filename).suffix:
        filename += ".xlsx"

    try:
        if not source.google_sheet_id:
            raise SheetSourceError("No Google Sheet configured for this source.")

        service = _get_sheets_service()
        tab = source.google_sheet_tab or "Sheet1"
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=source.google_sheet_id, range=tab)
            .execute()
        )
        values = result.get("values", [])
        if not values:
            raise SheetSourceError(f"Tab '{tab}' is empty or not found.")

        # Some sheets (e.g. the Calling Sheet) use a two-row merged header:
        # row 1 = group labels spanning several columns (mostly blank cells),
        # row 2 = the actual per-column name. Detect that pattern — row 1
        # filled in less than half as often as row 2 — and use row 2 as the
        # header instead, so this generic fetch never saves a report with
        # blank/garbage column names.
        if len(values) >= 2:
            row1_filled = sum(1 for c in values[0] if str(c).strip())
            row2_filled = sum(1 for c in values[1] if str(c).strip())
            two_row_header = row2_filled > 0 and row1_filled < row2_filled / 2
        else:
            two_row_header = False

        if two_row_header:
            header, *rows = values[1:]
        else:
            header, *rows = values
        # Sheets rows can be ragged (trailing empty cells dropped) — pad every
        # row out to the header width so pandas doesn't misalign columns.
        rows = [row + [""] * (len(header) - len(row)) for row in rows]
        df = pd.DataFrame(rows, columns=header)

        unique_name = f"{uuid.uuid4().hex[:8]}_{filename}"
        dest_path = REPORTS_AUTO_DIR / unique_name
        df.to_excel(dest_path, index=False)

        upload = ReportUpload(
            report_master_id=source.report_master_id,
            file_name=filename,
            stored_path=str(dest_path),
            file_type="xlsx",
            file_size_bytes=dest_path.stat().st_size,
            uploaded_by=source.created_by,
        )
        db.add(upload)
        db.flush()

        run = ReportSourceRun(
            report_source_id=source.id, status=RunStatus.SUCCESS,
            resolved_filename=filename, stored_path=str(dest_path),
            report_upload_id=upload.id, triggered_by=triggered_by,
        )
        db.add(run)
        db.flush()

        logger.info("Sheet source '%s' fetched %d rows from tab '%s'.", source.name, len(df), tab)
        return {"success": True, "upload": upload, "error": None, "run": run}

    except Exception as exc:  # noqa: BLE001
        logger.error("Sheet source fetch failed for '%s': %s", source.name, exc)
        run = ReportSourceRun(
            report_source_id=source.id, status=RunStatus.FAILED,
            resolved_filename=filename, error=str(exc)[:2000], triggered_by=triggered_by,
        )
        db.add(run)
        db.flush()
        return {"success": False, "upload": None, "error": str(exc), "run": run}
