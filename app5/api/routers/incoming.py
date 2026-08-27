from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from api.auth import get_current_user
from database.db import get_db
from database.incoming_models import IncomingEmail, IncomingReplyTemplate
from services import automation_settings_service as auto_settings
from services import incoming_service as svc
from services import sent_mail_service
from services.incoming_ack_service import ACK_INTENTS, _is_sbi_sender
from utils.helpers import utc_iso

router = APIRouter(prefix="/api/incoming", tags=["incoming"])


@router.get("/kpis")
def kpis(user: dict = Depends(get_current_user)):
    return svc.get_incoming_kpis()


@router.get("/automation-summary")
def automation_summary(sbi_only: bool = False, user: dict = Depends(get_current_user)):
    return svc.get_automation_summary(sbi_only=sbi_only)


@router.get("/subject-patterns")
def subject_patterns(user: dict = Depends(get_current_user)):
    return svc.get_subject_patterns()


@router.get("/recipient-kind-summary")
def recipient_kind_summary(user: dict = Depends(get_current_user)):
    return svc.get_recipient_kind_summary()


@router.get("/triage-summary")
def triage_summary(sbi_only: bool = False, user: dict = Depends(get_current_user)):
    return svc.get_triage_summary(sbi_only=sbi_only)


@router.get("/messages")
def messages(
    tier: str | None = None, intent: str | None = None, limit: int = 200,
    page: int | None = Query(None, ge=1), pageSize: int | None = Query(None, ge=1, le=100),
    sbi_only: bool = False,
    user: dict = Depends(get_current_user),
):
    return svc.get_incoming_messages(
        tier=tier, intent=intent, limit=limit, page=page, page_size=pageSize, sbi_only=sbi_only,
    )


# ======================================================================
# Task queue — the workable units parsed out of task-tier mail.
# Marking a task done changes ONLY this app's record: it never replies to,
# archives, or otherwise touches the source email in Gmail.
# ======================================================================
@router.get("/tasks")
def tasks(
    status: str = "open", task_type: str | None = None, limit: int = 500,
    user: dict = Depends(get_current_user),
):
    return svc.get_task_queue(status=status, task_type=task_type, limit=limit)


@router.get("/tasks-summary")
def tasks_summary(user: dict = Depends(get_current_user)):
    return svc.get_task_queue_summary()


# ---- Limit-approval forwarding workflow (REAL SEND) -------------------
# create-forward-sends calls gmail_service.send_message — the forward goes
# out immediately, no draft/review step. Manual trigger for testing; the
# scheduler's own cycle (services/scheduler_service.py) is what actually
# drives this in normal operation.
@router.post("/limit-forward-sends")
def limit_forward_sends(max_items: int = 25, user: dict = Depends(get_current_user)):
    from services import limit_forward_service as lfs
    return lfs.create_forward_sends(max_items=max_items)


@router.post("/limit-close-approved")
def limit_close_approved(user: dict = Depends(get_current_user)):
    from services import limit_forward_service as lfs
    return lfs.close_tickets_from_approvals()


@router.get("/limit-forward-status")
def limit_forward_status(user: dict = Depends(get_current_user)):
    from services import limit_forward_service as lfs
    with get_db() as db:
        since = auto_settings.get_limit_forward_since(db)
        return {
            "enabled": auto_settings.get_limit_forward_enabled(db),
            "since": since.isoformat() if since else None,
            "forwardTo": lfs.FORWARD_TO,
            "forwardCc": lfs.FORWARD_CC,
        }


@router.patch("/limit-forward-toggle")
def limit_forward_toggle(body: ToggleIn, user: dict = Depends(get_current_user)):
    with get_db() as db:
        auto_settings.set_limit_forward_enabled(db, body.enabled)
        since = auto_settings.get_limit_forward_since(db)
    return {"enabled": body.enabled, "since": since.isoformat() if since else None}


@router.post("/tasks/extract")
def tasks_extract(user: dict = Depends(get_current_user)):
    return svc.sync_extracted_tasks()


class TaskStatusIn(BaseModel):
    status: str


