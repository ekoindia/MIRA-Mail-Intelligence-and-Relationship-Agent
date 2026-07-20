"""
Per-report aggregation over the live Calling Sheet.

Each function takes a Calling Sheet DataFrame (see
services/calling_sheet_service.load_calling_sheet) already filtered to one
recipient's own rows (see filter_for_recipient below) and returns a dict of
computed template variables (becomes EmailLog.context_override_json, then
substituted into that recipient's email via {{Variable}} tags — see
utils.helpers.render_template).

Two kinds of keys are returned by each aggregator:
  - report-specific keys (SSS_Target, PMJDY_MTD_Achievement, ...) kept for
    anything that inspects the raw numbers directly.
  - GENERIC keys matching the actual {{...}} placeholders in the shared
    email templates (Target_Achievement_Percent, Top_Performers, ...) —
    see services/upgrade_templates.py for the exact template wording these
    correspond to. Several templates are shared by 2-3 report types (e.g.
    "Daily RBO Update" serves SSS, Account Opening AND Re-KYC), so every
    aggregator that shares a template MUST emit the same generic key names
    (a value the template renders, or "No data available" when this report
    genuinely lacks a way to compute that particular field).

Only the 6 reports with clean, unambiguous matching columns in the sheet
are implemented here, per the confirmed scheme mapping
(SSS = PMJJBY + PMSBY + APY, Account Opening = PMJDY) and the confirmed
decision to skip Server Issue / CSP Physical Camp for now:
  - SSS (Social Security Schemes)
  - Account Opening
  - Inactive CSPs
  - Loan Lead Generation
  - DFS Incentive Slab
  - CSP Income Impact

Two more reports have partial data (promise text only, no numeric
"completed" count) and are intentionally NOT implemented here yet:
  - Re-KYC & Inoperative Accounts -> only free-text promise columns exist
And one has no matching column at all:
  - Inputs for Month-on-Month Growth -> no new-CSP-addition/migration column

Numbers this module cannot honestly compute (week-over-week deltas, lead
conversion counts, etc. — nothing in the sheet tracks a historical
snapshot to compare against) are reported as "No data available" rather
than fabricated. See services/email_service.py's TEMPLATE_VARIABLE_DEFAULTS
for the account-wide fallback used when a report has no aggregator at all.
"""
from __future__ import annotations

import re

import pandas as pd

from database.org_models import OrgLevel

_SLAB_AMOUNT_RE = re.compile(r"₹\s*([\d,]+)")

# CSP Score bucketing for the "CSP score distribution (A/B/C/D)" template
# line. The sheet has no defined grade bands, so these thresholds are a
# reasonable default split of the observed score range — adjust here if
# the business defines official bands later.
_SCORE_BANDS = (("A", 8), ("B", 4), ("C", 0))  # else "D"


def filter_for_recipient(df: pd.DataFrame, org_level: str, org_unit_name: str) -> pd.DataFrame:
    """
    Return only the rows belonging to a given recipient. Corporate Center
    gets the whole sheet (no filter); every other level filters on its own
    column.
    """
    level = OrgLevel(org_level) if not isinstance(org_level, OrgLevel) else org_level
    name_cf = (org_unit_name or "").strip().casefold()

    if level == OrgLevel.CORP:
        return df
    if level == OrgLevel.AO:
        return df[df["ao"].astype(str).str.strip().str.casefold() == name_cf]
    if level == OrgLevel.RBO:
        return df[df["rbo"].astype(str).str.strip().str.casefold() == name_cf]
    if level == OrgLevel.LHO:
        return df[df["lho"].astype(str).str.strip().str.casefold() == name_cf]
    if level == OrgLevel.BRANCH:
        return df[
            (df["branch_name"].astype(str).str.strip().str.casefold() == name_cf)
            | (df["branch_code"].astype(str).str.strip() == (org_unit_name or "").strip())
        ]
    raise ValueError(f"Unknown org level: {org_level}")


def _pct(achieved: float, target: float) -> float:
    if not target:
        return 0.0
    return round((achieved / target) * 100, 1)


def _fmt_num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.1f}"


def _top_bottom_csps(df: pd.DataFrame, metric_col: str, n: int = 3) -> tuple[str, str]:
    """
    Real CSP-level ranking for a "Top performers" / "CSPs requiring
    follow-up" template line — top N by the metric, and CSPs at zero (the
    ones needing outreach).
    """
    valid = df[["csp_name", metric_col]].dropna(subset=[metric_col])
    if valid.empty:
        return "No data available", "No data available"

    top = valid.nlargest(n, metric_col)
    top_str = ", ".join(f"{name} ({_fmt_num(val)})" for name, val in top.itertuples(index=False))

    zero_or_below = valid[valid[metric_col] <= 0]
    if zero_or_below.empty:
        bottom_str = "None — every CSP has at least one"
    else:
        names = zero_or_below["csp_name"].head(n).tolist()
        extra = len(zero_or_below) - len(names)
        bottom_str = ", ".join(names) + (f", and {extra} more" if extra > 0 else "")
    return top_str, bottom_str


