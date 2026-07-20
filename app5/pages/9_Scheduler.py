"""Scheduler: configure recurring (daily/weekly/monthly) distribution runs,
plus fully-automated "download from REST API -> distribute" schedules."""
from __future__ import annotations

import streamlit as st

from config import settings
from database.db import get_db
from database.models import EmailTemplate, ReportMaster
from database.org_models import OrgLevel
from database.report_source_models import ReportSource
from services.auto_distribution_service import (
    create_auto_schedule,
    list_auto_schedules,
    set_auto_schedule_active,
)
from services.org_service import list_org_units
from services.scheduler_service import create_schedule, get_scheduler, list_schedules, set_schedule_active
from utils.ui import inject_css, page_header, render_sidebar_user_box, require_login

st.set_page_config(page_title=f"Scheduler | {settings.app_name}", page_icon="⏱️", layout="wide")
user = require_login()
inject_css()
render_sidebar_user_box()

# Ensure the background scheduler/poller is running for this process.
get_scheduler()

page_header("Distribution Scheduler", "Automate recurring report distribution runs.", "⏱️")

with get_db() as db:
    report_types = db.query(ReportMaster).filter(ReportMaster.is_active.is_(True)).all()
    report_options = {r.report_name: r.id for r in report_types}

with st.expander("➕ Create New Schedule", expanded=not report_options == {}):
    if not report_options:
        st.warning("Create a report type first (see **Report Master**).")
    else:
        with st.form("new_schedule"):
            name = st.text_input("Schedule Name*", placeholder="e.g. Weekly Sales Report - Every Monday")
            report_name = st.selectbox("Report Type*", list(report_options.keys()))
            frequency = st.selectbox("Frequency*", ["Daily", "Weekly", "Monthly"])
            run_time = st.time_input("Run Time*")

            day_of_week, day_of_month = None, None
            if frequency == "Weekly":
                dow_label = st.selectbox("Day of Week", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])
                day_of_week = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].index(dow_label)
            elif frequency == "Monthly":
                day_of_month = st.number_input("Day of Month", min_value=1, max_value=28, value=1)

            submitted = st.form_submit_button("Create Schedule", type="primary")

        if submitted:
            if not name.strip():
                st.error("Schedule name is required.")
            else:
                with get_db() as db:
                    create_schedule(
                        db, name.strip(), report_options[report_name], frequency,
                        run_time.strftime("%H:%M"), day_of_week, day_of_month,
                        user["id"], user["username"],
                    )
                st.success(f"Schedule '{name}' created.")
                st.rerun()

st.markdown("### Active & Upcoming Schedules")
with get_db() as db:
    schedules = list_schedules(db)
    rows = [
        {
            "id": s.id, "Name": s.name, "Report": s.report_master.report_name,
            "Frequency": s.frequency.value, "Time": s.run_time,
            "Last Run": s.last_run_at.strftime("%d-%b-%Y %H:%M") if s.last_run_at else "Never",
            "Next Run": s.next_run_at.strftime("%d-%b-%Y %H:%M") if s.next_run_at else "-",
            "Active": s.is_active,
        }
        for s in schedules
    ]

if rows:
    for r in rows:
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
            with c1:
                st.markdown(f"**{r['Name']}**")
                st.caption(f"{r['Report']} · {r['Frequency']} at {r['Time']}")
            with c2:
                st.caption(f"Last run: {r['Last Run']}")
            with c3:
                st.caption(f"Next run: {r['Next Run']}")
            with c4:
                label = "Pause" if r["Active"] else "Resume"
                if st.button(label, key=f"sched_{r['id']}"):
                    with get_db() as db:
                        set_schedule_active(db, r["id"], not r["Active"])
                    st.rerun()
else:
    st.info("No schedules configured yet.")

st.caption(
    "⚠️ For production, also run `python scheduler_worker.py` as a standalone always-on process so "
    "schedules still fire when nobody has the Streamlit app open (see README)."
)

st.markdown("---")
page_header(
    "Automated Report Download + Distribution",
    "Fetch a report from a connected REST API and email it out on a schedule — no manual upload needed.",
    "🤖",
)

