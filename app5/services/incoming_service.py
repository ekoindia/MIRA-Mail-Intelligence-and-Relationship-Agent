"""
Incoming-email orchestration + read layer.

`ingest_new_messages()` is the single entry point the poller/scheduler calls:
it lists candidate Gmail messages, skips any already stored (dedup by Gmail
id), then for each new message runs classify -> extract -> file attachments
-> persist -> (optionally) create a DRAFT acknowledgment. It NEVER sends mail.

The read helpers at the bottom power the upgraded Dashboard page. They query
only the additive incoming_* tables and reuse the existing get_db() session
factory — no existing service or table is modified.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from database.db import get_db
from database.incoming_models import (
    ExtractedMetric,
    IncomingAttachment,
    IncomingEmail,
    IncomingStatus,
)
from services.audit_service import log_action
from services import gmail_service as gs
from utils.logger import get_logger

logger = get_logger(__name__)

# Confidence below this routes to NEEDS_REVIEW instead of auto-drafting.
REVIEW_THRESHOLD = 0.4

# Distribution matrix (report -> downstream owner levels), from the framework.
ROUTING: dict[str, list[str]] = {
    "Social Security Scheme": ["RBO"],
    "Account Opening": ["RBO"],
    "Re-KYC & Inoperative": ["RBO"],
    "Loan Lead": ["Branch", "RBO", "LHO", "Corp"],
    "Server Issue": ["LHO", "Corp"],
    "CSP Camp": ["RBO", "LHO", "Corp"],
    "Inactive CSP": ["LHO", "Corp"],
    "DFS Slab": ["LHO", "Corp"],
    "Income Impact": ["LHO", "Corp"],
    "MoM Inputs": ["LHO", "Corp"],
}


def _already_ingested(db: Session, gmail_id: str) -> bool:
    return (
        db.query(IncomingEmail.id)
        .filter(IncomingEmail.gmail_message_id == gmail_id)
        .first()
        is not None
    )


def ingest_new_messages(
    max_messages: int = 100,
    create_drafts: bool = True,
    query: str | None = None,
) -> dict:
    """
    Fetch and process new inbox messages. Returns a run summary dict.

    Safe to call repeatedly (idempotent): dedup by Gmail message id means a
    message is processed exactly once, even across overlapping runs.
    """
    summary = {"scanned": 0, "new": 0, "attachments": 0, "drafts": 0,
               "needs_review": 0, "errors": 0}

    try:
        service = gs.get_gmail_client()
    except Exception as exc:  # noqa: BLE001
        logger.error("Gmail client unavailable: %s", exc)
        summary["error"] = str(exc)
        return summary

    try:
        ids = gs.list_message_ids(service, query=query, max_results=max_messages)
    except Exception as exc:  # noqa: BLE001
        logger.error("Gmail list failed: %s", exc)
        summary["error"] = str(exc)
        return summary

    summary["scanned"] = len(ids)

    for gmail_id in ids:
        # Dedup guard #1 — cheap DB check before any fetch.
        with get_db() as db:
            if _already_ingested(db, gmail_id):
                continue

        try:
            parsed = gs.fetch_message(service, gmail_id)
            cls = gs.classify(parsed.subject, parsed.body_text, parsed.sender)
            metrics = gs.extract_metrics(cls.report_type, parsed.body_text)

            with get_db() as db:
                # Dedup guard #2 — race-safe: unique constraint + recheck.
                if _already_ingested(db, gmail_id):
                    continue

                status = (
                    IncomingStatus.NEEDS_REVIEW
                    if cls.confidence < REVIEW_THRESHOLD or not cls.report_type
                    else IncomingStatus.EXTRACTED
                )

                row = IncomingEmail(
                    gmail_message_id=parsed.gmail_message_id,
                    gmail_thread_id=parsed.gmail_thread_id,
                    sender=parsed.sender,
                    subject=parsed.subject,
                    snippet=parsed.snippet,
                    body_text=parsed.body_text,
                    received_at=parsed.received_at,
                    report_type=cls.report_type,
                    level=cls.level,
                    lho_name=cls.lho_name,
                    rbo_name=cls.rbo_name,
                    classify_confidence=cls.confidence,
                    status=status,
                )
                db.add(row)
                db.flush()  # get row.id

                # Attachments
                for att in parsed.attachments:
                    saved = gs.save_attachment(
                        cls.report_type, cls.lho_name, parsed.received_at,
                        att["name"], att.get("data", b""),
                    )
                    db.add(IncomingAttachment(
                        incoming_email_id=row.id,
                        original_name=saved["original_name"],
                        stored_name=saved["stored_name"],
                        stored_path=saved["stored_path"],
                        mime_type=att.get("mime"),
                        size_bytes=saved["size_bytes"],
                    ))
                    summary["attachments"] += 1

                # Metrics
                for key, val in metrics.items():
                    db.add(ExtractedMetric(
                        incoming_email_id=row.id, metric_key=key, metric_value=val,
                    ))

                summary["new"] += 1
                if status == IncomingStatus.NEEDS_REVIEW:
                    summary["needs_review"] += 1

                log_action(
                    db, "INCOMING_INGEST", entity_type="IncomingEmail", entity_id=row.id,
                    details=f"report={cls.report_type}, lho={cls.lho_name}, conf={cls.confidence}",
                )

            # DRAFT acknowledgment (outside the write txn; drafts-only).
            if create_drafts and status == IncomingStatus.EXTRACTED and parsed.sender:
                try:
                    owners = ROUTING.get(cls.report_type or "", [])
                    ack_subject = f"Received: {cls.report_type} — {cls.lho_name or ''} — " \
                                  f"{(parsed.received_at or datetime.utcnow()).strftime('%d-%b-%Y')}"
                    ack_body = (
                        "Dear Sir/Ma'am,\n\n"
                        f"Acknowledging receipt of the {cls.report_type} submission"
                        f"{' for ' + cls.lho_name if cls.lho_name else ''}. "
                        "Data has been logged and consolidated"
                        f"{'; routing to ' + ', '.join(owners) if owners else ''}.\n\n"
                        "Regards,\nOperations, Eko Bharat Ventures Pvt. Ltd. | BC Code 1A85"
                    )
                    draft_id = gs.create_ack_draft(
                        service, _sender_email(parsed.sender), ack_subject, ack_body,
                        thread_id=parsed.gmail_thread_id,
                    )
                    with get_db() as db:
                        r = db.query(IncomingEmail).filter(
                            IncomingEmail.gmail_message_id == gmail_id).first()
                        if r:
                            r.ack_draft_id = draft_id
                            r.status = IncomingStatus.ROUTED
                    summary["drafts"] += 1
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Ack draft failed for %s: %s", gmail_id, exc)

        except Exception as exc:  # noqa: BLE001
            logger.error("Ingest failed for %s: %s", gmail_id, exc)
            summary["errors"] += 1
            with get_db() as db:
                if not _already_ingested(db, gmail_id):
                    db.add(IncomingEmail(
                        gmail_message_id=gmail_id,
                        status=IncomingStatus.ERROR,
                        error=str(exc)[:1000],
                    ))

    logger.info("Incoming ingest summary: %s", summary)
    return summary


def _sender_email(raw: str | None) -> str:
    if not raw:
        return ""
    m = __import__("re").search(r"<([^>]+)>", raw)
    return (m.group(1) if m else raw).strip()


# ======================================================================
# READ LAYER (for the dashboard) — queries incoming_* tables only.
# ======================================================================
def get_incoming_kpis() -> dict:
    with get_db() as db:
        total = db.query(IncomingEmail).count()
        today = db.query(IncomingEmail).filter(
            IncomingEmail.created_at >= datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        ).count()
        needs_review = db.query(IncomingEmail).filter(
            IncomingEmail.status == IncomingStatus.NEEDS_REVIEW).count()
        drafts = db.query(IncomingEmail).filter(
            IncomingEmail.ack_draft_id.isnot(None)).count()
        return {
            "total_incoming": total,
            "incoming_today": today,
            "needs_review": needs_review,
            "ack_drafts": drafts,
        }


def get_incoming_by_lho() -> list[dict]:
    """Per-LHO incoming counts for the seven circles (0-filled if absent)."""
    from sqlalchemy import func
    rows = {name: 0 for name in gs.KNOWN_LHOS}
    with get_db() as db:
        q = (
            db.query(IncomingEmail.lho_name, func.count(IncomingEmail.id))
            .filter(IncomingEmail.lho_name.isnot(None))
            .group_by(IncomingEmail.lho_name)
            .all()
        )
        for name, cnt in q:
            if name in rows:
                rows[name] = cnt
    return [{"LHO": k, "Incoming Emails": v} for k, v in rows.items()]


# Which IncomingEmail column identifies "which unit" for a given level —
# only LHO and RBO are actually resolved to a specific name by the incoming
# classifier (see services/gmail_service.classify); AO/Branch/Corporate
# Center aren't, so those levels fall back to a single "Unclassified" bucket.
_INCOMING_NAME_COLUMN = {"LHO": IncomingEmail.lho_name, "RBO": IncomingEmail.rbo_name}


def get_incoming_by_level(level: str) -> list[dict]:
    """Per-unit incoming counts for any org level (LHO/RBO/AO/Branch/Corporate Center)."""
    from sqlalchemy import func

    name_col = _INCOMING_NAME_COLUMN.get(level)
    with get_db() as db:
        if name_col is not None:
            rows = (
                db.query(name_col, func.count(IncomingEmail.id))
                .filter(IncomingEmail.level == level, name_col.isnot(None))
                .group_by(name_col)
                .all()
            )
            return [{"name": name, "incoming": cnt} for name, cnt in rows]

        total = db.query(IncomingEmail).filter(IncomingEmail.level == level).count()
        return [{"name": "Unclassified", "incoming": total}] if total else []


def get_recent_incoming(limit: int = 15) -> list[dict]:
    with get_db() as db:
        rows = (
            db.query(IncomingEmail)
            .order_by(IncomingEmail.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "Timestamp": r.received_at or r.created_at,
                "Report Type": r.report_type or "—",
                "LHO": r.lho_name or "—",
                "Level": r.level or "—",
                "Status": r.status.value if r.status else "—",
                "Confidence": r.classify_confidence if r.classify_confidence is not None else "—",
            }
            for r in rows
        ]


def get_incoming_trend(days: int = 7) -> list[dict]:
    since = datetime.utcnow() - timedelta(days=days)
    with get_db() as db:
        rows = (
            db.query(IncomingEmail)
            .filter(IncomingEmail.created_at >= since)
            .all()
        )
    buckets: dict[str, int] = {}
    for r in rows:
        d = (r.received_at or r.created_at).date().isoformat()
        buckets[d] = buckets.get(d, 0) + 1
    return [{"date": k, "count": v} for k, v in sorted(buckets.items())]
