"""Dashboard: KPIs, mail activity (incoming vs outgoing by LHO), and recent jobs."""
from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st

from config import settings
from database.db import get_db
from database.models import DistributionJob, EmailLog, EmailStatus, ReportUpload
from database.org_models import OrgLevel
from services import email_service, incoming_service
from services.gmail_service import KNOWN_LHOS
from services.org_service import list_org_units
from utils.gmail_guard import render_gmail_banner
from utils.ui import inject_css, page_header, render_kpi_row, render_sidebar_user_box, require_login, status_badge

st.set_page_config(page_title=f"Dashboard | {settings.app_name}", page_icon="📊", layout="wide")
user = require_login()
inject_css()
render_sidebar_user_box()
render_gmail_banner(stop_if_disconnected=False)  # show status, don't block

page_header("Dashboard", "Report distribution activity and mail traffic overview.", "📊")

# ── Top-line KPIs ───────────────────────────────────────────────────────
with get_db() as db:
    total_reports = db.query(ReportUpload).count()
    total_sent = db.query(EmailLog).filter(EmailLog.status == EmailStatus.SENT).count()
    total_failed = db.query(EmailLog).filter(EmailLog.status == EmailStatus.FAILED).count()

incoming_kpis = incoming_service.get_incoming_kpis()

render_kpi_row([
    ("Reports Uploaded", str(total_reports), "", "📄"),
    ("Total Outgoing", str(total_sent), "", "📤"),
    ("Total Incoming", str(incoming_kpis["total_incoming"]), "", "📥"),
    ("Failed Emails", str(total_failed), "", "⚠️"),
])

st.write("")

# ── Mail Activity: incoming vs outgoing, filterable by LHO ─────────────
st.markdown("### Mail Activity")

with get_db() as db:
    lho_units = list_org_units(db, level=OrgLevel.LHO)
lho_names = [u.unit_name for u in lho_units] or KNOWN_LHOS

lho_filter = st.selectbox("Filter by LHO", ["All LHOs"] + lho_names)

incoming_by_lho = pd.DataFrame(incoming_service.get_incoming_by_lho())
outgoing_by_lho = pd.DataFrame(email_service.get_outgoing_by_lho())
if outgoing_by_lho.empty:
    outgoing_by_lho = pd.DataFrame({"LHO": lho_names, "Outgoing Emails": [0] * len(lho_names)})

combined = incoming_by_lho.merge(outgoing_by_lho, on="LHO", how="outer").fillna(0)
if lho_filter != "All LHOs":
    combined = combined[combined["LHO"] == lho_filter]
    st.caption(
        f"Showing **{lho_filter}**: "
        f"{int(combined['Incoming Emails'].sum()) if 'Incoming Emails' in combined else 0} incoming, "
        f"{int(combined['Outgoing Emails'].sum()) if 'Outgoing Emails' in combined else 0} outgoing."
    )

if not combined.empty:
    melted = combined.melt(id_vars="LHO", value_vars=["Incoming Emails", "Outgoing Emails"],
                            var_name="Direction", value_name="Count")
    fig = px.bar(
        melted, x="LHO", y="Count", color="Direction", barmode="group",
        color_discrete_map={"Incoming Emails": "#0284c7", "Outgoing Emails": "#16a34a"},
    )
    fig.update_layout(height=340, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No LHO-tagged mail activity yet.")

col1, col2 = st.columns(2)
with col1:
    st.markdown("##### Recent Incoming")
    recent_in = incoming_service.get_recent_incoming(limit=15)
    if lho_filter != "All LHOs":
        recent_in = [r for r in recent_in if r["LHO"] == lho_filter]
    if recent_in:
        st.dataframe(recent_in, use_container_width=True, hide_index=True)
    else:
        st.info("No incoming mail yet.")

with col2:
    st.markdown("##### Recent Outgoing")
    recent_out = email_service.get_recent_outgoing(limit=15)
    if lho_filter != "All LHOs":
        recent_out = [r for r in recent_out if r["LHO"] == lho_filter]
    if recent_out:
        st.dataframe(recent_out, use_container_width=True, hide_index=True)
    else:
        st.info("No outgoing mail yet.")

st.write("")

# ── Recent distribution jobs ────────────────────────────────────────────
st.markdown("### Recent Distribution Jobs")
with get_db() as db:
    jobs = db.query(DistributionJob).order_by(DistributionJob.created_at.desc()).limit(15).all()
    job_rows = [
        {
            "Job ID": j.id,
            "Report": j.upload.report_master.report_name if j.upload else "-",
            "Status": j.status.value,
            "Recipients": j.total_recipients,
            "Sent": j.sent_count,
            "Failed": j.failed_count,
            "Created": j.created_at,
        }
        for j in jobs
    ]

if job_rows:
    df_display = pd.DataFrame(job_rows).copy()
    df_display["Status"] = df_display["Status"].apply(status_badge)
    st.write(df_display.to_html(escape=False, index=False), unsafe_allow_html=True)
else:
    st.info("No distribution jobs have been created yet. Go to **Upload Reports** or **Scheduler** to get started.")
