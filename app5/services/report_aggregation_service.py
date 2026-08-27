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


_RECIPIENT_EMAIL_COLUMN = {
    OrgLevel.AO: "ao_email",
    OrgLevel.RBO: "rbo_email",
    OrgLevel.LHO: "lho_email",
    OrgLevel.BRANCH: "branch_email",
}


def filter_for_recipient(
    df: pd.DataFrame, org_level: str, org_unit_name: str, org_unit_email: str | None = None,
) -> pd.DataFrame:
    """
    Return only the rows belonging to a given recipient. Corporate Center
    gets the whole sheet (no filter); every other level filters on its own
    column.

    When org_unit_email is available, it is the ONLY filter applied — email
    is this recipient's true identity, name is just a display label that
    can be inconsistent. Two real cases this has to handle correctly:
      - One name, multiple emails (e.g. RBO "5" splits into two different
        officers for two different branch clusters, both sharing the name
        "5"): filtering by name alone would give both officers the same
        combined data. Email-only filtering correctly separates them.
      - One email, multiple names (e.g. the same real inbox is entered as
        RBO "2" for one stray row and RBO "3" for the other 23 — confirmed
        live in the calling sheet): filtering by name AND email would
        silently drop whichever rows carry the "other" name label, even
        though they belong to the same recipient. Email-only filtering
        correctly includes all of them.
    Name-based filtering is only a fallback for recipients with no known
    email (e.g. some manually-configured org recipients).
    """
    level = OrgLevel(org_level) if not isinstance(org_level, OrgLevel) else org_level

    if level in (OrgLevel.CORP, OrgLevel.INTERNAL):
        # Neither maps to a subset of calling-sheet rows — Corporate
        # Center gets the whole sheet by long-standing convention;
        # INTERNAL-level reports (e.g. SBI Kiosk Growth) don't use this
        # df at all, their own aggregator sources data independently, so
        # what's returned here is never actually read.
        return df

    email_col = _RECIPIENT_EMAIL_COLUMN.get(level)
    if org_unit_email and email_col:
        email_cf = org_unit_email.strip().casefold()
        return df[df[email_col].astype(str).str.strip().str.casefold() == email_cf]

    name_cf = (org_unit_name or "").strip().casefold()
    if level == OrgLevel.AO:
        result = df[df["ao"].astype(str).str.strip().str.casefold() == name_cf]
    elif level == OrgLevel.RBO:
        result = df[df["rbo"].astype(str).str.strip().str.casefold() == name_cf]
    elif level == OrgLevel.LHO:
        result = df[df["lho"].astype(str).str.strip().str.casefold() == name_cf]
    elif level == OrgLevel.BRANCH:
        result = df[
            (df["branch_name"].astype(str).str.strip().str.casefold() == name_cf)
            | (df["branch_code"].astype(str).str.strip() == (org_unit_name or "").strip())
        ]
    else:
        raise ValueError(f"Unknown org level: {org_level}")
    return result


def _pct(achieved: float, target: float) -> float:
    if not target:
        return 0.0
    return round((achieved / target) * 100, 1)


