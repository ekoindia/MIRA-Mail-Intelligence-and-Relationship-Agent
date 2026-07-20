"""
Shared Streamlit UI kit: theming, auth guards, KPI cards, badges, tables,
page chrome, and small composable helpers used across every page.

Theming works by generating a `:root { --token: value; }` block in Python
(see THEMES below) and prepending it to the static stylesheet, which is
written entirely in terms of `var(--token)`. This avoids the previous
approach of bolting on ad-hoc dark-mode overrides after the fact.
"""
from __future__ import annotations

from pathlib import Path

import streamlit as st

from config import BASE_DIR, settings

CSS_PATH = BASE_DIR / "static" / "style.css"

# ----------------------------------------------------------------------
# Theme tokens
# ----------------------------------------------------------------------
THEMES = {
    "light": {
        "bg-canvas": "#f6f7f9",
        "bg-surface": "#ffffff",
        "bg-subtle": "#f1f3f6",
        "bg-sidebar": "#ffffff",
        "border-default": "#e4e7ec",
        "border-strong": "#cbd2dc",
        "text-primary": "#111827",
        "text-secondary": "#66707e",
        "accent": "#3454d1",
        "accent-strong": "#28399e",
        "accent-soft": "#e9edfc",
        "success": "#16a34a",
        "success-soft": "#e8f7ee",
        "danger": "#dc2626",
        "danger-soft": "#fdecec",
        "warning": "#d97706",
        "warning-soft": "#fdf3e2",
        "info": "#0284c7",
        "info-soft": "#e6f4fb",
        "shadow-sm": "0 1px 2px rgba(16,24,40,0.05)",
        "shadow-md": "0 4px 12px rgba(16,24,40,0.08)",
        "shadow-lg": "0 12px 32px rgba(16,24,40,0.12)",
    },
    "dark": {
        "bg-canvas": "#0c1220",
        "bg-surface": "#131b2c",
        "bg-subtle": "#182236",
        "bg-sidebar": "#0e1526",
        "border-default": "#24304a",
        "border-strong": "#37456a",
        "text-primary": "#e7eaf1",
        "text-secondary": "#95a0ba",
        "accent": "#5b7cfa",
        "accent-strong": "#8aa2ff",
        "accent-soft": "#1c2848",
        "success": "#3ecf7e",
        "success-soft": "#123524",
        "danger": "#f16565",
        "danger-soft": "#3a1c22",
        "warning": "#f0a742",
        "warning-soft": "#3a2a12",
        "info": "#4cc3f0",
        "info-soft": "#123244",
        "shadow-sm": "0 1px 2px rgba(0,0,0,0.35)",
        "shadow-md": "0 6px 16px rgba(0,0,0,0.45)",
        "shadow-lg": "0 16px 40px rgba(0,0,0,0.55)",
    },
}


def _theme_css(theme: str) -> str:
    tokens = THEMES.get(theme, THEMES["light"])
    lines = ["    :root {"]
    for key, value in tokens.items():
        lines.append(f"        --{key}: {value};")
    lines.append("    }")
    return "\n".join(lines)


def inject_css() -> None:
    theme = st.session_state.get("theme", "light")
    css = CSS_PATH.read_text() if CSS_PATH.exists() else ""
    st.markdown(f"<style>{_theme_css(theme)}\n{css}</style>", unsafe_allow_html=True)


# ----------------------------------------------------------------------
# Auth guards
# ----------------------------------------------------------------------
def require_login() -> dict:
    user = st.session_state.get("user")
    if not user:
        st.warning("Please log in from the Home page to continue.")
        st.stop()
    return user


def require_admin() -> dict:
    user = require_login()
    if user["role"] != "Admin":
        st.error("This page is restricted to Administrators.")
        st.stop()
    return user


