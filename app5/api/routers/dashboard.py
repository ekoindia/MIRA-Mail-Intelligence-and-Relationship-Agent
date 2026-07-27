from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from fastapi import APIRouter, Depends

from api.auth import get_current_user
from config import settings
from database.db import get_db
from database.models import DistributionJob, EmailLog, EmailStatus, ReportMaster
from services.automation_settings_service import get_autosend_enabled
from services.calling_sheet_service import load_calling_sheet
from services.report_aggregation_service import (
    MERGED_INTO_OTHER_REPORT,
    NOT_YET_AUTOMATED_REPORTS,
    aggregate_account_opening,
    aggregate_inactive_csps,
    aggregate_loan_lead_generation,
    aggregate_sss,
)
from utils.helpers import utc_iso

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _since_yesterday() -> datetime:
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return today_start - timedelta(days=1)


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
    total_configured = (
        db.query(ReportMaster).filter(ReportMaster.org_levels.isnot(None), ReportMaster.org_levels != "").count()
    )
    paused = sorted(NOT_YET_AUTOMATED_REPORTS)
    merged = [
        {"report": name, "mergedInto": target} for name, target in sorted(MERGED_INTO_OTHER_REPORT.items())
    ]
    active_count = total_configured - len(paused) - len(merged)

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
    total = db.query(EmailLog).filter(EmailLog.status == EmailStatus.SENT, EmailLog.sent_at >= since).count()
    opened = db.query(EmailLog).filter(
        EmailLog.status == EmailStatus.SENT, EmailLog.sent_at >= since, EmailLog.opened_at.isnot(None),
    ).count()
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
