"""
Real-data source for the "Calling Sheet" — the single Google Sheet
(tab "Calling Sheet New") that backs every automated report defined in
"Scheduled Outgoing Mail – Reporting Framework.docx".

This is a LIVE, actively-edited operational sheet: its width grows every
day (a new 5-column "calling block" is appended per calling date), and its
*structure* can also shift (columns get inserted/removed by whoever edits
it — e.g. "Circle Head Email"/"Branch Email"/"LHO mail ID" were added
after this integration first shipped, shifting every column after them by
4). Several header labels are also month-parameterized text that rolls
over every month (e.g. "MTD Achievement July'26" becomes "...August'26").

Because of that, columns are NEVER addressed by a hardcoded integer
position. Every fetch re-resolves the columns it needs from the sheet's
*current* two-row header (row 1 = merged group label, row 2 = field name)
by name/pattern — see `_resolve_columns()`. If the sheet is edited again
tomorrow, the next fetch adapts automatically instead of silently reading
the wrong column.
"""
from __future__ import annotations

import os
import re

import pandas as pd

from services.gmail_auth import get_credentials
from utils.logger import get_logger

logger = get_logger(__name__)

# No hardcoded fallback on purpose — the real spreadsheet ID is an
# operational detail, not something to keep in source. Set
# CALLING_SHEET_SPREADSHEET_ID in .env (see .env.example).
DEFAULT_SHEET_ID = os.getenv("CALLING_SHEET_SPREADSHEET_ID", "").strip()
DEFAULT_TAB = "Calling Sheet New"

# Scheme mapping confirmed by user:
#   SSS             = PMJJBY + PMSBY + APY
#   Account Opening = PMJDY
SSS_SCHEMES = ("PMJJBY", "PMSBY", "APY")
ACCOUNT_OPENING_SCHEME = "PMJDY"

# Reports with no clean matching columns in this sheet — skipped per user
# confirmation until a matching data source is identified:
#   - Server Issue report      (its own tab has only Date/Day/Type/Start/End)
#   - CSP Physical Camp report (no matching columns anywhere in the workbook)
UNMAPPED_REPORTS = ("Server Issue", "CSP Physical Camp")


class CallingSheetError(Exception):
    pass


def _norm(text: str) -> str:
    """Collapse whitespace/newlines and strip — header cells contain both."""
    return " ".join((text or "").split())


def _get_sheets_service():
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:  # noqa: BLE001
        raise CallingSheetError("google-api-python-client not installed.") from exc

    creds = get_credentials(interactive=False)
    if creds is None:
        raise CallingSheetError(
            "Gmail/Google account not connected. Connect it in Settings first — "
            "the same connection is used to read the Calling Sheet."
        )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def fetch_calling_sheet_raw(
    sheet_id: str = DEFAULT_SHEET_ID, tab: str = DEFAULT_TAB
) -> list[list[str]]:
    """
    Return every raw row (including both header rows) from the sheet.
    Unqualified `range=tab` — no fixed A1:<col><row> bound — so the fetch
    auto-sizes to however wide/tall the sheet actually is right now,
    including any columns appended or inserted since the last fetch.
    """
    service = _get_sheets_service()
    result = (
        service.spreadsheets()
        .values()
        .get(spreadsheetId=sheet_id, range=f"'{tab}'")
        .execute()
    )
    values = result.get("values", [])
    if len(values) < 3:
        raise CallingSheetError(f"Tab '{tab}' has no data rows below the header.")
    return values


