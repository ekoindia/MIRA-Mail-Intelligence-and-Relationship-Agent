"""Configure REST API report sources: where to auto-download reports from."""
from __future__ import annotations

import streamlit as st

from config import settings
from database.db import get_db
from database.models import ReportMaster
from database.report_source_models import AuthType, HttpMethod, ReportSource
from services.audit_service import log_action
from services.report_source_service import fetch_report, list_recent_runs
from utils.ui import (
    empty_state,
    inject_css,
    page_header,
    render_sidebar_user_box,
    require_login,
    section_card_close,
    section_card_open,
)

st.set_page_config(page_title=f"Report Sources | {settings.app_name}", page_icon="🔌", layout="wide")
user = require_login()
inject_css()
render_sidebar_user_box()

page_header(
    "Report Sources", "Connect a REST API that serves report files, so they can be auto-downloaded and distributed.", "🔌"
)

with get_db() as db:
    report_types = db.query(ReportMaster).filter(ReportMaster.is_active.is_(True)).order_by(ReportMaster.report_name).all()
    report_options = {r.report_name: r.id for r in report_types}
    report_descriptions = {r.report_name: r.description for r in report_types}

with st.expander("➕ Add New Report Source", expanded=not report_options == {}):
    if not report_options:
        st.warning("Create a report type first (see **Report Master**).")
    else:
        with st.form("new_report_source"):
            name = st.text_input("Source Name*", placeholder="e.g. Weekly Sales Report API")
            report_name = st.selectbox(
                "Report Type*", list(report_options.keys()),
                help="Recipients & frequency for each report are set on the Scheduler page — this just wires up where the file comes from.",
            )
            if report_descriptions.get(report_name):
                st.caption(f"Goes to: {report_descriptions[report_name]}")

            c1, c2 = st.columns(2)
            with c1:
                base_url = st.text_input("Base URL*", placeholder="https://internal-dashboard.company.com/api")
                method = st.selectbox("HTTP Method", [m.value for m in HttpMethod])
            with c2:
                endpoint_path = st.text_input(
                    "Endpoint Path", placeholder="/reports/weekly?date={date:%Y-%m-%d}",
                    help="Supports {date}, {yesterday}, {week}, {month} placeholders, optionally with a strftime "
                         "spec e.g. {date:%d-%m-%Y}. Leave blank to hit the base URL directly.",
                )
                filename_template = st.text_input(
                    "Filename to Save As*", placeholder="Weekly_Report_{date:%d-%m-%Y}.xlsx",
                )

            st.markdown("**Authentication**")
            c3, c4, c5 = st.columns(3)
            with c3:
                auth_type = st.selectbox("Auth Type", [a.value for a in AuthType])
            with c4:
                auth_header_name = st.text_input(
                    "Header Name / Username",
                    help="For API Key: the header name (e.g. X-API-Key). For Basic Auth: the username.",
                )
            with c5:
                auth_secret = st.text_input("API Key / Token / Password", type="password")

            submitted = st.form_submit_button("Create Report Source", type="primary")

        if submitted:
            if not name.strip() or not base_url.strip() or not filename_template.strip():
                st.error("Source Name, Base URL and Filename are required.")
            else:
                with get_db() as db:
                    if db.query(ReportSource).filter(ReportSource.name == name.strip()).first():
                        st.error("A report source with this name already exists.")
                    else:
                        src = ReportSource(
                            name=name.strip(),
                            report_master_id=report_options[report_name],
                            base_url=base_url.strip(),
                            http_method=HttpMethod(method),
                            endpoint_path_template=endpoint_path.strip() or None,
                            auth_type=AuthType(auth_type),
                            auth_header_name=auth_header_name.strip() or None,
                            auth_secret=auth_secret or None,
                            filename_template=filename_template.strip(),
                            created_by=user["id"],
                        )
                        db.add(src)
                        db.flush()
                        log_action(db, "CREATE_REPORT_SOURCE", user_id=user["id"], username=user["username"],
                                   entity_type="ReportSource", entity_id=src.id, details=name)
                        st.success(f"Report source '{name}' created.")
                        st.rerun()

st.markdown("### Configured Report Sources")
with get_db() as db:
    sources = db.query(ReportSource).order_by(ReportSource.name).all()

    if not sources:
        empty_state("No report sources configured yet", "Use the form above to connect a REST API.", "🔌")

    for src in sources:
        section_card_open()
        c1, c2, c3, c4 = st.columns([3, 3, 2, 2])
        with c1:
            st.markdown(f"**{src.name}**")
            st.caption(f"{src.report_master.report_name} · {src.http_method.value} · {src.auth_type.value}")
            if src.report_master.description:
                st.caption(f"→ {src.report_master.description}")
        with c2:
            st.code(f"{src.base_url}{src.endpoint_path_template or ''}", language=None)
            st.caption(f"Saves as: {src.filename_template}")
        with c3:
            if st.button("🧪 Test Fetch Now", key=f"test_{src.id}", use_container_width=True):
                with st.spinner("Fetching…"):
                    with get_db() as db2:
                        record = db2.query(ReportSource).get(src.id)
                        result = fetch_report(db2, record, triggered_by="manual")
                if result["success"]:
                    st.success(f"Downloaded '{result['run'].resolved_filename}' successfully.")
                else:
                    st.error(f"Fetch failed: {result['error']}")
        with c4:
            toggle_label = "Deactivate" if src.is_active else "Activate"
            if st.button(toggle_label, key=f"toggle_src_{src.id}", use_container_width=True):
                with get_db() as db2:
                    record = db2.query(ReportSource).get(src.id)
                    record.is_active = not record.is_active
                st.rerun()

        recent = list_recent_runs(db, src.id, limit=5)
        if recent:
            with st.expander(f"Recent runs ({len(recent)})"):
                for r in recent:
                    icon = "✅" if r.status.value == "Success" else "❌"
                    st.caption(
                        f"{icon} {r.run_at.strftime('%d-%b-%Y %H:%M')} · {r.triggered_by} · "
                        f"{r.resolved_filename or '-'}" + (f" · {r.error}" if r.error else "")
                    )
        section_card_close()
