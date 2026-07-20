"""Complete audit trail: uploads, sends, edits, logins."""
from __future__ import annotations

from datetime import datetime, timedelta

import streamlit as st

from config import settings
from database.db import get_db
from services.audit_service import get_audit_logs
from utils.ui import inject_css, page_header, render_sidebar_user_box, require_admin

st.set_page_config(page_title=f"Audit Logs | {settings.app_name}", page_icon="🧾", layout="wide")
user = require_admin()
inject_css()
render_sidebar_user_box()

page_header("Audit Logs", "Immutable trail of logins, uploads, sends, and edits. (Admin only)", "🧾")

c1, c2, c3 = st.columns(3)
with c1:
    date_from = st.date_input("From", value=datetime.now() - timedelta(days=30))
with c2:
    date_to = st.date_input("To", value=datetime.now())
with c3:
    action_filter = st.selectbox(
        "Action",
        ["All", "LOGIN", "LOGIN_FAILED", "UPLOAD_REPORT", "UPLOAD_MASTER", "SEND_DISTRIBUTION",
         "CREATE_DISTRIBUTION_JOB", "CREATE_REPORT_TYPE", "EDIT_REPORT_TYPE", "CREATE_TEMPLATE",
         "EDIT_TEMPLATE", "DELETE_TEMPLATE", "CREATE_SCHEDULE", "CREATE_USER", "EDIT_USER", "CHANGE_PASSWORD"],
    )

with get_db() as db:
    logs = get_audit_logs(
        db,
        action=None if action_filter == "All" else action_filter,
        date_from=datetime.combine(date_from, datetime.min.time()),
        date_to=datetime.combine(date_to, datetime.max.time()),
        limit=1000,
    )
    rows = [
        {
            "Timestamp": l.created_at.strftime("%d-%b-%Y %H:%M:%S"),
            "User": l.username or "-",
            "Action": l.action,
            "Entity": f"{l.entity_type or ''} {l.entity_id or ''}".strip() or "-",
            "Details": l.details or "-",
        }
        for l in logs
    ]

st.caption(f"{len(rows)} audit record(s)")
if rows:
    st.dataframe(rows, use_container_width=True, hide_index=True)
    import pandas as pd
    csv = pd.DataFrame(rows).to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Export CSV", data=csv, file_name="audit_logs.csv", mime="text/csv")
else:
    st.info("No audit records match the selected filters.")
