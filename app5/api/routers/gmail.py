from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse

from api.auth import get_current_user
from config import settings
from services.gmail_auth import (
    build_authorization_url,
    complete_authorization,
    connect_gmail,
    disconnect_gmail,
    get_connection_status,
    uses_redirect_flow,
)

router = APIRouter(prefix="/api/gmail", tags=["gmail"])


@router.get("/status")
def status(user: dict = Depends(get_current_user)):
    s = get_connection_status()
    return {
        "connected": s["connected"],
        "email": s["email"],
        "error": s["error"],
        # Tells the Settings page which "Connect Gmail" flow to run — a
        # redirect to Google (server deployments with a Web OAuth client
        # and PUBLIC_BASE_URL set) or the old local-browser-popup flow
        # (local dev, Desktop OAuth client). See uses_redirect_flow().
        "flow_mode": "redirect" if uses_redirect_flow() else "local_server",
    }


@router.post("/connect")
def connect(user: dict = Depends(get_current_user)):
    """
    Local-dev flow only: opens a browser window on THIS machine (the
    backend and the user are assumed to be on the same desktop). Blocks
    until the user finishes the consent screen or it times out. Server
    deployments use GET /connect-url + GET /oauth-callback instead — see
    uses_redirect_flow() in services/gmail_auth.py for why.
    """
    try:
        result = connect_gmail()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    if not result["connected"]:
        raise HTTPException(status_code=400, detail=result.get("error") or "Connection failed.")
    return {"connected": True, "email": result["email"]}


@router.get("/connect-url")
def connect_url(user: dict = Depends(get_current_user)):
    """Server-deployment flow: returns the Google consent URL for the
    frontend to navigate the browser to directly (no popup, no localhost)."""
    if not uses_redirect_flow():
        raise HTTPException(
            status_code=400,
            detail="This deployment isn't configured for the redirect flow — use Connect Gmail's local-browser flow instead.",
        )
    try:
        return {"url": build_authorization_url()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/oauth-callback")
def oauth_callback(code: str = "", state: str = "", error: str = ""):
    """
    Google redirects the user's browser here after they approve (or
    decline) access. No auth dependency: Google's redirect can't carry our
    bearer token, so `state` (single-use, minted in build_authorization_url)
    is what proves this callback belongs to a connection attempt we started.
    """
    settings_url = f"{settings.public_base_url}/settings"
    if error:
        return RedirectResponse(f"{settings_url}?gmail=error&detail={quote(error)}")
    try:
        complete_authorization(code, state)
    except Exception as exc:  # noqa: BLE001
        return RedirectResponse(f"{settings_url}?gmail=error&detail={quote(str(exc))}")
    return RedirectResponse(f"{settings_url}?gmail=connected")


@router.post("/disconnect")
def disconnect(user: dict = Depends(get_current_user)):
    disconnect_gmail()
    return {"connected": False}