class _HeaderIndex:
    """
    Parses the sheet's two-row header into something columns can be looked
    up from by name/pattern instead of a hardcoded position.

    `groups` is row 1 forward-filled across its merged span (Excel/Sheets
    convention: a group label only appears in the leftmost cell of its
    span, every other cell in that span is blank in the raw data).
    """

    def __init__(self, row1: list[str], row2: list[str], width: int):
        row1 = row1 + [""] * (width - len(row1))
        row2 = row2 + [""] * (width - len(row2))

        groups: list[str] = []
        current = ""
        for v in row1:
            nv = _norm(v)
            if nv:
                current = nv
            groups.append(current)

        self.groups = groups
        self.fields = [_norm(v) for v in row2]
        self.width = width

    def field_indices(self, *, group: str | None = None, group_prefix: str | None = None,
                       field: str | None = None, field_prefix: str | None = None) -> list[int]:
        """Indices matching all given constraints, in column order."""
        out = []
        for i in range(self.width):
            if group is not None and self.groups[i] != group:
                continue
            if group_prefix is not None and not self.groups[i].startswith(group_prefix):
                continue
            if field is not None and self.fields[i] != field:
                continue
            if field_prefix is not None and not self.fields[i].startswith(field_prefix):
                continue
            out.append(i)
        return out

    def one(self, label: str, **kwargs) -> int:
        matches = self.field_indices(**kwargs)
        if not matches:
            raise CallingSheetError(f"Calling Sheet column not found: {label} ({kwargs})")
        return matches[0]

    def last(self, label: str, **kwargs) -> int:
        matches = self.field_indices(**kwargs)
        if not matches:
            raise CallingSheetError(f"Calling Sheet column not found: {label} ({kwargs})")
        return matches[-1]

    def ordered_groups(self, prefix: str) -> list[str]:
        """Distinct group labels starting with `prefix`, in first-seen (= chronological) order."""
        seen: list[str] = []
        for g in self.groups:
            if g.startswith(prefix) and g not in seen:
                seen.append(g)
        return seen


