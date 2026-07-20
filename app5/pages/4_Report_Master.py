"""Configure report types: name, description, recipient type.
Template assignment is managed from the Templates page (a template can map
to many reports at once), so this page shows the current mapping read-only
with a shortcut link.
"""
from __future__ import annotations

import streamlit as st

from config import settings
from database.db import get_db
from database.models import ReportMaster
from services.audit_service import log_action
from utils.ui import inject_css, page_header, render_sidebar_user_box, require_login, section_card_open, section_card_close, empty_state

st.set_page_config(page_title=f"Report Master | {settings.app_name}", page_icon="🗂️", layout="wide")
user = require_login()
inject_css()
render_sidebar_user_box()

page_header("Report Master", "Define the report types your organization distributes.", "🗂️", eyebrow="Configuration")

with st.expander("➕ Add New Report Type", expanded=False):
    with st.form("new_report_type"):
        name = st.text_input("Report Name*")
        description = st.text_area("Description")
        recipient_type = st.selectbox("Recipient Type*", ["Branch", "LHO", "Both"])
        st.caption("You can attach an email template to this report afterwards from the **Templates** page.")
        submitted = st.form_submit_button("Create Report Type", type="primary")

    if submitted:
        if not name.strip():
            st.error("Report Name is required.")
        else:
            try:
                with get_db() as db:
                    if db.query(ReportMaster).filter(ReportMaster.report_name == name.strip()).first():
                        raise ValueError("A report type with this name already exists.")
                    rm = ReportMaster(
                        report_name=name.strip(),
                        description=description.strip(),
                        recipient_type=recipient_type,
                    )
                    db.add(rm)
                    db.flush()
                    log_action(db, "CREATE_REPORT_TYPE", user_id=user["id"], username=user["username"],
                               entity_type="ReportMaster", entity_id=rm.id, details=name)
                st.success(f"Report type '{name}' created.")
                st.rerun()
            except ValueError as e:
                st.error(str(e))

st.markdown("##### Configured report types")
with get_db() as db:
    report_types = db.query(ReportMaster).order_by(ReportMaster.report_name).all()

    if not report_types:
        empty_state("No report types configured yet", "Use the form above to create one.", "🗂️")

    for rt in report_types:
        section_card_open()
        c1, c2, c3 = st.columns([3, 2, 1])
        with c1:
            st.markdown(f"**{rt.report_name}**")
            st.caption(rt.description or "No description")
        with c2:
            st.markdown(f"Recipients: `{rt.recipient_type.value}`")
            if rt.default_template:
                st.caption(f"Template: {rt.default_template.name}")
            else:
                st.caption("Template: not attached — set this in **Templates**")
        with c3:
            toggle_label = "Deactivate" if rt.is_active else "Activate"
            if st.button(toggle_label, key=f"toggle_{rt.id}", use_container_width=True):
                with get_db() as db2:
                    record = db2.query(ReportMaster).get(rt.id)
                    record.is_active = not record.is_active
                    log_action(db2, "EDIT_REPORT_TYPE", user_id=user["id"], username=user["username"],
                               entity_type="ReportMaster", entity_id=rt.id,
                               details=f"is_active={record.is_active}")
                st.rerun()
        section_card_close()

if report_types and st.button("✉️ Manage template mappings"):
    st.switch_page("pages/6_Templates.py")
