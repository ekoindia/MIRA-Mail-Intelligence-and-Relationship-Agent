"""Upload weekly report files (PDF/XLSX), validated and tied to a report type."""
from __future__ import annotations

import streamlit as st

from config import settings
from database.db import get_db
from database.models import ReportMaster, ReportUpload
from services.upload_service import UploadError, save_report_file
from utils.helpers import format_bytes
from utils.ui import inject_css, page_header, render_sidebar_user_box, require_login

st.set_page_config(page_title=f"Upload Reports | {settings.app_name}", page_icon="📤", layout="wide")
user = require_login()
inject_css()
render_sidebar_user_box()

page_header("Upload Weekly Reports", "Upload PDF or Excel report files for automatic distribution.", "📤")

with get_db() as db:
    report_types = db.query(ReportMaster).filter(ReportMaster.is_active.is_(True)).order_by(ReportMaster.report_name).all()
    report_options = {r.report_name: r.id for r in report_types}

if not report_options:
    st.warning("No report types configured yet. Go to **Report Master** to create one first.")
    st.stop()

col1, col2 = st.columns([2, 1])
with col1:
    selected_report = st.selectbox("Report Type", list(report_options.keys()))
with col2:
    st.markdown("&nbsp;")
    allowed = ", ".join(settings.allowed_report_extensions)
    st.caption(f"Allowed: {allowed} · Max {settings.max_upload_size_mb} MB each")

uploaded_files = st.file_uploader(
    "Drop report files here (multiple allowed)",
    type=[e.lstrip(".") for e in settings.allowed_report_extensions],
    accept_multiple_files=True,
)

if uploaded_files:
    st.markdown(f"**{len(uploaded_files)} file(s) selected:**")
    for f in uploaded_files:
        st.write(f"• {f.name} — {format_bytes(f.size)}")

    if st.button("Upload All", type="primary"):
        report_master_id = report_options[selected_report]
        successes, failures = [], []
        with get_db() as db:
            for f in uploaded_files:
                try:
                    upload = save_report_file(db, report_master_id, f, user["id"], user["username"])
                    successes.append(upload.file_name)
                except UploadError as e:
                    failures.append((f.name, str(e)))

        if successes:
            st.success(f"Uploaded successfully: {', '.join(successes)}")
        for name, err in failures:
            st.error(f"{name}: {err}")
        if successes:
            st.info("Go to **Distribution** to preview recipients and send this report.")

st.markdown("### Recent Uploads")
with get_db() as db:
    recent = (
        db.query(ReportUpload)
        .join(ReportMaster)
        .order_by(ReportUpload.uploaded_at.desc())
        .limit(20)
        .all()
    )
    rows = [
        {
            "File": r.file_name,
            "Report Type": r.report_master.report_name,
            "Size": format_bytes(r.file_size_bytes),
            "Uploaded By": r.uploader.username if r.uploader else "-",
            "Uploaded At": r.uploaded_at.strftime("%d-%b-%Y %H:%M"),
        }
        for r in recent
    ]

if rows:
    st.dataframe(rows, use_container_width=True, hide_index=True)
else:
    st.info("No reports uploaded yet.")
