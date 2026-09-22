"""
Small helpers shared between report_aggregation_service.py and
loan_lead_approval_service.py. Kept in their own module (rather than in
report_aggregation_service.py itself, where they used to live) specifically
to avoid a circular import: report_aggregation_service.AGGREGATORS needs
loan_lead_approval_service's aggregate_new_loan_leads, and
loan_lead_approval_service needs these two parsing helpers — both modules
importing from each other directly would fail depending on which one
happens to be imported first.
"""
from __future__ import annotations

import re

LOAN_TYPE_PAIR_RE = re.compile(r"([A-Za-z][A-Za-z ]*?)-(\d+)")


def distribution_str(d: dict) -> str:
    if not d:
        return "No data available"
    return ", ".join(f"{k}: {v}" for k, v in sorted(d.items(), key=lambda kv: -kv[1]))
