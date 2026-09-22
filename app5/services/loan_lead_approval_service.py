"""
"Loan Lead Approval (Daily)" — a Branch-level email asking the branch to
approve whichever loan leads were newly generated since the last time this
ran, distinct from the existing "Loan Lead Generation" report (which shows
a plain month-to-date total, no day-over-day delta, no approval framing).

The Calling Sheet only ever exposes a cumulative MTD count and a cumulative
MTD type breakdown per CSP — there is no "generated on this exact day"
column. "New leads" is therefore always a delta against a stored snapshot
of yesterday's (or the last available day's) MTD figures, computed here,
not read directly off the sheet. See DailyLoanLeadSnapshot
(database/snapshot_models.py) for the stored baseline and
save_daily_snapshot below for how it's refreshed.

Month rollover: on the first day a new month's MTD figures appear, the
stored "previous" snapshot is still last month's (much larger) cumulative
total, so a naive subtraction would go negative. When the previous
snapshot's month doesn't match today's, today's raw count is treated as
entirely new instead of subtracted against — MTD only just reset, so
everything in it this month-to-date genuinely is new since the reset.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from services.report_aggregation_helpers import LOAN_TYPE_PAIR_RE as _LOAN_TYPE_PAIR_RE
from services.report_aggregation_helpers import distribution_str as _distribution_str

# report_name key this whole module is wired under — see AGGREGATORS in
# report_aggregation_service.py, the snapshot hook in
# segmented_distribution_service.py, and the zero-new-leads skip in
# report_send_service.py. Kept as one constant so all three stay in sync.
REPORT_NAME = "Loan Lead Approval (Daily)"


def _parse_type_counts_for_row(loan_type_detail) -> dict[str, int]:
    """Same 'Type-Count, Type-Count' parsing as report_aggregation_service.
    _parse_loan_type_counts, but for one CSP's own cell instead of summed
    across a whole dataframe — needed here because the day-over-day delta
    has to be computed per CSP, per type, before being summed back up."""
    if not isinstance(loan_type_detail, str) or not loan_type_detail:
        return {}
    counts: dict[str, int] = {}
    for name, count in _LOAN_TYPE_PAIR_RE.findall(loan_type_detail):
        name = name.strip()
        if not name:
            continue
        counts[name] = counts.get(name, 0) + int(count)
    return counts


def save_daily_snapshot(db, df: pd.DataFrame, snapshot_date: date) -> int:
    """
    Upsert one DailyLoanLeadSnapshot row per CSP in `df` (pass the FULL,
    unfiltered Calling Sheet — this is a global daily capture, not scoped
    to any one recipient). Safe to call more than once for the same date
    (e.g. a retried send): each call overwrites that day's row with the
    latest figures rather than duplicating it.

    Takes the CALLER'S db session rather than opening its own. This is
    always called from inside report_send_service.send_report_now's
    already-open, uncommitted transaction (see CLAUDE.md: a full batch
    send is one long-held SQLite transaction for the whole job) — a
    second writer connection against the same SQLite file while that's
    open deadlocks with "database is locked" (confirmed: this used to
    open its own get_db() per row and did exactly that). Joining the
    caller's transaction instead means these rows become durable at the
    same moment the rest of the job's writes do, via db.flush() here and
    the outer caller's eventual commit — not the usual per-row-commit
    pattern this codebase otherwise uses for bulk loops, but that pattern
    is for loops that own their own transaction, which this one never
    does.
    """
    from database.snapshot_models import DailyLoanLeadSnapshot

    saved = 0
    for _, row in df.iterrows():
        code = str(row["csp_code"]) if pd.notna(row["csp_code"]) else ""
        if not code:
            continue
        mtd_count = int(row["loan_lead_count_curr"]) if pd.notna(row["loan_lead_count_curr"]) else 0
        type_counts = _parse_type_counts_for_row(row.get("loan_type_detail"))

        existing = (
            db.query(DailyLoanLeadSnapshot)
            .filter(
                DailyLoanLeadSnapshot.snapshot_date == snapshot_date,
                DailyLoanLeadSnapshot.csp_code == code,
            )
            .first()
        )
        if existing is None:
            existing = DailyLoanLeadSnapshot(snapshot_date=snapshot_date, csp_code=code)
            db.add(existing)
        existing.csp_name = str(row["csp_name"]) if pd.notna(row["csp_name"]) else ""
        existing.branch_name = str(row["branch_name"]) if pd.notna(row["branch_name"]) else ""
        existing.branch_code = str(row["branch_code"]) if pd.notna(row["branch_code"]) else ""
        existing.mtd_count = mtd_count
        existing.type_counts_json = _json_dumps(type_counts)
        saved += 1

    db.flush()
    return saved


def _json_dumps(obj) -> str:
    import json

    return json.dumps(obj)


def _json_loads(text) -> dict:
    import json

    if not text:
        return {}
    return json.loads(text)


def _load_previous_snapshots(csp_codes: list[str], before_date: date) -> dict[str, dict]:
    """Most recent DailyLoanLeadSnapshot row strictly before `before_date`,
    per CSP code — not necessarily yesterday exactly (a skipped day
    shouldn't erase the comparison, it just compares against the last day
    that WAS captured)."""
    from database.db import get_db
    from database.snapshot_models import DailyLoanLeadSnapshot

    if not csp_codes:
        return {}

    with get_db() as db:
        rows = (
            db.query(DailyLoanLeadSnapshot)
            .filter(
                DailyLoanLeadSnapshot.csp_code.in_(csp_codes),
                DailyLoanLeadSnapshot.snapshot_date < before_date,
            )
            .order_by(DailyLoanLeadSnapshot.snapshot_date.desc())
            .all()
        )
        latest: dict[str, dict] = {}
        for r in rows:
            if r.csp_code in latest:
                continue  # already have a more recent one for this CSP
            latest[r.csp_code] = {
                "snapshot_date": r.snapshot_date,
                "mtd_count": r.mtd_count,
                "type_counts": _json_loads(r.type_counts_json),
            }
        return latest


def _delta(curr: int, prev: dict | None, today: date) -> int:
    """See module docstring re: month rollover. No previous snapshot at
    all (brand-new CSP, or this is the very first day this report has
    ever run) is treated the same way — nothing to subtract against, so
    the whole current count counts as new."""
    if prev is None:
        return max(curr, 0)
    prev_date = prev["snapshot_date"]
    if (prev_date.year, prev_date.month) != (today.year, today.month):
        return max(curr, 0)
    return max(curr - prev["mtd_count"], 0)


def _type_deltas(curr_types: dict[str, int], prev: dict | None, today: date) -> dict[str, int]:
    prev_types = {} if prev is None else prev.get("type_counts") or {}
    prev_date = None if prev is None else prev["snapshot_date"]
    rollover = prev_date is not None and (prev_date.year, prev_date.month) != (today.year, today.month)

    deltas: dict[str, int] = {}
    for name in set(curr_types) | set(prev_types):
        curr_v = curr_types.get(name, 0)
        if prev is None or rollover:
            d = curr_v
        else:
            d = max(curr_v - prev_types.get(name, 0), 0)
        if d > 0:
            deltas[name] = d
    return deltas


def aggregate_new_loan_leads(df: pd.DataFrame) -> dict:
    """
    Aggregator for AGGREGATORS["Loan Lead Approval (Daily)"] — `df` is
    already filtered to one Branch's own CSPs (see filter_for_recipient).
    Computes each CSP's new-leads-since-last-snapshot delta (see module
    docstring), never touching the sheet's own cumulative MTD figures
    directly in the output — every number here is a delta.
    """
    today = date.today()
    codes = [str(c) for c in df["csp_code"].dropna().astype(str) if c]
    previous = _load_previous_snapshots(codes, today)

    total_new = 0
    type_totals: dict[str, int] = {}
    csp_rows = []

    for _, row in df.iterrows():
        code = str(row["csp_code"]) if pd.notna(row["csp_code"]) else ""
        if not code:
            continue
        curr_mtd = int(row["loan_lead_count_curr"]) if pd.notna(row["loan_lead_count_curr"]) else 0
        curr_types = _parse_type_counts_for_row(row.get("loan_type_detail"))
        prev = previous.get(code)

        new_count = _delta(curr_mtd, prev, today)
        new_types = _type_deltas(curr_types, prev, today)

        if new_count <= 0:
            continue

        total_new += new_count
        for name, cnt in new_types.items():
            type_totals[name] = type_totals.get(name, 0) + cnt

        csp_rows.append({
            "csp_code": code,
            "csp_name": str(row["csp_name"]) if pd.notna(row["csp_name"]) else "",
            "branch_name": str(row["branch_name"]) if pd.notna(row["branch_name"]) else "",
            "branch_code": str(row["branch_code"]) if pd.notna(row["branch_code"]) else "",
            "new_leads": new_count,
            "type_breakdown": _distribution_str(new_types) if new_types else "-",
        })

    csp_rows.sort(key=lambda r: r["new_leads"], reverse=True)

    return {
        "New_Leads_Count": total_new,
        "New_Lead_Type": _distribution_str(type_totals) if type_totals else "No data available",
        "CSPs_With_New_Leads": len(csp_rows),
        "Total_CSP_Count": len(df),
        "Has_New_Leads": total_new > 0,
        "_new_lead_snapshot": {
            "total_new_leads": total_new,
            "csp_count": len(csp_rows),
            "rows": csp_rows,
        },
    }


def drop_zero_new_lead_recipients(db, job) -> int:
    """
    Remove (not just skip) any EmailLog row with no new leads today —
    "please approve these new leads" makes no sense to send when there's
    nothing new. Mirrors segmented_distribution_service.
    drop_zero_activity_daily_recipients, called only for this report (see
    report_send_service.py) since a plain Daily report with genuinely zero
    activity is still worth showing as "0", unlike this approval-request
    framing.
    """
    import json

    removed = 0
    for log_row in list(job.email_logs):
        if not log_row.context_override_json:
            continue
        context = json.loads(log_row.context_override_json)
        if not context.get("Has_New_Leads"):
            db.delete(log_row)
            removed += 1

    if removed:
        job.total_recipients = max(job.total_recipients - removed, 0)
        db.flush()
    return removed
