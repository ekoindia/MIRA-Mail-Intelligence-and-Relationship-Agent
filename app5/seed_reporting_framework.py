"""
One-time (idempotent) seed script: creates the ReportMaster + EmailTemplate
rows exactly matching "Scheduled Outgoing Mail – Reporting Framework.docx"
(Eko Bharat Ventures Pvt. Ltd.) — the 13 report x frequency x recipient-level
rows in Part 1, wired to the 9 email templates in Part 2.

Safe to re-run: matches existing rows by name and updates them instead of
duplicating. Run with:

    python seed_reporting_framework.py

After running, go to Report Sources to point each report at the REST API
it should be downloaded from, and Scheduler to set its frequency + which
org level(s) it sends to (already reflected in each report's description
below, taken directly from the framework document).

Note: bracketed metric placeholders (e.g. "Target vs. achievement: [ ]%")
are kept verbatim from the source document — the platform does not yet
auto-extract these figures from the report file itself. Fill them in before
sending, or ask for automatic extraction once real report-file samples are
available.
"""
from __future__ import annotations

from database.db import get_db, init_db
from database.models import EmailTemplate, ReportMaster, RecipientType
from database.org_models import OrgLevel

# ----------------------------------------------------------------------
# Part 2 — Mail Templates (verbatim from the framework document, HTML body)
# ----------------------------------------------------------------------
TEMPLATES = {
    "Daily RBO Update": {
        "subject": "{{Report_Name}} – Daily Update | {{Date}} | RBO {{RBO_Name}}",
        "body_html": (
            "<p>Dear Sir/Ma'am,</p>"
            "<p>Please find below the daily {{Report_Name}} status for RBO {{RBO_Name}} as on {{Date}}.</p>"
            "<ul>"
            "<li>Target vs. achievement: [ ]%</li>"
            "<li>MTD &amp; FTD achievement: [ ]</li>"
            "<li>Top-performing CSPs: [ ]</li>"
            "<li>CSPs requiring follow-up: [ ]</li>"
            "</ul>"
            "<p>Detailed CSP-wise data is attached.</p>"
            "<p>Regards,<br/>[Name] | Operations, Eko Bharat Ventures Pvt. Ltd.</p>"
        ),
    },
    "Weekly Server Issue & Downtime": {
        "subject": "Server Issue & Downtime Impact – Weekly | Week {{Week_Number}}, {{Month_Year}}",
        "body_html": (
            "<p>Dear Sir/Ma'am,</p>"
            "<p>Summary of server / downtime incidents for the week {{Week_Start}}–{{Week_End}}:</p>"
            "<ul>"
            "<li>Total downtime incidents: [ ]</li>"
            "<li>Cumulative downtime hours: [ ]</li>"
            "<li>Estimated transaction / business impact: [ ]</li>"
            "<li>Circles affected: [ ]</li>"
            "<li>Resolution status / pending escalations: [ ]</li>"
            "</ul>"
            "<p>Detailed incident log attached.</p>"
            "<p>Regards,<br/>[Name] | Operations, Eko Bharat Ventures Pvt. Ltd.</p>"
        ),
    },
    "Weekly CSP Physical Camp": {
        "subject": "CSP Physical Camp – Weekly Report | Week {{Week_Number}}, {{Month_Year}}",
        "body_html": (
            "<p>Dear Sir/Ma'am,</p>"
            "<p>Weekly summary of physical camps conducted (Re-KYC / Inoperative Activation / SSSA):</p>"
            "<ul>"
            "<li>Camps held: [ ] | Locations: [ ]</li>"
            "<li>Accounts serviced: [ ]</li>"
            "<li>Enrollments generated: [ ]</li>"
            "<li>Upcoming camps (next week): [ ]</li>"
            "</ul>"
            "<p>Photo documentation and location-wise data attached.</p>"
            "<p>Regards,<br/>[Name] | Operations, Eko Bharat Ventures Pvt. Ltd.</p>"
        ),
    },
    "Weekly Consolidated RBO/LHO": {
        "subject": "{{Report_Name}} – Weekly Consolidated | Week {{Week_Number}}, {{Month_Year}}",
        "body_html": (
            "<p>Dear Sir/Ma'am,</p>"
            "<p>Consolidated weekly performance for {{Report_Name}}:</p>"
            "<ul>"
            "<li>Weekly total: [ ] | WoW change: [ ]%</li>"
            "<li>MTD cumulative: [ ]</li>"
            "<li>RBO-wise / LHO-wise breakdown: [ ]</li>"
            "<li>Top and bottom performers: [ ]</li>"
            "</ul>"
            "<p>Detailed sheet attached.</p>"
            "<p>Regards,<br/>[Name] | Operations, Eko Bharat Ventures Pvt. Ltd. | BC Code 1A85</p>"
        ),
    },
    "Weekly Loan Lead Generation": {
        "subject": "Loan Lead Generation – Weekly Report | Week {{Week_Number}}, {{Month_Year}}",
        "body_html": (
            "<p>Dear Sir/Ma'am,</p>"
            "<p>Weekly loan lead status:</p>"
            "<ul>"
            "<li>Leads generated: [ ] | Converted: [ ]</li>"
            "<li>Lead Type: [ ]</li>"
            "<li>Active lead-generating CSPs: [ ] | Non-responders: [ ]</li>"
            "<li>Branch / RBO-wise breakdown: [ ]</li>"
            "</ul>"
            "<p>Detailed lead data attached.</p>"
            "<p>Regards,<br/>[Name] | Operations, Eko Bharat Ventures Pvt. Ltd.</p>"
        ),
    },
    "Weekly Inactive CSP Status": {
        "subject": "Inactive CSP Status – Weekly | Week {{Week_Number}}, {{Month_Year}}",
        "body_html": (
            "<p>Dear Sir/Ma'am,</p>"
            "<p>Weekly inactive CSP tracking:</p>"
            "<ul>"
            "<li>Total inactive CSPs: [ ] | WoW change: [ ]</li>"
            "<li>Newly inactive this week: [ ] | Reactivated: [ ]</li>"
            "<li>Circle-wise distribution: [ ]</li>"
            "<li>Reactivation action plan: [ ]</li>"
            "</ul>"
            "<p>Detailed list attached.</p>"
            "<p>Regards,<br/>[Name] | Operations, Eko Bharat Ventures Pvt. Ltd. | BC Code 1A85</p>"
        ),
    },
    "Monthly DFS Incentive Slab": {
        "subject": "DFS Incentive Slab Achievement – {{Month_Year}}",
        "body_html": (
            "<p>Dear Sir/Ma'am,</p>"
            "<p>Monthly DFS incentive slab summary for {{Month_Year}}:</p>"
            "<ul>"
            "<li>CSPs qualifying per slab: [ ]</li>"
            "<li>Scheme-wise counts (PMJJBY / PMSBY / APY / PMJDY): [ ]</li>"
            "<li>Total incentive earned: [ ]</li>"
            "<li>MoM comparison: [ ]</li>"
            "</ul>"
            "<p>Detailed slab-wise data attached.</p>"
            "<p>Regards,<br/>[Name] | Operations, Eko Bharat Ventures Pvt. Ltd. | BC Code 1A85</p>"
        ),
    },
    "Monthly CSP Income Impact": {
        "subject": "CSP Income Impact Analysis – {{Month_Year}}",
        "body_html": (
            "<p>Dear Sir/Ma'am,</p>"
            "<p>Monthly CSP income impact analysis for {{Month_Year}}:</p>"
            "<ul>"
            "<li>Average CSP income (Total Commission ÷ Total CSPs): [ ]</li>"
            "<li>Income streak / stream analysis: [ ]</li>"
            "<li>CSP score distribution (A/B/C/D): [ ]</li>"
            "<li>Month-on-month growth: [ ]%</li>"
            "</ul>"
            "<p>Detailed analysis attached.</p>"
            "<p>Regards,<br/>[Name] | Operations, Eko Bharat Ventures Pvt. Ltd. | BC Code 1A85</p>"
        ),
    },
    "Monthly MoM Growth Inputs": {
        "subject": "MoM Growth – Input Metrics | {{Month_Year}}",
        "body_html": (
            "<p>Dear Sir/Ma'am,</p>"
            "<p>Key input drivers for month-on-month growth, {{Month_Year}}:</p>"
            "<ul>"
            "<li>New CSP additions: [ ] | Migrations: [ ]</li>"
            "<li>Component-wise commission contribution: [ ]</li>"
            "<li>Growth levers and identified gaps: [ ]</li>"
            "<li>Recommended focus areas (next month): [ ]</li>"
            "</ul>"
            "<p>Detailed data attached.</p>"
            "<p>Regards,<br/>[Name] | Operations, Eko Bharat Ventures Pvt. Ltd. | BC Code 1A85</p>"
        ),
    },
}

