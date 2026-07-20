"""Failed email management: view, retry selected, or retry all."""
from __future__ import annotations

import streamlit as st

from config import settings
from database.db import get_db
from database.models import EmailLog, EmailStatus
from services.email_service import retry_failed_emails
from utils.ui import inject_css, page_header, render_sidebar_user_box, require_login

st.set_page_config(page_title=f"Failed Emails | {settings.app_name}", page_icon="⚠️", layout="wide")
user = require_login()
inject_css()
render_sidebar_user_box()

page_header("Failed Email Management", "Review and retry emails that failed to send.", "⚠️")

with get_db() as db:
    failed = (
        db.query(EmailLog)
        .filter(EmailLog.status == EmailStatus.FAILED)
        .order_by(EmailLog.created_at.desc())
        .limit(500)
        .all()
    )
    rows = [
        {
            "Select": False,
            "ID": l.id,
            "Recipient": l.recipient_name,
            "Email": l.recipient_email,
            "Report": l.job.upload.report_master.report_name if l.job and l.job.upload else "-",
            "Attempts": l.attempt_count,
            "Last Error": (l.last_error or "-")[:100],
            "Failed At": l.created_at.strftime("%d-%b-%Y %H:%M"),
        }
        for l in failed
    ]

st.caption(f"{len(rows)} failed email(s)")

if rows:
    edited = st.data_editor(
        rows, use_container_width=True, hide_index=True, key="failed_editor",
        column_config={"Select": st.column_config.CheckboxColumn(required=True)},
        disabled=["ID", "Recipient", "Email", "Report", "Attempts", "Last Error", "Failed At"],
    )

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔁 Retry Selected", type="primary"):
            selected_ids = [r["ID"] for r in edited if r["Select"]]
            if not selected_ids:
                st.warning("Select at least one row to retry.")
            else:
                progress = st.progress(0, text="Retrying selected emails...")

                def _cb(done, total):
                    progress.progress(done / total, text=f"Retrying {done}/{total}...")

                with get_db() as db:
                    result = retry_failed_emails(db, email_log_ids=selected_ids, progress_callback=_cb)
                st.success(f"Retried {result['retried']} email(s).")
                st.rerun()
    with col2:
        if st.button("🔁 Retry All Failed"):
            progress = st.progress(0, text="Retrying all failed emails...")

            def _cb(done, total):
                progress.progress(done / total, text=f"Retrying {done}/{total}...")

            with get_db() as db:
                result = retry_failed_emails(db, progress_callback=_cb)
            st.success(f"Retried {result['retried']} email(s).")
            st.rerun()
else:
    st.success("No failed emails right now. 🎉")
