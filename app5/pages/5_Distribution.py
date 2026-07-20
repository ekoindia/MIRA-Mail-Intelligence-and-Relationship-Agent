"""Distribution preview and send: resolve recipients, override, dispatch."""
from __future__ import annotations

import streamlit as st

from config import settings
from database.db import get_db
from database.models import Branch, EmailTemplate, LHO, ReportUpload
from services.distribution_service import RecipientOverride, create_distribution_job, resolve_recipients
from services.email_service import run_distribution_job
from utils.gmail_guard import render_gmail_banner
from utils.ui import inject_css, page_header, render_sidebar_user_box, require_login

st.set_page_config(page_title=f"Distribution | {settings.app_name}", page_icon="📮", layout="wide")
user = require_login()
inject_css()
render_sidebar_user_box()

page_header("Distribution", "Preview recipients and send an uploaded report.", "📮")
render_gmail_banner(stop_if_disconnected=True)

with get_db() as db:
    uploads = (
        db.query(ReportUpload)
        .order_by(ReportUpload.uploaded_at.desc())
        .limit(50)
        .all()
    )
    upload_options = {
        f"{u.file_name}  ·  {u.report_master.report_name}  ·  {u.uploaded_at.strftime('%d-%b-%Y %H:%M')}": u.id
        for u in uploads
    }
    templates = db.query(EmailTemplate).order_by(EmailTemplate.name).all()
    template_options = {"Use report's default": None} | {t.name: t.id for t in templates}
    all_branches = [b.branch_code for b in db.query(Branch).filter(Branch.is_active.is_(True)).all()]
    all_lhos = [l.lho_name for l in db.query(LHO).filter(LHO.is_active.is_(True)).all()]
    all_regions = sorted({b.region for b in db.query(Branch).filter(Branch.region.isnot(None)).all()})

if not upload_options:
    st.warning("No reports uploaded yet. Go to **Upload Reports** first.")
    st.stop()

selected_upload_label = st.selectbox("Select Uploaded Report", list(upload_options.keys()))
upload_id = upload_options[selected_upload_label]

col1, col2 = st.columns(2)
with col1:
    template_label = st.selectbox("Template", list(template_options.keys()),
                                   help="Templates are managed on the Templates page.")
with col2:
    st.markdown("&nbsp;")
    use_override = st.checkbox("Manually override recipients")

override = RecipientOverride()
if use_override:
    oc1, oc2, oc3 = st.columns(3)
    with oc1:
        override.branch_codes = st.multiselect("Specific Branches", all_branches)
    with oc2:
        override.lho_names = st.multiselect("Specific LHOs", all_lhos)
    with oc3:
        override.regions = st.multiselect("Specific Regions", all_regions)

if st.button("🔍 Preview Recipients", type="primary"):
    with get_db() as db:
        upload = db.query(ReportUpload).get(upload_id)
        recipients = resolve_recipients(db, upload.report_master.recipient_type.value, override)
        st.session_state["preview_recipients"] = [
            {"Name": r.name, "Email": r.email, "Type": r.recipient_type} for r in recipients
        ]
        st.session_state["preview_upload_id"] = upload_id
        st.session_state["preview_report_name"] = upload.report_master.report_name
        st.session_state["preview_file_name"] = upload.file_name

if "preview_recipients" in st.session_state and st.session_state.get("preview_upload_id") == upload_id:
    recipients = st.session_state["preview_recipients"]
    st.markdown("### Distribution Preview")
    m1, m2, m3 = st.columns(3)
    m1.metric("Report", st.session_state["preview_report_name"])
    m2.metric("Attachment", st.session_state["preview_file_name"])
    m3.metric("Total Recipients", len(recipients))

    branch_count = len([r for r in recipients if r["Type"] == "Branch"])
    lho_count = len([r for r in recipients if r["Type"] == "LHO"])
    st.caption(f"Branches: {branch_count} · LHOs: {lho_count}")

    if recipients:
        st.dataframe(recipients, use_container_width=True, hide_index=True)

        if st.button("🚀 Confirm & Send Distribution", type="primary"):
            with get_db() as db:
                from services.distribution_service import ResolvedRecipient
                resolved = [
                    ResolvedRecipient(r["Name"], r["Email"], r["Type"]) for r in recipients
                ]
                job = create_distribution_job(
                    db, upload_id, template_options[template_label], resolved, user["id"], user["username"]
                )
                job_id = job.id

            progress = st.progress(0, text="Starting distribution...")
            status_text = st.empty()

            def _cb(done: int, total: int) -> None:
                progress.progress(done / total, text=f"Sending {done}/{total}...")

            with get_db() as db:
                completed_job = run_distribution_job(db, job_id, progress_callback=_cb)
                sent, failed = completed_job.sent_count, completed_job.failed_count

            status_text.success(f"Done. Sent: {sent} · Failed: {failed}")
            del st.session_state["preview_recipients"]
    else:
        st.warning("No recipients matched. Check your master data or override selection.")
