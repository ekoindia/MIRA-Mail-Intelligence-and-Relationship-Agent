"""
Settings — Gmail connection, batch/retry, appearance, user management.
Admin only.
"""
from __future__ import annotations

import streamlit as st

from config import settings
from database.db import get_db
from database.models import AppSetting
from services.auth_service import create_user, list_users, set_user_active
from services.gmail_auth import (
    GMAIL_CREDENTIALS_PATH,
    connect_gmail,
    credentials_file_exists,
    disconnect_gmail,
    get_connection_status,
    get_credentials_info,
    save_credentials_from_upload,
)
from utils.security import is_strong_password
from utils.ui import inject_css, page_header, render_sidebar_user_box, require_admin

st.set_page_config(
    page_title=f"Settings | {settings.app_name}",
    page_icon="⚙️",
    layout="wide",
)
user = require_admin()
inject_css()
render_sidebar_user_box()
page_header("Settings", "System configuration. (Admin only)", "⚙️")

tab_gmail, tab_batch, tab_appearance, tab_users = st.tabs(
    ["📧 Gmail Connection", "📦 Batch & Retry", "🎨 Appearance", "👤 User Management"]
)

# ══════════════════════════════════════════════════════════════════════════
# TAB 1 — GMAIL CONNECTION
# ══════════════════════════════════════════════════════════════════════════
with tab_gmail:

    gmail_status = get_connection_status()
    cred_info    = get_credentials_info()

    # ── Status card ──────────────────────────────────────────────────────
    if gmail_status["connected"]:
        st.markdown(f"""
        <div style="background:linear-gradient(135deg,#f0fdf4,#dcfce7);
                    border:1.5px solid #86efac;border-radius:14px;
                    padding:1.2rem 1.6rem;margin-bottom:1.2rem">
            <div style="display:flex;align-items:center;gap:0.6rem;margin-bottom:0.4rem">
                <span style="font-size:1.4rem">✅</span>
                <span style="font-size:1.1rem;font-weight:800;color:#166534">Gmail Connected</span>
            </div>
            <div style="color:#15803d;font-size:0.92rem">
                Sending as &nbsp;<code style="background:#bbf7d0;padding:0.1rem 0.5rem;
                border-radius:6px;font-weight:700">{gmail_status['email']}</code>
            </div>
            <div style="color:#4ade80;font-size:0.78rem;margin-top:0.3rem">
                Token cached · refreshes automatically
            </div>
        </div>
        """, unsafe_allow_html=True)

        col_disc, col_recheck = st.columns([1, 3])
        with col_disc:
            if st.button("🔌 Disconnect Gmail", type="secondary", use_container_width=True):
                disconnect_gmail()
                st.success("Gmail disconnected. Token removed.")
                st.rerun()
        with col_recheck:
            if st.button("🔄 Re-check Connection", use_container_width=True):
                st.rerun()

    else:
        # Not connected — show why and what to do
        if gmail_status["error"]:
            st.markdown(f"""
            <div style="background:#fff7ed;border:1.5px solid #fed7aa;
                        border-radius:14px;padding:1.2rem 1.6rem;margin-bottom:1rem">
                <span style="font-size:1.3rem">⚠️</span>
                <span style="font-weight:700;color:#c2410c"> Connection Error</span>
                <div style="color:#9a3412;font-size:0.88rem;margin-top:0.3rem">
                    {gmail_status['error']}
                </div>
            </div>
            """, unsafe_allow_html=True)
        else:
            st.markdown("""
            <div style="background:#eff6ff;border:1.5px solid #bfdbfe;
                        border-radius:14px;padding:1.2rem 1.6rem;margin-bottom:1rem">
                <span style="font-size:1.3rem">📧</span>
                <span style="font-weight:700;color:#1e40af"> Gmail Not Connected</span>
                <div style="color:#1d4ed8;font-size:0.88rem;margin-top:0.3rem">
                    Connect a Gmail account to enable automated report distribution.
                </div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("---")

    # ── STEP 1: credentials.json ──────────────────────────────────────────
    st.markdown("### Step 1 — Google OAuth Credentials")

    if cred_info["exists"]:
        st.markdown(f"""
        <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:10px;
                    padding:0.8rem 1.2rem;margin-bottom:0.8rem;display:flex;
                    align-items:center;gap:0.8rem">
            <span style="font-size:1.2rem">✅</span>
            <div>
                <div style="font-weight:700;color:#166534;font-size:0.9rem">
                    credentials.json found
                </div>
                <div style="color:#4ade80;font-size:0.78rem">
                    Project: {cred_info.get('project_id','—')} &nbsp;·&nbsp;
                    Last modified: {cred_info.get('modified','—')}
                </div>
            </div>
        </div>
        """, unsafe_allow_html=True)

        with st.expander("🔄 Replace credentials.json"):
            new_cred = st.file_uploader(
                "Upload new credentials.json",
                type=["json"],
                key="replace_creds",
            )
            if new_cred:
                if st.button("Replace File", type="primary"):
                    try:
                        save_credentials_from_upload(new_cred.read())
                        st.success("credentials.json replaced.")
                        st.rerun()
                    except ValueError as e:
                        st.error(str(e))
    else:
        st.markdown("""
        <div style="background:#fef2f2;border:1.5px solid #fecaca;border-radius:10px;
                    padding:0.8rem 1.2rem;margin-bottom:0.8rem">
            <span style="font-size:1.1rem">❌</span>
            <span style="font-weight:700;color:#991b1b"> credentials.json not found</span>
            <div style="color:#b91c1c;font-size:0.82rem;margin-top:0.2rem">
                Upload your Google OAuth client file below to get started.
            </div>
        </div>
        """, unsafe_allow_html=True)

        # How to get credentials guide
        with st.expander("📋 How to get your credentials.json file", expanded=True):
            st.markdown("""
**1. Go to Google Cloud Console**
👉 [console.cloud.google.com](https://console.cloud.google.com)
Sign in with the Gmail account you want to use for sending reports.

**2. Create a project** *(skip if you already have one)*
- Click the project dropdown at the top → **New Project**
- Name it e.g. `ReportDashboard` → **Create**

**3. Enable Gmail API**
- Search bar → type `Gmail API` → click it → **Enable**

**4. Configure OAuth Consent Screen**
- Left menu → **APIs & Services → OAuth consent screen**
- Select **External** → **Create**
- Fill in App name, support email, developer email → **Save & Continue** through all steps

**5. Add yourself as Test User**
- On the OAuth consent screen page → scroll to **Test users** → **+ Add Users**
- Enter your Gmail address → **Save**

**6. Create OAuth Credentials**
- Left menu → **Credentials** → **+ Create Credentials** → **OAuth client ID**
- Application type: **Desktop app** → **Create**

**7. Download & upload here**
- Click **Download JSON** on the popup
- Upload that file below ↓
            """)

        uploaded_cred = st.file_uploader(
            "📁 Upload credentials.json",
            type=["json"],
            key="upload_creds",
            help="Download this from Google Cloud Console → APIs & Services → Credentials → your OAuth client → Download JSON",
        )
        if uploaded_cred:
            try:
                save_credentials_from_upload(uploaded_cred.read())
                st.success("✅ credentials.json saved successfully!")
                st.rerun()
            except ValueError as e:
                st.error(f"Invalid file: {e}")

    st.markdown("---")

    # ── STEP 2: Connect ───────────────────────────────────────────────────
    st.markdown("### Step 2 — Connect Your Gmail Account")

    if not cred_info["exists"]:
        st.info("Complete Step 1 first — upload your credentials.json above.")
    elif gmail_status["connected"]:
        st.success(f"Already connected as **{gmail_status['email']}** — nothing to do here.")
    else:
        st.markdown("""
        <div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:12px;
                    padding:1rem 1.4rem;margin-bottom:1rem">
            <div style="font-weight:700;color:#0f172a;margin-bottom:0.4rem">
                🔐 What happens when you click Connect Gmail:
            </div>
            <ol style="color:#374151;font-size:0.88rem;margin:0;padding-left:1.2rem;line-height:2">
                <li>A browser tab opens automatically on this machine</li>
                <li>You sign in with your Gmail account</li>
                <li>Google shows what access is requested (read + send + compose)</li>
                <li>You click <b>Allow</b></li>
                <li>The browser closes and returns here — ✅ Connected</li>
            </ol>
            <div style="color:#64748b;font-size:0.8rem;margin-top:0.6rem">
                Your password is never stored. Only a secure OAuth token is saved locally.
            </div>
        </div>
        """, unsafe_allow_html=True)

        col_btn, col_hint = st.columns([1, 2])
        with col_btn:
            if st.button("🔗 Connect Gmail", type="primary", use_container_width=True):
                with st.spinner("Opening browser for Google sign-in…"):
                    try:
                        result = connect_gmail()
                        if result["connected"]:
                            st.success(f"✅ Connected as {result['email']}!")
                            st.rerun()
                        else:
                            st.error(f"Connection failed: {result.get('error', 'Unknown error')}")
                    except Exception as exc:
                        st.error(f"Error: {exc}")
        with col_hint:
            st.caption(
                "A browser window will open on this machine. "
                "If it doesn't open automatically, check your taskbar."
            )

    st.markdown("---")

    # ── STEP 3: Send mode ─────────────────────────────────────────────────
    st.markdown("### Step 3 — Send Mode")
    st.caption(
        "Controls whether report emails are sent directly or created as Gmail drafts "
        "for you to review first."
    )

    with get_db() as db:
        mode_row = db.query(AppSetting).filter(AppSetting.key == "gmail_send_mode").first()
        current_mode = mode_row.value if mode_row else "draft_only"

    new_mode = st.radio(
        "Send Mode",
        options=["draft_only", "direct_send"],
        format_func=lambda v: (
            "📝 Draft only — emails are created as Gmail drafts for your review before sending"
            if v == "draft_only"
            else "🚀 Direct send — emails are sent automatically without review"
        ),
        index=0 if current_mode == "draft_only" else 1,
        label_visibility="collapsed",
    )
    if st.button("💾 Save Send Mode", type="primary"):
        with get_db() as db:
            row = db.query(AppSetting).filter(AppSetting.key == "gmail_send_mode").first()
            if row:
                row.value = new_mode
            else:
                db.add(AppSetting(key="gmail_send_mode", value=new_mode))
        st.success("Send mode saved.")

# ══════════════════════════════════════════════════════════════════════════
# TAB 2 — BATCH & RETRY
# ══════════════════════════════════════════════════════════════════════════
with tab_batch:
    st.markdown("#### Batch Processing & Retry Policy")
    with get_db() as db:
        cb = db.query(AppSetting).filter(AppSetting.key == "batch_size").first()
        cr = db.query(AppSetting).filter(AppSetting.key == "max_retries").first()
        batch_val   = int(cb.value) if cb else settings.default_batch_size
        retries_val = int(cr.value) if cr else settings.default_max_retries

    new_batch   = st.slider("Batch Size (emails per batch)", 5, 100, batch_val, 5)
    new_retries = st.slider("Max Retries per Email", 1, 10, retries_val)

    if st.button("Save Batch Settings", type="primary"):
        with get_db() as db:
            for k, v in (("batch_size", str(new_batch)), ("max_retries", str(new_retries))):
                row = db.query(AppSetting).filter(AppSetting.key == k).first()
                if row:
                    row.value = v
                else:
                    db.add(AppSetting(key=k, value=v))
        st.success("Batch settings saved.")

# ══════════════════════════════════════════════════════════════════════════
# TAB 3 — APPEARANCE
# ══════════════════════════════════════════════════════════════════════════
with tab_appearance:
    st.markdown("#### Theme")
    theme = st.radio(
        "App Theme",
        ["light", "dark"],
        index=0 if st.session_state.get("theme", "light") == "light" else 1,
    )
    if st.button("Apply Theme"):
        st.session_state["theme"] = theme
        st.rerun()

# ══════════════════════════════════════════════════════════════════════════
# TAB 4 — USER MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════
with tab_users:
    st.markdown("#### Create User")
    with st.form("create_user_form"):
        uc1, uc2 = st.columns(2)
        with uc1:
            new_username = st.text_input("Username")
            new_email    = st.text_input("Email")
        with uc2:
            new_password = st.text_input("Temporary Password", type="password")
            new_role     = st.selectbox("Role", ["Operator", "Admin"])
        submitted = st.form_submit_button("Create User", type="primary")

    if submitted:
        ok, reason = is_strong_password(new_password)
        if not ok:
            st.error(reason)
        else:
            try:
                with get_db() as db:
                    create_user(db, new_username, new_email, new_password, new_role, user["username"])
                st.success(f"User '{new_username}' created.")
                st.rerun()
            except ValueError as e:
                st.error(str(e))

    st.markdown("#### Existing Users")
    with get_db() as db:
        users_list = list_users(db)
        user_rows = [
            {
                "id": u.id,
                "Username": u.username,
                "Email": u.email,
                "Role": u.role.value,
                "Active": u.is_active,
                "Last Login": u.last_login.strftime("%d-%b-%Y %H:%M") if u.last_login else "Never",
            }
            for u in users_list
        ]

    for r in user_rows:
        with st.container(border=True):
            c1, c2, c3 = st.columns([3, 2, 1])
            with c1:
                st.markdown(f"**{r['Username']}** · {r['Role']}")
                st.caption(r["Email"])
            with c2:
                st.caption(f"Last login: {r['Last Login']}")
            with c3:
                label = "Deactivate" if r["Active"] else "Activate"
                if st.button(label, key=f"usr_{r['id']}"):
                    with get_db() as db:
                        set_user_active(db, r["id"], not r["Active"], user["username"])
                    st.rerun()
