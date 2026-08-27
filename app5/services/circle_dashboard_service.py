"""
Circle 1A85 Admin Dashboard scraper — feeds the "Growth" section of the
SBI Kiosk — Onboarding & Growth (Daily) report (Focus Products, DFS, GTV,
Loan Lead Generation). Added 2026-08-27 per explicit instruction: "growth
waala data issi dashboard se lena hai, wiring kar do."

WHY SCRAPING, NOT AN API: the dashboard (internal admin dashboard, URL in
CIRCLE_DASHBOARD_URL) is a periodically-regenerated static HTML file, not
a live API — confirmed by fetching it with a plain HTTP GET (no JS
execution) and finding every number already present in the raw response.
The page's tab switcher (Focus Products / Loan Leads / Commission / ...)
is pure CSS (`.sec{display:none}`, `.sec.on{display:block}`) — the HTML
for every tab is always present, just visually hidden, so a raw GET +
BeautifulSoup parse sees everything without needing a headless browser or
clicking anything.

FAIL-SAFE, same discipline as incoming_ack_service.py's Excel parsers:
every getter here returns None (or a dict with None values) on any
structural surprise — a missing element, an unparsable number — rather
than guessing or raising. Callers must treat None as "couldn't read it
this time", not as a real zero, and fall back to a placeholder rather
than asserting a number that might be wrong.
"""
from __future__ import annotations

import os
import re

import requests
from bs4 import BeautifulSoup

from utils.logger import get_logger

logger = get_logger(__name__)

# No hardcoded fallback on purpose — this is an internal, unauthenticated
# admin dashboard; its address must come from the environment, never from
# source. Set CIRCLE_DASHBOARD_URL in .env (see .env.example).
DASHBOARD_URL = os.getenv("CIRCLE_DASHBOARD_URL", "").strip()

_FETCH_TIMEOUT_SECONDS = 20


def fetch_dashboard_soup() -> BeautifulSoup | None:
    """Plain GET, no login/JS needed (see module docstring). Returns None
    on any network/HTTP failure — never raises, so one dashboard outage
    can't take down the whole report send."""
    try:
        resp = requests.get(DASHBOARD_URL, timeout=_FETCH_TIMEOUT_SECONDS)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Circle dashboard fetch failed (%s): %s", DASHBOARD_URL, exc)
        return None
    # The server doesn't send a charset header, so requests falls back to
    # ISO-8859-1 for resp.text — silently mangling every ₹/▲/▼ multi-byte
    # UTF-8 character in the page (each becomes 2-3 garbage characters).
    # Feeding BeautifulSoup the raw bytes instead lets it auto-detect the
    # real encoding (UTF-8, per the page's own <meta charset>) correctly.
    return BeautifulSoup(resp.content, "html.parser")


def _num(text: str | None) -> float | None:
    """'33,350' / '₹198.6Cr' / '59%' / '▼4%' -> float, stripping currency
    symbols, commas, arrows and unit suffixes it doesn't try to convert
    (Cr/L are size units, not something to multiply here — callers that
    need the rupee-crore value keep the original string alongside this)."""
    if not text:
        return None
    cleaned = re.sub(r"[₹,%▲▼\s]", "", text)
    cleaned = re.sub(r"(Cr|L)$", "", cleaned)
    try:
        return float(cleaned)
    except ValueError:
        return None


