"""
Outgoing (Sent) mail scan + classification.

Reads the connected Gmail account's SENT folder (read-only, `in:sent`) and
classifies each message into a small set of fixed categories by keyword
matching — same transparent, non-ML approach as incoming_service.
classify_reply_template. This is deliberately a DIFFERENT data source than
the app's own automated report distribution (DistributionJob/EmailLog,
shown under the Outgoing dashboard tab): it captures every message ever
sent from the mailbox, whether through this app or manually, so the
Incoming Mail dashboard can show the full outbound picture for the
connected account. Never drafts or sends anything.
"""
from __future__ import annotations

from database.db import get_db
from database.incoming_models import SentEmail
from services import gmail_service as gs
from utils.logger import get_logger

logger = get_logger(__name__)

SENT_GMAIL_QUERY = "in:sent"

# Keyword buckets — plain substring matching (same non-ML approach as
# incoming_service.classify_reply_template), checked against SUBJECT and
# BODY separately with subject weighted much higher. This app's own report
# emails are themselves full of performance language ("MTD", "FTD",
# "Target vs Achievement") since that IS the report content — a pure
# body-density score would misfile every routine report send as
# "Performance-Based" just because performance metrics are mentioned
# throughout, when the email's actual purpose (its subject) clearly says
# "Progress Report". Subject keywords therefore avoid generic metric terms
# and stick to phrases that indicate the email's own purpose.
CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "Performance-Based": [
        "performance review", "performance evaluation", "incentive slab",
        "kpi", "appraisal", "rating", "productivity",
    ],
    "Report Distribution": [
        "progress report", "daily update", "weekly update", "monthly update",
        "digest", "distribution", "rbo update", "branch update", "lho update",
        "corporate update", "please find attached",
    ],
    "Issue Related": [
        "issue", "problem", "complaint", "error", "not working", "escalation",
        "resolve", "concern", "downtime", "failure", "urgent", "reset",
    ],
}

# Subject hits count for far more than body hits — the subject states the
# email's purpose deliberately; body text is long and noisy (see note above).
_SUBJECT_WEIGHT = 3
_BODY_WEIGHT = 1


def classify_outgoing_category(subject: str | None, body: str | None) -> tuple[str, float]:
    """Keyword-bucket classifier, not an ML model. Returns (category,
    confidence) — confidence is the winning bucket's weighted score
    normalized against its own max possible score. Falls back to
    ("Other", 0.0) when nothing matches."""
    subj = (subject or "").lower()
    body_text = (body or "").lower()
    best_cat, best_score = "Other", 0
    for cat, keywords in CATEGORY_KEYWORDS.items():
        score = 0
        for k in keywords:
            if k in subj:
                score += _SUBJECT_WEIGHT
            elif k in body_text:
                score += _BODY_WEIGHT
        if score > best_score:
            best_cat, best_score = cat, score
    if best_score == 0:
        return "Other", 0.0
    max_possible = len(CATEGORY_KEYWORDS[best_cat]) * _SUBJECT_WEIGHT
    return best_cat, round(min(best_score / max_possible, 1.0), 3)


def _already_scanned(db, gmail_id: str) -> bool:
    return db.query(SentEmail.id).filter(SentEmail.gmail_message_id == gmail_id).first() is not None


