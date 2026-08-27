from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from fastapi import APIRouter, Depends, Query

from api.auth import get_current_user
from config import settings
from database.db import get_db
from database.models import DistributionJob, EmailLog, EmailStatus, EmailTemplate, ReportMaster
from database.org_models import OrgLevel
from services.automation_settings_service import get_autosend_enabled
from services.calling_sheet_service import load_calling_sheet
from services.combined_digest_service import is_effectively_automated
from services.report_aggregation_service import (
    MERGED_INTO_OTHER_REPORT,
    aggregate_account_opening,
    aggregate_inactive_csps,
    aggregate_loan_lead_generation,
    aggregate_sss,
)
from utils.helpers import utc_iso

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# Fixed display order for org levels everywhere in the by-level breakdown —
# never derived from whatever order the DB happens to return, so the chart
# and tiles don't reshuffle between requests.
_LEVEL_ORDER = [OrgLevel.RBO.value, OrgLevel.LHO.value, OrgLevel.BRANCH.value, OrgLevel.CORP.value, OrgLevel.AO.value]

_WINDOW_DAYS = {"7d": 7, "30d": 30, "90d": 90, "all": None}


def _since_yesterday() -> datetime:
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return today_start - timedelta(days=1)


# How far back "mail that could plausibly be opened today" reaches. 2 days
# covers yesterday's 10:30 send plus a weekend-adjacent gap, without
# dragging in stale batches nobody is still reading.
_OPEN_LOOKBACK_DAYS = 2


def _scheme_block(target: int, mtd: int, ftd: int) -> dict:
    percent = round((mtd / target) * 100, 1) if target else 0.0
    return {"target": target, "mtd": mtd, "ftd": ftd, "percent": percent}


def _national_business_metrics(df: pd.DataFrame) -> dict:
    """Company-wide progress this month, recomputed fresh from the live
    Calling Sheet on every request — the same numbers the automated reports
    themselves are built from, just rolled up to every RBO/LHO at once
    instead of one recipient's slice."""
    ao = aggregate_account_opening(df)
    sss = aggregate_sss(df)
    inactive = aggregate_inactive_csps(df)
    loans = aggregate_loan_lead_generation(df)

    return {
        "pmjdy": _scheme_block(ao["PMJDY_Target"], ao["PMJDY_MTD_Achievement"], ao["PMJDY_FTD_Achievement"]),
        "schemes": {
            "APY": _scheme_block(sss["APY_Target"], sss["APY_MTD"], sss["APY_FTD"]),
            "PMSBY": _scheme_block(sss["PMSBY_Target"], sss["PMSBY_MTD"], sss["PMSBY_FTD"]),
            "PMJJBY": _scheme_block(sss["PMJJBY_Target"], sss["PMJJBY_MTD"], sss["PMJJBY_FTD"]),
        },
        "sssOverall": _scheme_block(sss["SSS_Target"], sss["SSS_MTD_Achievement"], sss["SSS_FTD_Achievement"]),
        "totalCsps": len(df),
        "inactiveCsps": inactive["Inactive_CSP_Count"],
        "inactivePercent": inactive["Inactive_Percent"],
        "loanLeadsMtd": loans["Loan_Lead_Count"],
        "activeLeadCsps": loans["CSPs_With_Leads"],
    }


def _org_leaderboard(df: pd.DataFrame, name_col: str, email_col: str, n: int, context_col: str | None = None) -> dict:
    """Top/bottom performing units at one org level, ranked by this month's
    PMJDY (Account Opening) target-achievement % — grouped by email (the
    true identity of a unit; the same name label can map to more than one
    real inbox and vice versa, confirmed live in this sheet).

    context_col disambiguates a display name that isn't unique on its own —
    RBO names in this sheet are bare per-circle numbers ("3", "5") reused
    independently across different LHOs, so two entirely different real
    RBOs can share the same label. When given, the majority value of that
    column (e.g. "lho") is appended so the leaderboard never shows two
    identical-looking rows for different real units.
    """
    sub = df.copy()
    sub[email_col] = sub[email_col].astype(str).str.strip()
    sub = sub[sub[email_col].str.contains("@", regex=False)]
    if sub.empty:
        return {"top": [], "bottom": []}

    sub["_key"] = sub[email_col].str.casefold()
    rows: list[dict] = []
    for _key, group in sub.groupby("_key"):
        names = group[name_col].astype(str).str.strip()
        names = names[names != ""]
        name = names.mode().iloc[0] if not names.empty else _key
        if context_col:
            ctx_values = group[context_col].astype(str).str.strip()
            ctx_values = ctx_values[ctx_values != ""]
            if not ctx_values.empty:
                name = f"{name} ({ctx_values.mode().iloc[0]})"
        ao = aggregate_account_opening(group)
        if ao["PMJDY_Target"] <= 0:
            continue
        rows.append({
            "name": name, "target": ao["PMJDY_Target"], "mtd": ao["PMJDY_MTD_Achievement"],
            "percent": ao["PMJDY_MTD_Percent"], "cspCount": ao["CSP_Count"],
        })

    rows.sort(key=lambda r: r["percent"], reverse=True)
    top = rows[:n]
    bottom = list(reversed(rows[-n:])) if len(rows) > n else []
    top_names = {r["name"] for r in top}
    bottom = [r for r in bottom if r["name"] not in top_names]
    return {"top": top, "bottom": bottom}