def get_focus_products_circle_total(soup: BeautifulSoup) -> dict | None:
    """Circle-wide Focus Products total — Target/MTD/FTD/Balance/Achievement%
    from the Aug'26 Product-wise Summary table's own CIRCLE TOTAL footer
    row, and LMTD/Change/%Change from the separate MTD vs LMTD Comparison
    table's footer row (same "CIRCLE TOTAL" label, different table)."""
    try:
        fp_body = soup.find(id="fp-table-body")
        fp_tfoot = fp_body.find_parent("table").find("tfoot") if fp_body else None
        fp_cells = fp_tfoot.find_all("td") if fp_tfoot else []
        if len(fp_cells) < 6:
            return None
        target, mtd, ftd, balance, achievement_pct = (
            _num(fp_cells[1].get_text()), _num(fp_cells[2].get_text()),
            _num(fp_cells[3].get_text()), _num(fp_cells[4].get_text()),
            _num(fp_cells[5].get_text()),
        )

        lmtd_body = soup.find(id="lmtd-cmp-tbody")
        lmtd_tfoot = lmtd_body.find_parent("table").find("tfoot") if lmtd_body else None
        lmtd_cells = lmtd_tfoot.find_all("td") if lmtd_tfoot else []
        lmtd = _num(lmtd_cells[1].get_text()) if len(lmtd_cells) >= 2 else None
        pct_change = _num(lmtd_cells[4].get_text()) if len(lmtd_cells) >= 5 else None
        # The dashboard's own arrow tells direction unambiguously — safer
        # than re-deriving up/down from a sign that regex-stripping ▲/▼
        # already threw away.
        direction = None
        if len(lmtd_cells) >= 4:
            change_text = lmtd_cells[3].get_text()
            direction = "up" if "▲" in change_text else ("down" if "▼" in change_text else None)

        if None in (target, mtd, ftd, balance, achievement_pct):
            return None
        return {
            "target": target, "mtd": mtd, "ftd": ftd, "balance": balance,
            "achievement_pct": achievement_pct, "lmtd": lmtd,
            "pct_change": pct_change, "direction": direction,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not parse Focus Products circle total from dashboard: %s", exc)
        return None


def get_focus_products_by_product(soup: BeautifulSoup) -> list[dict] | None:
    """Per-product MTD vs LMTD — every row of the MTD vs LMTD Comparison
    table (PMJDY, APY, PMJJBY, PMSBY, RD, FD, Passbook, mATM), not just
    the Circle Total footer. Matches the exact table the user pointed at
    (Product / LMTD / MTD / Change / % Change)."""
    try:
        tbody = soup.find(id="lmtd-cmp-tbody")
        if tbody is None:
            return None
        rows = []
        for tr in tbody.find_all("tr", recursive=False):
            cells = tr.find_all("td")
            if len(cells) < 5:
                continue
            name_cell = cells[0]
            code_b = name_cell.find("b")
            code = code_b.get_text(strip=True) if code_b else name_cell.get_text(strip=True)
            sub_div = name_cell.find("div")
            full_name = sub_div.get_text(strip=True) if sub_div else ""
            lmtd, mtd, pct_change = _num(cells[1].get_text()), _num(cells[2].get_text()), _num(cells[4].get_text())
            change_text = cells[3].get_text()
            direction = "up" if "▲" in change_text else ("down" if "▼" in change_text else None)
            if lmtd is None or mtd is None:
                continue
            rows.append({
                "code": code, "full_name": full_name, "lmtd": lmtd, "mtd": mtd,
                "pct_change": pct_change, "direction": direction,
            })
        return rows or None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not parse per-product Focus Products table from dashboard: %s", exc)
        return None


def get_dfs_summary(soup: BeautifulSoup) -> dict | None:
    """'88 of 539 CSPs have qualified...' headline, and each slab's
    Target vs Achievement for the CURRENT month only.

    NO month-over-month figure — checked thoroughly (2026-08-27, per
    explicit correction that the previous version's DFS growth number was
    wrong): the dashboard's slab data — both the visible pills
    (#slab-pills-tgt / #slab-pills-ach) and the embedded chart JS object
    (`"slabs": {"counts": [...], "targets": [...], "with_slab": 88}`) —
    carries ONLY the current month, no prior-month array anywhere on this
    page. An earlier version of this function substituted the unrelated
    "DFS Priorities Incentive" COMMISSION line (a transaction-count
    product, not a CSP-qualification count) as a stand-in growth signal —
    that number was accurate to the dashboard but the WRONG metric,
    which is what actually made it "look wrong". Removed rather than
    fixed differently: there is nothing genuinely comparable to show."""
    try:
        headline_el = soup.find(id="csps-with-slab-txt")
        headline = headline_el.get_text(strip=True) if headline_el else None
        m = re.search(r"(\d+)\s+of\s+(\d+)\s+CSPs", headline or "")
        qualified, total_csps = (int(m.group(1)), int(m.group(2))) if m else (None, None)
        if qualified is None or total_csps is None:
            return None

        slabs = []
        tgt_pills = soup.find(id="slab-pills-tgt")
        ach_pills = soup.find(id="slab-pills-ach")
        tgt_map, ach_map = {}, {}
        for pill in (tgt_pills.select(".sp") if tgt_pills else []):
            text = pill.get_text(" ", strip=True)
            mm = re.match(r"(.+?)\s+Target:\s*(\d+)\s*CSPs", text)
            if mm:
                tgt_map[mm.group(1).strip()] = int(mm.group(2))
        for pill in (ach_pills.select(".sp") if ach_pills else []):
            text = pill.get_text(" ", strip=True)
            mm = re.match(r"(.+?):\s*(\d+)\s*CSPs", text)
            if mm:
                ach_map[mm.group(1).strip()] = int(mm.group(2))
        for name in tgt_map.keys() | ach_map.keys():
            slabs.append({"name": name, "target": tgt_map.get(name), "achieved": ach_map.get(name)})
        slabs.sort(key=lambda s: s["name"])

        return {"qualified": qualified, "total_csps": total_csps, "slabs": slabs}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not parse DFS summary from dashboard: %s", exc)
        return None


def _kv_by_label(container, label_substring: str) -> str | None:
    """Finds the .kl (label) div whose text contains label_substring inside
    `container`, and returns the very next sibling .kv (value) div's text.
    Label-driven lookup, not position — the same convention this app's own
    calling_sheet_service.py uses for its own header resolution, so a
    future reorder of these cards on the dashboard doesn't silently break
    this."""
    if container is None:
        return None
    for label_div in container.select(".kl"):
        if label_substring.lower() in label_div.get_text(strip=True).lower():
            value_div = label_div.find_next_sibling(class_="kv")
            if value_div:
                return value_div.get_text(strip=True)
    return None


def get_gtv_summary(soup: BeautifulSoup) -> dict | None:
    """Circle Total GTV — FTD/MTD/LMTD, from the Transactions tab's own
    card grid (labelled 'FTD GTV (...)', 'MTD GTV (...)', 'LMTD GTV
    (...)' — the exact date range is baked into the label text since the
    dashboard regenerates it daily, so this matches on the stable
    'FTD GTV'/'MTD GTV'/'LMTD GTV' prefix only). The MTD card also
    carries the dashboard's own pre-computed "▼4% vs LMTD" badge
    (class .kb) — read that directly rather than re-deriving a percent
    from the Cr-rounded display strings, which would lose precision."""
    try:
        gtv_section = soup.find(id="s2")
        ftd = _kv_by_label(gtv_section, "FTD GTV")
        mtd = _kv_by_label(gtv_section, "MTD GTV")
        lmtd = _kv_by_label(gtv_section, "LMTD GTV")
        if mtd is None or lmtd is None:
            return None

        badge_text, direction = None, None
        for label_div in gtv_section.select(".kl"):
            if "MTD GTV" in label_div.get_text(strip=True):
                card = label_div.find_parent(class_="kc")
                badge = card.select_one(".kb") if card else None
                if badge:
                    badge_text = badge.get_text(strip=True)
                    direction = "down" if "bdn" in badge.get("class", []) else (
                        "up" if "bup" in badge.get("class", []) else None
                    )
                break
        return {
            "ftd_display": ftd, "mtd_display": mtd, "lmtd_display": lmtd,
            "change_badge_text": badge_text, "direction": direction,
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not parse GTV summary from dashboard: %s", exc)
        return None


def get_loan_lead_summary(soup: BeautifulSoup) -> dict | None:
    """Circle Total Loan Lead Generation — Today (FTD)/MTD/LMTD/CSPs with
    leads, from the Loan Leads tab's own stat cards. These cards don't use
    the .kl/.kv classes the rest of the dashboard does (inline styles
    instead), so matched by the label DIV's own text + its next sibling
    DIV, not by class."""
    try:
        content = soup.find(id="lead-gen-content")
        if content is None:
            return None
        label_divs = content.find_all("div", recursive=True)
        wanted = {
            "Leads Today (FTD)": "ftd",
            "Total Leads (MTD)": "mtd",
            "Leads LMTD": "lmtd",
            "CSPs With Leads": "csps_with_leads_display",
        }
        out: dict = {}
        for div in label_divs:
            text = div.get_text(strip=True)
            for prefix, key in wanted.items():
                if text.startswith(prefix):
                    value_div = div.find_next_sibling("div")
                    if value_div:
                        raw = value_div.get_text(strip=True)
                        out[key] = raw if key == "csps_with_leads_display" else _num(raw)
                    break
        if "mtd" not in out or "lmtd" not in out:
            return None
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not parse Loan Lead summary from dashboard: %s", exc)
        return None


def get_circle_growth_context() -> dict:
    """Top-level entry point for the SBI Kiosk Growth Report's Growth
    section — fetches once, parses all 4 metrics, and returns a flat
    context dict. Any metric that couldn't be read is simply absent from
    the dict (never a guessed value) — the template-rendering side is
    responsible for showing a placeholder for whatever's missing rather
    than asserting a number. Never raises."""
    soup = fetch_dashboard_soup()
    if soup is None:
        return {"Growth_Dashboard_Reachable": False}

    focus = get_focus_products_circle_total(soup)
    focus_by_product = get_focus_products_by_product(soup)
    dfs = get_dfs_summary(soup)
    gtv = get_gtv_summary(soup)
    loan_lead = get_loan_lead_summary(soup)

    ctx: dict = {"Growth_Dashboard_Reachable": True}
    if focus:
        ctx.update({
            "Growth_Focus_MTD": focus["mtd"], "Growth_Focus_Target": focus["target"],
            "Growth_Focus_LMTD": focus["lmtd"], "Growth_Focus_Achievement_Pct": focus["achievement_pct"],
            "Growth_Focus_Pct_Change": focus["pct_change"], "Growth_Focus_Direction": focus["direction"],
        })
    if focus_by_product:
        ctx["Growth_Focus_By_Product"] = focus_by_product
    if dfs:
        ctx.update({
            "Growth_DFS_Qualified": dfs["qualified"], "Growth_DFS_Total_CSPs": dfs["total_csps"],
            "Growth_DFS_Slabs": dfs["slabs"],
        })
    if gtv:
        ctx.update({
            "Growth_GTV_FTD_Display": gtv["ftd_display"], "Growth_GTV_MTD_Display": gtv["mtd_display"],
            "Growth_GTV_LMTD_Display": gtv["lmtd_display"],
            "Growth_GTV_Change_Badge_Text": gtv["change_badge_text"], "Growth_GTV_Direction": gtv["direction"],
        })
    if loan_lead:
        ctx.update({
            "Growth_LoanLead_FTD": loan_lead.get("ftd"), "Growth_LoanLead_MTD": loan_lead.get("mtd"),
            "Growth_LoanLead_LMTD": loan_lead.get("lmtd"),
            "Growth_LoanLead_CSPs_Display": loan_lead.get("csps_with_leads_display"),
        })
    return ctx


# ---------------------------------------------------------------------------
# HTML rendering — same teal-blue card grid as the rest of the SBI Kiosk
# Growth Report, driven by whatever get_circle_growth_context() actually
# managed to read. Each card degrades to a "—" placeholder independently
# if its own metric is missing, rather than the whole section vanishing
# because one dashboard tab changed shape.
# ---------------------------------------------------------------------------
def _fmt_int(n: float | None) -> str | None:
    return f"{int(round(n)):,}" if n is not None else None


def _chip(direction: str | None, pct_text: str) -> str:
    if direction == "up":
        return (
            '<span style="display:inline-block;padding:1px 7px;border-radius:99px;font-size:10px;'
            f'font-weight:700;background:#e3f7ea;color:#127a38;">&#9650; {pct_text}</span>'
        )
    if direction == "down":
        return (
            '<span style="display:inline-block;padding:1px 7px;border-radius:99px;font-size:10px;'
            f'font-weight:700;background:#fce8e6;color:#b3261e;">&#9660; {pct_text}</span>'
        )
    return (
        '<span style="display:inline-block;padding:1px 7px;border-radius:99px;font-size:10px;'
        f'font-weight:700;background:#eef3f4;color:#5c7278;">{pct_text}</span>'
    )


def _card(label: str, value_html: str, sub_html: str, width: str = "50%") -> str:
    return (
        f'<td width="{width}" valign="top" style="padding:5px;">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#ffffff;border:1px solid #cfe6ec;border-radius:8px;height:100%;">'
        '<tr><td style="padding:12px 14px;">'
        f'<div style="font-size:10px;font-weight:800;letter-spacing:0.04em;text-transform:uppercase;'
        f'color:#00668f;margin-bottom:4px;">{label}</div>'
        f'<div style="font-size:19px;font-weight:800;color:#1c2b30;font-variant-numeric:tabular-nums;">{value_html}</div>'
        f'<div style="font-size:11px;color:#5c7278;margin-top:4px;">{sub_html}</div>'
        '</td></tr></table></td>'
    )


def _metric_row(label: str, value_html: str) -> str:
    """One labelled line inside an elaborated (full-width) card — 'MTD',
    'LMTD', 'Today' etc. each on their own row instead of crammed into
    one paragraph. A blank/&mdash; value is a deliberate "no data yet",
    not an error — same fail-safe convention as the rest of this module."""
    return (
        '<tr>'
        f'<td style="padding:3px 0;font-size:11px;color:#5c7278;width:100px;">{label}</td>'
        f'<td style="padding:3px 0;font-size:13px;font-weight:700;color:#1c2b30;'
        f'font-variant-numeric:tabular-nums;">{value_html}</td>'
        '</tr>'
    )


def _elaborated_card(title: str, headline_html: str, rows_html: str) -> str:
    """Full-width card (one per row, not squeezed 3-across) — a headline
    number plus a small label/value table underneath for the supporting
    figures (MTD/LMTD/Today/Change), per explicit instruction 2026-08-27
    ("looking congested" with the previous 33%-width 3-card layout)."""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="background:#ffffff;border:1px solid #cfe6ec;border-radius:8px;margin-bottom:8px;">'
        '<tr><td style="padding:14px 16px;">'
        f'<div style="font-size:10px;font-weight:800;letter-spacing:0.04em;text-transform:uppercase;'
        f'color:#00668f;margin-bottom:6px;">{title}</div>'
        f'<div style="font-size:22px;font-weight:800;color:#1c2b30;font-variant-numeric:tabular-nums;'
        f'margin-bottom:8px;">{headline_html}</div>'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0">{rows_html}</table>'
        '</td></tr></table>'
    )


_UNAVAILABLE_SUB = '<span style="color:#9aa8ab;">Could not read this from the dashboard just now.</span>'

_FP_TH = (
    '<th align="{align}" style="background:#e6f2f5;color:#00668f;font-size:9px;font-weight:800;'
    'text-transform:uppercase;letter-spacing:0.04em;padding:6px 8px;border-bottom:1px solid #cfe6ec;">{label}</th>'
)
_FP_TD = '<td style="padding:6px 8px;font-size:12px;color:#1c2b30;text-align:{align};border-bottom:1px solid #eef6f8;">{v}</td>'


def _render_focus_products_table(rows: list[dict] | None) -> str:
    """Product-wise LMTD/MTD/Change/%Change — the exact table the user
    pointed at (screenshot, 2026-08-27), not just a circle-total card."""
    if not rows:
        return (
            '<div style="font-size:11px;color:#5c7278;">'
            '<strong style="color:#00668f;">Focus Products</strong> — ' + _UNAVAILABLE_SUB + '</div>'
        )
    head = (
        _FP_TH.format(align="left", label="Product")
        + _FP_TH.format(align="right", label="LMTD")
        + _FP_TH.format(align="right", label="MTD")
        + _FP_TH.format(align="right", label="Change")
    )
    body_rows = []
    for r in rows:
        chip = _chip(r["direction"], f'{r["pct_change"]:g}%') if r["pct_change"] is not None else "&mdash;"
        body_rows.append(
            "<tr>"
            + _FP_TD.format(align="left", v=f'<strong>{r["code"]}</strong>')
            + _FP_TD.format(align="right", v=_fmt_int(r["lmtd"]))
            + _FP_TD.format(align="right", v=_fmt_int(r["mtd"]))
            + _FP_TD.format(align="right", v=chip)
            + "</tr>"
        )
    return (
        '<div style="font-size:10px;font-weight:800;letter-spacing:0.04em;text-transform:uppercase;'
        'color:#00668f;margin-bottom:6px;">Focus Products (Circle Total, product-wise)</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        'style="border-collapse:collapse;border:1px solid #cfe6ec;border-radius:8px;overflow:hidden;">'
        f'<thead><tr>{head}</tr></thead><tbody>{"".join(body_rows)}</tbody></table>'
    )


def _render_dfs_card(ctx: dict) -> str:
    qualified, total = ctx.get("Growth_DFS_Qualified"), ctx.get("Growth_DFS_Total_CSPs")
    if qualified is None or total is None:
        return _elaborated_card("DFS Incentive Slab", "&mdash;", _metric_row("Status", _UNAVAILABLE_SUB))
    headline = f'{qualified} <span style="font-size:13px;font-weight:600;color:#5c7278;">/ {total} CSPs qualified</span>'
    slabs = ctx.get("Growth_DFS_Slabs") or []
    slab_texts = []
    for s in slabs:
        achieved = s["achieved"] if s["achieved"] is not None else "&mdash;"
        target_part = f'/{s["target"]}' if s["target"] is not None else ""
        slab_texts.append(f'{s["name"]}: {achieved}{target_part}')
    slab_line = " &middot; ".join(slab_texts) if slab_texts else "&mdash;"
    # "Last Month" row is deliberately blank ("&mdash;"), not omitted —
    # per explicit instruction 2026-08-27, every card shows the field
    # even when there's genuinely nothing to put in it, rather than the
    # field just not existing. The dashboard (checked its underlying
    # chart data too, not just the page) carries no prior-month slab
    # figure anywhere — that's a real gap in the source, not a bug here.
    rows = (
        _metric_row("This Month", slab_line)
        + _metric_row("Last Month", "&mdash;")
    )
    return _elaborated_card("DFS Incentive Slab", headline, rows)


def _render_gtv_card(ctx: dict) -> str:
    gtv_mtd = ctx.get("Growth_GTV_MTD_Display")
    if gtv_mtd is None:
        return _elaborated_card("GTV (Circle Total)", "&mdash;", _metric_row("Status", _UNAVAILABLE_SUB))
    lmtd_disp, ftd_disp = ctx.get("Growth_GTV_LMTD_Display"), ctx.get("Growth_GTV_FTD_Display")
    # Strip the dashboard's own leading arrow glyph — _chip() adds its own
    # based on `direction`, so passing both through would double up.
    badge_text = ctx.get("Growth_GTV_Change_Badge_Text")
    badge_text_clean = re.sub(r"^[▲▼]\s*", "", badge_text) if badge_text else None
    chip = _chip(ctx.get("Growth_GTV_Direction"), badge_text_clean) if badge_text_clean else "&mdash;"
    headline = f'{gtv_mtd} <span style="font-size:13px;font-weight:600;color:#5c7278;">MTD</span>'
    rows = (
        _metric_row("Last Month (LMTD)", lmtd_disp or "&mdash;")
        + _metric_row("Change", chip)
        + _metric_row("Today (FTD)", ftd_disp or "&mdash;")
    )
    return _elaborated_card("GTV (Circle Total)", headline, rows)


def _render_loan_lead_card(ctx: dict) -> str:
    ll_mtd = ctx.get("Growth_LoanLead_MTD")
    if ll_mtd is None:
        return _elaborated_card("Loan Lead Generation", "&mdash;", _metric_row("Status", _UNAVAILABLE_SUB))
    headline = f'{_fmt_int(ll_mtd)} <span style="font-size:13px;font-weight:600;color:#5c7278;">MTD leads</span>'
    ll_lmtd, ll_ftd = ctx.get("Growth_LoanLead_LMTD"), ctx.get("Growth_LoanLead_FTD")
    change_chip = "&mdash;"
    if ll_lmtd is not None and ll_lmtd > 0:
        pct = round(abs(ll_mtd - ll_lmtd) / ll_lmtd * 100)
        direction = "up" if ll_mtd >= ll_lmtd else "down"
        change_chip = _chip(direction, f"{pct}%")
    rows = (
        _metric_row("Last Month (LMTD)", _fmt_int(ll_lmtd) or "&mdash;")
        + _metric_row("Change", change_chip)
        + _metric_row("Today (FTD)", _fmt_int(ll_ftd) or "&mdash;")
    )
    return _elaborated_card("Loan Lead Generation", headline, rows)


def render_growth_section_html(ctx: dict) -> str:
    """Renders the Growth section from a get_circle_growth_context()
    result: Focus Products as a full product-wise table (matching what
    was asked for, 2026-08-27), DFS/GTV/Loan Lead as 3 cards below. If
    the dashboard itself was unreachable, the whole thing degrades to
    one honest message instead of fabricated "—" cards that would look
    identical to genuinely-zero data."""
    if not ctx.get("Growth_Dashboard_Reachable"):
        return (
            '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            'style="background:#f0f9fb;border:1px dashed #a8d8e8;border-radius:8px;">'
            '<tr><td style="padding:14px 16px;font-size:13px;color:#3d6b78;">'
            '<strong style="color:#00668f;">Overall Growth</strong> &mdash; the Circle Admin Dashboard '
            'could not be reached just now.</td></tr></table>'
        )

    focus_table = _render_focus_products_table(ctx.get("Growth_Focus_By_Product"))
    # DFS/GTV/Loan Lead render as full-width elaborated cards, stacked —
    # per explicit instruction 2026-08-27, the earlier 3-cards-in-one-row
    # (33% width each) looked congested. Each is now its own row with
    # its own small labelled table (This/Last Month, Change, Today).
    dfs_card = _render_dfs_card(ctx)
    gtv_card = _render_gtv_card(ctx)
    loan_lead_card = _render_loan_lead_card(ctx)

    return (
        '<div style="font-size:11px;color:#9aa8ab;margin-bottom:8px;">'
        'Source: Circle 1A85 Admin Dashboard, fetched live.</div>'
        f'<div style="margin-bottom:10px;">{focus_table}</div>'
        f'{dfs_card}{gtv_card}{loan_lead_card}'
    )
