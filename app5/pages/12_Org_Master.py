"""Org hierarchy master data: Branch / RBO / AO / LHO / Corp Center bulk Excel upload + directory.

Each level gets its own upload sheet, matching the "Scheduled Outgoing Mail –
Reporting Framework" distribution matrix (Branch / RBO / AO / LHO / Corp. Center)."""
from __future__ import annotations

import io

import pandas as pd
import streamlit as st

from config import settings
from database.db import get_db
from database.org_models import OrgLevel
from services.org_service import OrgUploadError, get_org_directory, import_org_units
from utils.ui import inject_css, page_header, render_sidebar_user_box, require_login, searchable_dataframe

st.set_page_config(page_title=f"Org Master | {settings.app_name}", page_icon="🏛️", layout="wide")
user = require_login()
inject_css()
render_sidebar_user_box()

page_header(
    "Org Master", "Upload Branch, RBO, AO, LHO and Corporate Center contact data used for automated report distribution.", "🏛️"
)

LEVEL_TABS = [
    ("Branch", OrgLevel.BRANCH),
    ("RBO", OrgLevel.RBO),
    ("AO", OrgLevel.AO),
    ("LHO", OrgLevel.LHO),
    ("Corp Center", OrgLevel.CORP),
]

tabs = st.tabs([label for label, _ in LEVEL_TABS] + ["📖 Directory"])

for (label, level), tab in zip(LEVEL_TABS, tabs[:-1]):
    with tab:
        st.markdown(f"""
        Upload an Excel file with these columns for **{label}**:
        **Unit Name, Email** *(Unit Code, Parent Unit Name, Region optional)*

        `Parent Unit Name` can reference a unit already uploaded at any level
        (e.g. an LHO's parent might be an AO or a Corp Center row) — upload
        parent levels first if you want linkage to resolve.
        """)

        template_df = pd.DataFrame({
            "Unit Name": [f"{label} Example"], "Unit Code": ["UNIT001"],
            "Email": [f"example.{label.lower().replace(' ', '')}@bank.com"],
            "Parent Unit Name": [""], "Region": ["North"],
        })
        buf = io.BytesIO()
        template_df.to_excel(buf, index=False)
        st.download_button(
            f"⬇️ Download {label} Template", data=buf.getvalue(),
            file_name=f"{level.name.lower()}_master_template.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"tpl_{level.name}",
        )

        master_file = st.file_uploader(f"Upload {label} Master Excel", type=["xlsx", "xls"], key=f"upl_{level.name}")
        if master_file and st.button(f"Import {label} Data", type="primary", key=f"btn_{level.name}"):
            try:
                df = pd.read_excel(master_file)
                with get_db() as db:
                    result = import_org_units(db, level, df, user["username"])
                st.success(f"{label}: +{result['created']} new / ~{result['updated']} updated")
                if result["errors"]:
                    with st.expander(f"⚠️ {len(result['errors'])} row(s) had issues"):
                        for e in result["errors"]:
                            st.write(f"- {e}")
            except OrgUploadError as e:
                st.error(str(e))
            except Exception as e:  # noqa: BLE001
                st.error(f"Failed to import file: {e}")

with tabs[-1]:
    with get_db() as db:
        rows = get_org_directory(db)
    st.caption(f"{len(rows)} org unit(s) on file across all levels")
    if rows:
        df = pd.DataFrame(rows)
        level_filter = st.multiselect("Filter by level", options=[l.value for _, l in LEVEL_TABS])
        if level_filter:
            df = df[df["Level"].isin(level_filter)]
        searchable_dataframe(df, key="org_directory_search")
    else:
        st.info("No org units uploaded yet. Use the tabs above to get started.")
