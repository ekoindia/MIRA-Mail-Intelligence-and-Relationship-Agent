from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import get_current_user
from database.db import get_db
from database.models import DistributionJob, EmailLog, EmailStatus, ReportUpload
from database.org_models import OrgLevel
from services import email_service, incoming_service
from services.gmail_service import KNOWN_LHOS
from services.org_service import list_org_units

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
def get_dashboard(user: dict = Depends(get_current_user)):
    with get_db() as db:
        total_reports = db.query(ReportUpload).count()
        total_sent = db.query(EmailLog).filter(EmailLog.status == EmailStatus.SENT).count()
        total_failed = db.query(EmailLog).filter(EmailLog.status == EmailStatus.FAILED).count()

        jobs = db.query(DistributionJob).order_by(DistributionJob.created_at.desc()).limit(15).all()
        recent_jobs = [
            {
                "id": j.id,
                "report": j.upload.report_master.report_name if j.upload else "-",
                "status": j.status.value,
                "recipients": j.total_recipients,
                "sent": j.sent_count,
                "failed": j.failed_count,
                "createdAt": j.created_at.isoformat(),
            }
            for j in jobs
        ]

        lho_units = list_org_units(db, level=OrgLevel.LHO)

    lho_names = [u.unit_name for u in lho_units] or KNOWN_LHOS
    incoming_kpis = incoming_service.get_incoming_kpis()
    outgoing_kpis = email_service.get_outgoing_kpis()

    incoming_by_lho = {row["LHO"]: row["Incoming Emails"] for row in incoming_service.get_incoming_by_lho()}
    outgoing_by_lho = {row["LHO"]: row["Outgoing Emails"] for row in email_service.get_outgoing_by_lho()}
    all_lho_names = sorted(set(lho_names) | set(incoming_by_lho) | set(outgoing_by_lho))
    mail_by_lho = [
        {"lho": name, "incoming": incoming_by_lho.get(name, 0), "outgoing": outgoing_by_lho.get(name, 0)}
        for name in all_lho_names
    ]

    return {
        "kpis": {
            "reportsUploaded": total_reports,
            "totalOutgoing": total_sent,
            "totalIncoming": incoming_kpis["total_incoming"],
            "failedEmails": total_failed,
        },
        "mailByLho": mail_by_lho,
        "recentIncoming": incoming_service.get_recent_incoming(limit=10),
        "recentOutgoing": email_service.get_recent_outgoing(limit=10),
        "recentJobs": recent_jobs,
    }
