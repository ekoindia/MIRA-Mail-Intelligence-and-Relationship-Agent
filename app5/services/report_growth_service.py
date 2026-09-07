"""
Per-CSP week-over-week growth behind the "click a metric card" links in
the WEEKLY combined digest emails (Weekly RBO/LHO/Corporate Center
Update). Distinct from services/report_detail_service.py (the DAILY
report's per-CSP current-value breakdown) — this compares two
consecutive weeks' snapshots for the same CSP, not just one week's
numbers.

Data flow: combined_digest_service.py stashes "_csp_snapshot" (see
report_aggregation_service.build_csp_metric_snapshot) into each weekly
recipient's context, which is stored verbatim as EmailLog.
context_override_json (the CURRENT week) and, via
snapshot_service.save_drafted_report_snapshot, copied into
WeeklyReportSnapshot.context_json for that recipient (becomes a PAST
week once a later run supersedes it). This service reads the current
week straight off the EmailLog the token identifies, and the most
recent prior week from WeeklyReportSnapshot.
"""
from __future__ import annotations

import json

from database.models import EmailLog
from database.snapshot_models import WeeklyReportSnapshot

_METRICS = ("PMJDY", "APY", "PMSBY", "PMJJBY", "LL")


class ReportGrowthError(Exception):
    pass


def get_metric_growth(token: str, metric: str) -> dict:
    from database.db import get_db

    metric = metric.upper()
    if metric not in _METRICS:
        raise ReportGrowthError(f"Unknown metric '{metric}'.")

    with get_db() as db:
        log_row = db.query(EmailLog).filter(EmailLog.tracking_token == token).first()
        if log_row is None:
            raise ReportGrowthError("This link is no longer valid.")
        if not log_row.context_override_json:
            raise ReportGrowthError("Growth detail isn't available for this email.")

        current_context = json.loads(log_row.context_override_json)
        current_snapshot = (current_context.get("_csp_snapshot") or {}).get(metric)
        if not current_snapshot:
            raise ReportGrowthError("Growth detail isn't available for this metric.")

        recipient_email = (log_row.recipient_email or "").lower()
        prior_row = (
            db.query(WeeklyReportSnapshot)
            .filter(
                WeeklyReportSnapshot.recipient_email.ilike(recipient_email),
            )
            .order_by(WeeklyReportSnapshot.report_date.desc(), WeeklyReportSnapshot.id.desc())
            .first()
        )
        prior_snapshot = {}
        prior_date = None
        if prior_row is not None:
            prior_date = prior_row.report_date.isoformat()
            try:
                prior_context = json.loads(prior_row.context_json)
                prior_snapshot = (prior_context.get("_csp_snapshot") or {}).get(metric) or {}
            except (TypeError, ValueError):
                prior_snapshot = {}

        recipient_type = log_row.recipient_type
        recipient_name = log_row.recipient_name

    codes = set(current_snapshot) | set(prior_snapshot)
    rows = []
    for code in codes:
        cur = current_snapshot.get(code)
        prev = prior_snapshot.get(code)
        current_mtd = cur["mtd"] if cur else 0
        previous_mtd = prev["mtd"] if prev else 0
        rows.append({
            "csp_code": code,
            "csp_name": (cur or prev or {}).get("csp_name", ""),
            "branch_name": (cur or prev or {}).get("branch_name", ""),
            "current_mtd": current_mtd,
            "previous_mtd": previous_mtd,
            "delta": current_mtd - previous_mtd,
        })
    rows.sort(key=lambda r: r["delta"], reverse=True)

    return {
        "metric": metric,
        "recipient_type": recipient_type,
        "recipient_name": recipient_name,
        "has_prior_week": bool(prior_snapshot),
        "prior_week_date": prior_date,
        "total_current": sum(r["current_mtd"] for r in rows),
        "total_previous": sum(r["previous_mtd"] for r in rows),
        "rows": rows,
    }
