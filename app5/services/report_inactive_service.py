"""
Full per-CSP inactive list + circle distribution behind the "click the
Inactive CSPs / Circle Spread card" link on the Weekly LHO / Corporate
Center Update email. Point-in-time snapshot (EmailLog.
context_override_json["_inactive_snapshot"], see report_aggregation_
service.build_inactive_csp_breakdown and combined_digest_service.py) —
inactive count is a state, not a flow, so unlike report_growth_service.py
there's nothing to compare against a prior week here, only what this
email itself said.
"""
from __future__ import annotations

import json

from database.models import EmailLog


class ReportInactiveError(Exception):
    pass


def get_inactive_breakdown(token: str) -> dict:
    from database.db import get_db

    with get_db() as db:
        log_row = db.query(EmailLog).filter(EmailLog.tracking_token == token).first()
        if log_row is None:
            raise ReportInactiveError("This link is no longer valid.")
        if not log_row.context_override_json:
            raise ReportInactiveError("Inactive CSP breakdown isn't available for this email.")

        context = json.loads(log_row.context_override_json)
        snapshot = context.get("_inactive_snapshot")
        if not snapshot:
            raise ReportInactiveError("Inactive CSP breakdown isn't available for this email.")

        recipient_type = log_row.recipient_type
        recipient_name = log_row.recipient_name

    return {
        "recipient_type": recipient_type,
        "recipient_name": recipient_name,
        "total_csp_count": snapshot["total_csp_count"],
        "inactive_count": snapshot["inactive_count"],
        "circle_distribution": snapshot["circle_distribution"],
        "rows": snapshot["rows"],
    }