def _resolve_columns(header: _HeaderIndex) -> dict:
    """
    Resolve every column the aggregation layer needs, by name/pattern
    against the sheet's CURRENT header — never a hardcoded position. Runs
    on every load_calling_sheet() call so structural edits to the live
    sheet (columns inserted/removed, month-rollover on labels like
    "MTD Achievement July'26") are picked up automatically.
    """
    col: dict = {}

    # CSP Code's header cell was live-edited on the sheet 2026-09-02 (caught
    # mid-edit at one point reading the stray "p"), settling on "CSP ID" —
    # per explicit instruction, treat that as the same column rather than
    # hard-failing every report over a rename. Tries the original name
    # first so this is a no-op the day the sheet reverts, if it ever does.
    csp_code_idx = header.field_indices(field="CSP Code") or header.field_indices(field="CSP ID")
    if not csp_code_idx:
        raise CallingSheetError("Calling Sheet column not found: CSP Code (tried 'CSP Code' and 'CSP ID')")
    col["csp_code"] = csp_code_idx[0]

    # --- Identity / hierarchy (unique field names, no group) ---
    identity = {
        "csp_name": "CSP Name",
        "csp_email": "CSP Mail ID",
        "rm_email": "Email of RM",
        "ao": "AO",
        "branch_code": "Branch Code",
        "branch_name": "Branch Name",
        "rbo": "RBO",
        "lho": "Circle (LHO)",
        "terminal_status": "Terminal Status",
        # DC's own name — was previously unresolved (only "Email ID DC" /
        # dc_email existed); added 2026-08-27 for the SBI Kiosk Growth
        # Report's Physical Camp table, which is organised by DC rather
        # than by RM.
        "dc_name": "District Coordinator",
    }
    for key, field in identity.items():
        col[key] = header.one(field, field=field)

    # Newly-added per-level contact emails (may not exist on an older copy
    # of the sheet — resolved as optional so a missing column degrades
    # gracefully instead of hard-failing the whole fetch). "CSP Score" moved
    # here 2026-09-02: it was wrongly bundled into the strict `identity`
    # group above, so its absence from the live sheet was hard-failing
    # EVERY report's load_calling_sheet() call — including Account Opening
    # (Daily), which never reads it at all. Only aggregate_csp_income_impact
    # (Monthly) actually uses csp_score, and it already handles a fully-NaN
    # column gracefully (empty score distribution), so resolving it as
    # optional here is safe end to end.
    for key, field in {
        "circle_head_email": "Circle Head Email",
        "branch_email": "Branch Email",
        "lho_email": "LHO mail ID",
        "rbo_email": "RBO Email",
        "ao_email": "AO Email ID",
        "dc_email": "Email ID DC",
        "csp_score": "CSP Score",
    }.items():
        matches = header.field_indices(field=field)
        col[key] = matches[0] if matches else None

    # Inactivity days: field text is month-parameterized ("Inactivity Days Jul'26").
    col["inactivity_days"] = header.one("inactivity days", field_prefix="Inactivity Days")

    # --- Avg Balance group (stable group label, stable field names) ---
    col["total_accounts"] = header.one("total accounts", group="Avg Balance", field="Total Accounts")
    col["avg_balance"] = header.one("avg balance", group="Avg Balance", field="Avg Balance")
    col["amount_to_deposit"] = header.last(
        "amount to deposit", group="Avg Balance", field_prefix="Amount to be deposited"
    )

    # --- CSP Commission group: fields are "<Month>'<YY>" plus one "...Target".
    # The most recent *actual* (non-target) month is the last non-Target
    # field in the group, by column order; the one before it is "previous
    # month" (used for a real month-on-month comparison, not a fabricated one).
    commission_fields = header.field_indices(group="CSP Commission")
    commission_actual = [i for i in commission_fields if "target" not in header.fields[i].lower()]
    if not commission_actual:
        raise CallingSheetError("Calling Sheet: no CSP Commission actual-month column found.")
    col["commission_curr_month"] = commission_actual[-1]
    col["commission_prev_month"] = commission_actual[-2] if len(commission_actual) >= 2 else None

    # --- Growth Streak group: same "<Month>'<YY>" rolling pattern as CSP
    # Commission, plus a "CSP Score" field bled into the same forward-filled
    # group (row 1 doesn't repeat the label for every column in its span).
    growth_streak_fields = [
        i for i in header.field_indices(group="Growth Streak") if header.fields[i] != "CSP Score"
    ]
    col["growth_streak_curr"] = growth_streak_fields[-1] if growth_streak_fields else None

    # --- Targets block: group label is "Targets <Month> <Year>", rolls
    # monthly — matched by prefix and take the most recent (last) one.
    target_groups = header.ordered_groups("Targets ")
    if not target_groups:
        raise CallingSheetError("Calling Sheet: no 'Targets <Month>' group found.")
    target_group = target_groups[-1]
    for scheme in (*SSS_SCHEMES, ACCOUNT_OPENING_SCHEME):
        col[f"target_{scheme.lower()}"] = header.one(
            f"target {scheme}", group=target_group, field_prefix=scheme
        )
    # DFS Slab target/achievement are CATEGORICAL labels ("Slab-1 (₹1,200)",
    # "No Slab"), not numeric counts — kept as text and aggregated as a
    # distribution (see report_aggregation_service.aggregate_dfs_incentive_slab).
    col["slab_target_label"] = header.one("target dfs slab", group=target_group, field_prefix="DFS Slab")
    # Loan Lead Generation's own monthly target ("Lead Gen\n Monthly") lives
    # in this same Targets block — previously never resolved at all, so
    # Loan Lead Generation had no target/achievement % (only a raw count).
    col["target_loan_lead"] = header.one("target lead gen", group=target_group, field_prefix="Lead Gen")

    # --- MTD Achievement blocks: one group per month (Apr, May, Jun, Jul, ...).
    # Current month = the last such group in sheet order.
    mtd_groups = header.ordered_groups("MTD Achievement")
    if not mtd_groups:
        raise CallingSheetError("Calling Sheet: no 'MTD Achievement' group found.")
    mtd_group = mtd_groups[-1]
    for scheme in (*SSS_SCHEMES, ACCOUNT_OPENING_SCHEME):
        col[f"mtd_{scheme.lower()}"] = header.one(f"mtd {scheme}", group=mtd_group, field=scheme)
    col["slab_mtd_label"] = header.one("mtd slab", group=mtd_group, field="Slab")

    # --- FTD Achievement block: current-month only, single group. ---
    ftd_groups = header.ordered_groups("FTD Achievement")
    if not ftd_groups:
        raise CallingSheetError("Calling Sheet: no 'FTD Achievement' group found.")
    ftd_group = ftd_groups[-1]
    for scheme in (*SSS_SCHEMES, ACCOUNT_OPENING_SCHEME):
        col[f"ftd_{scheme.lower()}"] = header.one(f"ftd {scheme}", group=ftd_group, field=scheme)

    # --- Loan lead generation: field text is "Count of loan lead generate <Month>'<YY>",
    # two occurrences (previous + current month) — current is the last one.
    col["loan_lead_count_curr"] = header.last(
        "loan lead count", field_prefix="Count of loan lead generate"
    )
    # "Type on loan" sits immediately after the CURRENT month's count column
    # (the previous month's count is paired with a "Status" column instead,
    # not a loan-type breakdown) — free text like "Personal Loan-1" or,
    # when a CSP generated leads across more than one type this month,
    # "Agri Loan-1, Personal Loan-5" (comma-separated "Type-Count" pairs).
    # Parsed in report_aggregation_service.aggregate_loan_lead_generation.
    col["loan_type_detail"] = header.one("loan type", field="Type on loan")

    return col