# ----------------------------------------------------------------------
# Part 1 — Reporting Distribution Matrix
# (report_name, frequency, org_levels[OrgLevel enum values], template name)
# ----------------------------------------------------------------------
REPORTS = [
    ("Social Security Scheme (Daily)", "Daily", [OrgLevel.RBO], "Daily RBO Update"),
    ("Account Opening (Daily)", "Daily", [OrgLevel.RBO], "Daily RBO Update"),
    ("Re-KYC & Inoperative Accounts (Daily)", "Daily", [OrgLevel.RBO], "Daily RBO Update"),

    ("Server Issue (Weekly)", "Weekly", [OrgLevel.LHO, OrgLevel.CORP], "Weekly Server Issue & Downtime"),
    ("CSP Physical Camp (Weekly)", "Weekly", [OrgLevel.RBO, OrgLevel.LHO, OrgLevel.CORP], "Weekly CSP Physical Camp"),
    ("Social Security Scheme (Weekly)", "Weekly", [OrgLevel.RBO, OrgLevel.LHO], "Weekly Consolidated RBO/LHO"),
    ("Account Opening (Weekly)", "Weekly", [OrgLevel.RBO, OrgLevel.LHO], "Weekly Consolidated RBO/LHO"),
    ("Re-KYC & Inoperative Accounts (Weekly)", "Weekly", [OrgLevel.RBO, OrgLevel.LHO], "Weekly Consolidated RBO/LHO"),
    ("Loan Lead Generation (Weekly)", "Weekly", [OrgLevel.BRANCH, OrgLevel.RBO, OrgLevel.LHO, OrgLevel.CORP], "Weekly Loan Lead Generation"),
    ("Inactive CSPs (Weekly)", "Weekly", [OrgLevel.LHO, OrgLevel.CORP], "Weekly Inactive CSP Status"),

    ("DFS Incentive Slab (Monthly)", "Monthly", [OrgLevel.LHO, OrgLevel.CORP], "Monthly DFS Incentive Slab"),
    ("CSP Income Impact (Monthly)", "Monthly", [OrgLevel.LHO, OrgLevel.CORP], "Monthly CSP Income Impact"),
    ("Inputs for Month-on-Month Growth (Monthly)", "Monthly", [OrgLevel.LHO, OrgLevel.CORP], "Monthly MoM Growth Inputs"),
]


