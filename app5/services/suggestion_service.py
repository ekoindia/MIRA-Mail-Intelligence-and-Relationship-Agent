"""
Suggestion detection + approval.

Detection (run_suggestion_scan) is read-only and safe to run on a timer —
it only ever inserts "pending" rows. Approval (approve_suggestion) is the
only thing that ever mutates other tables, and only ever does so for a
category with a pre-defined, bounded executor (never arbitrary code, never
anything that drafts/sends an email or touches Gmail).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from database.models import DistributionJob, EmailTemplate, ReportMaster
from database.org_models import OrgLevel
from database.suggestion_models import Suggestion
from services.audit_service import log_action
from services.combined_digest_service import (
    ALL_LEVELS,
    automated_reports_for_level,
    resolve_digest_template_id,
)
from services import incoming_service

_FREQUENCIES = ("Daily", "Weekly", "Monthly")


def _upsert(db, *, category, title, description, severity, entity_type, entity_id,
            fingerprint, can_auto_fix, proposed_action=None) -> None:
    existing = db.query(Suggestion).filter(Suggestion.fingerprint == fingerprint).first()
    if existing and existing.status == "pending":
        return  # already open, nothing new to say
    if existing:
        db.delete(existing)  # previously resolved/dismissed — a fresh occurrence supersedes it
        db.flush()
    db.add(Suggestion(
        category=category, title=title, description=description, severity=severity,
        entity_type=entity_type, entity_id=entity_id, fingerprint=fingerprint,
        can_auto_fix=can_auto_fix,
        proposed_action_json=json.dumps(proposed_action) if proposed_action else None,
        status="pending", detected_at=datetime.utcnow(),
    ))


def _detect_broken_digest_templates(db) -> None:
    for frequency in _FREQUENCIES:
        for level in ALL_LEVELS:
            reports = automated_reports_for_level(db, frequency, level)
            if not reports:
                continue
            try:
                resolve_digest_template_id(db, frequency, level, reports)
            except ValueError as exc:
                _handle_broken_digest_template(db, frequency, level, reports, str(exc))


def _handle_broken_digest_template(db, frequency: str, level: OrgLevel, reports: list[ReportMaster], reason: str) -> None:
    fingerprint = f"template_fk_null:{frequency}:{level.value}"
    title = f"{frequency} {level.value} digest has no working template"
    description = (
        f"Drafting/sending for {level.value} / {frequency} will fail until this is fixed: {reason} "
        f"Affected report(s): {', '.join(r.report_name for r in reports)}."
    )

    can_auto_fix = False
    proposed_action = None
    entity_id = None
    if len(reports) == 1:
        report = reports[0]
        entity_id = report.id
        candidates = [
            t for t in db.query(EmailTemplate).all()
            if frequency in t.name and level.value in t.name
        ]
        if len(candidates) == 1:
            can_auto_fix = True
            proposed_action = {
                "type": "set_default_template_id",
                "report_id": report.id,
                "template_id": candidates[0].id,
                "template_name": candidates[0].name,
            }
            description += f" A single matching template ('{candidates[0].name}') was found and can be re-linked automatically."
        else:
            description += " No single unambiguous template name match was found — please re-link it on the Templates page."

    _upsert(
        db, category="template_fk_null", title=title, description=description,
        severity="critical", entity_type="ReportMaster", entity_id=entity_id,
        fingerprint=fingerprint, can_auto_fix=can_auto_fix, proposed_action=proposed_action,
    )


def _detect_duplicate_same_day_drafts(db) -> None:
    today = datetime.utcnow().date()
    jobs = (
        db.query(DistributionJob)
        .filter(DistributionJob.created_at >= datetime(today.year, today.month, today.day))
        .all()
    )
    by_template: dict[int, list[DistributionJob]] = {}
    for j in jobs:
        if j.template_id:
            by_template.setdefault(j.template_id, []).append(j)

    for template_id, group in by_template.items():
        if len(group) < 2:
            continue
        template = db.query(EmailTemplate).get(template_id)
        template_name = template.name if template else f"template #{template_id}"
        job_ids = sorted(j.id for j in group)
        total_recipients = sum(j.total_recipients or 0 for j in group)
        fingerprint = f"duplicate_draft:{today.isoformat()}:{template_id}"
        _upsert(
            db, category="duplicate_draft_batch",
            title=f"'{template_name}' was drafted {len(group)} times today",
            description=(
                f"Distribution jobs {job_ids} all ran today for '{template_name}', covering "
                f"{total_recipients} recipient-sends combined. This usually means Gmail now has "
                f"duplicate drafts per recipient (the older batch's numbers may be stale). "
                f"This can't be safely auto-fixed here — check Gmail drafts for this template manually."
            ),
            severity="warning", entity_type="EmailTemplate", entity_id=template_id,
            fingerprint=fingerprint, can_auto_fix=False,
        )


def _detect_incoming_new_categories(db) -> None:
    """Agent-facing: mines recurring incoming subjects (see
    incoming_service.get_new_category_suggestions) that aren't yet covered
    by a reply-template match and proposes creating a category for each —
    the pattern's own verbatim subject becomes the suggested keyword, so
    approving it instantly covers every past occurrence."""
    for cand in incoming_service.get_new_category_suggestions():
        key = cand["normalized_subject"]
        fingerprint = f"incoming_new_category:{key}"
        category_name = cand["example_subject"][:120]
        title = f'New reply category candidate: "{cand["example_subject"][:70]}"'
        description = (
            f"{cand['count']} incoming email(s) share this subject, but only {cand['matched_count']} "
            f"({round(cand['coverage'] * 100)}%) currently match a saved reply category. Approving creates "
            f"a new category using this exact subject as the match keyword — it covers all {cand['count']} "
            f"past occurrence(s) and any future ones with the same subject immediately."
        )
        proposed_action = {
            "type": "create_incoming_reply_template",
            "category_name": category_name,
            "match_keywords": key,
        }
        _upsert(
            db, category="incoming_new_category", title=title, description=description,
            severity="info", entity_type="IncomingEmailPattern", entity_id=None,
            fingerprint=fingerprint, can_auto_fix=True, proposed_action=proposed_action,
        )


def _detect_incoming_low_confidence_categories(db) -> None:
    """Agent-facing, self-improving half: revises categories that already
    exist but are matching weakly, rather than only ever proposing new
    ones — see incoming_service.get_low_confidence_category_suggestions."""
    for cand in incoming_service.get_low_confidence_category_suggestions():
        fingerprint = f"incoming_low_confidence:{cand['template_id']}:{cand['suggested_keyword']}"
        title = f"'{cand['category_name']}' matches are low-confidence (avg {round(cand['avg_confidence'] * 100)}%)"
        description = (
            f"Across {cand['matched_count']} matched email(s), \"{cand['suggested_keyword']}\" appears in "
            f"{cand['keyword_hit_count']} of their subjects but isn't in this category's keyword list yet. "
            f"Approving adds it, strengthening future matches for this category."
        )
        proposed_action = {
            "type": "add_incoming_reply_template_keyword",
            "template_id": cand["template_id"],
            "keyword": cand["suggested_keyword"],
        }
        _upsert(
            db, category="incoming_low_confidence", title=title, description=description,
            severity="info", entity_type="IncomingReplyTemplate", entity_id=cand["template_id"],
            fingerprint=fingerprint, can_auto_fix=True, proposed_action=proposed_action,
        )


def _detect_incoming_ingest_errors(db) -> None:
    """Agent-facing error detector: surfaces incoming messages that were
    fetched from Gmail but failed to process, so a recurring parse/fetch
    problem doesn't sit invisible in the error column of a table nobody
    checks. Never auto-fixable — the underlying causes vary too much to
    safely automate."""
    from database.incoming_models import IncomingEmail, IncomingStatus

    since = datetime.utcnow() - timedelta(days=7)
    rows = (
        db.query(IncomingEmail)
        .filter(IncomingEmail.status == IncomingStatus.ERROR, IncomingEmail.created_at >= since)
        .all()
    )
    if not rows:
        return
    fingerprint = f"incoming_ingest_error:{since.date().isoformat()}"
    sample = (rows[0].error or "unknown error")[:200]
    title = f"{len(rows)} incoming message(s) failed to ingest in the last 7 days"
    description = (
        f'Fetched from Gmail but could not be processed. Example error: "{sample}". '
        f"This can't be safely auto-fixed — check message id(s) "
        f"{', '.join(r.gmail_message_id for r in rows[:5])}{' and more' if len(rows) > 5 else ''} manually."
    )
    _upsert(
        db, category="incoming_ingest_error", title=title, description=description,
        severity="warning", entity_type="IncomingEmail", entity_id=None,
        fingerprint=fingerprint, can_auto_fix=False,
    )


def run_suggestion_scan(db) -> int:
    """Runs every detector. Returns the number of open (pending) suggestions after the scan."""
    _detect_broken_digest_templates(db)
    _detect_duplicate_same_day_drafts(db)
    _detect_incoming_new_categories(db)
    _detect_incoming_low_confidence_categories(db)
    _detect_incoming_ingest_errors(db)
    db.flush()  # session is autoflush=False (database/db.py) — make just-added rows visible to the count below
    return db.query(Suggestion).filter(Suggestion.status == "pending").count()


def run_scheduled_suggestion_scan() -> None:
    """Poller entry point: opens its own session, same pattern as
    autosend_service.check_and_run_daily_autosend (no request context to
    reuse a session from). Detection-only — never mutates anything else."""
    from database.db import get_db
    from utils.logger import get_logger

    logger = get_logger(__name__)
    try:
        with get_db() as db:
            open_count = run_suggestion_scan(db)
            logger.info("Suggestion scan complete: %d open suggestion(s).", open_count)
    except Exception:  # noqa: BLE001
        logger.exception("Suggestion scan failed.")


def _apply_set_default_template_id(db, action: dict, user: dict) -> str:
    report = db.query(ReportMaster).get(action["report_id"])
    if not report:
        raise ValueError(f"ReportMaster {action['report_id']} no longer exists.")
    template = db.query(EmailTemplate).get(action["template_id"])
    if not template:
        raise ValueError(f"EmailTemplate {action['template_id']} no longer exists.")
    before = report.default_template_id
    report.default_template_id = template.id
    return f"default_template_id changed {before!r} -> {template.id} ('{template.name}') for '{report.report_name}'."


def _apply_create_incoming_reply_template(db, action: dict, user: dict) -> str:
    """Writes the new template in its OWN committed transaction before
    reclassifying — reclassify_reply_templates opens fresh DB sessions of
    its own, which (correctly, per SQLite/SQLAlchemy isolation) can't see
    this row while it's still only flushed-not-committed inside the
    caller's still-open approve_suggestion transaction. Without this, the
    brand new category would silently fail to match any of the very
    emails it was created for."""
    from database.db import get_db
    from database.incoming_models import IncomingReplyTemplate

    name = (action["category_name"].strip() or "New Category")[:150]
    with get_db() as tdb:
        if tdb.query(IncomingReplyTemplate).filter(IncomingReplyTemplate.category_name == name).first():
            name = f"{name} ({action['match_keywords'][:20]})"[:150]
        tdb.add(IncomingReplyTemplate(
            category_name=name,
            match_keywords=action["match_keywords"],
            subject_template="Re: ",
            body_template="<p>Dear Sir/Ma'am,</p><p></p><p>Regards,<br />Operations Team</p>",
            is_active=True,
            created_by=user.get("id"),
        ))
    updated = incoming_service.reclassify_reply_templates()
    return f"Created reply category '{name}' (keyword: '{action['match_keywords']}'); reclassified {updated} email(s)."


def _apply_add_incoming_reply_template_keyword(db, action: dict, user: dict) -> str:
    """Same commit-before-reclassify reasoning as
    _apply_create_incoming_reply_template above."""
    from database.db import get_db
    from database.incoming_models import IncomingReplyTemplate

    keyword = action["keyword"].strip()
    with get_db() as tdb:
        template = tdb.query(IncomingReplyTemplate).get(action["template_id"])
        if not template:
            raise ValueError(f"Reply template {action['template_id']} no longer exists.")
        existing = [k.strip() for k in template.match_keywords.split(",") if k.strip()]
        if keyword.lower() in {k.lower() for k in existing}:
            return f"'{keyword}' is already in '{template.category_name}''s keywords — nothing to add."
        template.match_keywords = ", ".join(existing + [keyword])
        category_name = template.category_name
    updated = incoming_service.reclassify_reply_templates()
    return f"Added keyword '{keyword}' to '{category_name}'; reclassified {updated} email(s)."


_EXECUTORS = {
    "set_default_template_id": _apply_set_default_template_id,
    "create_incoming_reply_template": _apply_create_incoming_reply_template,
    "add_incoming_reply_template_keyword": _apply_add_incoming_reply_template_keyword,
}


def approve_suggestion(db, suggestion_id: int, user: dict) -> Suggestion:
    suggestion = db.query(Suggestion).get(suggestion_id)
    if not suggestion:
        raise ValueError("Suggestion not found.")
    if suggestion.status != "pending":
        raise ValueError(f"Suggestion is already '{suggestion.status}'.")
    if not suggestion.can_auto_fix or not suggestion.proposed_action_json:
        raise ValueError("This suggestion has no automatic fix — resolve it manually.")

    action = json.loads(suggestion.proposed_action_json)
    executor = _EXECUTORS.get(action.get("type"))
    if not executor:
        raise ValueError(f"No executor registered for action type '{action.get('type')}'.")

    try:
        result = executor(db, action, user)
        suggestion.status = "applied"
        suggestion.result_detail = result
    except Exception as exc:  # noqa: BLE001 — record the failure on the row, don't 500
        suggestion.status = "failed"
        suggestion.result_detail = str(exc)

    suggestion.resolved_at = datetime.utcnow()
    suggestion.resolved_by_id = user.get("id")
    suggestion.resolved_by_username = user.get("username")

    log_action(
        db, "APPROVE_SUGGESTION", user_id=user.get("id"), username=user.get("username"),
        entity_type="Suggestion", entity_id=suggestion.id,
        details=f"{suggestion.title} -> {suggestion.status}: {suggestion.result_detail}",
    )
    return suggestion


def dismiss_suggestion(db, suggestion_id: int, user: dict) -> Suggestion:
    suggestion = db.query(Suggestion).get(suggestion_id)
    if not suggestion:
        raise ValueError("Suggestion not found.")
    if suggestion.status != "pending":
        raise ValueError(f"Suggestion is already '{suggestion.status}'.")

    suggestion.status = "dismissed"
    suggestion.resolved_at = datetime.utcnow()
    suggestion.resolved_by_id = user.get("id")
    suggestion.resolved_by_username = user.get("username")

    log_action(
        db, "DISMISS_SUGGESTION", user_id=user.get("id"), username=user.get("username"),
        entity_type="Suggestion", entity_id=suggestion.id, details=suggestion.title,
    )
    return suggestion
