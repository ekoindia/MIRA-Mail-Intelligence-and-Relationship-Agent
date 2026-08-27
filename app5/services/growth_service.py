"""
Week-over-week growth / de-growth for the weekly combined digest.

Compares each recipient's CURRENT computed context against that same
recipient's WeeklyReportSnapshot history, and fills the WoW_* template
variables that have shipped as "No data available" since the templates were
written (there was never any history to compare against — see
services/snapshot_service.py).

Comparison is PER RECIPIENT, matched on recipient_email: every RBO/LHO sees
its own movement, not a national figure. A recipient with no prior snapshot
(new territory, or the first run after this feature landed) correctly gets
"No data available" rather than a fabricated 100% growth.

CUMULATIVE vs PERIOD metrics — this distinction is load-bearing:

AO_Weekly_Total and SSS_Weekly_Total are Month-To-Date cumulative totals
(report_aggregation_service.py sets "Weekly_Total": mtd), NOT an isolated
week's activity — confirmed empirically: across every RBO recipient with 3+
weeks of snapshot history, these two never decreased week to week (0/20
non-monotonic). Comparing two raw MTD readings as "Previous Week" vs
"Current Week" is misleading: both numbers include every prior week of the
month, so a strong "+66.7% growth" reading can mask an actual SLOWDOWN in the
current week's own pace. The fix requires THREE data points (two snapshots
back, one snapshot back, current) to isolate each week's own contribution:
    this_week_alone    = current_MTD - last_week_MTD
    last_week_alone     = last_week_MTD - two_weeks_ago_MTD
    growth% = (this_week_alone - last_week_alone) / last_week_alone * 100

Loan Lead Generation (LL_Leads_Generated / Leads_Generated) is NOT
cumulative — confirmed empirically: 10 of 20 RBO recipients had it DECREASE
between snapshots (e.g. 3 -> 0 -> 2), meaning the sheet already reports each
period's own count directly, not a running total. De-cumulating it would
therefore corrupt it. It keeps the simple, direct current-vs-previous
comparison.

INACT_Inactive_Count is a point-in-time state (how many CSPs are inactive
RIGHT NOW), not a flow metric at all — also compared directly, never
de-cumulated.

Detection/derivation only — nothing here sends, drafts, or mutates a report.
"""
from __future__ import annotations

import json
from datetime import date

from sqlalchemy.orm import Session

from database.snapshot_models import WeeklyReportSnapshot
from utils.logger import get_logger

logger = get_logger(__name__)

# Which template variable is derived from which underlying metric.
#   target_var -> (metric_key, higher_is_better, is_cumulative)
#
# higher_is_better=False for inactive CSPs: a DROP there is good news, so the
# wording must not call a reduction "de-growth".
#
# is_cumulative=True means the raw value is a running Month-To-Date total, so
# growth must be computed from two ISOLATED week-over-week deltas (needs 3
# data points) rather than directly from two raw readings (see module
# docstring). Only AO and SSS are cumulative; Loan Leads and Inactive CSPs
# are not and keep the direct comparison.
GROWTH_METRICS: dict[str, tuple[str, bool, bool]] = {
    "AO_WoW_Change_Percent": ("AO_Weekly_Total", True, True),
    "SSS_WoW_Change_Percent": ("SSS_Weekly_Total", True, True),
    # The metric key must be the one the aggregator actually emits and the
    # template actually displays — INACT_Inactive_Count. An earlier
    # "INACT_Total_Inactive" matched nothing, so this line silently rendered
    # "No data available" on every run instead of a real comparison.
    "INACT_WoW_Change": ("INACT_Inactive_Count", False, False),
    # Loan leads. Two entries because the metric is named differently by
    # level: RBO/LHO/Corporate Center carry the LL_-prefixed combined-digest
    # key, while Branch (whose digest is loan-lead-only) carries the bare
    # one. Only whichever key is actually present gets filled, so a level
    # never gets the other one as a stray variable.
    "LL_WoW_Change_Percent": ("LL_Leads_Generated", True, False),
    "Leads_WoW_Change_Percent": ("Leads_Generated", True, False),
}