def _get_automation_status(db) -> dict:
    reports = (
        db.query(ReportMaster).filter(ReportMaster.org_levels.isnot(None), ReportMaster.org_levels != "").all()
    )
    total_configured = len(reports)
    # A report merged into another one's combined email (e.g. Social
    # Security Scheme folded into Account Opening) IS genuinely automated
    # — it gets real computed data, just co-sent under Account Opening's
    # subject line rather than as its own email — so it counts toward
    # activeCount here, same as everywhere else in the app that shows
    # automation status (Templates page, Reports mapping). Only report
    # names with no aggregator at all (see NOT_YET_AUTOMATED_REPORTS)
    # are genuinely paused.
    active_count = sum(1 for r in reports if is_effectively_automated(r.report_name))
    paused = sorted(r.report_name for r in reports if not is_effectively_automated(r.report_name))
    merged = [
        {"report": name, "mergedInto": target} for name, target in sorted(MERGED_INTO_OTHER_REPORT.items())
    ]

    return {
        "autosendEnabled": get_autosend_enabled(db),
        "fetchTime": settings.autosend_fetch_time,
        "sendTime": settings.autosend_send_time,
        "totalConfigured": total_configured,
        "activeCount": active_count,
        "paused": paused,
        "merged": merged,
    }


def _open_stats(db, since: datetime) -> dict:
    """Opens that HAPPENED in the window, over the mail that was in play
    during it — deliberately NOT "opens among mail sent in the window".

    Reports go out at ~10:30 and are typically read the next morning, so
    keying `opened` off `sent_at` meant yesterday's report opened today
    could never be counted: its sent_at falls before today's window start.
    The denominator therefore also has to reach back — a rate whose
    numerator can include yesterday's mail but whose denominator can't
    would be nonsense (and could exceed 100%).

    Both sides now use a lookback that covers the previous send cycle.
    NOTE: this only ever reports real data once PUBLIC_BASE_URL is set —
    without it no tracking pixel is embedded at all (see
    services/email_service._inject_tracking_pixel), so opened_at stays NULL
    for every row and this correctly returns 0.
    """
    sent_since = since - timedelta(days=_OPEN_LOOKBACK_DAYS)
    in_play = db.query(EmailLog).filter(
        EmailLog.status == EmailStatus.SENT, EmailLog.sent_at >= sent_since,
    )
    total = in_play.count()
    opened = in_play.filter(EmailLog.opened_at >= since).count()
    return {"opened": opened, "total": total}


@router.get("")
def get_dashboard(user: dict = Depends(get_current_user)):
    since = _since_yesterday()
    now = datetime.now()
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    df = load_calling_sheet()
    national = _national_business_metrics(df)
    rbo_leaderboard = _org_leaderboard(df, "rbo", "rbo_email", n=5, context_col="lho")
    lho_leaderboard = _org_leaderboard(df, "lho", "lho_email", n=3)

    with get_db() as db:
        sent_q = db.query(EmailLog).filter(EmailLog.status == EmailStatus.SENT, EmailLog.sent_at >= since)
        emails_sent = sent_q.filter(EmailLog.sent_via.in_(["gmail", "graph", "smtp"])).count()
        drafted = sent_q.filter(EmailLog.sent_via == "gmail_draft").count()
        failed = db.query(EmailLog).filter(
            EmailLog.status == EmailStatus.FAILED, EmailLog.created_at >= since,
        ).count()

        jobs = (
            db.query(DistributionJob)
            .filter(DistributionJob.created_at >= since)
            .order_by(DistributionJob.created_at.desc())
            .limit(8)
            .all()
        )
        recent_jobs = [
            {
                "id": j.id,
                "report": j.upload.report_master.report_name if j.upload else "-",
                "status": j.status.value,
                "recipients": j.total_recipients,
                "sent": j.sent_count,
                "failed": j.failed_count,
                "createdAt": utc_iso(j.created_at),
            }
            for j in jobs
        ]

        automation_status = _get_automation_status(db)
        open_today = _open_stats(db, day_start)

    return {
        "lastSynced": utc_iso(now),
        "business": national,
        "rboLeaderboard": rbo_leaderboard,
        "lhoLeaderboard": lho_leaderboard,
        "automationStatus": automation_status,
        "operations": {
            "emailsSent": emails_sent,
            "drafted": drafted,
            "failed": failed,
            "openToday": open_today,
            "recentJobs": recent_jobs,
        },
    }


def _window_start(window: str) -> datetime | None:
    days = _WINDOW_DAYS.get(window, 30)
    return None if days is None else datetime.now() - timedelta(days=days)


