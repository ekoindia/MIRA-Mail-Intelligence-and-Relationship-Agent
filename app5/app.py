"""
Reporting Console
Main entry point: handles login and routes into the multipage app.

Run with:  streamlit run app.py
"""
from __future__ import annotations

import streamlit as st

from config import settings
from database.db import init_db, get_db
from services.auth_service import AuthError, authenticate
from utils.logger import setup_logging
from utils.ui import inject_css, page_header, pipeline, render_kpi_row, render_sidebar_user_box

st.set_page_config(
    page_title=settings.app_name,
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

setup_logging()
init_db()

if "theme" not in st.session_state:
    st.session_state["theme"] = "light"

inject_css()


def login_view() -> None:
    st.markdown("<div class='login-shell'>", unsafe_allow_html=True)
    _, mid, _ = st.columns([1, 1.15, 1])
    with mid:
        initials = "".join(w[0] for w in settings.app_name.split()[:2]).upper() or "RC"
        st.markdown(
            f"""
            <div class='login-card'>
                <div class='login-mark'>{initials}</div>
                <h2>{settings.app_name}</h2>
                <div class='login-org'>{settings.org_tagline}</div>
            """,
            unsafe_allow_html=True,
        )
        with st.form("login_form", clear_on_submit=False):
            username = st.text_input("Username", placeholder="Enter your username")
            password = st.text_input("Password", type="password", placeholder="Enter your password")
            submitted = st.form_submit_button("Sign in", use_container_width=True, type="primary")

        if submitted:
            if not username or not password:
                st.error("Please enter both username and password.")
            else:
                try:
                    with get_db() as db:
                        user = authenticate(db, username, password)
                        st.session_state["user"] = {
                            "id": user.id,
                            "username": user.username,
                            "email": user.email,
                            "role": user.role.value,
                        }
                    st.rerun()
                except AuthError as e:
                    st.error(str(e))
        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='login-footnote'>Default admin credentials are set in your "
            "<code>.env</code> file on first run.</div>",
            unsafe_allow_html=True,
        )
    st.markdown("</div>", unsafe_allow_html=True)


def landing_dashboard() -> None:
    from database.models import DistributionJob, EmailLog, EmailStatus, ReportUpload
    from utils.helpers import pct

    user = st.session_state["user"]
    page_header(f"Welcome back, {user['username']}", "Here's what's happening with your report distribution.",
                "", eyebrow="Home")

    with get_db() as db:
        total_reports = db.query(ReportUpload).count()
        total_sent = db.query(EmailLog).filter(EmailLog.status == EmailStatus.SENT).count()
        total_failed = db.query(EmailLog).filter(EmailLog.status == EmailStatus.FAILED).count()
        total_pending = db.query(EmailLog).filter(
            EmailLog.status.in_([EmailStatus.PENDING, EmailStatus.RETRYING])
        ).count()
        total_jobs = db.query(DistributionJob).count()
        success_rate = pct(total_sent, total_sent + total_failed)

    render_kpi_row([
        ("Reports Uploaded", str(total_reports), "", "📄"),
        ("Emails Sent", str(total_sent), "", "✅"),
        ("Pending / Retrying", str(total_pending), "", "⏳"),
        ("Success Rate", f"{success_rate}%", "", "🎯"),
    ])

    st.write("")
    st.markdown("##### Distribution pipeline")
    pipeline(["Upload report", "Auto-mapping", "Template", "Draft & preview", "Send", "Log"], current_index=0)

    st.markdown("##### Quick navigation")
    cols = st.columns(4)
    nav_items = [
        ("Full Dashboard", "pages/1_Dashboard.py"),
        ("Upload Reports", "pages/2_Upload_Reports.py"),
        ("Distribution", "pages/5_Distribution.py"),
        ("Delivery Logs", "pages/7_Delivery_Logs.py"),
    ]
    for col, (label, target) in zip(cols, nav_items):
        with col:
            if st.button(label, use_container_width=True):
                st.switch_page(target)

    st.caption(
        "Use the sidebar to move between Master Data, Reports, Distribution, Templates, "
        "Scheduler, Logs, Audit and Settings."
    )


if "user" not in st.session_state:
    login_view()
else:
    render_sidebar_user_box()
    landing_dashboard()