NO_DATA = "No data available"

# A weekly comparison is only honest if the baseline really is about a week
# old. Beyond this, the percentage is arithmetically fine but semantically
# wrong — labelling a 21-day movement as week-over-week growth would
# overstate it. In that case no percentage is shown at all; the run still
# SAVES its snapshot, so the next run has a true 7-day baseline.
#
# This also self-heals a skipped week: it won't silently present a
# two-week jump as if it were one week's growth.
_MAX_COMPARISON_GAP_DAYS = 10
_PENDING_MSG = "Starts from next week's report"
_NO_ISOLATION_BASELINE_MSG = "Not enough history yet to isolate the week before that's pace"

# Label shift, per explicit instruction: the report is drafted Monday
# morning, when the week that just started has ~0 activity of its own — so
# the freshest reading available (today's live fetch, compared against last
# Monday's snapshot) actually reflects the week that JUST CONCLUDED, not a
# week still in progress. Calling that "Current Week" overstated how current
# it really was. It is now labelled "Previous Week" (the most recently
# completed week); the OLDER of the two comparison points is "Previous to
# Previous Week". There is no "Current Week" label at all — nothing here
# claims to represent the still-in-progress week, since there's nothing
# meaningful to report about it yet.
_OLDER_LABEL = "Previous to Previous Week"
_NEWER_LABEL = "Previous Week"


def _fmt_change(current: float, previous: float, higher_is_better: bool) -> str:
    """Human-readable movement for a DIRECT (non-cumulative) comparison —
    "Previous to Previous Week: 2 | Previous Week: 5 | Growth: 5 - 2 = +3 | +150.0% growth"

    Percentage change is undefined when the baseline is 0 — reporting
    "+100%" (or dividing by zero) would be a fabricated number, so that case
    states the movement in absolute terms instead, with no percentage.
    """
    delta = current - previous
    # Plain ASCII on purpose: these strings also land in logs, and the
    # Windows console here is cp1252, which cannot encode "→" or "—".
    base = (f"{_OLDER_LABEL}: {previous:g} | {_NEWER_LABEL}: {current:g} | "
            f"Growth: {current:g} - {previous:g} = {delta:+g}")

    if previous == 0:
        if current == 0:
            return f"{base} | No change"
        return base

    pct = (delta / previous) * 100
    if abs(pct) < 0.05:
        return f"{base} | No change"

    if higher_is_better:
        label = "growth" if delta > 0 else "de-growth"
        return f"{base} | {pct:+.1f}% {label}"

    verdict = "improvement" if delta < 0 else "needs attention"
    return f"{base} | {pct:+.1f}% - {verdict}"


def _fmt_isolated_change(current_mtd: float, prev_mtd: float, prev2_mtd: float | None,
                          higher_is_better: bool) -> str:
    """Human-readable movement for a CUMULATIVE (MTD) metric, de-cumulated
    into two isolated week-over-week figures before comparing:
    "Previous to Previous Week: 11 | Previous Week: 8 | Growth: 8 - 11 = -3 | -27.3% de-growth"

    "Previous Week" here means "what this recipient achieved DURING the most
    recently completed week alone" (current_mtd - prev_mtd), not the raw MTD
    reading — that distinction is the whole point of this function.
    """
    newer_alone = current_mtd - prev_mtd

    if prev2_mtd is None:
        # Only one prior snapshot exists — enough to isolate the most recent
        # completed week's activity, not enough to also isolate the week
        # before that (needs a snapshot from before that one too). Show
        # what's knowable; don't fabricate a comparison.
        return f"{_NEWER_LABEL}: {newer_alone:+g} | {_NO_ISOLATION_BASELINE_MSG}"

    older_alone = prev_mtd - prev2_mtd
    base = (f"{_OLDER_LABEL}: {older_alone:g} | {_NEWER_LABEL}: {newer_alone:g} | "
            f"Growth: {newer_alone:g} - {older_alone:g} = {newer_alone - older_alone:+g}")

    if older_alone == 0:
        if newer_alone == 0:
            return f"{base} | No change"
        return base

    pct = ((newer_alone - older_alone) / older_alone) * 100
    if abs(pct) < 0.05:
        return f"{base} | No change"

    if higher_is_better:
        label = "growth" if newer_alone > older_alone else "de-growth"
        return f"{base} | {pct:+.1f}% {label}"

    verdict = "improvement" if newer_alone < older_alone else "needs attention"
    return f"{base} | {pct:+.1f}% - {verdict}"


