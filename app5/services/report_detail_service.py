"""
CSP-level breakdown behind the "click a metric card" links in automated
report emails. Reads the snapshot captured at send time (EmailLog.
csp_breakdown_json, see report_aggregation_service.build_csp_metric_
breakdown and segmented_distribution_service.apply_segmented_overrides) —
deliberately NOT a live re-query of the calling sheet, so the detail page
always shows exactly what that specific email said, even if the recipient
clicks days later and the underlying sheet has since moved on.
"""
from __future__ import annotations

import json

from database.models import EmailLog

_METRICS = ("PMJDY", "APY", "PMSBY", "PMJJBY", "LL")

_METRIC_LABELS = {
    "PMJDY": "PMJDY (Account Opening)", "APY": "Atal Pension Yojana",
    "PMSBY": "PM Suraksha Bima Yojana", "PMJJBY": "PM Jeevan Jyoti Bima Yojana",
    "LL": "Loan Lead Generation",
}


class ReportDetailError(Exception):
    pass


def get_metric_breakdown(token: str, metric: str) -> dict:
    """
    Current-only (no week-over-week comparison) per-CSP breakdown. Daily
    emails source this from EmailLog.csp_breakdown_json (see
    report_aggregation_service.build_csp_metric_breakdown). Weekly emails
    don't set that column — they only carry "_csp_snapshot" (every CSP,
    for the growth comparison) in context_override_json — so this falls
    back to that, filtered to active (mtd > 0) rows, for the "current
    mode" links used on a first-Monday-of-month send (see email_service.
    Card_Mode): the growth comparison is intentionally not shown that
    week, but the click-through should still work, just without it.
    """
    from database.db import get_db

    metric = metric.upper()
    if metric not in _METRICS:
        raise ReportDetailError(f"Unknown metric '{metric}'.")

    with get_db() as db:
        log_row = db.query(EmailLog).filter(EmailLog.tracking_token == token).first()
        if log_row is None:
            raise ReportDetailError("This link is no longer valid.")
        recipient_type = log_row.recipient_type
        recipient_name = log_row.recipient_name

        if log_row.csp_breakdown_json:
            breakdown = json.loads(log_row.csp_breakdown_json)
            if metric not in breakdown:
                raise ReportDetailError("Detailed breakdown isn't available for this metric.")
            data = breakdown[metric]
            return {
                "metric": metric, "metric_label": data["metric_label"],
                "recipient_type": recipient_type, "recipient_name": recipient_name,
                "target": data["target"], "mtd_achievement": data["mtd_achievement"],
                "ftd_achievement": data["ftd_achievement"], "csp_count": data["csp_count"],
                "csps_with_activity": data["csps_with_activity"], "rows": data["rows"],
            }

        if not log_row.context_override_json:
            raise ReportDetailError("Detailed breakdown isn't available for this email.")
        context = json.loads(log_row.context_override_json)
        snapshot = (context.get("_csp_snapshot") or {}).get(metric)
        if not snapshot:
            raise ReportDetailError("Detailed breakdown isn't available for this metric.")

    rows = [
        {"csp_code": code, "csp_name": v.get("csp_name", ""), "branch_name": v.get("branch_name", ""),
         "mtd": v.get("mtd", 0), "ftd": 0}
        for code, v in snapshot.items() if v.get("mtd", 0) > 0
    ]
    rows.sort(key=lambda r: r["mtd"], reverse=True)
    return {
        "metric": metric, "metric_label": _METRIC_LABELS[metric],
        "recipient_type": recipient_type, "recipient_name": recipient_name,
        "target": 0, "mtd_achievement": sum(v.get("mtd", 0) for v in snapshot.values()),
        "ftd_achievement": 0, "csp_count": len(snapshot), "csps_with_activity": len(rows),
        "rows": rows,
    }