def scan_sent_mail(max_messages: int = 1500) -> dict:
    """Fetch + classify new Sent-folder messages. Dedup by Gmail message id,
    safe to call repeatedly. Commits per-row (same reasoning as
    incoming_service's per-row commits) so a slow or interrupted run keeps
    whatever it already finished. Read-only against Gmail."""
    summary = {"scanned": 0, "new": 0, "errors": 0}
    try:
        service = gs.get_gmail_client()
    except Exception as exc:  # noqa: BLE001
        logger.error("Gmail client unavailable for sent-mail scan: %s", exc)
        summary["error"] = str(exc)
        return summary

    try:
        ids = gs.list_message_ids(service, query=SENT_GMAIL_QUERY, max_results=max_messages)
    except Exception as exc:  # noqa: BLE001
        logger.error("Gmail sent-mail list failed: %s", exc)
        summary["error"] = str(exc)
        return summary

    summary["scanned"] = len(ids)

    for gmail_id in ids:
        with get_db() as db:
            if _already_scanned(db, gmail_id):
                continue
        try:
            parsed = gs.fetch_message(service, gmail_id)
            category, confidence = classify_outgoing_category(parsed.subject, parsed.body_text)
            replied, replied_at = gs.thread_has_incoming_reply(
                service, parsed.gmail_thread_id, after=parsed.received_at
            )
            with get_db() as db:
                if _already_scanned(db, gmail_id):
                    continue
                db.add(SentEmail(
                    gmail_message_id=parsed.gmail_message_id,
                    gmail_thread_id=parsed.gmail_thread_id,
                    to_header=parsed.to_header,
                    subject=parsed.subject,
                    snippet=parsed.snippet,
                    body_text=parsed.body_text,
                    sent_at=parsed.received_at,  # Date header — the send time for a Sent-folder message
                    category=category,
                    category_confidence=confidence,
                    replied=replied,
                    replied_at=replied_at,
                ))
                summary["new"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("Sent-mail scan failed for %s: %s", gmail_id, exc)
            summary["errors"] += 1

    logger.info("Sent-mail scan summary: %s", summary)
    return summary


def reclassify_sent_mail() -> int:
    """Re-runs classify_outgoing_category against every already-scanned
    row — purely local (subject/body already stored, no Gmail calls), so
    it's safe to call after tuning the keyword lists without re-scanning
    Gmail. Commits per-row, same reasoning as everywhere else here."""
    with get_db() as db:
        row_ids = [r.id for r in db.query(SentEmail.id).all()]

    updated = 0
    for row_id in row_ids:
        with get_db() as db:
            row = db.query(SentEmail).get(row_id)
            if row is None:
                continue
            category, confidence = classify_outgoing_category(row.subject, row.body_text)
            if row.category != category or row.category_confidence != confidence:
                row.category = category
                row.category_confidence = confidence
                updated += 1
    logger.info("Sent-mail reclassify: %d/%d rows updated.", updated, len(row_ids))
    return updated


def backfill_sent_reply_status(max_check: int = 2000) -> int:
    """Fills in replied/replied_at for rows scanned before that tracking
    existed (replied IS NULL) — a lightweight thread-metadata-only check
    per row (no full message re-fetch), same shape as incoming_service.
    backfill_recipient_kind. Commits per-row so a slow or interrupted run
    keeps whatever it already finished. Read-only against Gmail."""
    try:
        service = gs.get_gmail_client()
    except Exception as exc:  # noqa: BLE001
        logger.error("Gmail client unavailable for sent-reply backfill: %s", exc)
        return 0

    with get_db() as db:
        pending_ids = [
            r.id for r in
            db.query(SentEmail.id)
            .filter(SentEmail.replied.is_(None), SentEmail.gmail_thread_id.isnot(None))
            .order_by(SentEmail.sent_at.desc())
            .limit(max_check)
            .all()
        ]

    updated = 0
    for row_id in pending_ids:
        with get_db() as db:
            row = db.query(SentEmail).get(row_id)
            if row is None or row.replied is not None:
                continue
            try:
                replied, replied_at = gs.thread_has_incoming_reply(service, row.gmail_thread_id, after=row.sent_at)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Sent-reply backfill failed for thread %s: %s", row.gmail_thread_id, exc)
                continue
            row.replied = replied
            row.replied_at = replied_at
            updated += 1
    logger.info("Sent-reply backfill: %d/%d rows updated.", updated, len(pending_ids))
    return updated


def get_outgoing_reply_summary() -> dict:
    """Reply rate for outgoing mail, overall and broken down per category —
    e.g. answers 'how many Issue Related outgoing mails got a reply back'.
    Rows still awaiting backfill (replied IS NULL) are excluded from the
    denominator rather than counted as unreplied, so the rate reflects only
    what's actually been checked."""
    with get_db() as db:
        rows = (
            db.query(SentEmail.category, SentEmail.replied)
            .filter(SentEmail.replied.isnot(None))
            .all()
        )

    totals: dict[str, dict[str, int]] = {}
    for cat, replied in rows:
        c = cat or "Other"
        bucket = totals.setdefault(c, {"total": 0, "replied": 0})
        bucket["total"] += 1
        if replied:
            bucket["replied"] += 1

    ordered = ["Performance-Based", "Report Distribution", "Issue Related", "Other"]
    by_category = []
    for cat in ordered:
        t = totals.get(cat, {"total": 0, "replied": 0})
        if t["total"] == 0:
            continue
        by_category.append({
            "category": cat, "total": t["total"], "replied": t["replied"],
            "reply_rate": round(t["replied"] / t["total"], 3),
        })

    total_checked = sum(t["total"] for t in totals.values())
    total_replied = sum(t["replied"] for t in totals.values())
    return {
        "total_checked": total_checked,
        "total_replied": total_replied,
        "reply_rate": round(total_replied / total_checked, 3) if total_checked else 0.0,
        "by_category": by_category,
    }


def get_outgoing_mail_summary() -> dict:
    from sqlalchemy import func

    with get_db() as db:
        total = db.query(SentEmail).count()
        rows = (
            db.query(SentEmail.category, func.count(SentEmail.id))
            .group_by(SentEmail.category)
            .all()
        )
    by_category = {(cat or "Other"): cnt for cat, cnt in rows}
    ordered = ["Performance-Based", "Report Distribution", "Issue Related", "Other"]
    return {
        "total_outgoing": total,
        "by_category": [
            {"category": cat, "count": by_category.get(cat, 0), "pct": round(by_category.get(cat, 0) / total, 3) if total else 0.0}
            for cat in ordered if by_category.get(cat, 0) > 0
        ],
    }


def get_sent_mail_detail(category: str | None = None, page: int = 1, page_size: int = 25) -> dict:
    """Paginated individual SentEmail rows for the Outgoing Mail drill-down
    — the same category filter get_outgoing_mail_summary rolls up, one
    level deeper. Read-only."""
    from utils.helpers import utc_iso

    with get_db() as db:
        q = db.query(SentEmail)
        if category:
            q = q.filter(SentEmail.category == category)
        total = q.count()
        rows = (
            q.order_by(SentEmail.sent_at.desc().nullslast())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        out = [
            {
                "id": r.id,
                "to": r.to_header,
                "subject": r.subject,
                "category": r.category or "Other",
                "sentAt": utc_iso(r.sent_at) if r.sent_at else None,
                "replied": bool(r.replied),
            }
            for r in rows
        ]
    return {"total": total, "page": page, "pageSize": page_size, "rows": out}