# ----------------------------------------------------------------------
# Sidebar
# ----------------------------------------------------------------------
def render_brand_block() -> None:
    initials = "".join(w[0] for w in settings.app_name.split()[:2]).upper() or "RC"
    with st.sidebar:
        st.markdown(
            f"""
            <div class="brand-block">
                <div class="brand-mark">{initials}</div>
                <div>
                    <div class="brand-name">{settings.app_name}</div>
                    <div class="brand-sub">{settings.org_tagline}</div>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


def render_sidebar_user_box() -> None:
    user = st.session_state.get("user")
    if not user:
        return
    render_brand_block()
    with st.sidebar:
        sender_html = ""
        try:
            from services.gmail_auth import get_connection_status
            status = get_connection_status()
            if status.get("connected") and status.get("email"):
                sender_html = f"<div class='sender-chip'><span class='dot'></span>Sending as {status['email']}</div>"
        except Exception:
            pass
        st.markdown(
            f"""
            <div class="sidebar-userbox">
                <div class="u-name">{user['username']}</div>
                <span class="u-role">{user['role']}</span>
                {sender_html}
            </div>
            """,
            unsafe_allow_html=True,
        )
        st.write("")
        col1, col2 = st.columns(2)
        with col1:
            theme = st.session_state.get("theme", "light")
            if st.button("Theme", use_container_width=True, help="Toggle light / dark mode"):
                st.session_state["theme"] = "dark" if theme == "light" else "light"
                st.rerun()
        with col2:
            if st.button("Log out", use_container_width=True):
                for key in ("user",):
                    st.session_state.pop(key, None)
                st.rerun()


# ----------------------------------------------------------------------
# Page chrome
# ----------------------------------------------------------------------
def page_header(title: str, subtitle: str = "", icon: str = "", eyebrow: str = "") -> None:
    """Clean, native page title — no custom HTML tags, renders identically in every browser."""
    if eyebrow:
        st.caption(eyebrow.upper())
    st.title(f"{icon} {title}".strip())
    if subtitle:
        st.caption(subtitle)


def pipeline(steps: list[str], current_index: int) -> None:
    """Render a horizontal pipeline indicator, e.g. Report -> Mapping -> Template -> Draft -> Send."""
    html = "<div class='pipeline'>"
    for i, step in enumerate(steps):
        cls = "done" if i < current_index else ("active" if i == current_index else "")
        html += f"<div class='pipeline-step {cls}'>{i + 1}. {step}</div>"
        if i < len(steps) - 1:
            html += "<span class='pipeline-arrow'>&rarr;</span>"
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)


# ----------------------------------------------------------------------
# KPI cards
# ----------------------------------------------------------------------
def kpi_card(label: str, value: str, delta: str | None = None, icon: str = "", trend: str = "flat") -> str:
    delta_html = f"<div class='kpi-delta {trend}'>{delta}</div>" if delta else ""
    icon_html = f"<div class='kpi-icon'>{icon}</div>" if icon else ""
    return f"""
    <div class='kpi-card'>
        <div class='kpi-top'>{icon_html}</div>
        <div class='kpi-label'>{label}</div>
        <div class='kpi-value'>{value}</div>
        {delta_html}
    </div>
    """


def render_kpi_row(cards: list[tuple[str, str, str, str]]) -> None:
    """cards: list of (label, value, delta, icon). delta may be prefixed with up/down styling via trend inference."""
    cols = st.columns(len(cards))
    for col, card in zip(cols, cards):
        label, value, delta, icon = card
        trend = "flat"
        if delta:
            trend = "down" if delta.strip().startswith("-") else "up"
        with col:
            st.markdown(kpi_card(label, value, delta, icon, trend), unsafe_allow_html=True)


# ----------------------------------------------------------------------
# Status badges
# ----------------------------------------------------------------------
_BADGE_TOKENS = {
    "Sent": "success", "Completed": "success", "Active": "success", "Verified": "success",
    "Failed": "danger", "Inactive": "danger", "Error": "danger",
    "Pending": "warning", "Retrying": "warning", "Draft": "warning",
    "In Progress": "info", "Sending": "info", "Queued": "info", "Scheduled": "info",
    "Completed With Errors": "warning",
}


def status_badge(status: str) -> str:
    token = _BADGE_TOKENS.get(status, "info")
    return (
        f"<span class='badge' style=\"background:var(--{token}-soft);color:var(--{token});\">"
        f"<span class='dot' style=\"background:var(--{token});\"></span>{status}</span>"
    )


# ----------------------------------------------------------------------
# Layout helpers
# ----------------------------------------------------------------------
def section_card_open(title: str = "", action_label: str = "") -> bool:
    """Opens a styled section card. Returns True if the (optional) action button was clicked.
    Caller is responsible for closing with `section_card_close()`."""
    st.markdown("<div class='section-card'>", unsafe_allow_html=True)
    clicked = False
    if title:
        if action_label:
            c1, c2 = st.columns([5, 1])
            c1.markdown(f"<div class='section-title-row'><h4>{title}</h4></div>", unsafe_allow_html=True)
            with c2:
                clicked = st.button(action_label, key=f"action_{title}")
        else:
            st.markdown(f"<div class='section-title-row'><h4>{title}</h4></div>", unsafe_allow_html=True)
    return clicked


def section_card_close() -> None:
    st.markdown("</div>", unsafe_allow_html=True)


def empty_state(title: str, subtitle: str = "", icon: str = "\U0001F4ED") -> None:
    st.markdown(
        f"""
        <div class='empty-state'>
            <div class='icon'>{icon}</div>
            <div class='title'>{title}</div>
            <div>{subtitle}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def tag_pills(items: list[str]) -> str:
    return "".join(f"<span class='tag-pill'>{i}</span>" for i in items)


def searchable_dataframe(df, search_columns: list[str] | None = None, key: str = "search", **kwargs):
    """Render a text-search box above a dataframe and filter rows client-side.
    `search_columns` restricts which columns are matched; defaults to all string columns."""
    query = st.text_input("Search", key=key, placeholder="Filter by any visible column...", label_visibility="collapsed")
    if query and len(df):
        cols = search_columns or [c for c in df.columns if df[c].dtype == object]
        mask = False
        for c in cols:
            mask = mask | df[c].astype(str).str.contains(query, case=False, na=False)
        df = df[mask]
    st.dataframe(df, use_container_width=True, hide_index=True, **kwargs)
    return df


def toast_success(message: str) -> None:
    st.toast(message, icon="✅")


def toast_error(message: str) -> None:
    st.toast(message, icon="⚠️")