def _distribution_str(d: dict) -> str:
    if not d:
        return "No data available"
    return ", ".join(f"{k}: {v}" for k, v in sorted(d.items(), key=lambda kv: -kv[1]))


def aggregate_sss(df: pd.DataFrame) -> dict:
    """Social Security Schemes = PMJJBY + PMSBY + APY."""
    target = int(df[["target_pmjjby", "target_pmsby", "target_apy"]].sum().sum())
    mtd = int(df[["mtd_pmjjby", "mtd_pmsby", "mtd_apy"]].sum().sum())
    ftd = int(df[["ftd_pmjjby", "ftd_pmsby", "ftd_apy"]].sum().sum())

    mtd_per_csp = df[["mtd_pmjjby", "mtd_pmsby", "mtd_apy"]].sum(axis=1)
    ranked = df.assign(_sss_mtd=mtd_per_csp)
    top_str, bottom_str = _top_bottom_csps(ranked, "_sss_mtd")

    return {
        "SSS_Target": target,
        "SSS_MTD_Achievement": mtd,
        "SSS_FTD_Achievement": ftd,
        "SSS_MTD_Percent": _pct(mtd, target),
        "PMJJBY_MTD": int(df["mtd_pmjjby"].sum()),
        "PMSBY_MTD": int(df["mtd_pmsby"].sum()),
        "APY_MTD": int(df["mtd_apy"].sum()),
        "CSP_Count": len(df),
        # Generic keys for "Daily RBO Update" / "Weekly Consolidated RBO/LHO".
        # "%" is embedded in the value itself (not hardcoded in the template)
        # so the "No data available" fallback never reads as "...available%".
        "Target_Achievement_Percent": f"{_pct(mtd, target)}%",
        "MTD_FTD_Achievement": f"{mtd} MTD / {ftd} FTD",
        "Top_Performers": top_str,
        "Followup_CSPs": bottom_str,
        "Weekly_Total": mtd,
        "WoW_Change_Percent": "No data available",
        "MTD_Cumulative": mtd,
        "Level_Breakdown": f"{len(df)} CSP(s) in scope",
        "Top_Bottom_Performers": f"Top: {top_str} | Needing follow-up: {bottom_str}",
    }


def aggregate_account_opening(df: pd.DataFrame) -> dict:
    """Account Opening = PMJDY."""
    target = int(df["target_pmjdy"].sum())
    mtd = int(df["mtd_pmjdy"].sum())
    ftd = int(df["ftd_pmjdy"].sum())

    top_str, bottom_str = _top_bottom_csps(df, "mtd_pmjdy")

    return {
        "PMJDY_Target": target,
        "PMJDY_MTD_Achievement": mtd,
        "PMJDY_FTD_Achievement": ftd,
        "PMJDY_MTD_Percent": _pct(mtd, target),
        "CSP_Count": len(df),
        "Target_Achievement_Percent": f"{_pct(mtd, target)}%",
        "MTD_FTD_Achievement": f"{mtd} MTD / {ftd} FTD",
        "Top_Performers": top_str,
        "Followup_CSPs": bottom_str,
        "Weekly_Total": mtd,
        "WoW_Change_Percent": "No data available",
        "MTD_Cumulative": mtd,
        "Level_Breakdown": f"{len(df)} CSP(s) in scope",
        "Top_Bottom_Performers": f"Top: {top_str} | Needing follow-up: {bottom_str}",
    }


def inactive_csps_only(df: pd.DataFrame) -> pd.DataFrame:
    """Attachment filter for the Inactive CSPs report — inactive rows only."""
    return df[df["terminal_status"].astype(str).str.strip().str.casefold() != "active"]


def aggregate_inactive_csps(df: pd.DataFrame) -> dict:
    inactive = inactive_csps_only(df)
    # The sheet only tracks a day-count while a CSP is still Active-but-declining;
    # once flagged fully Inactive the field goes blank, so mean() over an
    # all-blank inactive group is NaN — not a bug, just nothing to average.
    avg_days = inactive["inactivity_days"].mean() if len(inactive) else None

    circle_dist = inactive["lho"].astype(str).str.strip().replace({"": "Unspecified"}).value_counts().to_dict()

    return {
        "Total_CSP_Count": len(df),
        "Inactive_CSP_Count": len(inactive),
        "Inactive_Percent": _pct(len(inactive), len(df)),
        "Avg_Inactivity_Days": round(float(avg_days), 1) if pd.notna(avg_days) else 0,
        # Generic keys for "Weekly Inactive CSP Status"
        "Inactive_Count": len(inactive),
        "WoW_Change": "No data available",
        "Newly_Inactive": "No data available",
        "Reactivated": "No data available",
        "Circle_Distribution": _distribution_str(circle_dist),
        "Action_Plan": "See attached CSP-wise remarks for outreach status.",
    }