def _latest_snapshot_date_before(db: Session, before: date | None) -> date | None:
    q = db.query(WeeklyReportSnapshot.report_date).distinct()
    if before is not None:
        q = q.filter(WeeklyReportSnapshot.report_date < before)
    rows = q.order_by(WeeklyReportSnapshot.report_date.desc()).limit(1).all()
    return rows[0][0] if rows else None


def _contexts_for_date(db: Session, snap_date: date) -> dict[str, dict]:
    # Ordered by source_job_id so that when a date holds MORE THAN ONE run
    # (observed: 2026-07-27 and 2026-08-03 each have 132 rows = the weekly
    # report drafted twice that day), the later job's numbers deterministically
    # win. Without the ordering, whichever row the DB happened to return last
    # would silently become the baseline.
    rows = (
        db.query(WeeklyReportSnapshot)
        .filter(WeeklyReportSnapshot.report_date == snap_date)
        .order_by(WeeklyReportSnapshot.source_job_id.asc(), WeeklyReportSnapshot.id.asc())
        .all()
    )
    out: dict[str, dict] = {}
    for r in rows:
        try:
            out[(r.recipient_email or "").lower()] = json.loads(r.context_json)
        except (TypeError, ValueError):
            continue
    return out


def load_previous_contexts(db: Session, before: date | None = None) -> tuple[date | None, dict[str, dict]]:
    """The most recent snapshot strictly BEFORE `before`, as
    {recipient_email: context}. Returns (snapshot_date, mapping)."""
    snap_date = _latest_snapshot_date_before(db, before)
    if snap_date is None:
        return None, {}
    return snap_date, _contexts_for_date(db, snap_date)


def load_two_previous_contexts(
    db: Session, before: date | None = None
) -> tuple[date | None, dict[str, dict], date | None, dict[str, dict]]:
    """The two most recent snapshots strictly BEFORE `before` — needed to
    isolate a cumulative (MTD) metric's own week-over-week pace, which takes
    three consecutive data points (two-weeks-ago, last-week, current).
    Returns (date1, contexts1, date2, contexts2) where date1 is more recent
    than date2. Either pair may be (None, {}) if that much history doesn't
    exist yet.
    """
    date1 = _latest_snapshot_date_before(db, before)
    if date1 is None:
        return None, {}, None, {}
    ctx1 = _contexts_for_date(db, date1)

    date2 = _latest_snapshot_date_before(db, date1)
    if date2 is None:
        return date1, ctx1, None, {}
    ctx2 = _contexts_for_date(db, date2)
    return date1, ctx1, date2, ctx2


def is_first_monday_of_month(d) -> bool:
    """True only for the first Monday (day 1-7 AND a Monday). A later
    Monday always has day > 7. Moved here (from weekly_autosend_service.py)
    2026-08-27 — this is now growth-comparison logic, not send-gating
    logic: the first Monday still sends, it just skips the WoW comparison
    (see apply_growth's is_first_monday param)."""
    return d.weekday() == 0 and d.day <= 7


