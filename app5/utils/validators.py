"""Validation helpers for file uploads and master data."""
from __future__ import annotations

import re
from pathlib import Path

from config import settings

EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


def is_valid_email(email: str) -> bool:
    return bool(email) and bool(EMAIL_REGEX.match(email.strip()))


def validate_report_file(filename: str, size_bytes: int) -> tuple[bool, str]:
    """Validate an uploaded report file's extension and size."""
    ext = Path(filename).suffix.lower()
    if ext not in settings.allowed_report_extensions:
        allowed = ", ".join(settings.allowed_report_extensions)
        return False, f"'{ext}' is not allowed. Allowed types: {allowed}"

    max_bytes = settings.max_upload_size_mb * 1024 * 1024
    if size_bytes > max_bytes:
        return False, f"File exceeds the {settings.max_upload_size_mb} MB limit."

    if size_bytes == 0:
        return False, "File is empty."

    return True, ""


def validate_master_excel_columns(columns: list[str], required: list[str]) -> tuple[bool, str]:
    """Validate that an uploaded master Excel file has all required columns."""
    normalized = {c.strip().lower() for c in columns}
    missing = [r for r in required if r.lower() not in normalized]
    if missing:
        return False, f"Missing required column(s): {', '.join(missing)}"
    return True, ""


def sanitize_filename(filename: str) -> str:
    """Strip unsafe characters from a filename before storing on disk."""
    name = Path(filename).name
    name = re.sub(r"[^A-Za-z0-9._\-]", "_", name)
    return name
