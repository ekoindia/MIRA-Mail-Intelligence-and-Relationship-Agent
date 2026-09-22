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


def parse_type_counts(cell: str, *, default_count: int = 1) -> dict[str, int]:
    """
    Parse one loan-type cell into {type_name: count}.

    Primary shape: comma-separated "Type-Count" pairs (e.g. "Agri Loan-1,
    Personal Loan-5") — a CSP that generated leads across more than one
    type this month. Falls back to treating the WHOLE cell as one type
    with `default_count` when no such pair matches but the cell isn't
    blank — the live Calling Sheet's loan-type column was restructured
    2026-09-22 from that comma/dash format to a single category code with
    no count attached (e.g. "OTHER_P_SEG_LOAN_LEAD"); only one example of
    this new shape has been observed so far, so this fallback is a
    best-effort reading (best real data beats a confident-looking "No
    data available"), not a confirmed spec — revisit once more real
    values are seen or the business side confirms the intended format.
    """
    if not isinstance(cell, str) or not cell.strip():
        return {}
    pairs = LOAN_TYPE_PAIR_RE.findall(cell)
    if pairs:
        counts: dict[str, int] = {}
        for name, count in pairs:
            name = name.strip()
            if name:
                counts[name] = counts.get(name, 0) + int(count)
        return counts
    return {cell.strip(): default_count}
