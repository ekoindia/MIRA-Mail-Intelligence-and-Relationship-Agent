"""
Templates — single home for reusable email templates.

A template is defined once (Template Name + Subject + Body with variables)
and can be attached to one or many report types at the same time, since the
same wording is frequently reused across different reports (e.g. all three
"Daily RBO" reports share one template). Mapping is many-report -> one-template,
managed entirely from this page.
"""
from __future__ import annotations

import streamlit as st

from config import settings
from database.db import get_db
from database.models import EmailTemplate, ReportMaster
from services.audit_service import log_action
from utils.helpers import SUPPORTED_TEMPLATE_VARS, render_template
from utils.ui import (
    inject_css, page_header, render_sidebar_user_box, require_login,
    section_card_open, section_card_close, tag_pills, empty_state, toast_success,
)

st.set_page_config(page_title=f"Templates | {settings.app_name}", page_icon="✉️", layout="wide")
user = require_login()
inject_css()
render_sidebar_user_box()

page_header(
    "Templates",
    "Define reusable email templates once, then attach them to every report that should use the same wording.",
    "✉️",
    eyebrow="Configuration",
)

with get_db() as db:
    templates = db.query(EmailTemplate).order_by(EmailTemplate.name).all()
    template_map = {t.name: t for t in templates}
    report_types = db.query(ReportMaster).order_by(ReportMaster.report_name).all()
    report_options = {r.report_name: r.id for r in report_types}
    # which reports currently point at which template
    reports_by_template: dict[int, list[str]] = {}
    for r in report_types:
        if r.default_template_id:
            reports_by_template.setdefault(r.default_template_id, []).append(r.report_name)

tab_manage, tab_edit = st.tabs(["📚 All Templates", "✏️ Template Name & Content"])

# ------------------------------------------------------------------
# Tab 1 — overview of every template and what it's wired to
# ------------------------------------------------------------------
with tab_manage:
    if not templates:
        empty_state(
            "No templates yet",
            "Create your first template in the 'Template Name & Content' tab.",
            "✉️",
        )
    for t in templates:
        mapped_reports = reports_by_template.get(t.id, [])
        section_card_open()
        c1, c2 = st.columns([4, 1.3])
        with c1:
            star = " ⭐ Default" if t.is_default else ""
            st.markdown(f"**{t.name}**{star}")
            st.caption(t.subject)
            if mapped_reports:
                st.markdown(
                    "Used by report(s): " + tag_pills(mapped_reports),
                    unsafe_allow_html=True,
                )
            else:
                st.caption("Not attached to any report yet.")
        with c2:
            st.caption(f"Updated {t.updated_at.strftime('%d-%b-%Y')}")
            if st.button("Edit", key=f"edit_{t.id}", use_container_width=True):
                st.session_state["template_edit_target"] = t.name
                st.toast(f"'{t.name}' loaded — switch to the ✏️ Template Name & Content tab above to edit it.", icon="✏️")
                st.rerun()
        section_card_close()