_SHEET_CACHE: dict[tuple[str, str], tuple[float, pd.DataFrame]] = {}
_SHEET_CACHE_TTL_SECONDS = 30


def clear_calling_sheet_cache() -> None:
    _SHEET_CACHE.clear()


def load_calling_sheet(
    sheet_id: str = DEFAULT_SHEET_ID, tab: str = DEFAULT_TAB, force_fresh: bool = False,
) -> pd.DataFrame:
    """
    Fetch the Calling Sheet and return it as a DataFrame with stable,
    friendly column names — resolved fresh from the sheet's current header
    every call (see _resolve_columns), so the result stays correct even
    after the live sheet's columns are inserted, removed, or reordered.
    Includes a 30s TTL cache so frequent dashboard reloads do not hammer Google Sheets.
    """
    import time
    cache_key = (sheet_id, tab)
    now = time.time()
    if not force_fresh and cache_key in _SHEET_CACHE:
        cached_time, cached_df = _SHEET_CACHE[cache_key]
        if now - cached_time < _SHEET_CACHE_TTL_SECONDS:
            return cached_df.copy()

    try:
        values = fetch_calling_sheet_raw(sheet_id, tab)
    except Exception as exc:
        if cache_key in _SHEET_CACHE:
            logger.warning("Failed to fetch fresh Calling Sheet (%s); returning cached copy.", exc)
            return _SHEET_CACHE[cache_key][1].copy()
        raise

    width = max(len(row) for row in values)
    header = _HeaderIndex(values[0], values[1], width)
    resolved = _resolve_columns(header)

    data_rows = values[2:]
    data_rows = [row + [""] * (width - len(row)) for row in data_rows]
    raw = pd.DataFrame(data_rows)

    df = pd.DataFrame(index=raw.index)
    for name, idx in resolved.items():
        df[name] = raw[idx] if idx is not None else pd.NA

    # "Inactivity Days ..." is free text like "0 Days inactive" — pull the
    # leading integer out before treating it as a number.
    df["inactivity_days"] = pd.to_numeric(
        df["inactivity_days"].astype(str).str.extract(r"(\d+)")[0], errors="coerce"
    )

    # "Total Balance"/"Avg Balance"/"Amount to be deposited" are formatted
    # currency ("₹ 34,00,000") — strip the symbol and thousands separators
    # before coercing.
    for c in ("avg_balance", "amount_to_deposit"):
        df[c] = pd.to_numeric(
            df[c].astype(str).str.replace("₹", "", regex=False).str.replace(",", "", regex=False).str.strip(),
            errors="coerce",
        )

    numeric_cols = [
        "total_accounts", "commission_curr_month", "commission_prev_month",
        "loan_lead_count_curr", "target_loan_lead", "csp_score", "growth_streak_curr",
        *(f"target_{s.lower()}" for s in (*SSS_SCHEMES, ACCOUNT_OPENING_SCHEME)),
        *(f"mtd_{s.lower()}" for s in (*SSS_SCHEMES, ACCOUNT_OPENING_SCHEME)),
        *(f"ftd_{s.lower()}" for s in (*SSS_SCHEMES, ACCOUNT_OPENING_SCHEME)),
    ]
    for c in numeric_cols:
        df[c] = pd.to_numeric(df[c], errors="coerce")

    _SHEET_CACHE[cache_key] = (now, df)
    return df.copy()


