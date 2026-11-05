from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.auth import get_current_user
from database.db import get_db
from database.suggestion_models import Suggestion
from services.suggestion_service import FEED_START_UTC, approve_suggestion, dismiss_suggestion, run_suggestion_scan

router = APIRouter(prefix="/api/suggestions", tags=["suggestions"])


def _utc_iso(dt) -> str | None:
    # Stored as naive UTC; without the "Z" the browser reads it as local time
    # and shows IST users a timestamp 5h30m early.
    return dt.isoformat() + "Z" if dt else None


def _to_dict(s: Suggestion) -> dict:
    return {
        "id": s.id, "category": s.category, "title": s.title, "description": s.description,
        "severity": s.severity, "entityType": s.entity_type, "entityId": s.entity_id,
        "canAutoFix": s.can_auto_fix, "status": s.status,
        "detectedAt": _utc_iso(s.detected_at),
        "resolvedAt": _utc_iso(s.resolved_at),
        "resolvedByUsername": s.resolved_by_username, "resultDetail": s.result_detail,
    }


@router.get("")
def list_suggestions(category_prefix: str | None = None, user: dict = Depends(get_current_user)):
    with get_db() as db:
        q = db.query(Suggestion).filter(Suggestion.status != "dismissed", Suggestion.detected_at >= FEED_START_UTC)
        if category_prefix:
            q = q.filter(Suggestion.category.like(f"{category_prefix}%"))
        rows = q.order_by(Suggestion.detected_at.desc()).all()
        return [_to_dict(s) for s in rows]


@router.post("/scan")
def scan_now(user: dict = Depends(get_current_user)):
    with get_db() as db:
        open_count = run_suggestion_scan(db)
        return {"openCount": open_count}


@router.post("/{suggestion_id}/approve")
def approve(suggestion_id: int, user: dict = Depends(get_current_user)):
    with get_db() as db:
        try:
            suggestion = approve_suggestion(db, suggestion_id, user)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _to_dict(suggestion)


@router.post("/{suggestion_id}/dismiss")
def dismiss(suggestion_id: int, user: dict = Depends(get_current_user)):
    with get_db() as db:
        try:
            suggestion = dismiss_suggestion(db, suggestion_id, user)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return _to_dict(suggestion)
