"""Delivery logs with filters by date, report, status, branch, LHO."""
from __future__ import annotations

from datetime import datetime, timedelta

import streamlit as st

from config import settings
from database.db import get_db
from database.models import DistributionJob, EmailLog, ReportMaster, ReportUpload
from utils.ui import inject_css, page_header, render_sidebar_user_box, require_login, status_badge

st.set_page_config(page_title=f"Delivery Logs | {settings.app_name}", page_icon="📈", layout="wide")
user = require_login()
inject_css()
render_sidebar_user_box()

page_header("Delivery Logs", "Track every email sent, with full filtering.", "📈")

with get_db() as db:
    report_names = [r.report_name for r in db.query(ReportMaster).order_by(ReportMaster.report_name).all()]

c1, c2, c3, c4, c5 = st.columns(5)
with c1:
    date_from = st.date_input("From", value=datetime.now() - timedelta(days=30))
with c2:
    date_to = st.date_input("To", value=datetime.now())
with c3:
    report_filter = st.selectbox("Report", ["All"] + report_names)
with c4:
    status_filter = st.selectbox("Status", ["All", "Sent", "Failed", "Pending", "Retrying"])
with c5:
    search = st.text_input("Search branch/LHO/email")

with get_db() as db:
    query = (
        db.query(EmailLog)
        .join(DistributionJob, EmailLog.job_id == DistributionJob.id)
        .join(ReportUpload, DistributionJob.upload_id == ReportUpload.id)
        .join(ReportMaster, ReportUpload.report_master_id == ReportMaster.id)
    )
    query = query.filter(EmailLog.created_at >= datetime.combine(date_from, datetime.min.time()))
    query = query.filter(EmailLog.created_at <= datetime.combine(date_to, datetime.max.time()))
    if report_filter != "All":
        query = query.filter(ReportMaster.report_name == report_filter)
    if status_filter != "All":
        query = query.filter(EmailLog.status == status_filter)
    if search:
        like = f"%{search}%"
        query = query.filter(
            (EmailLog.recipient_name.ilike(like))
            | (EmailLog.recipient_email.ilike(like))
            | (EmailLog.branch_code.ilike(like))
            | (EmailLog.lho_name.ilike(like))
        )

    logs = query.order_by(EmailLog.created_at.desc()).limit(1000).all()
    rows = [
        {
            "Recipient": l.recipient_name,
            "Email": l.recipient_email,
            "Type": l.recipient_type,
            "Report": l.job.upload.report_master.report_name if l.job and l.job.upload else "-",
            "Status": l.status.value,
            "Channel": l.sent_via or "-",
            "Attempts": l.attempt_count,
            "Sent At": l.sent_at.strftime("%d-%b-%Y %H:%M") if l.sent_at else "-",
            "Error": (l.last_error or "-")[:80],
        }
        for l in logs
    ]

st.caption(f"{len(rows)} record(s) found")
if rows:
    import pandas as pd
    df = pd.DataFrame(rows)
    df_display = df.copy()
    df_display["Status"] = df_display["Status"].apply(status_badge)
    st.write(df_display.to_html(escape=False, index=False), unsafe_allow_html=True)

    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Export CSV", data=csv, file_name="delivery_logs.csv", mime="text/csv")
else:
    st.info("No delivery logs match the selected filters.")
