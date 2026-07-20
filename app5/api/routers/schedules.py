"""
Simple on/off automation per report. One AutoDistributionSchedule per
report's ReportSource; frequency and recipient org level(s) are inherited
straight from the ReportMaster (set once in the reporting-framework seed),
so turning a report "on" here is the only decision left to make — no
re-entering frequency/recipients per schedule.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from api.auth import get_current_user
from database.db import get_db
from database.models import ReportMaster
from database.org_models import OrgLevel
from database.report_source_models import AutoDistributionSchedule, ReportSource
from services.auto_distribution_service import DEFAULT_FETCH_TIME, DEFAULT_SEND_TIME, create_auto_schedule
from services.audit_service import log_action

router = APIRouter(prefix="/api/schedules", tags=["schedules"])


class EnableIn(BaseModel):
    fetchTime: str | None = None
    sendTime: str | None = None


@router.get("")
def list_schedules(user: dict = Depends(get_current_user)):
    with get_db() as db:
        reports = db.query(ReportMaster).order_by(ReportMaster.report_name).all()
        out = []
        for r in reports:
            source = db.query(ReportSource).filter(ReportSource.report_master_id == r.id).first()
            schedule = None
            if source:
                schedule = (
                    db.query(AutoDistributionSchedule)
                    .filter(AutoDistributionSchedule.report_source_id == source.id)
                    .first()
                )
            out.append({
                "reportId": r.id, "reportName": r.report_name,
                "frequency": r.frequency, "orgLevels": r.org_levels.split(",") if r.org_levels else [],
                "hasSource": source is not None,
                "sourceId": source.id if source else None,
                "isOn": bool(schedule and schedule.is_active),
                "fetchTime": schedule.fetch_time if schedule else DEFAULT_FETCH_TIME,
                "sendTime": schedule.run_time if schedule else DEFAULT_SEND_TIME,
                "lastFetchAt": schedule.last_fetch_at.isoformat() if schedule and schedule.last_fetch_at else None,
                "nextFetchAt": schedule.next_fetch_at.isoformat() if schedule and schedule.next_fetch_at else None,
                "lastRunAt": schedule.last_run_at.isoformat() if schedule and schedule.last_run_at else None,
                "nextRunAt": schedule.next_run_at.isoformat() if schedule and schedule.next_run_at else None,
            })
        return out


@router.post("/{report_id}/enable")
def enable(report_id: int, body: EnableIn | None = None, user: dict = Depends(get_current_user)):
    fetch_time = (body.fetchTime if body else None) or DEFAULT_FETCH_TIME
    send_time = (body.sendTime if body else None) or DEFAULT_SEND_TIME

    with get_db() as db:
        rm = db.query(ReportMaster).get(report_id)
        if not rm:
            raise HTTPException(status_code=404, detail="Report not found.")
        source = db.query(ReportSource).filter(ReportSource.report_master_id == report_id).first()
        if not source:
            raise HTTPException(
                status_code=400,
                detail="Connect this report's source (Google Sheet or API) before turning automation on.",
            )
        if not rm.frequency or not rm.org_levels:
            raise HTTPException(status_code=400, detail="This report has no configured frequency/recipients.")

        schedule = (
            db.query(AutoDistributionSchedule)
            .filter(AutoDistributionSchedule.report_source_id == source.id)
            .first()
        )
        if schedule:
            schedule.is_active = True
        else:
            levels = [OrgLevel(v) for v in rm.org_levels.split(",")]
            day_of_week = 0 if rm.frequency == "Weekly" else None
            day_of_month = 1 if rm.frequency == "Monthly" else None
            create_auto_schedule(
                db, name=f"Auto: {rm.report_name}", report_source_id=source.id,
                template_id=rm.default_template_id, org_levels=levels, org_unit_ids=None,
                frequency=rm.frequency, send_time=send_time, fetch_time=fetch_time,
                day_of_week=day_of_week, day_of_month=day_of_month,
                created_by_id=user["id"], created_by_username=user["username"],
            )
        log_action(db, "SCHEDULE_ON", user_id=user["id"], username=user["username"],
                   entity_type="ReportMaster", entity_id=report_id,
                   details=f"{rm.report_name} (fetch {fetch_time}, send {send_time})")
    return {"isOn": True, "fetchTime": fetch_time, "sendTime": send_time}


@router.post("/{report_id}/disable")
def disable(report_id: int, user: dict = Depends(get_current_user)):
    with get_db() as db:
        rm = db.query(ReportMaster).get(report_id)
        if not rm:
            raise HTTPException(status_code=404, detail="Report not found.")
        source = db.query(ReportSource).filter(ReportSource.report_master_id == report_id).first()
        if source:
            schedule = (
                db.query(AutoDistributionSchedule)
                .filter(AutoDistributionSchedule.report_source_id == source.id)
                .first()
            )
            if schedule:
                schedule.is_active = False
        log_action(db, "SCHEDULE_OFF", user_id=user["id"], username=user["username"],
                   entity_type="ReportMaster", entity_id=report_id, details=rm.report_name)
    return {"isOn": False}
