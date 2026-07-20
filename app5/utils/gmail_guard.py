"""
Reusable Gmail connection guard + status banner.
Import and call render_gmail_banner() at the top of any page that sends email.
"""
from __future__ import annotations
import streamlit as st
from services.gmail_auth import get_connection_status


def render_gmail_banner(stop_if_disconnected: bool = False) -> bool:
    """
    Renders a connection status banner.
    Returns True if connected, False if not.
    If stop_if_disconnected=True, calls st.stop() when not connected.
    """
    status = get_connection_status()

    if status["connected"]:
        st.markdown(f"""
        <div style="background:var(--success-soft);border:1px solid var(--success);border-radius:10px;
                    padding:0.6rem 1.2rem;margin-bottom:1rem;
                    display:flex;align-items:center;gap:0.6rem">
            <span style="width:8px;height:8px;border-radius:50%;background:var(--success);flex-shrink:0"></span>
            <span style="font-size:0.88rem;color:var(--text-primary);font-weight:600">
                Gmail connected as <code>{status['email']}</code>
            </span>
        </div>
        """, unsafe_allow_html=True)
        return True
    else:
        msg = status.get("error") or "Not connected."
        st.markdown(f"""
        <div style="background:var(--danger-soft);border:1.5px solid var(--danger);border-radius:10px;
                    padding:0.8rem 1.2rem;margin-bottom:1rem">
            <div style="display:flex;align-items:center;gap:0.6rem">
                <span style="width:8px;height:8px;border-radius:50%;background:var(--danger);flex-shrink:0"></span>
                <span style="font-weight:700;color:var(--text-primary)">Gmail not connected</span>
            </div>
            <div style="color:var(--text-secondary);font-size:0.83rem;margin-top:0.3rem">{msg}</div>
        </div>
        """, unsafe_allow_html=True)
        if st.button("→ Go to Settings to connect Gmail", type="primary"):
            st.switch_page("pages/11_Settings.py")
        if stop_if_disconnected:
            st.stop()
        return False