def aggregate_loan_lead_generation(df: pd.DataFrame) -> dict:
    total_leads = int(df["loan_lead_count_curr"].sum())
    csps_with_leads = int((df["loan_lead_count_curr"].fillna(0) > 0).sum())
    non_responders = max(len(df) - csps_with_leads, 0)

    return {
        "Loan_Lead_Count": total_leads,
        "CSPs_With_Leads": csps_with_leads,
        "Total_CSP_Count": len(df),
        # Generic keys for "Weekly Loan Lead Generation"
        "Leads_Generated": total_leads,
        "Leads_Converted": "No data available",
        "Lead_Type": "No data available",
        "Active_Lead_CSPs": csps_with_leads,
        "Non_Responders": non_responders,
        "Level_Breakdown": f"{len(df)} CSP(s) in scope",
    }


def _slab_amount_lookup(df: pd.DataFrame) -> dict[str, float]:
    """
    Achieved-slab labels ("No Slab", "Slab-2") don't carry a rupee amount,
    but the target-slab labels for the same CSPs do ("Slab-2 (₹1,500)") —
    build a name->amount lookup from whatever target labels are present in
    this recipient's own rows, rather than hardcoding slab values.
    """
    lookup: dict[str, float] = {"No Slab": 0.0}
    for label in df["slab_target_label"].dropna().astype(str):
        match = _SLAB_AMOUNT_RE.search(label)
        if match:
            name = label.split("(")[0].strip()
            lookup[name] = float(match.group(1).replace(",", ""))
    return lookup


def aggregate_dfs_incentive_slab(df: pd.DataFrame) -> dict:
    """
    Slab target/achievement are categorical labels ("Slab-1 (₹1,200)",
    "No Slab"), not counts to sum — report how many CSPs fall in each
    slab instead of a meaningless numeric total.
    """
    def _distribution(col: str) -> dict:
        cleaned = df[col].astype(str).str.strip().replace({"": "Unspecified", "nan": "Unspecified"})
        return cleaned.value_counts().to_dict()

    target_dist = _distribution("slab_target_label")
    mtd_dist = _distribution("slab_mtd_label")

    # Achieved-slab names ("Slab-3") only have a known rupee value if some
    # CSP in this same recipient's rows has that slab as their *target*
    # this month ("Slab-3 (₹1,800)") — if none do, that slab's amount is
    # simply unknown here. Report the partial total honestly rather than
    # silently under-counting it as if it were complete.
    amount_lookup = _slab_amount_lookup(df)
    known_total = 0.0
    unknown_slabs: list[str] = []
    for name, count in mtd_dist.items():
        if name in amount_lookup:
            known_total += amount_lookup[name] * count
        elif name != "Unspecified":
            unknown_slabs.append(name)

    if amount_lookup == {"No Slab": 0.0} and not unknown_slabs:
        total_incentive_str = "No data available"
    elif unknown_slabs:
        total_incentive_str = f"₹{known_total:,.0f} (partial — amount unknown for: {', '.join(unknown_slabs)})"
    else:
        total_incentive_str = f"₹{known_total:,.0f}"

    scheme_counts = {
        "PMJJBY": int((df["mtd_pmjjby"].fillna(0) > 0).sum()),
        "PMSBY": int((df["mtd_pmsby"].fillna(0) > 0).sum()),
        "APY": int((df["mtd_apy"].fillna(0) > 0).sum()),
        "PMJDY": int((df["mtd_pmjdy"].fillna(0) > 0).sum()),
    }

    return {
        "DFS_Slab_Target_Distribution": target_dist,
        "DFS_Slab_MTD_Distribution": mtd_dist,
        "CSP_Count": len(df),
        # Generic keys for "Monthly DFS Incentive Slab"
        "Slab_Distribution": _distribution_str(mtd_dist),
        "Scheme_Counts": ", ".join(f"{k}: {v}" for k, v in scheme_counts.items()),
        "Total_Incentive": total_incentive_str,
        "MoM_Comparison": "No data available",
    }


