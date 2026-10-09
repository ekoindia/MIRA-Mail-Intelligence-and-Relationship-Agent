"""
CSP-level metric breakdown behind the clickable cards in automated report
emails. Same no-auth shape as api/routers/tracking.py — fetched by whoever
clicks the link in their mail client, not a logged-in frontend user. The
tracking_token is the only "credential"; it identifies which EmailLog's
recipient to show data for and grants no write access.

A click only counts as the recipient's engagement when the viewer is not
logged into MIRA: the same email (and token) also sits in the CC'd team
inbox and the sender's Sent folder, so a staff click would otherwise be
recorded as the official's.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from api.auth import is_internal_viewer
from services.report_detail_service import ReportDetailError, get_metric_breakdown, mark_detail_opened
from services.report_growth_service import ReportGrowthError, get_metric_growth
from services.report_inactive_service import ReportInactiveError, get_inactive_breakdown
from services.report_income_service import ReportIncomeError, get_income_breakdown
from services.report_new_lead_service import ReportNewLeadError, get_new_lead_breakdown

router = APIRouter(prefix="/api/public/report-detail", tags=["report-detail"])


def _record_click(token: str, internal: bool) -> None:
    if not internal:
        mark_detail_opened(token)


@router.get("/{token}")
def report_detail(token: str, metric: str, internal: bool = Depends(is_internal_viewer)):
    try:
        result = get_metric_breakdown(token, metric)
    except ReportDetailError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _record_click(token, internal)
    return result


@router.get("/{token}/growth")
def report_growth(token: str, metric: str, internal: bool = Depends(is_internal_viewer)):
    try:
        result = get_metric_growth(token, metric)
    except ReportGrowthError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _record_click(token, internal)
    return result


@router.get("/{token}/income")
def report_income(token: str, internal: bool = Depends(is_internal_viewer)):
    try:
        result = get_income_breakdown(token)
    except ReportIncomeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _record_click(token, internal)
    return result


@router.get("/{token}/inactive")
def report_inactive(token: str, internal: bool = Depends(is_internal_viewer)):
    try:
        result = get_inactive_breakdown(token)
    except ReportInactiveError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _record_click(token, internal)
    return result


@router.get("/{token}/new-leads")
def report_new_leads(token: str, internal: bool = Depends(is_internal_viewer)):
    try:
        result = get_new_lead_breakdown(token)
    except ReportNewLeadError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    _record_click(token, internal)
    return result