def _bar_pct(pct: float) -> float:
    """A progress-bar fill can't exceed its own box — cap at 100 for the
    `width:{{...}}%` style value ONLY. The plain *_Percent key keeps the
    real (possibly >100%) figure for the number shown next to the bar;
    every template uses this capped twin (*_BarPercent) for the bar's
    width instead so an over-achieving CSP/RBO doesn't render a bar that
    spills out of its rounded pill container."""
    return min(100.0, pct)


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
        "PMJJBY_FTD": int(df["ftd_pmjjby"].sum()),
        "PMSBY_FTD": int(df["ftd_pmsby"].sum()),
        "APY_FTD": int(df["ftd_apy"].sum()),
        "PMJJBY_Target": int(df["target_pmjjby"].sum()),
        "PMSBY_Target": int(df["target_pmsby"].sum()),
        "APY_Target": int(df["target_apy"].sum()),
        "PMJJBY_Percent": _pct(int(df["mtd_pmjjby"].sum()), int(df["target_pmjjby"].sum())),
        "PMSBY_Percent": _pct(int(df["mtd_pmsby"].sum()), int(df["target_pmsby"].sum())),
        "APY_Percent": _pct(int(df["mtd_apy"].sum()), int(df["target_apy"].sum())),
        # Capped twins for the progress-bar width — see _bar_pct.
        "PMJJBY_BarPercent": _bar_pct(_pct(int(df["mtd_pmjjby"].sum()), int(df["target_pmjjby"].sum()))),
        "PMSBY_BarPercent": _bar_pct(_pct(int(df["mtd_pmsby"].sum()), int(df["target_pmsby"].sum()))),
        "APY_BarPercent": _bar_pct(_pct(int(df["mtd_apy"].sum()), int(df["target_apy"].sum()))),
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
        # Capped twin for the progress-bar width — see _bar_pct.
        "PMJDY_MTD_BarPercent": _bar_pct(_pct(mtd, target)),
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


def aggregate_account_opening_and_sss(df: pd.DataFrame) -> dict:
    """
    Account Opening and Social Security Scheme now go out as ONE email per
    recipient instead of two separate ones (per user instruction) — this
    computes both and namespaces every generic template key with AO_/SSS_
    so a single template body can show two clearly labeled sections.
    Used only by the "Account Opening" ReportMaster rows; "Social Security
    Scheme" rows no longer send their own email — see
    MERGED_INTO_OTHER_REPORT below.
    """
    ao = aggregate_account_opening(df)
    sss = aggregate_sss(df)

    merged = {
        # Overrides the default {{Report_Name}} (which would otherwise just
        # say "Account Opening") in both the subject and body opening line.
        "Report_Name": "Account Opening & Social Security Scheme",
        "CSP_Count": len(df),
    }
    merged.update({f"AO_{key}": value for key, value in ao.items()})
    merged.update({f"SSS_{key}": value for key, value in sss.items()})
    return merged


def inactive_csps_only(df: pd.DataFrame) -> pd.DataFrame:
    """
    Attachment filter for the Inactive CSPs report — inactive rows only.

    A blank Terminal Status means "not recorded", NOT "inactive". Filtering on
    `!= "active"` alone swept those blanks in: the live sheet holds 494
    Active, 42 Inactive and 1 blank, and the report was stating 43 inactive
    CSPs while also listing that blank row as an "Unspecified" circle in the
    distribution breakdown. Blanks are now excluded, so the figure reflects
    only CSPs the sheet actually flags.

    Any other non-blank value still counts as inactive, so a future status
    like "Suspended" is included rather than silently dropped.
    """
    status = df["terminal_status"].astype(str).str.strip()
    recorded = ~status.str.casefold().isin(["", "nan", "none"])
    return df[recorded & (status.str.casefold() != "active")]


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
        # Not a template placeholder — read via {{#if INACT_Has_Data}} to
        # skip this section only when this recipient has no CSPs in scope
        # at all (genuinely nothing to compute). Unlike Loan Lead
        # Generation, a real, computed "0 inactive CSPs" IS reportable —
        # it's a meaningful result, not missing data — so it's shown, not
        # skipped.
        "Has_Data": len(df) > 0,
    }


_LOAN_TYPE_PAIR_RE = re.compile(r"([A-Za-z][A-Za-z ]*?)-(\d+)")


def _parse_loan_type_counts(df: pd.DataFrame) -> dict[str, int]:
    """
    Sum lead counts per loan type from the free-text "Type on loan" column.
    A single cell can list more than one type this CSP generated leads for
    this month — e.g. "Agri Loan-1, Personal Loan-5" — so each cell is
    split on comma and every "Type-Count" pair is parsed individually
    rather than treating the whole cell as one category.
    """
    totals: dict[str, int] = {}
    for cell in df["loan_type_detail"].dropna().astype(str):
        for name, count in _LOAN_TYPE_PAIR_RE.findall(cell):
            name = name.strip()
            if not name:
                continue
            totals[name] = totals.get(name, 0) + int(count)
    return totals


