"""
CSP-level metric breakdown behind the clickable cards in automated report
emails. Same no-auth shape as api/routers/tracking.py — fetched by whoever
clicks the link in their mail client, not a logged-in frontend user. The
tracking_token is the only "credential"; it identifies which EmailLog's
recipient to show data for and grants no write access.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from services.report_detail_service import ReportDetailError, get_metric_breakdown

router = APIRouter(prefix="/api/public/report-detail", tags=["report-detail"])


@router.get("/{token}")
def report_detail(token: str, metric: str):
    try:
        return get_metric_breakdown(token, metric)
    except ReportDetailError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
