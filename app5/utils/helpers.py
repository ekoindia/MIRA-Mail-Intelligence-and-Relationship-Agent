"""Small shared helpers: template rendering, date math, formatting."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

TEMPLATE_VAR_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")
# {{#if Flag}}...{{/if}} — keeps the enclosed text (which may itself contain
# ordinary {{Variable}} placeholders) only when context[Flag] is truthy,
# otherwise drops the whole block. One level only (non-nested) — that's all
# any current template needs.
TEMPLATE_IF_PATTERN = re.compile(r"\{\{#if\s+(\w+)\s*\}\}(.*?)\{\{/if\}\}", re.DOTALL)


def utc_iso(dt: datetime | None) -> str | None:
    """
    ISO-8601 string for a naive UTC datetime (everywhere in this app,
    timestamps are stored via datetime.utcnow(), which has no tzinfo),
    WITH an explicit "Z" suffix. Without this, dt.isoformat() alone
    produces a string with no timezone marker at all — browsers then parse
    it as *local* time instead of UTC, silently shifting every displayed
    timestamp by the browser's UTC offset (e.g. 5.5 hours off for IST).
    """
    if dt is None:
        return None
    return dt.isoformat() + "Z"

SUPPORTED_TEMPLATE_VARS = [
    "Recipient_Name",
    "Branch_Name",
    "RBO_Name",
    "AO_Name",
    "LHO_Name",
    "Corp_Name",
    "Report_Name",
    "Date",
    "Week_Number",
    "Week_Start",
    "Week_End",
    "Month_Year",
]


def render_template(text: str, context: dict[str, str]) -> str:
    """Replace {{Variable}} placeholders in a template string with context values.

    Supports one level of conditional block, {{#if Flag}}...{{/if}}, resolved
    before plain variable substitution — lets a template author write a
    normal section (individually editable {{Variable}} placeholders and all)
    that's still omitted entirely for a recipient with nothing to report
    (e.g. zero loan leads this month), instead of showing up with all-zero
    numbers.

    Unknown variables (outside a false #if block) are left untouched so
    authors can spot typos.
    """

    def _sub_if(match: re.Match) -> str:
        key, body = match.group(1), match.group(2)
        return body if context.get(key) else ""

    text = TEMPLATE_IF_PATTERN.sub(_sub_if, text or "")

    def _sub(match: re.Match) -> str:
        key = match.group(1)
        return str(context.get(key, match.group(0)))

    return TEMPLATE_VAR_PATTERN.sub(_sub, text)


def render_email_body(text: str, context: dict[str, str]) -> str:
    """Same variable substitution as render_template, plus turning plain
    line breaks into visible line breaks in the sent email. Lets a
    template's body be authored as normal text (press Enter for a new
    line) without knowing any HTML — while any HTML you DO write (e.g. a
    <table> for a scheme achievement grid) still renders as a real table,
    since only literal newline characters are touched, not markup."""
    return render_template(text, context).replace("\n", "<br>\n")


def today_str(fmt: str = "%d-%b-%Y") -> str:
    return datetime.now().strftime(fmt)


def previous_date_str(now: datetime | None = None, fmt: str = "%d-%b-%Y") -> str:
    """Yesterday relative to `now` — the daily report is fetched/sent this
    morning but reports on the previous day's completed figures."""
    now = now or datetime.now()
    return (now - timedelta(days=1)).strftime(fmt)


def week_number_str(now: datetime | None = None) -> str:
    """ISO week number, zero-padded to 2 digits, e.g. '29'."""
    now = now or datetime.now()
    return f"{now.isocalendar()[1]:02d}"


def week_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """(Monday, Sunday) of the current week."""
    now = now or datetime.now()
    monday = now - timedelta(days=now.weekday())
    return monday, monday + timedelta(days=6)


def week_start_str(now: datetime | None = None, fmt: str = "%d") -> str:
    return week_bounds(now)[0].strftime(fmt)


def week_end_str(now: datetime | None = None, fmt: str = "%d %b %Y") -> str:
    return week_bounds(now)[1].strftime(fmt)


def month_year_str(now: datetime | None = None) -> str:
    """e.g. 'July 2026'."""
    now = now or datetime.now()
    return now.strftime("%B %Y")


def next_run_datetime(
    frequency: str,
    run_time: str,
    day_of_week: int | None = None,
    day_of_month: int | None = None,
    from_dt: datetime | None = None,
) -> datetime:
    """Compute the next run datetime for a schedule given its frequency/time."""
    now = from_dt or datetime.now()
    hour, minute = (int(x) for x in run_time.split(":"))

    if frequency == "Daily":
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            candidate += timedelta(days=1)
        return candidate

    if frequency == "Weekly":
        dow = day_of_week if day_of_week is not None else 0
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        days_ahead = (dow - candidate.weekday()) % 7
        candidate += timedelta(days=days_ahead)
        if candidate <= now:
            candidate += timedelta(days=7)
        return candidate

    if frequency == "Monthly":
        dom = min(day_of_month or 1, 28)
        candidate = now.replace(day=dom, hour=hour, minute=minute, second=0, microsecond=0)
        if candidate <= now:
            if candidate.month == 12:
                candidate = candidate.replace(year=candidate.year + 1, month=1)
            else:
                candidate = candidate.replace(month=candidate.month + 1)
        return candidate

    return now + timedelta(days=1)


def format_bytes(size: int) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB"):
        if size < step:
            return f"{size:.1f} {unit}"
        size /= step
    return f"{size:.1f} TB"


def pct(numerator: int, denominator: int) -> float:
    if not denominator:
        return 0.0
    return round((numerator / denominator) * 100, 1)
