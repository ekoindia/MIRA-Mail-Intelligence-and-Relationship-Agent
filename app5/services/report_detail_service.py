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

_METRICS = ("PMJDY", "APY", "PMSBY", "PMJJBY")


class ReportDetailError(Exception):
    pass


def get_metric_breakdown(token: str, metric: str) -> dict:
    from database.db import get_db

    metric = metric.upper()
    if metric not in _METRICS:
        raise ReportDetailError(f"Unknown metric '{metric}'.")

    with get_db() as db:
        log_row = db.query(EmailLog).filter(EmailLog.tracking_token == token).first()
        if log_row is None:
            raise ReportDetailError("This link is no longer valid.")
        if not log_row.csp_breakdown_json:
            raise ReportDetailError("Detailed breakdown isn't available for this email.")
        recipient_type = log_row.recipient_type
        recipient_name = log_row.recipient_name
        breakdown = json.loads(log_row.csp_breakdown_json)

    if metric not in breakdown:
        raise ReportDetailError("Detailed breakdown isn't available for this metric.")

    data = breakdown[metric]
    return {
        "metric": metric,
        "metric_label": data["metric_label"],
        "recipient_type": recipient_type,
        "recipient_name": recipient_name,
        "target": data["target"],
        "mtd_achievement": data["mtd_achievement"],
        "ftd_achievement": data["ftd_achievement"],
        "csp_count": data["csp_count"],
        "csps_with_activity": data["csps_with_activity"],
        "rows": data["rows"],
    }
