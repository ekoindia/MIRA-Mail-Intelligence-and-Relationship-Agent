"""
CSP-level breakdown behind the "New Loan Leads Generated" / "Lead Types"
cards on the Loan Lead Approval (Daily) email. Same shape as
report_income_service.py: reads a single frozen-at-send snapshot
(EmailLog.context_override_json["_new_lead_snapshot"], see
services.loan_lead_approval_service.aggregate_new_loan_leads) rather than
recomputing anything — the numbers shown must always match exactly what
that specific email said, not today's live sheet.
"""
from __future__ import annotations

import json

from database.models import EmailLog


class ReportNewLeadError(Exception):
    pass


def get_new_lead_breakdown(token: str) -> dict:
    from database.db import get_db

    with get_db() as db:
        log_row = db.query(EmailLog).filter(EmailLog.tracking_token == token).first()
        if log_row is None:
            raise ReportNewLeadError("This link is no longer valid.")
        if not log_row.context_override_json:
            raise ReportNewLeadError("New leads breakdown isn't available for this email.")

        context = json.loads(log_row.context_override_json)
        snapshot = context.get("_new_lead_snapshot")
        if not snapshot:
            raise ReportNewLeadError("New leads breakdown isn't available for this email.")

        recipient_type = log_row.recipient_type
        recipient_name = log_row.recipient_name

    return {
        "recipient_type": recipient_type,
        "recipient_name": recipient_name,
        "total_new_leads": snapshot["total_new_leads"],
        "csp_count": snapshot["csp_count"],
        "rows": snapshot["rows"],
    }
