"""
One-time (idempotent) upgrade: replaces the "[ ]" manual-fill placeholders
in the 6 email templates backed by real Calling Sheet data with actual
{{Variable}} tags — see services/report_aggregation_service.py for exactly
what each variable computes. Automates the templates originally seeded
verbatim (bracket placeholders included) from
"Scheduled Outgoing Mail – Reporting Framework.docx" by seed_reporting_framework.py.

Two of these templates are shared by more than one report type (e.g.
"Daily RBO Update" serves SSS, Account Opening AND Re-KYC & Inoperative) —
the {{Variable}} names below are generic on purpose so every report using
that template supplies a real value or a "No data available" string (see
report_aggregation_service.py's aggregators and
email_service.py's TEMPLATE_VARIABLE_DEFAULTS).

3 templates (Weekly Server Issue & Downtime, Weekly CSP Physical Camp,
Monthly MoM Growth Inputs) are NOT touched here — the sheet has no clean
matching data for those reports, so they keep their original "[ ]"
placeholders rather than silently claiming to be automated. Only the
"[Name]" sender placeholder is normalized across all 9 templates.

Safe to re-run: matches by template name and overwrites body_html.

    python upgrade_templates.py
"""
from __future__ import annotations

from database.db import get_db, init_db
from database.models import EmailTemplate

# Templates with a real aggregator behind them — body_html fully rewritten.
AUTOMATED_BODIES = {
    "Daily RBO Update": (
        "<p>Dear Sir/Ma'am,</p>"
        "<p>Please find below the daily {{Report_Name}} status for RBO {{RBO_Name}} as on {{Date}}.</p>"
        "<ul>"
        "<li>Target vs. achievement: {{Target_Achievement_Percent}}</li>"
        "<li>MTD &amp; FTD achievement: {{MTD_FTD_Achievement}}</li>"
        "<li>Top-performing CSPs: {{Top_Performers}}</li>"
        "<li>CSPs requiring follow-up: {{Followup_CSPs}}</li>"
        "</ul>"
        "<p>Regards,<br/>Operations Team, Eko Bharat Ventures Pvt. Ltd.</p>"
    ),
    "Weekly Consolidated RBO/LHO": (
        "<p>Dear Sir/Ma'am,</p>"
        "<p>Consolidated weekly performance for {{Report_Name}}:</p>"
        "<ul>"
        "<li>Weekly total: {{Weekly_Total}} | WoW change: {{WoW_Change_Percent}}</li>"
        "<li>MTD cumulative: {{MTD_Cumulative}}</li>"
        "<li>RBO-wise / LHO-wise breakdown: {{Level_Breakdown}}</li>"
        "<li>Top and bottom performers: {{Top_Bottom_Performers}}</li>"
        "</ul>"
        "<p>Regards,<br/>Operations Team, Eko Bharat Ventures Pvt. Ltd. | BC Code 1A85</p>"
    ),
    "Weekly Loan Lead Generation": (
        "<p>Dear Sir/Ma'am,</p>"
        "<p>Weekly loan lead status:</p>"
        "<ul>"
        "<li>Leads generated: {{Leads_Generated}} | Converted: {{Leads_Converted}}</li>"
        "<li>Lead Type: {{Lead_Type}}</li>"
        "<li>Active lead-generating CSPs: {{Active_Lead_CSPs}} | Non-responders: {{Non_Responders}}</li>"
        "<li>Branch / RBO-wise breakdown: {{Level_Breakdown}}</li>"
        "</ul>"
        "<p>Regards,<br/>Operations Team, Eko Bharat Ventures Pvt. Ltd.</p>"
    ),
    "Weekly Inactive CSP Status": (
        "<p>Dear Sir/Ma'am,</p>"
        "<p>Weekly inactive CSP tracking:</p>"
        "<ul>"
        "<li>Total inactive CSPs: {{Inactive_Count}} | WoW change: {{WoW_Change}}</li>"
        "<li>Newly inactive this week: {{Newly_Inactive}} | Reactivated: {{Reactivated}}</li>"
        "<li>Circle-wise distribution: {{Circle_Distribution}}</li>"
        "<li>Reactivation action plan: {{Action_Plan}}</li>"
        "</ul>"
        "<p>Regards,<br/>Operations Team, Eko Bharat Ventures Pvt. Ltd. | BC Code 1A85</p>"
    ),
    "Monthly DFS Incentive Slab": (
        "<p>Dear Sir/Ma'am,</p>"
        "<p>Monthly DFS incentive slab summary for {{Month_Year}}:</p>"
        "<ul>"
        "<li>CSPs qualifying per slab: {{Slab_Distribution}}</li>"
        "<li>Scheme-wise counts (PMJJBY / PMSBY / APY / PMJDY): {{Scheme_Counts}}</li>"
        "<li>Total incentive earned: {{Total_Incentive}}</li>"
        "<li>MoM comparison: {{MoM_Comparison}}</li>"
        "</ul>"
        "<p>Regards,<br/>Operations Team, Eko Bharat Ventures Pvt. Ltd. | BC Code 1A85</p>"
    ),
    "Monthly CSP Income Impact": (
        "<p>Dear Sir/Ma'am,</p>"
        "<p>Monthly CSP income impact analysis for {{Month_Year}}:</p>"
        "<ul>"
        "<li>Average CSP income (Total Commission ÷ Total CSPs): {{Avg_Income}}</li>"
        "<li>Income streak / stream analysis: {{Income_Analysis}}</li>"
        "<li>CSP score distribution (A/B/C/D): {{CSP_Score_Distribution}}</li>"
        "<li>Month-on-month growth: {{MoM_Growth_Percent}}</li>"
        "</ul>"
        "<p>Regards,<br/>Operations Team, Eko Bharat Ventures Pvt. Ltd. | BC Code 1A85</p>"
    ),
}

# Templates left with their original "[ ]" placeholders (no clean matching
# data in the Calling Sheet yet) — only the sender placeholder is normalized.
NOT_YET_AUTOMATED = (
    "Weekly Server Issue & Downtime",
    "Weekly CSP Physical Camp",
    "Monthly MoM Growth Inputs",
)


def run() -> None:
    init_db()
    with get_db() as db:
        updated, skipped = 0, []

        for name, body in AUTOMATED_BODIES.items():
            t = db.query(EmailTemplate).filter(EmailTemplate.name == name).first()
            if not t:
                skipped.append(name)
                continue
            t.body_html = body
            updated += 1
            print(f"  automated: {name}")

        for name in NOT_YET_AUTOMATED:
            t = db.query(EmailTemplate).filter(EmailTemplate.name == name).first()
            if not t:
                skipped.append(name)
                continue
            t.body_html = t.body_html.replace("[Name]", "Operations Team")
            print(f"  left manual (no clean data source yet): {name}")

    print(f"\nDone: {updated} templates automated, {len(NOT_YET_AUTOMATED)} left manual.")
    if skipped:
        print(f"Not found (run seed_reporting_framework.py first): {skipped}")


if __name__ == "__main__":
    run()
