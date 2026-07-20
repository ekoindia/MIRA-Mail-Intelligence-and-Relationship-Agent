"""Small shared helpers: template rendering, date math, formatting."""
from __future__ import annotations

import re
from datetime import datetime, timedelta

TEMPLATE_VAR_PATTERN = re.compile(r"\{\{\s*(\w+)\s*\}\}")

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

    Unknown variables are left untouched so authors can spot typos.
    """

    def _sub(match: re.Match) -> str:
        key = match.group(1)
        return str(context.get(key, match.group(0)))

    return TEMPLATE_VAR_PATTERN.sub(_sub, text or "")


def today_str(fmt: str = "%d-%b-%Y") -> str:
    return datetime.now().strftime(fmt)


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
