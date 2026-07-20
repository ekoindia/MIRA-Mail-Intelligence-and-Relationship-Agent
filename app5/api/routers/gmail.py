from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.auth import get_current_user
from services.gmail_auth import connect_gmail, disconnect_gmail, get_connection_status

router = APIRouter(prefix="/api/gmail", tags=["gmail"])


@router.get("/status")
def status(user: dict = Depends(get_current_user)):
    s = get_connection_status()
    return {"connected": s["connected"], "email": s["email"], "error": s["error"]}


@router.post("/connect")
def connect(user: dict = Depends(get_current_user)):
    """
    Runs the interactive OAuth flow — opens a browser window on THIS
    machine (the backend and the user are assumed to be on the same
    desktop, same as the previous Streamlit app). Blocks until the user
    finishes the consent screen or it times out.
    """
    try:
        result = connect_gmail()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(exc))
    if not result["connected"]:
        raise HTTPException(status_code=400, detail=result.get("error") or "Connection failed.")
    return {"connected": True, "email": result["email"]}


@router.post("/disconnect")
def disconnect(user: dict = Depends(get_current_user)):
    disconnect_gmail()
    return {"connected": False}