@router.patch("/tasks/{task_id}")
def task_set_status(task_id: int, body: TaskStatusIn, user: dict = Depends(get_current_user)):
    try:
        return svc.set_task_status(task_id, body.status, user.get("username"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/recent")
def recent(limit: int = 15, user: dict = Depends(get_current_user)):
    return svc.get_recent_incoming(limit=limit)


@router.get("/trend")
def trend(days: int = 7, user: dict = Depends(get_current_user)):
    return svc.get_incoming_trend(days=days)


@router.post("/sync")
def sync_now(user: dict = Depends(get_current_user)):
    # create_drafts hardcoded False here regardless of the service's own
    # default — this phase is detect-and-score only, never drafts or sends.
    # max_messages raised well past the service default (100) so a manual
    # click makes real progress on a full-mailbox backlog; dedup by Gmail
    # message id means clicking again just picks up where this left off.
    ingest_summary = svc.ingest_new_messages(create_drafts=False, max_messages=500)
    replies_updated = svc.recheck_pending_replies(max_check=500)
    recipient_kind_backfilled = svc.backfill_recipient_kind(max_check=500)
    # Local-only (no Gmail calls), so this is cheap to run every sync and
    # keeps triage tags current for anything ingested before the classifier
    # existed or by a code path that skipped it.
    triage_backfilled = svc.backfill_triage()
    # Also local-only, and insert-only (never reopens/closes an existing
    # task), so it's safe to run unattended on every sync.
    tasks_extracted = svc.sync_extracted_tasks()
    return {
        "ingest": ingest_summary, "replies_updated": replies_updated,
        "recipient_kind_backfilled": recipient_kind_backfilled,
        "triage_backfilled": triage_backfilled,
        "tasks_created": tasks_extracted["created"],
    }


@router.get("/sync-status")
def sync_status(user: dict = Depends(get_current_user)):
    with get_db() as db:
        return {"syncEnabled": auto_settings.get_incoming_sync_enabled(db)}


class ToggleIn(BaseModel):
    enabled: bool


@router.patch("/sync-toggle")
def sync_toggle(body: ToggleIn, user: dict = Depends(get_current_user)):
    with get_db() as db:
        auto_settings.set_incoming_sync_enabled(db, body.enabled)
    return {"syncEnabled": body.enabled}


# ======================================================================
# Incoming reply templates — the incoming-mail counterpart to the
# outbound Templates page. Storage + keyword-match classification only;
# nothing here ever drafts or sends a reply.
# ======================================================================
def _template_to_dict(t: IncomingReplyTemplate) -> dict:
    return {
        "id": t.id, "categoryName": t.category_name, "matchKeywords": t.match_keywords,
        "subjectTemplate": t.subject_template, "bodyTemplate": t.body_template,
        "isActive": t.is_active, "updatedAt": t.updated_at.isoformat(),
    }


@router.get("/reply-templates")
def list_reply_templates(user: dict = Depends(get_current_user)):
    with get_db() as db:
        rows = db.query(IncomingReplyTemplate).order_by(IncomingReplyTemplate.category_name).all()
        return [_template_to_dict(t) for t in rows]


class ReplyTemplateIn(BaseModel):
    categoryName: str
    matchKeywords: str
    subjectTemplate: str
    bodyTemplate: str
    isActive: bool = True


@router.post("/reply-templates")
def create_reply_template(body: ReplyTemplateIn, user: dict = Depends(get_current_user)):
    if not body.categoryName.strip() or not body.matchKeywords.strip():
        raise HTTPException(status_code=400, detail="Category name and match keywords are required.")
    with get_db() as db:
        if db.query(IncomingReplyTemplate).filter(IncomingReplyTemplate.category_name == body.categoryName.strip()).first():
            raise HTTPException(status_code=400, detail="A reply template with this category name already exists.")
        record = IncomingReplyTemplate(
            category_name=body.categoryName.strip(), match_keywords=body.matchKeywords,
            subject_template=body.subjectTemplate, body_template=body.bodyTemplate,
            is_active=body.isActive, created_by=user["id"],
        )
        db.add(record)
        db.flush()
        result = _template_to_dict(record)
    svc.reclassify_reply_templates()
    return result


@router.put("/reply-templates/{template_id}")
def update_reply_template(template_id: int, body: ReplyTemplateIn, user: dict = Depends(get_current_user)):
    if not body.categoryName.strip() or not body.matchKeywords.strip():
        raise HTTPException(status_code=400, detail="Category name and match keywords are required.")
    with get_db() as db:
        record = db.query(IncomingReplyTemplate).get(template_id)
        if not record:
            raise HTTPException(status_code=404, detail="Reply template not found.")
        record.category_name = body.categoryName.strip()
        record.match_keywords = body.matchKeywords
        record.subject_template = body.subjectTemplate
        record.body_template = body.bodyTemplate
        record.is_active = body.isActive
        result = _template_to_dict(record)
    svc.reclassify_reply_templates()
    return result


@router.delete("/reply-templates/{template_id}")
def delete_reply_template(template_id: int, user: dict = Depends(get_current_user)):
    with get_db() as db:
        record = db.query(IncomingReplyTemplate).get(template_id)
        if not record:
            raise HTTPException(status_code=404, detail="Reply template not found.")
        # Detach any emails currently matched to this template before deleting.
        from database.incoming_models import IncomingEmail
        db.query(IncomingEmail).filter(IncomingEmail.matched_reply_template_id == template_id).update(
            {IncomingEmail.matched_reply_template_id: None, IncomingEmail.match_confidence: None}
        )
        db.delete(record)
    return {"deleted": True}


@router.get("/reply-template-match-summary")
def reply_template_match_summary(user: dict = Depends(get_current_user)):
    return svc.get_reply_template_match_summary()


# ======================================================================
# Outgoing (Sent folder) mail — total volume + keyword classification for
# EVERYTHING sent from the connected account, not just this app's own
# automated report distribution (see database/incoming_models.SentEmail).
# ======================================================================
@router.get("/outgoing-mail-summary")
def outgoing_mail_summary(user: dict = Depends(get_current_user)):
    return sent_mail_service.get_outgoing_mail_summary()


@router.post("/outgoing-mail-sync")
def outgoing_mail_sync(user: dict = Depends(get_current_user)):
    # Deliberately deep — 300 skewed the classification toward whatever
    # campaign happened to be most recent (see incoming_service memory).
    # 1500 pulls a far more representative slice of the Sent folder;
    # dedup by Gmail message id means clicking again just keeps going
    # further back, same pattern as the incoming sync.
    scan_summary = sent_mail_service.scan_sent_mail(max_messages=1500)
    reply_backfilled = sent_mail_service.backfill_sent_reply_status(max_check=2000)
    return {**scan_summary, "reply_backfilled": reply_backfilled}


@router.get("/outgoing-reply-summary")
def outgoing_reply_summary(user: dict = Depends(get_current_user)):
    return sent_mail_service.get_outgoing_reply_summary()


@router.get("/outgoing-mail-detail")
def outgoing_mail_detail(
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    return sent_mail_service.get_sent_mail_detail(category=category, page=page, page_size=pageSize)


# ======================================================================
# Acknowledgment-draft automation (services/incoming_ack_service.py) — the
# "Mail Volume by Level" style drill-down, applied to the ack workflow: how
# many SBI status-push emails are drafted / still pending / already handled
# by a human reply, per category, with a click-through detail table.
# ======================================================================
@router.get("/ack-summary")
def ack_summary(user: dict = Depends(get_current_user)):
    with get_db() as db:
        rows = (
            db.query(IncomingEmail)
            .filter(IncomingEmail.recipient_kind == "to", IncomingEmail.triage_intent.in_(ACK_INTENTS))
            .all()
        )

    by_category: dict[str, dict] = {
        intent: {"category": intent, "total": 0, "drafted": 0, "repliedByHuman": 0, "pending": 0}
        for intent in ACK_INTENTS
    }
    skipped_not_sbi = 0
    for r in rows:
        if not _is_sbi_sender(r.sender):
            skipped_not_sbi += 1
            continue
        bucket = by_category[r.triage_intent]
        bucket["total"] += 1
        if r.replied:
            bucket["repliedByHuman"] += 1
        elif r.ack_draft_id:
            bucket["drafted"] += 1
        else:
            bucket["pending"] += 1

    categories = list(by_category.values())
    return {
        "totalAcrossCategories": sum(c["total"] for c in categories),
        "totalDrafted": sum(c["drafted"] for c in categories),
        "totalPending": sum(c["pending"] for c in categories),
        "totalRepliedByHuman": sum(c["repliedByHuman"] for c in categories),
        "categories": categories,
        "skippedNotSbi": skipped_not_sbi,
    }


@router.get("/ack-detail")
def ack_detail(
    category: str | None = Query(None),
    status: str | None = Query(None, pattern="^(drafted|pending|repliedByHuman)$"),
    page: int = Query(1, ge=1),
    pageSize: int = Query(25, ge=1, le=100),
    user: dict = Depends(get_current_user),
):
    with get_db() as db:
        q = db.query(IncomingEmail).filter(
            IncomingEmail.recipient_kind == "to",
            IncomingEmail.triage_intent.in_(ACK_INTENTS if not category else [category]),
        )
        rows = q.order_by(IncomingEmail.received_at.desc()).all()

    filtered = []
    for r in rows:
        if not _is_sbi_sender(r.sender):
            continue
        row_status = "repliedByHuman" if r.replied else ("drafted" if r.ack_draft_id else "pending")
        if status and row_status != status:
            continue
        filtered.append({
            "id": r.id,
            "sender": r.sender,
            "subject": r.subject,
            "category": r.triage_intent,
            "receivedAt": utc_iso(r.received_at) if r.received_at else None,
            "status": row_status,
            "ackDraftId": r.ack_draft_id,
        })

    total = len(filtered)
    start = (page - 1) * pageSize
    page_rows = filtered[start:start + pageSize]
    return {"total": total, "page": page, "pageSize": pageSize, "rows": page_rows}