_TABLE_CELL = 'style="border: 1px solid #999; padding: 6px 10px;"'


def _render_csp_lead_rows(df: pd.DataFrame) -> str:
    """
    One <tr> per CSP with at least one loan lead this month — CSP Code,
    CSP Name, Link Branch, Branch Code (read by column header name, same
    "Branch Code" column calling_sheet_service resolves for every other
    report — see col["branch_code"]), Count of loan lead generated (MTD),
    Types of leads. Unlike the rest of this report's fields, this is
    inherently a per-recipient VARIABLE-length list (however many CSPs
    had a lead), so it can't be represented as fixed {{Variable}}
    placeholders the way the summary line above it is — the template
    supplies its own literal table header text and this one placeholder
    for the data rows.
    """
    leads_df = df[df["loan_lead_count_curr"].fillna(0) > 0]
    rows = []
    for _, row in leads_df.iterrows():
        code = str(row["csp_code"]) if pd.notna(row["csp_code"]) else ""
        name = str(row["csp_name"]) if pd.notna(row["csp_name"]) else ""
        branch = str(row["branch_name"]) if pd.notna(row["branch_name"]) else ""
        branch_code = str(row["branch_code"]) if pd.notna(row["branch_code"]) else ""
        count = int(row["loan_lead_count_curr"])
        loan_type = str(row["loan_type_detail"]) if pd.notna(row["loan_type_detail"]) else "-"
        rows.append(
            f"<tr><td {_TABLE_CELL}>{code}</td><td {_TABLE_CELL}>{name}</td>"
            f"<td {_TABLE_CELL}>{branch}</td><td {_TABLE_CELL}>{branch_code}</td>"
            f"<td {_TABLE_CELL}>{count}</td><td {_TABLE_CELL}>{loan_type}</td></tr>"
        )
    return "".join(rows)


