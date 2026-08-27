"""
"Team wise calling Summary" tab (gid 2145281922, same spreadsheet
calling_sheet_service.py's "Calling Sheet New" lives in — a DIFFERENT
tab, don't confuse them) — feeds the Calling / WhatsApp Notification /
Voice Notification tables of the SBI Kiosk — Onboarding & Growth (Daily)
report. Added 2026-08-27.

ROSTER-CHANGE AWARE, ON PURPOSE — this is the one gotcha that actually
bit us: the RM roster changed mid-year (confirmed 10-Jul-2026: Kiran ->
Vandana, Savita -> Amuda, Shiwani unchanged), and the sheet reflects that
with a SECOND header block inserted mid-file, not a rename of the first
one. A naive single-header read silently mislabels current data under
people who already left — this happened once in this project's history
and was caught by the user re-reading the actual dates, not by re-
checking the sheet myself. Every call here re-detects header blocks
fresh rather than assuming a fixed roster, so a future roster change is
handled the same way instead of repeating the same mistake.

FAIL-SAFE, same discipline as circle_dashboard_service.py: never guess.
A structural surprise (no header block found, expected column missing)
raises CallingSheetError, which the caller must NOT paper over with a
default/fabricated value.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from services.calling_sheet_service import DEFAULT_SHEET_ID, CallingSheetError, fetch_calling_sheet_raw
from utils.logger import get_logger

logger = get_logger(__name__)

TAB_NAME = "Team wise calling Summary"

# Non-RM group labels that can appear in the forward-filled row1 header —
# never real RM names, always skipped when walking cols_by_rm.
_NON_RM_GROUPS = {"Date", "Day", "", "Remarks", "Category", "No. of CSPs",
                   "Monthly Income Range", "Priority Focus", "Total"}


def _build_col_map(row1: list, row2: list) -> dict[str, dict[str, int]]:
    """Forward-fills row1 (the merged RM-name header) and pairs it with
    row2's sub-labels (Total Call, Notifications, ...) to build
    {rm_name: {sub_label: column_index}}."""
    rm_names = []
    last = ""
    for v in row1:
        if isinstance(v, str) and v.strip():
            last = v.strip()
        rm_names.append(last)
    cols_by_rm: dict[str, dict[str, int]] = {}
    for i, (rm, label) in enumerate(zip(rm_names, row2)):
        if rm in ("Date", "Day", ""):
            continue
        label = "" if pd.isna(label) else str(label).strip()
        cols_by_rm.setdefault(rm, {})[label] = i
    return cols_by_rm


def _is_same_roster(a: dict, b: dict) -> bool:
    real = lambda cm: {k for k in cm if k not in _NON_RM_GROUPS}
    return real(a) == real(b)


def _current_roster_block(raw: pd.DataFrame) -> dict:
    """Detects every genuine header block (col0=='Date' AND col2==
    'Remarks' — excludes stray malformed re-prints elsewhere in the
    sheet) and returns the LAST one — the roster currently in force.
    Its data range extends to the end of the file, or to the next block
    whose roster is genuinely different (skips over same-roster re-print
    glitches automatically via _is_same_roster)."""
    header_starts = raw.index[
        (raw[0].astype(str).str.strip() == "Date") & (raw[2].astype(str).str.strip() == "Remarks")
    ].tolist()
    if not header_starts:
        raise CallingSheetError(f"'{TAB_NAME}': no header block found (expected col0=Date, col2=Remarks).")

    blocks = []
    for hs in header_starts:
        row1, row2 = raw.iloc[hs].tolist(), raw.iloc[hs + 1].tolist()
        blocks.append({"header_row": hs, "data_start": hs + 2, "col_map": _build_col_map(row1, row2)})

    current = blocks[-1]
    later_diff_roster = [
        b["header_row"] for b in blocks
        if b["header_row"] > current["header_row"] and not _is_same_roster(b["col_map"], current["col_map"])
    ]
    end = min(later_diff_roster) if later_diff_roster else len(raw)
    data = raw.iloc[current["data_start"]:end].reset_index(drop=True)
    # Drop stray non-date rows still inside the range (malformed re-print
    # header rows, blank spacer rows) — a row that doesn't parse as a
    # real date is never a data row.
    parsed = pd.to_datetime(data[0], format="%d-%m-%Y", errors="coerce")
    data = data[parsed.notna()].reset_index(drop=True)

    return {"col_map": current["col_map"], "data": data, "dates": pd.to_datetime(data[0], format="%d-%m-%Y")}


def _numcol(data: pd.DataFrame, idx: int | None) -> pd.Series | None:
    if idx is None:
        return None
    return pd.to_numeric(data[idx], errors="coerce").fillna(0)


def _period_sums(data, dates, col_idx, ftd_mask, lmtd_mask, mtd_mask):
    vals = _numcol(data, col_idx)
    if vals is None:
        return None
    return int(vals[ftd_mask].sum()), int(vals[lmtd_mask].sum()), int(vals[mtd_mask].sum())


def get_team_calling_summary(as_on: date | None = None) -> dict:
    """Top-level entry point. as_on defaults to yesterday (this report's
    own "as on" convention — see previous_date_str in utils/helpers.py).

    Returns {"rm_names": [...], "calling": [...], "notifications": [...],
    "voice": [...], "voice_no_column": [...]} — each of calling/
    notifications/voice is a list of {"name", "ftd", "lmtd", "mtd",
    "growth"} dicts, one per RM in the CURRENTLY active roster.
    "voice_no_column" lists RM names whose column group has no "Voice
    Notification" field at all (a real gap in the sheet — Amuda, as of
    the roster active 2026-08-27) so callers show "No column" instead of
    a misleading 0, rather than needing to know this special case
    themselves.

    Raises CallingSheetError on any structural surprise — never
    fabricates a number. Callers must not treat that as "zero activity".
    """
    if as_on is None:
        as_on = date.today() - timedelta(days=1)

    values = fetch_calling_sheet_raw(DEFAULT_SHEET_ID, TAB_NAME)
    width = max(len(row) for row in values)
    raw = pd.DataFrame([row + [""] * (width - len(row)) for row in values])

    block = _current_roster_block(raw)
    col_map, data, dates = block["col_map"], block["data"], block["dates"]

    roster_start = dates.min().date()
    mtd_start = as_on.replace(day=1)
    # Nominal "same day-count" LMTD window: 1st of last month through the
    # SAME day-of-month as as_on (e.g. as_on=26-Aug -> LMTD=1..26-Jul),
    # clamped to however many days last month actually had.
    last_month_end = mtd_start - timedelta(days=1)
    lmtd_start_nominal = last_month_end.replace(day=1)
    lmtd_end_nominal = min(last_month_end, lmtd_start_nominal.replace(day=min(as_on.day, last_month_end.day)))
    # Roster-aware: clamp the START forward to this roster's own first
    # day if it started after the 1st of last month — but the END stays
    # fixed at the nominal same-day-of-month cutoff (26-Jul), never
    # pushed later just because the start moved. A roster that started
    # AFTER the nominal end entirely has zero LMTD days available, which
    # is reported honestly (0 days), not fabricated.
    lmtd_start = max(lmtd_start_nominal, roster_start)
    lmtd_end_actual = lmtd_end_nominal

    ftd_mask = dates.dt.date == as_on
    mtd_mask = (dates.dt.date >= mtd_start) & (dates.dt.date <= as_on)
    lmtd_mask = (dates.dt.date >= lmtd_start) & (dates.dt.date <= lmtd_end_actual)

    rm_names = [rm for rm in col_map if rm not in _NON_RM_GROUPS]

    def _rows_for(sub_label: str) -> tuple[list[dict], list[str]]:
        rows, no_column = [], []
        for rm in rm_names:
            col_idx = col_map[rm].get(sub_label)
            sums = _period_sums(data, dates, col_idx, ftd_mask, lmtd_mask, mtd_mask)
            if sums is None:
                no_column.append(rm)
                continue
            ftd, lmtd, mtd = sums
            rows.append({"name": rm, "ftd": ftd, "lmtd": lmtd, "mtd": mtd,
                         "growth": "Growing" if mtd >= lmtd else "Degrowing"})
        return rows, no_column

    calling_rows = []
    for rm in rm_names:
        call_sums = _period_sums(data, dates, col_map[rm].get("Total Call"), ftd_mask, lmtd_mask, mtd_mask)
        if call_sums is None:
            continue
        ftd, lmtd, mtd = call_sums
        conn_vals = _numcol(data, col_map[rm].get("Total Connected"))
        conn_mtd = int(conn_vals[mtd_mask].sum()) if conn_vals is not None else None
        calling_rows.append({
            "name": rm, "ftd": ftd, "lmtd": lmtd, "mtd": mtd, "conn_mtd": conn_mtd,
            "growth": "Growing" if mtd >= lmtd else "Degrowing",
        })

    notif_rows, _ = _rows_for("Notifications")
    voice_rows, voice_no_column = _rows_for("Voice Notification")

    logger.info(
        "Team wise calling Summary: as_on=%s roster=%s LMTD window=[%s..%s]%s",
        as_on, rm_names, lmtd_start, lmtd_end_actual,
        " (PARTIAL - roster started mid-window)" if lmtd_start > lmtd_start_nominal else "",
    )

    return {
        "rm_names": rm_names,
        "calling": calling_rows,
        "notifications": notif_rows,
        "voice": voice_rows,
        "voice_no_column": voice_no_column,
        "as_on": as_on,
    }


# ---------------------------------------------------------------------------
# HTML rendering — SBI Kiosk teal-blue house style (matches
# circle_dashboard_service.py's palette: brand-500 #0092cc, brand-700
# #00668f). Kept in this module rather than a separate file since the
# data shape and its rendering are tightly coupled and only ever used
# together, same pattern as circle_dashboard_service.py itself.
# ---------------------------------------------------------------------------
_TABLE_OPEN = (
    '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
    'style="border-collapse:collapse;border:1px solid #cfe6ec;border-radius:8px;overflow:hidden;">'
)
_TH = (
    '<th align="left" style="background:#e6f2f5;color:#00668f;font-size:10px;font-weight:800;'
    'text-transform:uppercase;letter-spacing:0.04em;padding:8px 10px;border-bottom:1px solid #cfe6ec;">{}</th>'
)
_TD = '<td style="padding:8px 10px;font-size:13px;color:#1c2b30;border-bottom:1px solid #eef6f8;">{}</td>'
_TD_MUTED = '<td style="padding:8px 10px;font-size:13px;color:#9aa8ab;border-bottom:1px solid #eef6f8;">{}</td>'
_TD_TOTAL = (
    '<td style="padding:8px 10px;font-size:13px;font-weight:800;color:#00668f;'
    'background:#e6f2f5;border-top:2px solid #cfe6ec;">{}</td>'
)
_TD_TOTAL_MUTED = (
    '<td style="padding:8px 10px;font-size:13px;font-weight:700;color:#9aa8ab;'
    'background:#e6f2f5;border-top:2px solid #cfe6ec;">{}</td>'
)

# Each agent's own function alongside its name — ERIC = Rural Incentives,
# EYES = Inactive CSP monitoring, SARA = Social Security Schemes. No data
# source exists for any of these anywhere yet, so they render as
# placeholder rows until one does.
AGENTS = ("ERIC (Rural Incentives)", "EYES (Inactive CSP)", "SARA (SSS)")


def _growth_badge(label: str) -> str:
    if label == "Growing":
        return (
            '<span style="display:inline-block;padding:2px 9px;border-radius:99px;font-size:11px;'
            'font-weight:700;background:#e3f7ea;color:#127a38;">&#9650; Growing</span>'
        )
    return (
        '<span style="display:inline-block;padding:2px 9px;border-radius:99px;font-size:11px;'
        'font-weight:700;background:#fce8e6;color:#b3261e;">&#9660; Degrowing</span>'
    )


def render_calling_table(calling_rows: list[dict]) -> str:
    head = "".join(_TH.format(h) for h in (
        "RM Name", "Total Call (Attempts)", "Total Connected", "LMTD", "MTD", "FTD", "Growth",
    ))
    body = []
    for r in calling_rows:
        conn = r["conn_mtd"] if r["conn_mtd"] is not None else None
        body.append(
            "<tr>" + _TD.format(f'<strong>{r["name"]}</strong>')
            + _TD.format(f'{r["mtd"]:,}')
            + (_TD.format(f'{conn:,}') if conn is not None else _TD_MUTED.format("&mdash;"))
            + _TD.format(f'{r["lmtd"]:,}') + _TD.format(f'{r["mtd"]:,}') + _TD.format(f'{r["ftd"]:,}')
            + _TD.format(_growth_badge(r["growth"]))
            + "</tr>"
        )
    t_mtd = sum(r["mtd"] for r in calling_rows)
    t_conn = sum(r["conn_mtd"] or 0 for r in calling_rows)
    t_lmtd = sum(r["lmtd"] for r in calling_rows)
    t_ftd = sum(r["ftd"] for r in calling_rows)
    t_growth = "Growing" if t_mtd >= t_lmtd else "Degrowing"
    body.append(
        "<tr>" + _TD_TOTAL.format("TOTAL")
        + _TD_TOTAL.format(f'{t_mtd:,}') + _TD_TOTAL.format(f'{t_conn:,}')
        + _TD_TOTAL.format(f'{t_lmtd:,}') + _TD_TOTAL.format(f'{t_mtd:,}') + _TD_TOTAL.format(f'{t_ftd:,}')
        + _TD_TOTAL.format(_growth_badge(t_growth))
        + "</tr>"
    )
    return f'{_TABLE_OPEN}<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def render_whatsapp_table(notif_rows: list[dict]) -> str:
    head = "".join(_TH.format(h) for h in ("RM/Agent", "Messages Sent", "LMTD", "MTD", "FTD", "Growth"))
    body = []
    for r in notif_rows:
        body.append(
            "<tr>" + _TD.format(f'<strong>{r["name"]}</strong>')
            + _TD.format(f'{r["mtd"]:,}') + _TD.format(f'{r["lmtd"]:,}')
            + _TD.format(f'{r["mtd"]:,}') + _TD.format(f'{r["ftd"]:,}')
            + _TD.format(_growth_badge(r["growth"]))
            + "</tr>"
        )
    for sender in AGENTS:
        body.append(
            "<tr>" + _TD.format(f"<strong>{sender}</strong>")
            + "".join(_TD_MUTED.format("&mdash;") for _ in range(5))
            + "</tr>"
        )
    t_mtd, t_lmtd, t_ftd = (sum(r[k] for r in notif_rows) for k in ("mtd", "lmtd", "ftd"))
    t_growth = "Growing" if t_mtd >= t_lmtd else "Degrowing"
    body.append(
        "<tr>" + _TD_TOTAL.format("TOTAL (RM only &mdash; agents not yet tracked)")
        + _TD_TOTAL.format(f'{t_mtd:,}') + _TD_TOTAL.format(f'{t_lmtd:,}')
        + _TD_TOTAL.format(f'{t_mtd:,}') + _TD_TOTAL.format(f'{t_ftd:,}')
        + _TD_TOTAL.format(_growth_badge(t_growth))
        + "</tr>"
    )
    return f'{_TABLE_OPEN}<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def render_voice_table(voice_rows: list[dict], no_column_names: list[str]) -> str:
    head = "".join(_TH.format(h) for h in ("RM Name", "Messages Sent", "LMTD", "MTD", "FTD", "Growth"))
    body = []
    for r in voice_rows:
        body.append(
            "<tr>" + _TD.format(f'<strong>{r["name"]}</strong>')
            + _TD.format(f'{r["mtd"]:,}') + _TD.format(f'{r["lmtd"]:,}')
            + _TD.format(f'{r["mtd"]:,}') + _TD.format(f'{r["ftd"]:,}')
            + _TD.format(_growth_badge(r["growth"]))
            + "</tr>"
        )
    for name in no_column_names:
        body.append(
            "<tr>" + _TD.format(f"<strong>{name}</strong>")
            + "".join(_TD_MUTED.format("No column") for _ in range(4))
            + _TD_MUTED.format("&mdash;")
            + "</tr>"
        )
    t_mtd, t_lmtd, t_ftd = (sum(r[k] for r in voice_rows) for k in ("mtd", "lmtd", "ftd"))
    t_growth = "Growing" if t_mtd >= t_lmtd else "Degrowing"
    body.append(
        "<tr>" + _TD_TOTAL.format("TOTAL")
        + _TD_TOTAL.format(f'{t_mtd:,}') + _TD_TOTAL.format(f'{t_lmtd:,}')
        + _TD_TOTAL.format(f'{t_mtd:,}') + _TD_TOTAL.format(f'{t_ftd:,}')
        + _TD_TOTAL.format(_growth_badge(t_growth))
        + "</tr>"
    )
    return f'{_TABLE_OPEN}<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def get_dc_circle_pairs(limit: int | None = None) -> list[tuple[str, str]]:
    """Real (DC, Circle) pairs from the "Calling Sheet New" tab — DIFFERENT
    tab from this whole module's own "Team wise calling Summary", and a
    DIFFERENT "Circle" concept from the Circle 1A85 admin dashboard's own
    circle ID (confirmed 2026-08-27: these are genuinely different
    values, e.g. "Patna"/"Lucknow" here vs "1A85" there — never conflate
    them). "TBA" (unassigned territory) rows are excluded — not a real DC."""
    from services.calling_sheet_service import load_calling_sheet

    df = load_calling_sheet()
    pairs_df = df[["dc_name", "lho"]].dropna()
    pairs_df = pairs_df[
        (pairs_df["dc_name"].astype(str).str.strip() != "")
        & (pairs_df["lho"].astype(str).str.strip() != "")
        & (pairs_df["dc_name"].astype(str).str.strip() != "TBA")
    ]
    pairs = list(pairs_df.drop_duplicates().itertuples(index=False, name=None))
    return pairs[:limit] if limit else pairs


def get_sbi_kiosk_growth_context(_recipient_df=None, as_on: date | None = None) -> dict:
    """Top-level orchestrator for the SBI Kiosk — Onboarding & Growth
    (Daily) report's ENTIRE context — Calling/WhatsApp/Voice/Camp tables
    from this module's own live sheet read, Growth section from the
    Circle 1A85 admin dashboard (services/circle_dashboard_service.py).

    Registered as this report's aggregator in combined_digest_service.py
    -> _UNIT_AGGREGATORS, which always calls aggregator(recipient_df) —
    _recipient_df is accepted (as Corporate Center's own convention: the
    WHOLE unfiltered Calling Sheet New, per filter_for_recipient's own
    "Corporate Center gets the whole sheet" rule) but deliberately
    unused: this report has exactly ONE recipient (SBI Kiosk, a fixed
    org_units row) and every table in it is circle-wide, not filtered
    per-recipient, so there's nothing useful to do with it here — every
    table fetches its own live data directly instead.

    Never raises for a Growth-section-only failure (that section
    degrades to an honest "could not reach the dashboard" message on its
    own — see circle_dashboard_service.render_growth_section_html) but
    DOES let a Team-wise-Calling-Summary failure propagate — that data
    is this report's actual subject matter, so a failure there means
    there is genuinely nothing honest to send, not something to send a
    placeholder for.
    """
    from services.circle_dashboard_service import get_circle_growth_context, render_growth_section_html

    summary = get_team_calling_summary(as_on=as_on)
    dc_circle_pairs = get_dc_circle_pairs(limit=6)
    growth_ctx = get_circle_growth_context()

    return {
        "Report_Name": "SBI Kiosk — Onboarding & Growth",
        "Calling_Table_HTML": render_calling_table(summary["calling"]),
        "WhatsApp_Table_HTML": render_whatsapp_table(summary["notifications"]),
        "Voice_Table_HTML": render_voice_table(summary["voice"], summary["voice_no_column"]),
        "Camp_Table_HTML": render_camp_table(dc_circle_pairs),
        "Growth_Section_HTML": render_growth_section_html(growth_ctx),
    }


def render_camp_table(dc_circle_pairs: list[tuple[str, str]]) -> str:
    """Camps Held + LMTD/MTD/FTD/Growth are placeholder — no camp-activity
    data source exists anywhere yet. DC Name/Circle are real, from
    calling_sheet_service.load_calling_sheet()'s own dc_name/lho columns
    (passed in by the caller, NOT from the Circle 1A85 admin dashboard —
    that "Circle" is a different, unrelated concept)."""
    head = "".join(_TH.format(h) for h in ("DC Name", "Circle", "Camps Held", "LMTD", "MTD", "FTD", "Growth"))
    body = []
    for dc, circle in dc_circle_pairs:
        body.append(
            "<tr>" + _TD.format(f"<strong>{dc}</strong>") + _TD.format(circle)
            + "".join(_TD_MUTED.format("&mdash;") for _ in range(5))
            + "</tr>"
        )
    body.append(
        "<tr>" + _TD_TOTAL.format("TOTAL")
        + "".join(_TD_TOTAL_MUTED.format("&mdash;") for _ in range(6))
        + "</tr>"
    )
    return f'{_TABLE_OPEN}<thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'
