"""Audit logging service - records an immutable trail of key actions."""
from __future__ import annotations

from sqlalchemy import desc
from sqlalchemy.orm import Session

from database.models import AuditLog
from utils.logger import get_logger

logger = get_logger(__name__)


def log_action(
    db: Session,
    action: str,
    user_id: int | None = None,
    username: str | None = None,
    entity_type: str | None = None,
    entity_id: str | int | None = None,
    details: str | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    """Insert one audit log row. Caller's session is committed by the caller/context."""
    entry = AuditLog(
        user_id=user_id,
        username=username,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        details=details,
        ip_address=ip_address,
    )
    db.add(entry)
    db.flush()
    logger.info("AUDIT | %s | user=%s | %s:%s | %s", action, username, entity_type, entity_id, details)
    return entry


def get_audit_logs(
    db: Session,
    action: str | None = None,
    username: str | None = None,
    date_from=None,
    date_to=None,
    limit: int = 500,
) -> list[AuditLog]:
    query = db.query(AuditLog)
    if action:
        query = query.filter(AuditLog.action == action)
    if username:
        query = query.filter(AuditLog.username == username)
    if date_from:
        query = query.filter(AuditLog.created_at >= date_from)
    if date_to:
        query = query.filter(AuditLog.created_at <= date_to)
    return query.order_by(desc(AuditLog.created_at)).limit(limit).all()