@router.get("/outgoing-by-level")
def get_outgoing_by_level(
    window: str = Query("30d", pattern="^(7d|30d|90d|all)$"),
    user: dict = Depends(get_current_user),
):
    """Every EmailLog row (this app's own automated distribution — drafts
    and real sends), rolled up by recipient level, for the 'Mail Volume by
    Level' drill-down on the Outgoing dashboard. Small table (low hundreds
    of rows even across all history) — aggregated in Python rather than a
    grouped SQL query so the same row set backs both the level rollup and
    the per-level/per-report rollup without a second DB round trip.
    """
    start = _window_start(window)
    with get_db() as db:
        q = db.query(
            EmailLog.recipient_type, EmailLog.status, EmailLog.sent_via,
            EmailLog.opened_at, EmailLog.job_id,
        )
        if start is not None:
            q = q.filter(EmailLog.created_at >= start)
        rows = q.all()

        job_ids = {r.job_id for r in rows}
        template_by_job: dict[int, str] = {}
        if job_ids:
            job_rows = (
                db.query(DistributionJob.id, EmailTemplate.name)
                .join(EmailTemplate, DistributionJob.template_id == EmailTemplate.id)
                .filter(DistributionJob.id.in_(job_ids))
                .all()
            )
            template_by_job = {j: name for j, name in job_rows}

    by_level: dict[str, dict] = {
        lvl: {"level": lvl, "total": 0, "sent": 0, "drafted": 0, "failed": 0, "opened": 0}
        for lvl in _LEVEL_ORDER
    }
    by_level_report: dict[tuple[str, str], int] = {}

    for r in rows:
        lvl = r.recipient_type if r.recipient_type in by_level else "Other"
        if lvl not in by_level:
            by_level[lvl] = {"level": lvl, "total": 0, "sent": 0, "drafted": 0, "failed": 0, "opened": 0}
        bucket = by_level[lvl]
        bucket["total"] += 1
        if r.status == EmailStatus.FAILED:
            bucket["failed"] += 1
        elif r.sent_via == "gmail_draft":
            bucket["drafted"] += 1
        else:
            bucket["sent"] += 1
        if r.opened_at is not None:
            bucket["opened"] += 1

        report_name = template_by_job.get(r.job_id, "Other")
        key = (lvl, report_name)
        by_level_report[key] = by_level_report.get(key, 0) + 1

    levels = [
        {**bucket, "openRate": round(bucket["opened"] / bucket["total"], 3) if bucket["total"] else 0.0}
        for lvl, bucket in by_level.items() if bucket["total"] > 0 or lvl in _LEVEL_ORDER
    ]
    # Keep the fixed order for known levels; any unexpected level (there
    # shouldn't be one — recipient_type always comes from OrgLevel — appended
    # after, so a future new level still shows up rather than being dropped.
    order_index = {lvl: i for i, lvl in enumerate(_LEVEL_ORDER)}
    levels.sort(key=lambda x: order_index.get(x["level"], 999))

    by_level_report_list = [
        {"level": lvl, "report": report, "count": count}
        for (lvl, report), count in sorted(by_level_report.items(), key=lambda kv: -kv[1])
    ]

    return {
        "window": window,
        "totalAcrossLevels": sum(x["total"] for x in levels),
        "levels": levels,
        "byLevelAndReport": by_level_report_list,
    }


@router.get("/outgoing-detail")
def get_outgoing_detail(
    level: str | None = Query(None),
    report: str | None = Query(None),
    window: str = Query("30d", pattern="^(7d|30d|90d|all)$"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    """Paginated individual EmailLog rows for the drill-down table — the
    same window/level/report filters as outgoing-by-level, one level deeper.
    """
    start = _window_start(window)
    with get_db() as db:
        q = (
            db.query(EmailLog, EmailTemplate.name)
            .join(DistributionJob, EmailLog.job_id == DistributionJob.id)
            .outerjoin(EmailTemplate, DistributionJob.template_id == EmailTemplate.id)
        )
        if start is not None:
            q = q.filter(EmailLog.created_at >= start)
        if level:
            q = q.filter(EmailLog.recipient_type == level)
        if report:
            q = q.filter(EmailTemplate.name == report)

        total = q.count()
        rows = (
            q.order_by(EmailLog.created_at.desc())
            .offset((page - 1) * pageSize)
            .limit(pageSize)
            .all()
        )

        out = []
        for log, template_name in rows:
            out.append({
                "id": log.id,
                "recipientName": log.recipient_name,
                "recipientEmail": log.recipient_email,
                "level": log.recipient_type,
                "report": template_name or "Other",
                "status": log.status.value,
                "isDraft": log.sent_via == "gmail_draft",
                "createdAt": utc_iso(log.created_at),
                "sentAt": utc_iso(log.sent_at) if log.sent_at else None,
                "opened": log.opened_at is not None,
                "openedAt": utc_iso(log.opened_at) if log.opened_at else None,
            })

    return {"total": total, "page": page, "pageSize": pageSize, "rows": out}