def filter_by_rbo(df: pd.DataFrame, rbo_name: str) -> pd.DataFrame:
    """Rows belonging to a single RBO — used to build per-recipient attachments."""
    return df[df["rbo"].astype(str).str.strip().str.casefold() == rbo_name.strip().casefold()]


def filter_by_lho(df: pd.DataFrame, lho_name: str) -> pd.DataFrame:
    """Rows belonging to a single LHO (Circle) — used to build per-recipient attachments."""
    return df[df["lho"].astype(str).str.strip().str.casefold() == lho_name.strip().casefold()]


def filter_by_ao(df: pd.DataFrame, ao_name: str) -> pd.DataFrame:
    return df[df["ao"].astype(str).str.strip().str.casefold() == ao_name.strip().casefold()]


# Which (name column, email column) to use per level for
# resolve_sheet_recipients() below. Corporate Center still has no email
# column anywhere in the sheet, so it isn't listed here — that level still
# needs a manually-configured recipient (Settings page). RBO Email and AO
# Email ID were added to the live sheet on 2026-07-20 — per the user, both
# are currently filled with the same placeholder address for testing and
# will be replaced with real per-unit addresses later.
# Branch has no usable name column ("Branch Name" is blank on every single
# row in the live sheet) — branch_code is the only reliable identifier.
_SHEET_RECIPIENT_COLUMNS = {
    "LHO": ("lho", "lho_email"),
    "Branch": ("branch_code", "branch_email"),
    "RBO": ("rbo", "rbo_email"),
    "AO": ("ao", "ao_email"),
}

# Per-level CC source(s): LHO mail always CCs that circle's head; Branch
# mail always CCs that branch's District Coordinator; RBO mail (Daily
# reports) CCs both that RBO's circle head AND its district coordinator(s).
# Verified against the live sheet (2026-07-20): Circle Head Email is 100%
# consistent within an LHO; DC Email is consistent for 375/377 branches
# (the exceptions are handled by taking the most common non-blank value per
# group below, rather than picking an arbitrary row). AO has no per-unit CC
# source in the sheet, so it isn't listed here.
_SHEET_CC_COLUMNS: dict[str, tuple[str, ...]] = {
    "LHO": ("circle_head_email",),
    "Branch": ("dc_email",),
    "RBO": ("circle_head_email", "dc_email"),
}