def aggregate_csp_income_impact(df: pd.DataFrame) -> dict:
    curr_total = float(df["commission_curr_month"].sum())
    prev_total = float(df["commission_prev_month"].sum()) if df["commission_prev_month"].notna().any() else None
    avg_balance = df["avg_balance"].mean()

    csp_count = len(df)
    avg_income = round(curr_total / csp_count, 2) if csp_count else 0.0

    mom_growth = _pct(curr_total - prev_total, prev_total) if prev_total else None

    growing = int((df["growth_streak_curr"].fillna(0) > 0).sum())
    declining = int((df["growth_streak_curr"].fillna(0) < 0).sum())
    flat = csp_count - growing - declining
    income_analysis = f"{growing} growing, {declining} declining, {flat} flat"

    def _score_band(score: float) -> str:
        for label, threshold in _SCORE_BANDS:
            if score >= threshold:
                return label
        return "D"

    score_dist: dict[str, int] = {}
    for score in df["csp_score"].dropna():
        band = _score_band(float(score))
        score_dist[band] = score_dist.get(band, 0) + 1

    return {
        "CSP_Count": csp_count,
        "Avg_Balance": round(float(avg_balance), 2) if pd.notna(avg_balance) else 0,
        "Total_Amount_To_Deposit": round(float(df["amount_to_deposit"].sum()), 2),
        "Total_Commission_Curr_Month": round(curr_total, 2),
        # Generic keys for "Monthly CSP Income Impact"
        "Avg_Income": f"₹{avg_income:,.2f}",
        "Income_Analysis": income_analysis,
        "CSP_Score_Distribution": _distribution_str(score_dist),
        "MoM_Growth_Percent": f"{mom_growth}%" if mom_growth is not None else "No data available",
    }


# Report name -> aggregation function. Keys match ReportMaster.report_name
# exactly as seeded by seed_reporting_framework.py (REPORTS list) from the
# docx. Daily/Weekly variants of the same metric share one aggregator —
# frequency only affects when the schedule fires, not how the numbers for
# that recipient are computed.
AGGREGATORS = {
    "Social Security Scheme (Daily)": aggregate_sss,
    "Social Security Scheme (Weekly)": aggregate_sss,
    "Account Opening (Daily)": aggregate_account_opening,
    "Account Opening (Weekly)": aggregate_account_opening,
    "Inactive CSPs (Weekly)": aggregate_inactive_csps,
    "Loan Lead Generation (Weekly)": aggregate_loan_lead_generation,
    "DFS Incentive Slab (Monthly)": aggregate_dfs_incentive_slab,
    "CSP Income Impact (Monthly)": aggregate_csp_income_impact,
}

# Reports intentionally NOT in AGGREGATORS (data gaps, flagged to the user
# rather than fabricated):
#   "Re-KYC & Inoperative Accounts (Daily)"   -> only free-text promise columns, no numeric completed count
#   "Re-KYC & Inoperative Accounts (Weekly)"  -> same
#   "Inputs for Month-on-Month Growth (Monthly)" -> no new-CSP-addition/migration column
# Skipped entirely per user confirmation (no matching columns at all):
#   "Server Issue (Weekly)"
#   "CSP Physical Camp (Weekly)"

# Every generic {{Variable}} the 6 automated templates reference (see
# upgrade_templates.py), defaulted to "No data available". Templates 1
# ("Daily RBO Update") and 4 ("Weekly Consolidated RBO/LHO") are each
# shared with Re-KYC & Inoperative Accounts, which has NO aggregator above
# — without this, its emails would show the raw, unrendered "{{Variable}}"
# text instead of a real value. email_service.run_distribution_job seeds
# every recipient's context with these defaults before applying whatever
# real values that report's aggregator computed, so a report with no
# aggregator still sends a clean, honest email instead of a broken one.
TEMPLATE_VARIABLE_DEFAULTS = {
    "Target_Achievement_Percent": "No data available",
    "MTD_FTD_Achievement": "No data available",
    "Top_Performers": "No data available",
    "Followup_CSPs": "No data available",
    "Weekly_Total": "No data available",
    "WoW_Change_Percent": "No data available",
    "MTD_Cumulative": "No data available",
    "Level_Breakdown": "No data available",
    "Top_Bottom_Performers": "No data available",
    "Leads_Generated": "No data available",
    "Leads_Converted": "No data available",
    "Lead_Type": "No data available",
    "Active_Lead_CSPs": "No data available",
    "Non_Responders": "No data available",
    "Inactive_Count": "No data available",
    "WoW_Change": "No data available",
    "Newly_Inactive": "No data available",
    "Reactivated": "No data available",
    "Circle_Distribution": "No data available",
    "Action_Plan": "No data available",
    "Slab_Distribution": "No data available",
    "Scheme_Counts": "No data available",
    "Total_Incentive": "No data available",
    "MoM_Comparison": "No data available",
    "Avg_Income": "No data available",
    "Income_Analysis": "No data available",
    "CSP_Score_Distribution": "No data available",
    "MoM_Growth_Percent": "No data available",
}
