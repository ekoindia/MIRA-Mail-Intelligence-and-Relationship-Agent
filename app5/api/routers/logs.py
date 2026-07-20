from __future__ import annotations

from fastapi import APIRouter, Depends

from api.auth import get_current_user
from database.db import get_db
from database.models import AuditLog, EmailLog
from services.audit_service import get_audit_logs

router = APIRouter(prefix="/api/logs", tags=["logs"])

# Turns a raw action code + free-text details into one readable sentence,
# instead of showing the audit trail as a raw action/entity/details dump.
_ACTION_SENTENCES = {
    "LOGIN": lambda u, d: f"{u} signed in.",
    "LOGIN_FAILED": lambda u, d: f"Failed sign-in attempt{f' for {u}' if u else ''}.",
    "CREATE_REPORT_SOURCE": lambda u, d: f"{u} connected a new report source: {d}.",
    "CREATE_AUTO_DISTRIBUTION_SCHEDULE": lambda u, d: f"{u} set up automation: {d}.",
    "SCHEDULE_ON": lambda u, d: f"{u} turned automated sending ON for \"{d}\".",
    "SCHEDULE_OFF": lambda u, d: f"{u} turned automated sending OFF for \"{d}\".",
    "SEND_DISTRIBUTION": lambda u, d: f"Report sent to recipients — {d}.",
    "REPORT_SOURCE_FETCH": lambda u, d: f"Report downloaded from its source — {d}.",
    "UPLOAD_ORG_MASTER": lambda u, d: f"{u} uploaded recipient data — {d}.",
    "UPLOAD_REPORT": lambda u, d: f"{u} uploaded a report file — {d}.",
    "CREATE_TEMPLATE": lambda u, d: f"{u} created the email template \"{d}\".",
    "EDIT_TEMPLATE": lambda u, d: f"{u} edited the email template \"{d}\".",
    "DELETE_TEMPLATE": lambda u, d: f"{u} deleted the email template \"{d}\".",
    "CREATE_DISTRIBUTION_JOB": lambda u, d: f"A distribution job was created — {d}.",
    "CREATE_REPORT_TYPE": lambda u, d: f"{u} added a new report type: {d}.",
    "EDIT_REPORT_TYPE": lambda u, d: f"{u} updated a report type — {d}.",
    "CREATE_USER": lambda u, d: f"{u} created a user account — {d}.",
    "EDIT_USER": lambda u, d: f"{u} updated a user account — {d}.",
    "CHANGE_PASSWORD": lambda u, d: f"{u} changed a password.",
}


def _humanize(log: AuditLog) -> str:
    fn = _ACTION_SENTENCES.get(log.action)
    username = log.username or "Someone"
    details = log.details or ""
    if fn:
        return fn(username, details)
    # Fallback: turn "SOME_ACTION" into "Some action" and append details.
    readable_action = log.action.replace("_", " ").capitalize()
    return f"{username}: {readable_action}" + (f" — {details}" if details else ".")


@router.get("/delivery")
def delivery_logs(status: str | None = None, limit: int = 200, user: dict = Depends(get_current_user)):
    with get_db() as db:
        q = db.query(EmailLog).order_by(EmailLog.created_at.desc())
        if status:
            q = q.filter(EmailLog.status == status)
        rows = q.limit(limit).all()
        return [
            {
                "id": r.id, "recipientName": r.recipient_name, "recipientEmail": r.recipient_email,
                "recipientType": r.recipient_type, "report": r.job.upload.report_master.report_name
                if r.job and r.job.upload else "-",
                "status": r.status.value, "channel": r.sent_via, "attempts": r.attempt_count,
                "sentAt": r.sent_at.isoformat() if r.sent_at else None, "error": r.last_error,
            }
            for r in rows
        ]


@router.get("/audit")
def audit_logs(limit: int = 200, user: dict = Depends(get_current_user)):
    with get_db() as db:
        rows = get_audit_logs(db, limit=limit)
        return [
            {
                "id": r.id, "summary": _humanize(r), "action": r.action,
                "username": r.username, "createdAt": r.created_at.isoformat(),
            }
            for r in rows
        ]
