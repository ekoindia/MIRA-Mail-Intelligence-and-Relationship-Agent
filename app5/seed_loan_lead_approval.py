"""
One-off seeder for the new "Loan Lead Approval (Daily)" report — creates
its EmailTemplate, ReportMaster (delivery_mode="draft", per explicit
instruction to keep this in testing/draft-only for now) and ReportSource
(same Calling Sheet the other Daily/Weekly reports already read from).
Idempotent: safe to re-run, skips whatever already exists.

Run with: venv/Scripts/python.exe seed_loan_lead_approval.py
"""
from __future__ import annotations

from database.db import get_db
from database.models import EmailTemplate, ReportMaster, RecipientType, User
from database.report_source_models import ReportSource

REPORT_NAME = "Loan Lead Approval (Daily)"
TEMPLATE_NAME = "Daily Loan Lead Update"

# Same design system as every other automated report (table layout, inline
# styles only — see CLAUDE.md's Email templates section) with its own
# accent (indigo) so it reads as visually distinct from the existing
# orange "Loan Lead Generation" report it sits alongside. Only one
# {{#if}} level, per the template engine's hard limit.
BODY_HTML = (
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
    'style="max-width:600px;margin:0 auto;background:#ffffff;border:1px solid #e9e2d9;'
    'border-radius:12px;overflow:hidden;font-family:\'Segoe UI\',Helvetica,Arial,sans-serif;">'
    '<tr><td style="background:linear-gradient(135deg,#4338ca 0%,#3730a3 55%,#27216f 100%);padding:26px 28px;">'
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr><td valign="top">'
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
    '<tr><td style="font-size:11px;font-weight:700;letter-spacing:0.1em;text-transform:uppercase;'
    'color:#dfe1fb;padding-bottom:7px;">Eko Bharat Ventures &middot; Daily Report</td></tr>'
    '<tr><td style="font-size:22px;font-weight:800;color:#ffffff;line-height:1.3;">Loan Lead Approval</td></tr>'
    '<tr><td style="font-size:12px;font-weight:700;color:#ffffff;padding-top:6px;">Branch: {{Branch_Name}}</td></tr>'
    '<tr><td style="font-size:13px;color:#dfe1fb;padding-top:2px;">New leads as on {{Previous_Date}}</td></tr>'
    '</table></td></tr></table></td></tr>'
    '<tr><td style="padding:22px 26px 4px;"><p style="margin:0;font-size:14px;line-height:1.6;color:#312b26;">'
    "Dear Ma'am/Sir, on {{Previous_Date}}, new loan leads have been generated. "
    'Please review and get them approved.</p></td></tr>'
    '{{#if Has_New_Leads}}'
    '<tr><td style="padding:16px 20px 8px;"><table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
    '<tr>'
    '<td width="50%" valign="top" style="padding:6px;">'
    '<a href="{{Base_URL}}/report-detail?token={{Tracking_Token}}&mode=new_leads" '
    'style="text-decoration:none;color:inherit;display:block;" target="_blank">'
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
    'style="background:#ffffff;border:1px solid #cbd0f5;border-radius:10px;">'
    '<tr><td style="height:4px;background:#3730a3;border-radius:10px 10px 0 0;line-height:4px;font-size:0;">&nbsp;</td></tr>'
    '<tr><td style="padding:12px 12px 2px;font-size:10px;font-weight:800;letter-spacing:0.05em;'
    'text-transform:uppercase;color:#3730a3;">New Loan Leads Generated</td></tr>'
    '<tr><td style="padding:0 12px;font-size:28px;font-weight:800;color:#3730a3;'
    'font-variant-numeric:tabular-nums;line-height:1.1;">{{New_Leads_Count}}</td></tr>'
    '<tr><td style="padding:2px 12px 12px;font-size:10px;color:#7a6f64;">since last report &middot; '
    '{{CSPs_With_New_Leads}} CSP(s)</td></tr>'
    '</table></a></td>'
    '<td width="50%" valign="top" style="padding:6px;">'
    '<a href="{{Base_URL}}/report-detail?token={{Tracking_Token}}&mode=new_leads" '
    'style="text-decoration:none;color:inherit;display:block;" target="_blank">'
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
    'style="background:#ffffff;border:1px solid #c7d3f2;border-radius:10px;">'
    '<tr><td style="height:4px;background:#2452c0;border-radius:10px 10px 0 0;line-height:4px;font-size:0;">&nbsp;</td></tr>'
    '<tr><td style="padding:12px 12px 2px;font-size:10px;font-weight:800;letter-spacing:0.05em;'
    'text-transform:uppercase;color:#2452c0;">Lead Types</td></tr>'
    '<tr><td style="padding:0 12px 12px;font-size:13px;font-weight:700;color:#312b26;line-height:1.4;">'
    '{{New_Lead_Type}}</td></tr>'
    '</table></a></td>'
    '</tr></table></td></tr>'
    '<tr><td style="padding:0 26px 12px;"><p style="margin:0;font-size:11px;color:#a89a8c;text-align:center;">'
    'Tap a card above for CSP-wise detail &mdash; which CSP generated each lead.</p></td></tr>'
    '{{/if}}'
    '<tr><td style="height:10px;line-height:10px;font-size:0;">&nbsp;</td></tr>'
    '</table>'
)