def resolve_sheet_recipients(df: pd.DataFrame, level: str) -> list[dict]:
    """
    Recipient list derived directly from the Calling Sheet's own per-row
    mail-ID columns ("LHO mail ID", "Branch Email") — no manually-maintained
    list needed for these levels. Returns
    [{"name": ..., "email": ..., "level": ..., "cc_email": ... | None}],
    de-duplicated by name.
    """
    cols = _SHEET_RECIPIENT_COLUMNS.get(level)
    if cols is None:
        return []
    name_col, email_col = cols
    cc_cols = _SHEET_CC_COLUMNS.get(level, ())

    keep_cols = [name_col, email_col] + [c for c in cc_cols if c not in (name_col, email_col)]
    sub = df[keep_cols].copy()
    sub[name_col] = sub[name_col].astype(str).str.strip()
    sub[email_col] = sub[email_col].astype(str).str.strip()
    sub = sub[(sub[name_col] != "") & sub[email_col].str.contains("@", regex=False)]

    # Most common non-blank CC value(s) per group — grouped by EMAIL, not
    # name: RBO names in this sheet are bare per-circle numbers ("3", "5")
    # reused independently across different circles, so two entirely
    # different real RBOs can share a name — grouping CC lookup by name
    # would silently mix one RBO's circle-head/DC email onto the other's
    # mail. Email is this recipient's true identity (same principle as
    # report_aggregation_service.filter_for_recipient). A stray blank/
    # inconsistent row shouldn't drop or corrupt the CC for the whole unit,
    # hence taking the most common non-blank value per column.
    cc_by_email: dict[str, str] = {}
    if cc_cols:
        for key, grp in sub.groupby(sub[email_col].str.casefold()):
            parts: list[str] = []
            for cc_col in cc_cols:
                cc_clean = grp[cc_col].astype(str).str.strip()
                valid = cc_clean[cc_clean.str.contains("@", regex=False)]
                if not valid.empty:
                    parts.append(valid.mode().iloc[0])
            if parts:
                cc_by_email[key] = ", ".join(dict.fromkeys(parts))

    # De-dupe by (name, email) pair, NOT name alone. Name-only dedup was a
    # real bug: a name can legitimately map to more than one distinct real
    # email (e.g. RBO "5" splits into two different officers for two
    # different branch clusters, both entered under the name "5") — keying
    # on name alone silently kept only the first-seen email and dropped the
    # other recipient entirely, never sending them anything. Keying on both
    # still correctly collapses the ORIGINAL bug case this was written for
    # (same LHO under inconsistent casing, "Maharashtra" vs "maharashtra",
    # same email either way) because casefold() makes the name half of the
    # key identical regardless of casing — only a genuinely different email
    # now produces a second entry.
    seen: dict[tuple[str, str], dict] = {}
    for row in sub.itertuples(index=False):
        name, email = row[0], row[1]
        email_key = email.casefold()
        key = (name.casefold(), email_key)
        if key not in seen:
            seen[key] = {"name": name, "email": email, "level": level, "cc_email": cc_by_email.get(email_key)}
    return list(seen.values())


def calling_date_blocks(sheet_id: str = DEFAULT_SHEET_ID, tab: str = DEFAULT_TAB) -> list[dict]:
    """
    Return every per-date "calling block" as
    {"date": "17/07/2026", "status": idx, "call_type": idx, "count": idx, "owner": idx, "remarks": idx}
    in sheet order. Resolved fresh from the live header — new dates appended
    to the sheet show up automatically on the next call.
    """
    values = fetch_calling_sheet_raw(sheet_id, tab)
    width = max(len(row) for row in values)
    header = _HeaderIndex(values[0], values[1], width)

    date_pattern = re.compile(r"^\d{2}/\d{2}/\d{4}$")
    date_groups = [g for g in header.ordered_groups("") if date_pattern.match(g)]
    # ordered_groups("") matches every group (empty prefix) — filter to date-shaped ones only.
    blocks = []
    for date_label in date_groups:
        idxs = header.field_indices(group=date_label)
        fields_by_name = {header.fields[i]: i for i in idxs}
        blocks.append({
            "date": date_label,
            "status": fields_by_name.get("Calling Status"),
            "call_type": fields_by_name.get("Call Type"),
            "count": fields_by_name.get("Calling Count"),
            "owner": fields_by_name.get("Calling Owner"),
            "remarks": fields_by_name.get("Calling Remarks"),
        })
    return blocks
