"""
Gmail OAuth connection management.

Single shared Gmail account for all report sending.
The OAuth flow opens a browser tab on the machine running Streamlit
(local dev = same machine as the user; server deploy = use the
redirect-URI flow described in the advanced section below).

Public API used by the rest of the app
---------------------------------------
get_connection_status()   → dict  (safe to call on every page load)
connect_gmail()           → dict  (runs interactive browser flow)
disconnect_gmail()        → None
get_gmail_client()        → googleapiclient Resource | None
get_connected_email()     → str | None
credentials_file_exists() → bool
save_credentials_from_upload(bytes) → None   (for in-app upload)
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from config import BASE_DIR
from utils.logger import get_logger

logger = get_logger(__name__)

GMAIL_CREDENTIALS_PATH = os.getenv(
    "GMAIL_CREDENTIALS_PATH", str(BASE_DIR / "credentials.json")
)
GMAIL_TOKEN_PATH = os.getenv(
    "GMAIL_TOKEN_PATH", str(BASE_DIR / "data" / "gmail_token.json")
)

# Send + read + compose (Gmail) + read-only Sheets access (for the daily
# "calling sheet" report source). Scope changes require reconnecting —
# an existing cached token won't carry the new scope, so
# get_gmail_client() will surface an insufficient-scope error until the
# user disconnects and reconnects from Settings.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]


# ── Credential helpers ────────────────────────────────────────────────────

def credentials_file_exists() -> bool:
    return Path(GMAIL_CREDENTIALS_PATH).exists()


def save_credentials_from_upload(data: bytes) -> None:
    """
    Save raw bytes from a Streamlit file_uploader as credentials.json.
    Validates that it looks like a valid OAuth client file first.
    """
    try:
        parsed = json.loads(data)
        if "installed" not in parsed and "web" not in parsed:
            raise ValueError("Not a valid OAuth client JSON file.")
    except Exception as exc:
        raise ValueError(f"Invalid credentials file: {exc}") from exc

    path = Path(GMAIL_CREDENTIALS_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    logger.info("credentials.json saved to %s", path)


def get_credentials_info() -> dict:
    """Return metadata from credentials.json without exposing secrets."""
    path = Path(GMAIL_CREDENTIALS_PATH)
    if not path.exists():
        return {"exists": False}
    try:
        raw = json.loads(path.read_text())
        inner = raw.get("installed") or raw.get("web") or {}
        return {
            "exists": True,
            "project_id": inner.get("project_id", "unknown"),
            "client_id_suffix": inner.get("client_id", "")[-12:],
            "file_path": str(path),
            "modified": datetime.fromtimestamp(path.stat().st_mtime).strftime(
                "%d %b %Y %H:%M"
            ),
        }
    except Exception:
        return {"exists": True, "project_id": "unreadable"}


# ── Token / client ────────────────────────────────────────────────────────

def _load_creds():
    """Load cached token from disk; return None if absent or unreadable."""
    try:
        from google.oauth2.credentials import Credentials

        path = Path(GMAIL_TOKEN_PATH)
        if path.exists():
            return Credentials.from_authorized_user_file(str(path), SCOPES)
    except Exception as exc:
        logger.warning("Could not load cached token: %s", exc)
    return None


def get_credentials(interactive: bool = True):
    """
    Return valid OAuth Credentials for the connected Google account (shared
    across every Google API the app uses — Gmail, Sheets, ...).

    interactive=True  → runs browser OAuth flow if no valid token exists.
    interactive=False → returns None instead of prompting (safe for status checks).
    """
    try:
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError(
            "Gmail libraries not installed. Run: pip install -r requirements.txt"
        ) from exc

    token_path = Path(GMAIL_TOKEN_PATH)
    token_path.parent.mkdir(parents=True, exist_ok=True)

    creds = _load_creds()

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                token_path.write_text(creds.to_json())
                logger.info("Gmail token refreshed successfully.")
            except Exception as exc:
                logger.warning("Token refresh failed: %s", exc)
                creds = None

        if not creds:
            if not interactive:
                return None
            if not credentials_file_exists():
                raise RuntimeError(
                    f"credentials.json not found at {GMAIL_CREDENTIALS_PATH}. "
                    "Upload it in Settings → Gmail Connection first."
                )
            # Run the browser OAuth flow.
            flow = InstalledAppFlow.from_client_secrets_file(
                GMAIL_CREDENTIALS_PATH, SCOPES
            )
            creds = flow.run_local_server(port=0, open_browser=True)
            token_path.write_text(creds.to_json())
            logger.info("New Gmail token saved to %s", token_path)

    return creds


def get_gmail_client(interactive: bool = True):
    """Return an authorised Gmail API client, or None if not connected (interactive=False)."""
    from googleapiclient.discovery import build

    creds = get_credentials(interactive=interactive)
    if creds is None:
        return None
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def get_connected_email() -> str | None:
    """Return the connected Gmail address, or None if not connected."""
    try:
        svc = get_gmail_client(interactive=False)
        if svc is None:
            return None
        profile = svc.users().getProfile(userId="me").execute()
        return profile.get("emailAddress")
    except Exception:
        return None


# ── Public status / connect / disconnect ─────────────────────────────────

def get_connection_status() -> dict:
    """
    Check connection without triggering an interactive flow.
    Safe to call on every page load.

    Returns
    -------
    {
        connected: bool,
        email: str | None,
        error: str | None,
        token_exists: bool,
        creds_exist: bool,
    }
    """
    creds_exist = credentials_file_exists()
    token_exists = Path(GMAIL_TOKEN_PATH).exists()

    if not token_exists:
        return {
            "connected": False,
            "email": None,
            "error": None,
            "token_exists": False,
            "creds_exist": creds_exist,
        }

    try:
        svc = get_gmail_client(interactive=False)
        if svc is None:
            return {
                "connected": False,
                "email": None,
                "error": "Token expired and could not be refreshed automatically.",
                "token_exists": token_exists,
                "creds_exist": creds_exist,
            }
        profile = svc.users().getProfile(userId="me").execute()
        return {
            "connected": True,
            "email": profile.get("emailAddress"),
            "error": None,
            "token_exists": True,
            "creds_exist": creds_exist,
        }
    except Exception as exc:
        logger.warning("Gmail connection check failed: %s", exc)
        return {
            "connected": False,
            "email": None,
            "error": str(exc),
            "token_exists": token_exists,
            "creds_exist": creds_exist,
        }


def connect_gmail() -> dict:
    """
    Run the interactive OAuth browser flow and return the resulting status.
    Call this only when the user explicitly clicks 'Connect Gmail'.
    """
    get_gmail_client(interactive=True)
    return get_connection_status()


def disconnect_gmail() -> None:
    """Remove the cached token. Does not revoke the Google OAuth grant."""
    path = Path(GMAIL_TOKEN_PATH)
    if path.exists():
        path.unlink()
        logger.info("Gmail token removed (disconnected).")