# ------------------------------------------------------------------
# Tab 2 — Template Name, applicable reports, and template content
# ------------------------------------------------------------------
with tab_edit:
    default_choice = st.session_state.pop("template_edit_target", "-- New Template --")
    choices = ["-- New Template --"] + list(template_map.keys())
    edit_choice = st.selectbox(
        "Load existing template to edit (optional)",
        choices,
        index=choices.index(default_choice) if default_choice in choices else 0,
    )
    existing = template_map.get(edit_choice)
    existing_mapped = reports_by_template.get(existing.id, []) if existing else []

    # Keying every editable widget by `edit_choice` (the selected template's
    # name, or the sentinel for a new template) is essential: without a key
    # tied to the selection, Streamlit reuses the same widget state across
    # every template you pick, so switching templates in the dropdown above
    # never actually refreshes what's shown in the edit boxes below — it
    # keeps displaying whatever was first typed, making editing feel broken.
    widget_scope = edit_choice

    st.write("")
    section_card_open("Template Name")
    st.caption("A short, recognizable identifier for this template — you'll pick it by name elsewhere in the app.")
    name = st.text_input(
        "Template Name*",
        value=existing.name if existing else "",
        placeholder="e.g. Daily RBO Update, Monthly LHO Summary",
        label_visibility="collapsed",
        key=f"tpl_name_{widget_scope}",
    )
    section_card_close()

    section_card_open("Applies to Reports")
    st.caption(
        "Select every report this template should be used for. The same template can serve "
        "multiple reports — e.g. Social Security Scheme, Account Opening, and Re-KYC can all "
        "share one Daily RBO template."
    )
    applicable_reports = st.multiselect(
        "Applies to Reports",
        list(report_options.keys()),
        default=existing_mapped,
        placeholder="Choose one or more report types...",
        label_visibility="collapsed",
        key=f"tpl_reports_{widget_scope}",
    )
    if not report_options:
        st.info("No report types exist yet. Create them first in **Report Master**, then come back to map them here.")
    section_card_close()

    section_card_open("Template")
    st.caption("Available variables: " + " ".join(f"`{{{{{v}}}}}`" for v in SUPPORTED_TEMPLATE_VARS))
    subject = st.text_input(
        "Subject*",
        value=existing.subject if existing else "{{Report_Name}} - Report ({{Date}})",
        key=f"tpl_subject_{widget_scope}",
    )
    body = st.text_area(
        "Body (HTML supported)",
        value=existing.body_html if existing else (
            "<p>Dear {{Recipient_Name}},</p>\n"
            "<p>Please find attached the <b>{{Report_Name}}</b> report dated {{Date}}.</p>\n"
            "<p>Branch: {{Branch_Name}} &nbsp; LHO: {{LHO_Name}}</p>\n"
            "<p>Regards,<br/>Reports Distribution Team</p>"
        ),
        height=220,
        key=f"tpl_body_{widget_scope}",
    )
    is_default = st.checkbox(
        "Set as global fallback template (used when a report has no template attached)",
        value=existing.is_default if existing else False,
        key=f"tpl_default_{widget_scope}",
    )
    section_card_close()

    col1, col2, _ = st.columns([1, 1, 3])
    with col1:
        save_clicked = st.button("💾 Save Template", type="primary", use_container_width=True)
    with col2:
        delete_clicked = existing and st.button("🗑️ Delete Template", use_container_width=True)

    if save_clicked:
        if not name.strip() or not subject.strip():
            st.error("Template Name and Subject are required.")
        else:
            with get_db() as db:
                if is_default:
                    db.query(EmailTemplate).update({EmailTemplate.is_default: False})
                if existing:
                    record = db.query(EmailTemplate).get(existing.id)
                    record.name, record.subject, record.body_html, record.is_default = (
                        name.strip(), subject, body, is_default,
                    )
                    action = "EDIT_TEMPLATE"
                else:
                    record = EmailTemplate(
                        name=name.strip(), subject=subject, body_html=body, is_default=is_default,
                        created_by=user["id"],
                    )
                    db.add(record)
                    db.flush()
                    action = "CREATE_TEMPLATE"

                # Re-wire report -> template mapping: attach selected reports,
                # detach any previously-mapped report that was deselected.
                selected_ids = {report_options[r] for r in applicable_reports}
                previously_mapped_ids = {report_options[r] for r in existing_mapped if r in report_options}
                for rid in previously_mapped_ids - selected_ids:
                    rm = db.query(ReportMaster).get(rid)
                    if rm:
                        rm.default_template_id = None
                for rid in selected_ids:
                    rm = db.query(ReportMaster).get(rid)
                    if rm:
                        rm.default_template_id = record.id

                log_action(
                    db, action, user_id=user["id"], username=user["username"],
                    entity_type="EmailTemplate", entity_id=record.id,
                    details=f"{name} -> [{', '.join(applicable_reports) or 'no reports'}]",
                )
            toast_success(f"Template '{name}' saved and mapped to {len(applicable_reports)} report(s).")
            st.rerun()

    if delete_clicked:
        with get_db() as db:
            db.query(ReportMaster).filter(ReportMaster.default_template_id == existing.id).update(
                {ReportMaster.default_template_id: None}
            )
            db.query(EmailTemplate).filter(EmailTemplate.id == existing.id).delete()
            log_action(db, "DELETE_TEMPLATE", user_id=user["id"], username=user["username"],
                       entity_type="EmailTemplate", entity_id=existing.id, details=existing.name)
        toast_success("Template deleted.")
        st.rerun()

    st.markdown("##### Live preview")
    sample_context = {
        "Recipient_Name": "Rohit Sharma", "Branch_Name": "Connaught Place Branch",
        "RBO_Name": "Lucknow RBO", "AO_Name": "Lucknow AO", "LHO_Name": "Delhi LHO",
        "Corp_Name": "Corporate Center", "Report_Name": "Weekly Sales Report", "Date": "13-Jul-2026",
        "Week_Number": "29", "Week_Start": "13", "Week_End": "19 Jul 2026", "Month_Year": "July 2026",
    }
    section_card_open()
    st.markdown(f"**Subject:** {render_template(subject, sample_context)}")
    st.markdown("<hr class='divider-tight'>", unsafe_allow_html=True)
    st.markdown(render_template(body, sample_context), unsafe_allow_html=True)
    section_card_close()
