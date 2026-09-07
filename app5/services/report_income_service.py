"""
CSP-level current-vs-previous-month commission behind the "click the Avg
CSP Income card" link on the Monthly Corporate Center / LHO Update email.

Unlike report_growth_service.py (which needs a snapshot table to compare
two separate weekly runs), both months are already columns on the same
Calling Sheet row — so this reads a single frozen-at-send snapshot
(EmailLog.context_override_json["_income_snapshot"], see
report_aggregation_service.build_csp_income_breakdown and
combined_digest_service.py) rather than looking anything up in history.
"""
from __future__ import annotations

import json

from database.models import EmailLog


class ReportIncomeError(Exception):
    pass


def get_income_breakdown(token: str) -> dict:
    from database.db import get_db

    with get_db() as db:
        log_row = db.query(EmailLog).filter(EmailLog.tracking_token == token).first()
        if log_row is None:
            raise ReportIncomeError("This link is no longer valid.")
        if not log_row.context_override_json:
            raise ReportIncomeError("Income breakdown isn't available for this email.")

        context = json.loads(log_row.context_override_json)
        snapshot = context.get("_income_snapshot")
        if not snapshot:
            raise ReportIncomeError("Income breakdown isn't available for this email.")

        recipient_type = log_row.recipient_type
        recipient_name = log_row.recipient_name

    return {
        "recipient_type": recipient_type,
        "recipient_name": recipient_name,
        "csp_count": snapshot["csp_count"],
        "total_curr": snapshot["total_curr"],
        "total_prev": snapshot["total_prev"],
        "rows": snapshot["rows"],
    }