def apply_growth(
    context: dict,
    previous_context: dict | None,
    snapshot_date: date | None = None,
    current_date: date | None = None,
    previous_previous_context: dict | None = None,
    previous_previous_date: date | None = None,
    is_first_monday: bool = False,
) -> dict:
    """Fill the WoW_* variables in `context` in place, and return it.

    Also sets Growth_Comparison_Basis, so the email can state WHAT it was
    compared against. That matters: the first run compares against whatever
    snapshot exists, which may be weeks old rather than 7 days — presenting
    that as "week-over-week" without qualification would be misleading.

    is_first_monday: per explicit instruction 2026-08-27 — the first
    Monday of the month now SENDS (it used to be skipped entirely, see
    weekly_autosend_service.py), but still shows no growth comparison at
    all, cumulative or not: last Monday was a different month, and even
    the non-cumulative metrics (Loan Leads, Inactive CSPs) shouldn't get
    singled out as "the one comparison that still shows" when the whole
    point is a clean, comparison-free week. Every relevant var is set the
    same way as "no previous snapshot exists" — Has_Growth_Comparison=False
    hides the entire {{#if Has_Growth_Comparison}} block in the template,
    so this reads as "just this week's data", not as a broken/empty
    comparison. Checked FIRST, before anything about the snapshot itself.
    """
    if is_first_monday:
        relevant = {var: spec for var, spec in GROWTH_METRICS.items() if spec[0] in context}
        for var in relevant:
            context[var] = NO_DATA
        context["Growth_Comparison_Basis"] = (
            "First Monday of the month — this week's data only. "
            "Week-over-week comparison resumes next Monday."
        )
        context["Has_Growth_Comparison"] = False
        return context

    gap_days = None
    if snapshot_date and current_date:
        gap_days = (current_date - snapshot_date).days

    # Which variables are relevant to THIS recipient is decided by whether
    # their underlying metric is in the context, not by whether the variable
    # was already there — the loan-lead vars are new and no aggregator
    # pre-seeds them. This also keeps a level from picking up a stray
    # variable for a report it doesn't carry.
    relevant = {
        var: spec for var, spec in GROWTH_METRICS.items()
        if spec[0] in context
    }

    if not previous_context:
        for var in relevant:
            context[var] = NO_DATA
        context["Growth_Comparison_Basis"] = "No previous week on record yet"
        context["Has_Growth_Comparison"] = False
        return context

    # Baseline too old to call this "week-over-week" — suppress the numbers
    # rather than publish a figure that overstates one week's movement.
    if gap_days is not None and gap_days > _MAX_COMPARISON_GAP_DAYS:
        for var in relevant:
            context[var] = _PENDING_MSG
        context["Growth_Comparison_Basis"] = (
            f"Last comparable data is from {snapshot_date.strftime('%d %b %Y')} "
            f"({gap_days} days ago) - too old for a weekly comparison. "
            f"This week is being recorded; growth will appear in next week's report."
        )
        context["Has_Growth_Comparison"] = False
        return context

    # A cumulative (MTD) figure can only be de-cumulated across snapshots
    # that share the same calendar month — the sheet's own MTD column resets
    # to zero on the 1st, so subtracting across that boundary would produce a
    # fabricated, wildly wrong "isolated week" figure.
    same_month_prev_prev = (
        previous_previous_date is not None
        and snapshot_date is not None
        and previous_previous_date.month == snapshot_date.month
        and previous_previous_date.year == snapshot_date.year
    )
    same_month_current = (
        snapshot_date is not None and current_date is not None
        and snapshot_date.month == current_date.month
        and snapshot_date.year == current_date.year
    )

    filled = 0
    for var, (metric_key, higher_is_better, is_cumulative) in relevant.items():
        current = context.get(metric_key)
        previous = previous_context.get(metric_key)
        if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
            context[var] = NO_DATA
            continue

        if not is_cumulative:
            context[var] = _fmt_change(float(current), float(previous), higher_is_better)
            filled += 1
            continue

        # Cumulative metric: need same-month readings to de-cumulate at all.
        if not same_month_current:
            context[var] = NO_DATA
            continue

        prev2 = None
        if same_month_prev_prev and previous_previous_context:
            candidate = previous_previous_context.get(metric_key)
            if isinstance(candidate, (int, float)):
                prev2 = float(candidate)

        context[var] = _fmt_isolated_change(float(current), float(previous), prev2, higher_is_better)
        filled += 1

    if snapshot_date:
        context["Growth_Comparison_Basis"] = f"vs week of {snapshot_date.strftime('%d %b %Y')}"
    else:
        context["Growth_Comparison_Basis"] = "vs previous week"

    context["Has_Growth_Comparison"] = filled > 0
    return context