def run() -> None:
    init_db()
    with get_db() as db:
        template_ids: dict[str, int] = {}
        for name, spec in TEMPLATES.items():
            existing = db.query(EmailTemplate).filter(EmailTemplate.name == name).first()
            if existing:
                existing.subject = spec["subject"]
                existing.body_html = spec["body_html"]
                template_ids[name] = existing.id
                print(f"  updated template: {name}")
            else:
                t = EmailTemplate(name=name, subject=spec["subject"], body_html=spec["body_html"])
                db.add(t)
                db.flush()
                template_ids[name] = t.id
                print(f"  created template: {name}")

        for report_name, frequency, org_levels, template_name in REPORTS:
            org_levels_csv = ",".join(l.value for l in org_levels)
            description = f"{', '.join(l.value for l in org_levels)} — {frequency}"
            existing = db.query(ReportMaster).filter(ReportMaster.report_name == report_name).first()
            if existing:
                existing.description = description
                existing.frequency = frequency
                existing.org_levels = org_levels_csv
                existing.default_template_id = template_ids[template_name]
                print(f"  updated report:   {report_name}")
            else:
                db.add(ReportMaster(
                    report_name=report_name,
                    description=description,
                    frequency=frequency,
                    org_levels=org_levels_csv,
                    recipient_type=RecipientType.BOTH,  # legacy field, unused by org-level auto-distribution
                    default_template_id=template_ids[template_name],
                ))
                print(f"  created report:   {report_name}")

    print(f"\nDone: {len(TEMPLATES)} templates, {len(REPORTS)} report types.")
    print("Next: Report Sources -> connect each report's source API. "
          "Scheduler -> set frequency + org level(s) per the description above.")


if __name__ == "__main__":
    run()