def aggregate_loan_lead_generation(df: pd.DataFrame) -> dict:
    total_leads = int(df["loan_lead_count_curr"].sum())
    target = int(df["target_loan_lead"].sum())
    csps_with_leads = int((df["loan_lead_count_curr"].fillna(0) > 0).sum())
    non_responders = max(len(df) - csps_with_leads, 0)
    type_counts = _parse_loan_type_counts(df)

    return {
        "Loan_Lead_Count": total_leads,
        "Loan_Lead_Target": target,
        "Loan_Lead_Percent": _pct(total_leads, target),
        "CSPs_With_Leads": csps_with_leads,
        "Total_CSP_Count": len(df),
        # Generic keys for "Weekly Loan Lead Generation" / combined digests
        "Leads_Generated": total_leads,
        "Leads_Target": target,
        "Leads_Achievement_Percent": f"{_pct(total_leads, target)}%",
        # Numeric, capped twin for the progress-bar width (see _bar_pct) —
        # unlike Leads_Achievement_Percent above, this one is NOT a string
        # with "%" baked in, since the template appends its own "%" after
        # {{...}} for the width value; reusing the string version there
        # was producing invalid CSS ("width:70.2%%").
        "Leads_Achievement_BarPercent": _bar_pct(_pct(total_leads, target)),
        # Genuinely untracked: the sheet's paired "Status" column (meant for
        # approved/disbursed/rejected outcomes) is blank on every single row
        # with a lead this month — not a bug, just nothing recorded yet.
        "Leads_Converted": "No data available",
        "Lead_Type": _distribution_str(type_counts),
        "Active_Lead_CSPs": csps_with_leads,
        "Non_Responders": non_responders,
        "Level_Breakdown": f"{len(df)} CSP(s) in scope",
        # Per-CSP breakdown table body — see _render_csp_lead_rows.
        "CSP_Rows": _render_csp_lead_rows(df),
        # Not a template placeholder — read by combined_digest_service to
        # decide whether this recipient gets a Loan Lead Generation section
        # at all (per explicit instruction: skip the report entirely for
        # any RBO/Branch/LHO with zero leads this month, rather than
        # showing an all-zero section).
        "Has_Leads": total_leads > 0,
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
#
# "Social Security Scheme" is deliberately absent — it's merged into the
# "Account Opening" send (see aggregate_account_opening_and_sss and
# MERGED_INTO_OTHER_REPORT below), not sent as its own email anymore.
AGGREGATORS = {
    "Account Opening (Daily)": aggregate_account_opening_and_sss,
    "Account Opening (Weekly)": aggregate_account_opening_and_sss,
    "Inactive CSPs (Weekly)": aggregate_inactive_csps,
    "Loan Lead Generation (Weekly)": aggregate_loan_lead_generation,
    "DFS Incentive Slab (Monthly)": aggregate_dfs_incentive_slab,
    "CSP Income Impact (Monthly)": aggregate_csp_income_impact,
}

# Reports that must NEVER send/draft their own separate email — they're
# folded into another report's single combined email instead. Checked by
# services/report_send_service.send_report_now before any fetch/send work,
# same as NOT_YET_AUTOMATED_REPORTS below, just for a different reason
# ("sent together with X" rather than "not ready yet").
MERGED_INTO_OTHER_REPORT = {
    "Social Security Scheme (Daily)": "Account Opening (Daily)",
    "Social Security Scheme (Weekly)": "Account Opening (Weekly)",
}

# Reports intentionally NOT in AGGREGATORS (data gaps, flagged to the user
# rather than fabricated):
#   "Inputs for Month-on-Month Growth (Monthly)" -> no new-CSP-addition/migration column
# Skipped entirely per user confirmation (no matching columns at all):
#   "Server Issue (Weekly)"
#   "CSP Physical Camp (Weekly)"

# Reports whose EMAIL TEMPLATE still has the original "[ ]" manual-fill
# placeholders (see upgrade_templates.py's NOT_YET_AUTOMATED) — never send
# or draft these automatically, even if a stale ReportUpload/demo file
# happens to exist for them (e.g. leftover fake sample files from early
# testing), since the email body would go out with literal unfilled "[ ]"
# text.
#
# Re-KYC & Inoperative Accounts is ALSO here now, per explicit user
# instruction to put it on hold — it has no aggregator (only free-text
# promise columns, no numeric completed count) and previously fell back to
# an honest "No data available" render via TEMPLATE_VARIABLE_DEFAULTS, but
# the user wants it fully paused rather than sent with that fallback text.
NOT_YET_AUTOMATED_REPORTS = (
    "Server Issue (Weekly)",
    "CSP Physical Camp (Weekly)",
    "Inputs for Month-on-Month Growth (Monthly)",
    "Re-KYC & Inoperative Accounts (Daily)",
    "Re-KYC & Inoperative Accounts (Weekly)",
)

# Every generic {{Variable}} a template can reference where the report
# behind it might not have a full aggregator. email_service.run_distribution_job
# seeds every recipient's context with these defaults before applying
# whatever real values that report's aggregator computed, so a report
# without one still sends a clean, honest email instead of a broken one
# with raw, unrendered "{{Variable}}" text.
#
# The old single-block Daily RBO Update / Weekly Consolidated RBO/LHO
# generic keys (Target_Achievement_Percent, MTD_FTD_Achievement, ...) were
# removed from here — those templates now only ever render for the merged
# Account Opening & SSS report (aggregate_account_opening_and_sss always
# supplies its AO_/SSS_-prefixed keys), and Re-KYC, which used to need
# these as a fallback, is now in NOT_YET_AUTOMATED_REPORTS and never
# reaches template rendering at all.
TEMPLATE_VARIABLE_DEFAULTS = {
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