def main() -> None:
    with get_db() as db:
        template = db.query(EmailTemplate).filter(EmailTemplate.name == TEMPLATE_NAME).first()
        if template is None:
            template = EmailTemplate(
                name=TEMPLATE_NAME,
                subject="New Loan Leads for Approval | {{Previous_Date}}",
                body_html=BODY_HTML,
                is_default=False,
            )
            db.add(template)
            db.flush()
            print(f"Created EmailTemplate id={template.id}")
        else:
            template.body_html = BODY_HTML
            template.subject = "New Loan Leads for Approval | {{Previous_Date}}"
            print(f"Updated existing EmailTemplate id={template.id}")

        rm = db.query(ReportMaster).filter(ReportMaster.report_name == REPORT_NAME).first()
        if rm is None:
            rm = ReportMaster(
                report_name=REPORT_NAME,
                description=(
                    "Branch-level daily approval request for newly generated loan leads "
                    "since the last report — a day-over-day delta, not the plain MTD total "
                    "shown by Loan Lead Generation."
                ),
                recipient_type=RecipientType.BRANCH,
                frequency="Daily",
                org_levels="Branch",
                delivery_mode="draft",  # testing phase — never auto-sends, per explicit instruction
                default_template_id=template.id,
            )
            db.add(rm)
            db.flush()
            print(f"Created ReportMaster id={rm.id}")
        else:
            rm.default_template_id = template.id
            rm.delivery_mode = "draft"
            print(f"Found existing ReportMaster id={rm.id} (left as-is aside from template link + draft mode)")

        source = db.query(ReportSource).filter(ReportSource.report_master_id == rm.id).first()
        if source is None:
            # Same Calling Sheet every other automated report reads from —
            # see any existing row in report_sources for this exact shape.
            existing_any = db.query(ReportSource).filter(ReportSource.source_type == "GOOGLE_SHEET").first()
            if existing_any is None:
                raise RuntimeError(
                    "No existing GOOGLE_SHEET ReportSource found to copy the sheet ID/tab from — "
                    "set google_sheet_id/google_sheet_tab manually below."
                )
            # created_by feeds report_uploads.uploaded_by (NOT NULL) on every
            # auto-fetch this source triggers (see sheet_source_service.
            # fetch_sheet_report) — leaving it unset silently breaks the
            # very first fetch with an IntegrityError, not at seed time.
            admin = db.query(User).filter(User.username == "admin").first()
            source = ReportSource(
                name=f"Calling Sheet — {REPORT_NAME}",
                report_master_id=rm.id,
                source_type="GOOGLE_SHEET",
                http_method="GET",
                auth_type="NONE",
                filename_template="{name}_{date}.xlsx",
                google_sheet_id=existing_any.google_sheet_id,
                google_sheet_tab=existing_any.google_sheet_tab,
                is_active=True,
                created_by=admin.id if admin else None,
            )
            db.add(source)
            db.flush()
            print(f"Created ReportSource id={source.id}")
        else:
            print(f"ReportSource already exists id={source.id}")

        db.commit()
        print("Done.")


if __name__ == "__main__":
    main()
