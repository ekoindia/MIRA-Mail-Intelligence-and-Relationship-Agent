"""
External REST report-source connector.

Downloads a report file from a configurable REST API on demand (manual
"Test Fetch" or from the auto-distribution scheduler), saves it to disk,
and records it as a normal `ReportUpload` row so the rest of the
distribution pipeline (recipient resolution, templates, batch send) can
be reused unmodified.

Failures are captured into a `ReportSourceRun` row rather than raised, so
a bad/unreachable endpoint never crashes the scheduler poller.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import requests
from requests.auth import HTTPBasicAuth
from sqlalchemy.orm import Session

from config import BASE_DIR, settings
from database.models import ReportUpload
from database.report_source_models import AuthType, HttpMethod, ReportSource, ReportSourceRun, RunStatus
from services.audit_service import log_action
from utils.logger import get_logger
from utils.validators import sanitize_filename, validate_report_file

logger = get_logger(__name__)

REPORTS_AUTO_DIR = BASE_DIR / "uploads" / "reports_auto"
REPORTS_AUTO_DIR.mkdir(parents=True, exist_ok=True)

_PLACEHOLDER_PATTERN = re.compile(r"\{(date|yesterday|week|month)(?::([^}]+))?\}")

_JSON_URL_KEYS = ("download_url", "file_url", "url")

_FILE_EXTENSION_BY_CONTENT_TYPE = {
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "application/pdf": ".pdf",
    "text/csv": ".csv",
}


class ReportSourceError(Exception):
    pass


def resolve_placeholders(template: str, now: datetime | None = None) -> str:
    """
    Substitute {date}, {yesterday}, {week}, {month} into an endpoint/filename
    template. Each supports an optional strftime spec, e.g. {date:%d-%m-%Y}.
    Bare placeholders (no spec) default to %d-%m-%Y.
    """
    now = now or datetime.now()
    values = {
        "date": now,
        "yesterday": now - timedelta(days=1),
        "week": now - timedelta(days=now.weekday()),  # Monday of the current week
        "month": now.replace(day=1),
    }

    def _sub(match: re.Match) -> str:
        key, spec = match.group(1), match.group(2)
        return values[key].strftime(spec or "%d-%m-%Y")

    return _PLACEHOLDER_PATTERN.sub(_sub, template or "")


def _build_url(source: ReportSource, now: datetime) -> str:
    path = resolve_placeholders(source.endpoint_path_template or "", now)
    if path.lower().startswith("http"):
        return path
    base = source.base_url.rstrip("/")
    return f"{base}/{path.lstrip('/')}" if path else base


def _build_auth_kwargs(source: ReportSource) -> dict:
    kwargs: dict = {}
    if source.auth_type == AuthType.API_KEY:
        header = source.auth_header_name or "X-API-Key"
        kwargs["headers"] = {header: source.auth_secret or ""}
    elif source.auth_type == AuthType.BEARER:
        kwargs["headers"] = {"Authorization": f"Bearer {source.auth_secret or ''}"}
    elif source.auth_type == AuthType.BASIC:
        kwargs["auth"] = HTTPBasicAuth(source.auth_header_name or "", source.auth_secret or "")
    return kwargs


def _guess_extension(content_type: str, filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext:
        return ext
    return _FILE_EXTENSION_BY_CONTENT_TYPE.get(content_type.split(";")[0].strip().lower(), ".bin")


def fetch_report(
    db: Session,
    source: ReportSource,
    now: datetime | None = None,
    triggered_by: str = "manual",
) -> dict:
    """
    Fetch the report file for `source`. Returns
    {"success": bool, "upload": ReportUpload | None, "error": str | None, "run": ReportSourceRun}
    """
    now = now or datetime.now()
    url = _build_url(source, now)
    filename = sanitize_filename(resolve_placeholders(source.filename_template, now))
    auth_kwargs = _build_auth_kwargs(source)

    try:
        method = requests.post if source.http_method == HttpMethod.POST else requests.get
        resp = method(url, timeout=60, **auth_kwargs)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        file_bytes = resp.content

        if "application/json" in content_type.lower():
            payload = resp.json()
            download_url = next((payload[k] for k in _JSON_URL_KEYS if payload.get(k)), None)
            if not download_url:
                raise ReportSourceError(
                    f"Response was JSON but contained none of {_JSON_URL_KEYS}."
                )
            file_resp = requests.get(download_url, timeout=60, **auth_kwargs)
            file_resp.raise_for_status()
            content_type = file_resp.headers.get("Content-Type", "")
            file_bytes = file_resp.content

        if not file_bytes:
            raise ReportSourceError("Downloaded file was empty.")

        ext = _guess_extension(content_type, filename)
        if not Path(filename).suffix:
            filename = f"{filename}{ext}"

        ok, msg = validate_report_file(filename, len(file_bytes))
        if not ok:
            raise ReportSourceError(msg)

        unique_name = f"{uuid.uuid4().hex[:8]}_{filename}"
        dest_path = REPORTS_AUTO_DIR / unique_name
        dest_path.write_bytes(file_bytes)

        upload = ReportUpload(
            report_master_id=source.report_master_id,
            file_name=filename,
            stored_path=str(dest_path),
            file_type=Path(filename).suffix.lower().lstrip("."),
            file_size_bytes=len(file_bytes),
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

        log_action(
            db, "REPORT_SOURCE_FETCH", entity_type="ReportSource", entity_id=source.id,
            details=f"source={source.name}, file={filename}, {len(file_bytes)} bytes, via={triggered_by}",
        )
        return {"success": True, "upload": upload, "error": None, "run": run}

    except Exception as exc:  # noqa: BLE001
        logger.error("Report source fetch failed for '%s': %s", source.name, exc)
        run = ReportSourceRun(
            report_source_id=source.id, status=RunStatus.FAILED,
            resolved_filename=filename, error=str(exc)[:2000], triggered_by=triggered_by,
        )
        db.add(run)
        db.flush()
        return {"success": False, "upload": None, "error": str(exc), "run": run}


def fetch_any_report(
    db: Session, source: ReportSource, now: datetime | None = None, triggered_by: str = "manual",
) -> dict:
    """Dispatch to the right fetcher based on source.source_type (REST API vs Google Sheet)."""
    from database.report_source_models import SourceType

    if source.source_type == SourceType.GOOGLE_SHEET:
        from services.sheet_source_service import fetch_sheet_report
        return fetch_sheet_report(db, source, now=now, triggered_by=triggered_by)
    return fetch_report(db, source, now=now, triggered_by=triggered_by)


def list_recent_runs(db: Session, report_source_id: int | None = None, limit: int = 20) -> list[ReportSourceRun]:
    q = db.query(ReportSourceRun)
    if report_source_id:
        q = q.filter(ReportSourceRun.report_source_id == report_source_id)
    return q.order_by(ReportSourceRun.run_at.desc()).limit(limit).all()