with get_db() as db:
    sources = db.query(ReportSource).filter(ReportSource.is_active.is_(True)).all()
    source_options = {s.name: s.id for s in sources}
    templates = db.query(EmailTemplate).all()
    template_options = {"— none —": None}
    template_options.update({t.name: t.id for t in templates})

with st.expander("➕ Create New Auto-Distribution Schedule", expanded=not source_options == {}):
    if not source_options:
        st.warning("Connect a Report Source first (see **Report Sources**).")
    else:
        with st.form("new_auto_schedule"):
            auto_name = st.text_input("Schedule Name*", placeholder="e.g. Auto: Weekly Sales Report to all LHOs")
            source_name = st.selectbox("Report Source*", list(source_options.keys()))
            template_name = st.selectbox("Email Template", list(template_options.keys()))

            level_labels = st.multiselect(
                "Send To (Org Level(s))*", [l.value for l in OrgLevel],
                help="Most reports go to several levels in the same send, e.g. LHO + Corporate Center together.",
            )
            levels = [OrgLevel(v) for v in level_labels]
            unit_labels: dict[str, int] = {}
            with get_db() as db:
                for lvl in levels:
                    for u in list_org_units(db, level=lvl):
                        unit_labels[f"{u.unit_name} ({u.email})"] = u.id
            selected_units = st.multiselect(
                "Restrict to specific units (optional)", list(unit_labels.keys()),
                help="Leave empty to send to ALL active units at the selected level(s).",
            )

            auto_frequency = st.selectbox("Frequency*", ["Daily", "Weekly", "Monthly"], key="auto_freq")
            auto_run_time = st.time_input("Run Time*", key="auto_time")

            auto_dow, auto_dom = None, None
            if auto_frequency == "Weekly":
                dow_label = st.selectbox(
                    "Day of Week", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
                    key="auto_dow",
                )
                auto_dow = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"].index(dow_label)
            elif auto_frequency == "Monthly":
                auto_dom = st.number_input("Day of Month", min_value=1, max_value=28, value=1, key="auto_dom")

            auto_submitted = st.form_submit_button("Create Auto-Distribution Schedule", type="primary")

        if auto_submitted:
            if not auto_name.strip():
                st.error("Schedule name is required.")
            elif not levels:
                st.error("Select at least one org level to send to.")
            else:
                unit_ids = [unit_labels[u] for u in selected_units] if selected_units else None
                with get_db() as db:
                    create_auto_schedule(
                        db, auto_name.strip(), source_options[source_name],
                        template_options[template_name], levels, unit_ids,
                        auto_frequency, auto_run_time.strftime("%H:%M"), auto_dow, auto_dom,
                        user["id"], user["username"],
                    )
                st.success(f"Auto-distribution schedule '{auto_name}' created.")
                st.rerun()

st.markdown("### Active & Upcoming Auto-Distribution Schedules")
with get_db() as db:
    auto_schedules = list_auto_schedules(db)
    auto_rows = [
        {
            "id": s.id, "Name": s.name,
            "Source": s.report_source.name if s.report_source else "-",
            "Level": s.org_level,
            "Frequency": s.frequency.value, "Time": s.run_time,
            "Last Run": s.last_run_at.strftime("%d-%b-%Y %H:%M") if s.last_run_at else "Never",
            "Next Run": s.next_run_at.strftime("%d-%b-%Y %H:%M") if s.next_run_at else "-",
            "Active": s.is_active,
        }
        for s in auto_schedules
    ]

if auto_rows:
    for r in auto_rows:
        with st.container(border=True):
            c1, c2, c3, c4 = st.columns([3, 2, 2, 1])
            with c1:
                st.markdown(f"**{r['Name']}**")
                st.caption(f"{r['Source']} → {r['Level']} · {r['Frequency']} at {r['Time']}")
            with c2:
                st.caption(f"Last run: {r['Last Run']}")
            with c3:
                st.caption(f"Next run: {r['Next Run']}")
            with c4:
                label = "Pause" if r["Active"] else "Resume"
                if st.button(label, key=f"auto_sched_{r['id']}"):
                    with get_db() as db:
                        set_auto_schedule_active(db, r["id"], not r["Active"])
                    st.rerun()
else:
    st.info("No auto-distribution schedules configured yet.")
