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

import re
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from database.db import get_db
from database.incoming_models import (
    ExtractedMetric,
    IncomingAttachment,
    IncomingEmail,
    IncomingReplyTemplate,
    IncomingStatus,
)
from services.audit_service import log_action
from services import gmail_auth
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


def classify_reply_template(db: Session, subject: str | None, body: str | None) -> tuple[int | None, float]:
    """Matches subject+body against every active IncomingReplyTemplate's
    keyword list. Plain substring matching, not an ML model — confidence is
    (matched keywords / keywords defined) for whichever active template
    matches best. Returns (None, 0.0) if nothing matches at least one
    keyword. Detection only — never drafts or sends anything."""
    templates = db.query(IncomingReplyTemplate).filter(IncomingReplyTemplate.is_active.is_(True)).all()
    if not templates:
        return None, 0.0
    text = f"{subject or ''} {body or ''}".lower()
    best_id, best_score = None, 0.0
    for tpl in templates:
        keywords = [k.strip().lower() for k in tpl.match_keywords.split(",") if k.strip()]
        if not keywords:
            continue
        hits = sum(1 for k in keywords if k in text)
        if hits == 0:
            continue
        score = hits / len(keywords)
        if score > best_score:
            best_id, best_score = tpl.id, score
    return best_id, round(best_score, 3)


# ----------------------------------------------------------------------
# Triage classification — "what kind of work is this mail, if any".
#
# Deliberately separate from classify_reply_template (which answers "which
# canned reply fits"): a message can be a real task with no template yet,
# or noise that should never get a reply at all. Ordered list, FIRST MATCH
# WINS — so put narrower/high-confidence rules above broader ones.
#
# Derived from a real 90-day sample of this mailbox (2,458 messages, 1,016
# of them direct) — every bucket below was observed, none are speculative.
# Plain substring rules, no model, so any classification can be explained
# by pointing at the keyword that matched. Detection only: nothing here
# drafts, sends, files, or modifies the mailbox.
# ----------------------------------------------------------------------
TRIAGE_RULES: list[tuple[str, str, list[str]]] = [
    # --- noise: needs no reply, ever -----------------------------------
    ("noise", "Bounce / Undeliverable", [
        "delivery status notification", "undeliverable", "mail delivery subsystem",
        "failure notice", "returned mail",
    ]),
    ("noise", "Marketing / Social / Newsletter", [
        "you appeared in", "sent a message", "motivation for today", "webinar",
        "newsletter", "you have an invitation", "hiring cart", "linkedin",
        "unsubscribe from this", "view this email in your browser",
    ]),
    # Only genuine calendar traffic. A bare "meeting" keyword was here and
    # was far too broad: it swallowed real SBI work that merely MENTIONED a
    # meeting — "Meeting on CSP Reallocation (Chaired by GM FI)", "MINUTES
    # OF BC REVIEW MEETING", "Immediate Action Plan for Poor Performing
    # CSP". 9 of the 52 it captured had actually been replied to, which is
    # proof they were never noise. Real invites carry structural markers
    # (RFC-5545 style prefixes, "When:", RSVP wording) — match those.
    ("noise", "Calendar / Meeting", [
        "invitation:", "accepted:", "declined:", "updated invitation",
        "canceled event", "cancelled event", "tentatively accepted",
        "when: ", "rsvp",
    ]),
    ("noise", "Drive / Doc share request", ["requests access to", "share request for"]),

    # --- info: MUST stay above the task rules --------------------------
    # These are narrow, high-confidence phrases. The broad task keyword
    # "csp code" also appears inside these reports' own bodies (e.g. the
    # column header "TOTAL CSP CODE"), so with task first ~50 pure status
    # reports were being tagged "needs action" when they need none.
    # First-match-wins means specificity has to win on ordering.
    ("info", "SBI Data / Status Push", [
        "inactive code status", "inactive csp code", "code status as on",
        "inactive code", "please find inactive",
        # Added 2026-08-26: 9 real SBI mails carried the same recurring
        # inactive-CSP status push but without the word "code" in the
        # subject ("INACTIVE CSPs AS ON...", "INACTIVE CSP POSITION..."),
        # so they fell through to "other" instead of matching above.
        # "inactive cps" is a real observed typo (CPS instead of CSP), not
        # a guess. SUBJECT-ONLY: a real "BC CSP REVIEW MEETING" agenda's
        # BODY says "Deletion of Inactive CSPs" as a discussion item —
        # checking the full body would misclassify that meeting as this
        # status-push report; every real target subject carries the
        # phrase in its own subject line, so subject-only loses nothing.
        ("inactive csp", "subject"), ("inactive cps", "subject"),
    ]),
    # Two more high-volume periodic pushes found scanning 2 years of real
    # SBI-domain mail (1275 threads, 77% of which fell into "other" before
    # this) — both are pure status reports SBI sends on its own schedule,
    # 0% ever replied to historically. Narrow phrases, placed above the
    # task rules for the same reason as SBI Data / Status Push.
    ("info", "Micro ATM Report", [
        "micro atm report", "inactive micro atm",
        # Added 2026-08-26: 5 more real subjects for the same recurring
        # report family ("MATM REPORT AS ON...", "INACTIVE MATMs ON...",
        # "TARGET FOR ACTIVATION OF MATMs...", "m ATM Status", "...ENABLED
        # WITH MICRO ATM"). SUBJECT-ONLY, same reasoning as the Inactive
        # CSP additions above — and deliberately not the bare word "matm"
        # either way, since that would also match "matm key" in Terminal/
        # Device/Tech Issue below (a real different task).
        ("matm report", "subject"), ("inactive matm", "subject"),
        ("activation of matm", "subject"), ("atm status", "subject"),
        ("enabled with micro atm", "subject"), ("enabled for micro atm", "subject"),
    ]),
    ("info", "BC-CSP Agreement & PVR Pendency Report", [
        "pvr pendency", "bc-csp agreement", "pvr & agreement", "agreement & pvr",
        # Added 2026-08-26: 2 more real subjects for the same report family
        # ("PVR PENDING AS ON...", "CSP EXPIRED AGREEMENT POSITION...").
        # SUBJECT-ONLY, same reasoning as above.
        ("pvr pending", "subject"), ("csp expired agreement", "subject"),
    ]),
    # Two more recurring data-pushes added 2026-08-26, found in the same
    # sweep of the "other" bucket. Both narrow, subject-only phrases —
    # "passbook printer" alone also matches case-specific KO-mapping reply
    # threads ("Re: Request to passbook printer mapping for KO..."), which
    # are real one-off tasks, not this periodic report.
    ("info", "Passbook Printer Report", [
        ("passbook printer report", "subject"), ("passbook printer position", "subject"),
    ]),
    ("info", "BC Commission Report", [
        ("bc comm report", "subject"),
    ]),

    # --- task: a human must actually do something ----------------------
    # --- the limit-approval workflow, split three ways -----------------
    # Verified against 149 real messages by reading BODIES, not subjects.
    # The single "Limit / Balance Approval" bucket it replaces mixed up
    # three genuinely different things, and conflating them is dangerous:
    # forwarding logic built on the old bucket would have forwarded
    # Priyanshu's own 37 "Approved." replies straight back to Priyanshu.
    #
    # ORDER MATTERS: the approval-reply rule must precede the request rule,
    # because an approval carries the request's quoted subject too.
    # An approval is work COMPLETED, not work to do — so it's "info", not
    # "task". Keeping it in the task tier put it in the Work Queue and, worse,
    # generated duplicate task rows for tickets that the approval was
    # closing (observed: 35 tickets each holding 2-3 rows). The auto-close
    # logic finds these by triage_intent, so tier doesn't affect it.
    ("info", "Limit Approval — Approved (close ticket)", [
        "re: fwd: please approve my limit request",
    ]),
    ("info", "CSP Balance Reconciliation", [
        "balance reconciliation", "balance recon",
    ]),
    ("task", "Limit Approval Request", [
        "approve my limit request", "limit request", "limit increase",
    ]),
    ("task", "CSP Code Allotment / Reallocation", [
        "code allotment", "code reallocation", "replacement code", "csp code",
    ]),
    ("task", "Terminal / Device / Tech Issue", [
        "terminal reset", "reset the terminal", "matm key", "gps dongle",
        "not working", "error in report", "portal error", "login issue", "biometric",
    ]),
    ("task", "Commission / Payout Query", [
        "commission payout", "payout", "untagged credit", "settlement", "incentive",
    ]),
    # Added 2026-08-26, found in the same sweep. Both are recurring
    # SUBJECT patterns but genuinely need a human every time — not ack
    # candidates like the info-tier reports above. Audit Compliance mails
    # demand real, specific remarks/evidence on an actual audit finding
    # ("submit the remarks with evidence... treat the matter URGENT...
    # compliance is overdue") — a boilerplate "noted, will action" reply
    # would misrepresent an unread compliance deadline as handled, the
    # same risk that got "Report Submission / Status" excluded earlier.
    # BC Bills mails ask US to send something ("send us the BC bills by
    # 17th August") — an action FROM Eko, not a status push TO react to.
    # Classified here (not left as "other") so they surface in the Work
    # Queue for a human, instead of vanishing into the uncategorized pile.
    ("task", "Audit Compliance Request", [
        "audit compliance", "audit complience", "audit compliane",
    ]),
    ("task", "BC Bills Request", [
        ("bc bills for", "subject"),
    ]),
    ("task", "Form 16 / Tax Document", ["form 16", "form16", "tds certificate"]),
    ("task", "Onboarding / KYC / Queue Action", [
        "onboarding queue", "reject onboarding", "re-kyc", "rekyc",
        "kyc pending", "activation request",
    ]),

    # --- info: read/file it, but nothing to do -------------------------
    ("info", "Report Submission / Status", [
        "weekly work report", "daily report", "progress report",
        "submission of", "report draft",
    ]),
]

# How much of the body to consider. Long quoted reply chains and signature
# blocks below this point mostly re-state earlier mail and cause spurious
# matches (a thread quoting an old "limit request" is not itself one).
_TRIAGE_BODY_CHARS = 600


def classify_triage(subject: str | None, body: str | None) -> tuple[str, str | None]:
    """Returns (tier, intent) for one message — see TRIAGE_RULES. Falls back
    to ("other", None) when no rule matches, which is expected for roughly
    half of real mail and should NOT be treated as a classification failure:
    a genuine one-off deserves a human, not a bucket.

    A keyword is normally a plain string, checked against subject+body
    like always. It can instead be a (keyword, "subject") tuple to check
    ONLY the subject — needed for phrases too common in body prose to be
    body-safe (e.g. "inactive csp" legitimately recurs in the body of a
    "BC CSP REVIEW MEETING" agenda — "Deletion of Inactive CSPs" as a
    discussion item — which is a meeting, not a status-push report; only
    matching it in the SUBJECT keeps the real reports without also
    catching mentions like that one)."""
    subj = (subject or "").lower()
    text = f"{subject or ''} {(body or '')[:_TRIAGE_BODY_CHARS]}".lower()
    for tier, intent, keywords in TRIAGE_RULES:
        for k in keywords:
            if isinstance(k, tuple):
                keyword, scope = k
                haystack = subj if scope == "subject" else text
            else:
                keyword, haystack = k, text
            if keyword in haystack:
                return tier, intent
    return "other", None


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

    # Resolved once per batch, not per message — same account for every row.
    try:
        account_email = gmail_auth.get_connected_email()
    except Exception:  # noqa: BLE001
        account_email = None

    for gmail_id in ids:
        # Dedup guard #1 — cheap DB check before any fetch.
        with get_db() as db:
            if _already_ingested(db, gmail_id):
                continue

        try:
            parsed = gs.fetch_message(service, gmail_id)
            cls = gs.classify(parsed.subject, parsed.body_text, parsed.sender)
            metrics = gs.extract_metrics(cls.report_type, parsed.body_text)
            replied, replied_at = gs.thread_has_reply(service, parsed.gmail_thread_id, after=parsed.received_at)
            recipient_kind = gs.classify_recipient_kind(parsed.to_header, parsed.cc_header, account_email)

            with get_db() as db:
                # Dedup guard #2 — race-safe: unique constraint + recheck.
                if _already_ingested(db, gmail_id):
                    continue

                status = (
                    IncomingStatus.NEEDS_REVIEW
                    if cls.confidence < REVIEW_THRESHOLD or not cls.report_type
                    else IncomingStatus.EXTRACTED
                )
                reply_template_id, reply_match_confidence = classify_reply_template(
                    db, parsed.subject, parsed.body_text
                )
                triage_tier, triage_intent = classify_triage(parsed.subject, parsed.body_text)

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
                    replied=replied,
                    replied_at=replied_at,
                    recipient_kind=recipient_kind,
                    matched_reply_template_id=reply_template_id,
                    match_confidence=reply_match_confidence if reply_template_id else None,
                    triage_tier=triage_tier,
                    triage_intent=triage_intent,
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
    m = re.search(r"<([^>]+)>", raw)
    return (m.group(1) if m else raw).strip()


def recheck_pending_replies(max_check: int = 200) -> int:
    """Re-checks reply status for rows still marked unreplied — a reply can
    land on a thread well after the original message was ingested. Bounded
    to `max_check` per call so a sync never re-hits the whole table. Commits
    per-row so a slow or interrupted run keeps whatever it already finished.
    Returns how many rows flipped to replied=True. Read-only against Gmail."""
    try:
        service = gs.get_gmail_client()
    except Exception as exc:  # noqa: BLE001
        logger.error("Gmail client unavailable for reply recheck: %s", exc)
        return 0

    with get_db() as db:
        pending_ids = [
            r.id for r in
            db.query(IncomingEmail.id)
            .filter(IncomingEmail.replied.is_(False), IncomingEmail.gmail_thread_id.isnot(None))
            .order_by(IncomingEmail.received_at.desc())
            .limit(max_check)
            .all()
        ]

    updated = 0
    for row_id in pending_ids:
        with get_db() as db:
            row = db.query(IncomingEmail).get(row_id)
            if row is None or row.replied:
                continue
            try:
                replied, replied_at = gs.thread_has_reply(service, row.gmail_thread_id, after=row.received_at)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Reply recheck failed for thread %s: %s", row.gmail_thread_id, exc)
                continue
            if replied:
                row.replied = True
                row.replied_at = replied_at
                updated += 1
    logger.info("Reply recheck: %d/%d rows flipped to replied.", updated, len(pending_ids))
    return updated


def reclassify_reply_templates(only_recipient_kind: str | None = "to") -> int:
    """Re-runs classify_reply_template against every already-ingested row —
    purely local (subject/body are already stored, no Gmail calls), so this
    is fast and safe to call every time a template is added/edited/removed,
    unlike the Gmail-backed backfills above. Commits per-row (like
    backfill_recipient_kind) rather than one transaction for the whole
    batch: a single long-held write transaction across ~300 rows was
    observed colliding with the scheduler's 1-minute pollers ('database is
    locked', surfaced as a failed Suggestion approval) — short-lived
    per-row transactions avoid holding the write lock long enough to
    matter. Returns rows updated."""
    with get_db() as db:
        q = db.query(IncomingEmail.id)
        if only_recipient_kind is not None:
            q = q.filter(IncomingEmail.recipient_kind == only_recipient_kind)
        row_ids = [r.id for r in q.all()]

    updated = 0
    for row_id in row_ids:
        with get_db() as db:
            row = db.query(IncomingEmail).get(row_id)
            if row is None:
                continue
            template_id, confidence = classify_reply_template(db, row.subject, row.body_text)
            new_confidence = confidence if template_id else None
            if row.matched_reply_template_id != template_id or row.match_confidence != new_confidence:
                row.matched_reply_template_id = template_id
                row.match_confidence = new_confidence
                updated += 1
    logger.info("Reply-template reclassify: %d/%d rows updated.", updated, len(row_ids))
    return updated


def backfill_triage(only_missing: bool = True) -> int:
    """Applies classify_triage to already-ingested rows. Purely local —
    subject/body are already stored, so no Gmail calls at all, which makes
    this seconds-not-minutes unlike the Gmail-backed backfills. Pass
    only_missing=False to re-classify everything after editing
    TRIAGE_RULES. Commits per-row, same reasoning as the other backfills
    here (short write transactions don't collide with the scheduler)."""
    with get_db() as db:
        q = db.query(IncomingEmail.id)
        if only_missing:
            q = q.filter(IncomingEmail.triage_tier.is_(None))
        row_ids = [r.id for r in q.all()]

    updated = 0
    for row_id in row_ids:
        with get_db() as db:
            row = db.query(IncomingEmail).get(row_id)
            if row is None:
                continue
            tier, intent = classify_triage(row.subject, row.body_text)
            if row.triage_tier != tier or row.triage_intent != intent:
                row.triage_tier = tier
                row.triage_intent = intent
                updated += 1
    logger.info("Triage backfill: %d/%d rows updated.", updated, len(row_ids))
    return updated


# Display order for the triage tiers — most-actionable first, so the
# dashboard reads top-down as "what needs me / what's just FYI / what's junk".
_TIER_ORDER = ["task", "info", "noise", "other"]


# Duplicated from incoming_ack_service.SBI_DOMAINS (not imported — that
# module imports FROM this one, so importing it back here would be
# circular). Same two domains, kept in sync manually.
_SBI_DOMAINS_FOR_SUMMARY = ("sbi.co.in", "sbionline.onmicrosoft.com")


def get_triage_summary(only_recipient_kind: str | None = "to", sbi_only: bool = False) -> dict:
    """Volume per triage tier + per intent, scoped to direct mail by
    default (Cc'd mail was never this account's to action, same reasoning
    as get_subject_patterns). Read-only.

    `sbi_only` narrows this to mail actually sent from an SBI domain —
    added 2026-08-26 so the "Mail Breakdown" widget reflects what this
    app is actually automating (SBI-domain incoming mail), not every
    direct email regardless of sender.
    """
    from sqlalchemy import func, or_

    with get_db() as db:
        q = db.query(
            IncomingEmail.triage_tier, IncomingEmail.triage_intent, func.count(IncomingEmail.id)
        )
        if only_recipient_kind is not None:
            q = q.filter(IncomingEmail.recipient_kind == only_recipient_kind)
        if sbi_only:
            q = q.filter(or_(*(IncomingEmail.sender.ilike(f"%{d}%") for d in _SBI_DOMAINS_FOR_SUMMARY)))
        rows = q.group_by(IncomingEmail.triage_tier, IncomingEmail.triage_intent).all()

    total = sum(c for _, _, c in rows)
    by_tier: dict[str, dict] = {}
    for tier, intent, count in rows:
        t = tier or "other"
        bucket = by_tier.setdefault(t, {"tier": t, "count": 0, "intents": []})
        bucket["count"] += count
        if intent:
            bucket["intents"].append({"intent": intent, "count": count})

    ordered = []
    for t in _TIER_ORDER:
        if t not in by_tier:
            continue
        b = by_tier[t]
        b["pct"] = round(b["count"] / total, 3) if total else 0.0
        b["intents"].sort(key=lambda i: i["count"], reverse=True)
        ordered.append(b)

    return {"total": total, "by_tier": ordered}


def reverify_all_replies(max_check: int = 600, only_recipient_kind: str | None = "to") -> dict:
    """Full re-verification against the corrected thread_has_reply (now
    requires the reply to be genuinely after the incoming message, not just
    'a SENT message exists somewhere on this thread') — catches FALSE
    POSITIVES that recheck_pending_replies can't, since that one only ever
    looks at rows currently marked unreplied. Scoped to recipient_kind="to"
    by default since that's what the dashboard's automation scoring uses;
    pass None to re-verify the whole table. Commits per-row. Returns
    {checked, flipped_true, flipped_false}."""
    try:
        service = gs.get_gmail_client()
    except Exception as exc:  # noqa: BLE001
        logger.error("Gmail client unavailable for full reply reverify: %s", exc)
        return {"checked": 0, "flipped_true": 0, "flipped_false": 0}

    with get_db() as db:
        q = db.query(IncomingEmail.id).filter(IncomingEmail.gmail_thread_id.isnot(None))
        if only_recipient_kind is not None:
            q = q.filter(IncomingEmail.recipient_kind == only_recipient_kind)
        row_ids = [r.id for r in q.order_by(IncomingEmail.received_at.desc()).limit(max_check).all()]

    checked = flipped_true = flipped_false = 0
    for row_id in row_ids:
        with get_db() as db:
            row = db.query(IncomingEmail).get(row_id)
            if row is None:
                continue
            try:
                replied, replied_at = gs.thread_has_reply(service, row.gmail_thread_id, after=row.received_at)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Full reverify failed for thread %s: %s", row.gmail_thread_id, exc)
                continue
            checked += 1
            if replied and not row.replied:
                flipped_true += 1
            elif not replied and row.replied:
                flipped_false += 1
            row.replied = replied
            row.replied_at = replied_at if replied else None
    logger.info(
        "Full reply reverify: checked=%d flipped_true=%d flipped_false=%d",
        checked, flipped_true, flipped_false,
    )
    return {"checked": checked, "flipped_true": flipped_true, "flipped_false": flipped_false}


def backfill_recipient_kind(max_check: int = 600) -> int:
    """One-time (repeatable) catch-up for rows ingested before recipient_kind
    existed. Lightweight header-only fetch per row — read-only against
    Gmail, no body/attachments re-downloaded. Commits per-row (like
    ingest_new_messages) rather than one session for the whole batch, so a
    slow or interrupted run still keeps whatever it already finished.
    Returns rows updated."""
    try:
        service = gs.get_gmail_client()
        account_email = gmail_auth.get_connected_email()
    except Exception as exc:  # noqa: BLE001
        logger.error("Gmail client unavailable for recipient_kind backfill: %s", exc)
        return 0

    with get_db() as db:
        pending_ids = [
            r.id for r in
            db.query(IncomingEmail.id, IncomingEmail.gmail_message_id)
            .filter(IncomingEmail.recipient_kind.is_(None))
            .order_by(IncomingEmail.received_at.desc())
            .limit(max_check)
            .all()
        ]

    updated = 0
    for row_id in pending_ids:
        with get_db() as db:
            row = db.query(IncomingEmail).get(row_id)
            if row is None or row.recipient_kind is not None:
                continue
            try:
                to_header, cc_header = gs.fetch_message_headers(service, row.gmail_message_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Header backfill failed for %s: %s", row.gmail_message_id, exc)
                continue
            row.recipient_kind = gs.classify_recipient_kind(to_header, cc_header, account_email)
            updated += 1
    logger.info("recipient_kind backfill: %d/%d rows updated.", updated, len(pending_ids))
    return updated


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


def get_recipient_kind_summary() -> dict:
    """Direct (To) vs Cc'd vs unresolved counts — how much of the inbox
    volume is actually addressed to the connected account vs. just copied."""
    from sqlalchemy import func
    with get_db() as db:
        rows = (
            db.query(IncomingEmail.recipient_kind, func.count(IncomingEmail.id))
            .group_by(IncomingEmail.recipient_kind)
            .all()
        )
    counts = {"to": 0, "cc": 0, "unknown": 0}
    for kind, cnt in rows:
        counts[kind if kind in counts else "unknown"] += cnt
    total = sum(counts.values())
    return {
        "direct_count": counts["to"],
        "cc_count": counts["cc"],
        "unclassified_count": counts["unknown"],
        "total": total,
        "direct_pct": round(counts["to"] / total, 3) if total else 0.0,
        "cc_pct": round(counts["cc"] / total, 3) if total else 0.0,
    }


def get_reply_template_match_summary() -> dict:
    """How much of direct incoming mail matched an active reply template —
    the real, current 'how close are we to X% automated' number, since it's
    driven by the same templates that would actually be used, not just
    subject-recurrence heuristics."""
    from sqlalchemy import func
    with get_db() as db:
        direct_total = db.query(IncomingEmail).filter(IncomingEmail.recipient_kind == "to").count()
        matched_total = (
            db.query(IncomingEmail)
            .filter(IncomingEmail.recipient_kind == "to", IncomingEmail.matched_reply_template_id.isnot(None))
            .count()
        )
        by_template = (
            db.query(
                IncomingReplyTemplate.id, IncomingReplyTemplate.category_name,
                func.count(IncomingEmail.id), func.avg(IncomingEmail.match_confidence),
            )
            .join(IncomingEmail, IncomingEmail.matched_reply_template_id == IncomingReplyTemplate.id)
            .filter(IncomingEmail.recipient_kind == "to")
            .group_by(IncomingReplyTemplate.id, IncomingReplyTemplate.category_name)
            .all()
        )
    return {
        "direct_total": direct_total,
        "matched_total": matched_total,
        "matched_pct": round(matched_total / direct_total, 3) if direct_total else 0.0,
        "by_template": [
            {"template_id": tid, "category_name": name, "matched_count": cnt, "avg_confidence": round(avg or 0, 3)}
            for tid, name, cnt, avg in sorted(by_template, key=lambda r: r[2], reverse=True)
        ],
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


# ----------------------------------------------------------------------
# Task extraction — turn task-tier mail into a workable queue.
# ----------------------------------------------------------------------
# A 6-digit run that isn't part of a CSP code. Validated against real mail:
# CSP codes ("1A851884") do NOT match, because the digits are preceded by a
# word character so there's no \b — checked explicitly, not assumed.
_TICKET_RE = re.compile(r"\b(\d{6})\b")
# Eko/SBI CSP-KO code, e.g. 1A851884.
_CSP_CODE_RE = re.compile(r"\b(1A\d{6})\b", re.IGNORECASE)


def extract_task_identifiers(subject: str | None) -> list[tuple[str, str]]:
    """Returns [(identifier, kind)] found in a subject — ticket numbers take
    precedence over CSP codes, because for a "please approve ticket N for
    CSP X" mail the *ticket* is the unit of work and the CSP code is just
    context. Multiple tickets in one subject yield multiple entries: that
    email really is several approvals.

    Subject only, deliberately. Bodies contain quoted chains and signature
    blocks full of unrelated codes, which produced obvious false positives
    when tried."""
    s = subject or ""
    tickets = _TICKET_RE.findall(s)
    if tickets:
        seen, out = set(), []
        for t in tickets:
            if t not in seen:
                seen.add(t)
                out.append((t, "ticket"))
        return out
    codes = _CSP_CODE_RE.findall(s)
    seen, out = set(), []
    for c in codes:
        cu = c.upper()
        if cu not in seen:
            seen.add(cu)
            out.append((cu, "csp_code"))
    return out


def sync_extracted_tasks(only_recipient_kind: str | None = "to") -> dict:
    """Creates ExtractedTask rows for task-tier mail that doesn't have them
    yet. Purely local (no Gmail calls). Idempotent: the
    (incoming_email_id, identifier) unique constraint plus an existence
    check means re-running never duplicates, so this is safe to call on
    every sync.

    Deliberately only ever INSERTS. It never reopens, closes, or deletes an
    existing task — a human's open/done decision is theirs to keep, and a
    re-sync silently reverting it would be worse than useless."""
    from database.incoming_models import ExtractedTask

    with get_db() as db:
        q = db.query(IncomingEmail).filter(IncomingEmail.triage_tier == "task")
        if only_recipient_kind is not None:
            q = q.filter(IncomingEmail.recipient_kind == only_recipient_kind)
        candidates = [
            (r.id, r.subject, r.sender, r.received_at or r.created_at, r.triage_intent)
            for r in q.all()
        ]
        existing_email_ids = {
            t.incoming_email_id for t in db.query(ExtractedTask.incoming_email_id).all()
        }

    created = 0
    for email_id, subject, sender, received_at, intent in candidates:
        if email_id in existing_email_ids:
            continue
        pairs = extract_task_identifiers(subject) or [(None, None)]
        with get_db() as db:
            for identifier, kind in pairs:
                db.add(ExtractedTask(
                    incoming_email_id=email_id,
                    task_type=intent or "Other",
                    identifier=identifier,
                    identifier_kind=kind,
                    subject=(subject or "(no subject)")[:500],
                    sender=_sender_display(sender),
                    received_at=received_at,
                    status="open",
                ))
                created += 1

    logger.info("Task extraction: %d task(s) created.", created)
    return {"created": created, "scanned": len(candidates)}


def resync_task_types() -> int:
    """task_type is denormalised onto ExtractedTask at extraction time, and
    sync_extracted_tasks is insert-only — so renaming a triage intent leaves
    existing rows showing the OLD label forever. This re-points them at the
    source email's current intent. Local-only, no Gmail."""
    from database.incoming_models import ExtractedTask

    with get_db() as db:
        pairs = [
            (t.id, t.task_type, e.triage_intent)
            for t, e in db.query(ExtractedTask, IncomingEmail)
            .filter(IncomingEmail.id == ExtractedTask.incoming_email_id)
            .all()
        ]

    updated = 0
    for tid, current, wanted in pairs:
        if wanted and current != wanted:
            with get_db() as db:
                t = db.query(ExtractedTask).get(tid)
                if t:
                    t.task_type = wanted
                    updated += 1
    logger.info("Task type resync: %d row(s) relabelled.", updated)
    return updated


# Work Queue is scoped to the task types actually part of current work —
# right now that's just Limit Approval Request (the one with a real,
# live auto-forward workflow behind it). Added 2026-08-26 per explicit
# instruction: the other task types (CSP Code Allotment, Terminal/Device
# issues, Commission/Payout Query, Form 16, Onboarding/KYC) are real work
# too, just not what this dashboard is tracking right now — their
# ExtractedTask rows stay in the DB untouched, only hidden from this view.
WORK_QUEUE_TASK_TYPES = ("Limit Approval Request",)


def get_task_queue(status: str = "open", task_type: str | None = None, limit: int = 500) -> list[dict]:
    from database.incoming_models import ExtractedTask

    with get_db() as db:
        q = db.query(ExtractedTask).filter(ExtractedTask.task_type.in_(WORK_QUEUE_TASK_TYPES))
        if status and status != "all":
            q = q.filter(ExtractedTask.status == status)
        if task_type:
            q = q.filter(ExtractedTask.task_type == task_type)
        rows = q.order_by(ExtractedTask.received_at.desc().nullslast()).limit(limit).all()
        return [
            {
                "id": t.id, "emailId": t.incoming_email_id, "taskType": t.task_type,
                "identifier": t.identifier, "identifierKind": t.identifier_kind,
                "subject": t.subject, "sender": t.sender,
                "receivedAt": _utc_iso(t.received_at),
                "status": t.status,
                "resolvedAt": _utc_iso(t.resolved_at),
                "resolvedBy": t.resolved_by_username,
            }
            for t in rows
        ]


def get_task_queue_summary() -> dict:
    from sqlalchemy import func

    from database.incoming_models import ExtractedTask

    with get_db() as db:
        rows = (
            db.query(ExtractedTask.task_type, ExtractedTask.status, func.count(ExtractedTask.id))
            .filter(ExtractedTask.task_type.in_(WORK_QUEUE_TASK_TYPES))
            .group_by(ExtractedTask.task_type, ExtractedTask.status)
            .all()
        )
    by_type: dict[str, dict] = {}
    totals = {"open": 0, "done": 0, "dismissed": 0}
    for task_type, status, count in rows:
        b = by_type.setdefault(task_type, {"taskType": task_type, "open": 0, "done": 0, "dismissed": 0})
        if status in b:
            b[status] += count
        if status in totals:
            totals[status] += count
    return {
        "totals": totals,
        "by_type": sorted(by_type.values(), key=lambda b: b["open"], reverse=True),
    }


def set_task_status(task_id: int, status: str, username: str | None) -> dict:
    """Only ever changes this app's own record. Explicitly does NOT reply
    to, archive, or otherwise touch the source email in Gmail."""
    from database.incoming_models import ExtractedTask

    if status not in ("open", "done", "dismissed"):
        raise ValueError(f"Invalid status '{status}'.")

    with get_db() as db:
        task = db.query(ExtractedTask).get(task_id)
        if not task:
            raise ValueError("Task not found.")
        task.status = status
        if status == "open":
            task.resolved_at = None
            task.resolved_by_username = None
        else:
            task.resolved_at = datetime.utcnow()
            task.resolved_by_username = username
        return {"id": task.id, "status": task.status}


def backfill_received_at_utc(max_check: int = 3000) -> dict:
    """Rewrite received_at as true UTC using Gmail's internalDate.

    Needed because the old code stripped the Date header's offset instead of
    converting it, so the column holds a mix: +0530 senders stored correct
    IST, +0000 senders stored UTC. The offset is gone from the stored value,
    so this cannot be fixed locally — internalDate has to be re-read.

    Metadata-only fetch (no bodies), per-row commits. Read-only against
    Gmail. Rows whose message no longer exists are left as-is.
    """
    from datetime import datetime as _dt

    try:
        service = gs.get_gmail_client()
    except Exception as exc:  # noqa: BLE001
        logger.error("Gmail client unavailable for received_at backfill: %s", exc)
        return {"checked": 0, "changed": 0, "errors": 0}

    with get_db() as db:
        ids = [
            (r.id, r.gmail_message_id)
            for r in db.query(IncomingEmail.id, IncomingEmail.gmail_message_id)
            .order_by(IncomingEmail.received_at.desc())
            .limit(max_check)
            .all()
        ]

    stats = {"checked": 0, "changed": 0, "errors": 0}
    for row_id, gmail_id in ids:
        try:
            msg = (
                service.users().messages()
                .get(userId=GMAIL_USER_ID_FALLBACK, id=gmail_id, format="metadata",
                     metadataHeaders=["Date"])
                .execute()
            )
        except Exception:  # noqa: BLE001
            stats["errors"] += 1
            continue
        if not msg.get("internalDate"):
            continue
        try:
            true_utc = _dt.utcfromtimestamp(int(msg["internalDate"]) / 1000)
        except Exception:  # noqa: BLE001
            stats["errors"] += 1
            continue
        with get_db() as db:
            row = db.query(IncomingEmail).get(row_id)
            if row is None:
                continue
            stats["checked"] += 1
            if row.received_at != true_utc:
                row.received_at = true_utc
                stats["changed"] += 1
    logger.info("received_at UTC backfill: %s", stats)
    return stats


# gmail_service reads this from env; mirror the default rather than import a
# private name.
GMAIL_USER_ID_FALLBACK = gs.GMAIL_USER_ID


def _utc_iso(dt) -> str | None:
    """Serialise a naive-UTC datetime with an explicit 'Z'.

    Without the marker, `new Date("2026-08-14T07:24:16")` in the browser is
    parsed as LOCAL time per the ES spec, so a UTC value renders 5.5 hours
    early in IST. The suffix is what makes toLocaleString() correct.
    """
    return dt.isoformat() + "Z" if dt else None


def _sender_display(raw: str | None) -> str:
    """"Name <a@b.com>" -> "Name", bare address -> the address. Falls back
    to the raw header if neither shape parses."""
    if not raw:
        return "—"
    m = re.match(r'^\s*"?([^"<]+?)"?\s*<', raw)
    if m and m.group(1).strip():
        return m.group(1).strip()
    return _sender_email(raw) or raw.strip()


def get_incoming_messages(
    tier: str | None = None, intent: str | None = None,
    only_recipient_kind: str | None = "to", limit: int = 200,
    page: int | None = None, page_size: int | None = None,
    sbi_only: bool = False,
) -> list[dict] | dict:
    """The working list behind the dashboard's triage view: real messages
    filtered by triage tier/intent, newest first. Scoped to direct mail by
    default (same reasoning as the rest of the triage read layer — Cc'd
    mail was never this account's to action).

    Read-only, and note this is a *view* concern only: filtering here hides
    nothing in Gmail and changes no mailbox state, it just decides what the
    dashboard puts in front of you.

    Backward compatible: with page/page_size omitted, returns the same bare
    list it always has (limit-capped, no total count). Passing page/page_size
    switches to the paginated shape {total, page, pageSize, rows} the
    "Power BI" drill-down tables use — a real total lets the UI show "Page 3
    of 40" instead of a silent 200-row cap.
    """
    with get_db() as db:
        q = db.query(IncomingEmail)
        if only_recipient_kind is not None:
            q = q.filter(IncomingEmail.recipient_kind == only_recipient_kind)
        if tier:
            q = q.filter(IncomingEmail.triage_tier == tier)
        if intent:
            q = q.filter(IncomingEmail.triage_intent == intent)
        if sbi_only:
            from sqlalchemy import or_
            q = q.filter(or_(*(IncomingEmail.sender.ilike(f"%{d}%") for d in _SBI_DOMAINS_FOR_SUMMARY)))
        q = q.order_by(IncomingEmail.received_at.desc().nullslast())

        total = None
        if page is not None and page_size is not None:
            total = q.count()
            q = q.offset((page - 1) * page_size).limit(page_size)
        else:
            q = q.limit(limit)

        rows = [
            {
                "id": r.id,
                "receivedAt": _utc_iso(r.received_at or r.created_at),
                "sender": _sender_display(r.sender),
                "subject": (r.subject or "(no subject)")[:200],
                "tier": r.triage_tier or "other",
                "intent": r.triage_intent,
                "replied": bool(r.replied),
            }
            for r in q.all()
        ]
        if total is not None:
            return {"total": total, "page": page, "pageSize": page_size, "rows": rows}
        return rows


_SUBJECT_PREFIX_RE = re.compile(r"^(re|fwd?|fw)\s*:\s*", re.IGNORECASE)


def _normalize_subject(subject: str | None) -> str:
    """Strip repeated Re:/Fwd:/Fw: prefixes so "Re: Re: X" and "X" group
    together as the same recurring pattern."""
    if not subject:
        return ""
    s = subject.strip()
    prev = None
    while prev != s:
        prev = s
        s = _SUBJECT_PREFIX_RE.sub("", s).strip()
    return s.lower()


# A subject needs at least this many occurrences to count as a "recurring
# pattern" at all — a one-off isn't a pattern.
_MIN_PATTERN_OCCURRENCES = 3
# ...and at least this fraction of occurrences already got a reply, to be
# flagged as an automation *candidate* rather than just "recurring but we
# don't consistently act on it" (could be noise we correctly ignore).
_CANDIDATE_REPLY_RATE = 0.5
# A pattern counts as "covered" once this share of its occurrences already
# matched a saved reply category — same bar get_new_category_suggestions
# uses to decide whether a pattern is still worth proposing as a new
# category, so the dashboard's "Covered" label and the Suggestions agent's
# behavior never disagree about the same pattern.
_COVERED_THRESHOLD = 0.4


def get_subject_patterns(min_occurrences: int = _MIN_PATTERN_OCCURRENCES) -> list[dict]:
    """Groups incoming email by normalized subject — scoped to
    recipient_kind == "to" only. A reply automation candidate only makes
    sense for mail actually addressed to the connected account; Cc'd
    threads aren't the account's to answer, so they're excluded entirely
    rather than just scored differently. automation_score is a plain,
    documented formula — not a model — so it's easy to sanity-check:
    min(count / 5, 1.0) * reply_rate. is_candidate requires both a recurring
    volume (>= min_occurrences) AND a reply rate showing this pattern
    routinely gets acted on (>= _CANDIDATE_REPLY_RATE). Also reports
    matched_count/coverage/covered against saved reply categories, so the
    dashboard can show whether a candidate has actually been turned into a
    category yet rather than just repeating the same "Candidate" label
    forever after one already has been."""
    with get_db() as db:
        rows = db.query(IncomingEmail).filter(IncomingEmail.recipient_kind == "to").all()

    groups: dict[str, list[IncomingEmail]] = {}
    for r in rows:
        key = _normalize_subject(r.subject)
        if not key:
            continue
        groups.setdefault(key, []).append(r)

    patterns = []
    for key, items in groups.items():
        count = len(items)
        if count < min_occurrences:
            continue
        reply_count = sum(1 for i in items if i.replied)
        reply_rate = reply_count / count if count else 0.0
        automation_score = min(count / 5, 1.0) * reply_rate
        is_candidate = count >= min_occurrences and reply_rate >= _CANDIDATE_REPLY_RATE
        matched_count = sum(1 for i in items if i.matched_reply_template_id is not None)
        coverage = matched_count / count if count else 0.0
        timestamps = [i.received_at or i.created_at for i in items if (i.received_at or i.created_at)]
        patterns.append({
            "subject": items[0].subject or key,
            "count": count,
            "reply_count": reply_count,
            "reply_rate": round(reply_rate, 3),
            "automation_score": round(automation_score, 3),
            "is_candidate": is_candidate,
            "matched_count": matched_count,
            "coverage": round(coverage, 3),
            "covered": coverage >= _COVERED_THRESHOLD,
            "first_seen": min(timestamps) if timestamps else None,
            "last_seen": max(timestamps) if timestamps else None,
        })

    return sorted(patterns, key=lambda p: p["count"], reverse=True)


def get_automation_summary(sbi_only: bool = False) -> dict:
    """`sbi_only` scopes every count to SBI-domain senders — added
    2026-08-26 so the top KPI tiles tell the same story as the
    SBI-scoped Work Queue and SBI Mail Breakdown below them on the page,
    instead of mixing an all-sender Direct Mail count with an SBI-only
    Open Work Items count."""
    from sqlalchemy import or_

    with get_db() as db:
        base = db.query(IncomingEmail)
        if sbi_only:
            base = base.filter(or_(*(IncomingEmail.sender.ilike(f"%{d}%") for d in _SBI_DOMAINS_FOR_SUMMARY)))
        total = base.count()
        replied = base.filter(IncomingEmail.replied.is_(True)).count()
        direct_total = base.filter(IncomingEmail.recipient_kind == "to").count()
        cc_total = base.filter(IncomingEmail.recipient_kind == "cc").count()
        # Reply rate over DIRECT mail only. The all-mail rate reads
        # misleadingly low because Cc'd threads were never this account's to
        # answer — including them makes the inbox look neglected when it
        # isn't.
        direct_replied = base.filter(
            IncomingEmail.recipient_kind == "to", IncomingEmail.replied.is_(True)
        ).count()

    # Patterns/candidates are direct-mail-only (see get_subject_patterns) —
    # automatable_pct is against direct_total, not the whole inbox, so it
    # reads honestly as "share of mail actually addressed to you" rather
    # than being diluted by Cc volume that was never a candidate anyway.
    patterns = get_subject_patterns()
    candidates = [p for p in patterns if p["is_candidate"]]
    # A candidate that's already "covered" by a saved reply category is no
    # longer open work — split the count so the KPI can't silently drift
    # out of sync with the table's Covered/Candidate/Not yet badges (a
    # covered candidate shows a green "Covered" badge there, not amber
    # "Candidate", even though it still counts toward candidate_count).
    candidate_covered_count = sum(1 for p in candidates if p["covered"])
    candidate_open_count = len(candidates) - candidate_covered_count
    automatable_volume = sum(p["count"] for p in candidates)

    return {
        "total_incoming": total,
        "total_replied": replied,
        "reply_rate": round(replied / total, 3) if total else 0.0,
        "direct_total": direct_total,
        "cc_total": cc_total,
        "direct_replied": direct_replied,
        "direct_reply_rate": round(direct_replied / direct_total, 3) if direct_total else 0.0,
        # Subject-pattern fields below are retained for the API/Suggestions
        # agent, which still mines exact-subject recurrence. The dashboard
        # deliberately no longer surfaces them: a 90-day sample showed 541 of
        # 693 direct subjects occur exactly once (ticket numbers and names
        # are embedded in subject lines), so subject recurrence is the wrong
        # primitive for "how much is automatable" — triage tiers are.
        "pattern_count": len(patterns),
        "candidate_count": len(candidates),
        "candidate_covered_count": candidate_covered_count,
        "candidate_open_count": candidate_open_count,
        "automatable_volume": automatable_volume,
        "automatable_pct": round(automatable_volume / direct_total, 3) if direct_total else 0.0,
    }


def get_new_category_suggestions(
    min_occurrences: int = 3, max_coverage: float = _COVERED_THRESHOLD, limit: int = 10,
) -> list[dict]:
    """For each exact-subject recurring pattern that already qualifies as an
    automation candidate (same count + reply-rate bar as get_subject_
    patterns' is_candidate — recurring AND actually gets replied to, so
    bounce/undeliverable notifications with a 0% reply rate never qualify),
    checks how much of it is already covered by a matched reply template.
    Patterns below max_coverage are surfaced as new-category candidates:
    the pattern's own (already-verified-recurring) subject becomes the
    suggested match keyword, guaranteeing it covers every past occurrence
    immediately. This is the read-only mining step behind the Suggestions
    agent's incoming-mail detector (services/suggestion_service.py) — it
    never creates anything itself, only proposes."""
    with get_db() as db:
        rows = db.query(IncomingEmail).filter(IncomingEmail.recipient_kind == "to").all()

    groups: dict[str, list[IncomingEmail]] = {}
    for r in rows:
        key = _normalize_subject(r.subject)
        if not key:
            continue
        groups.setdefault(key, []).append(r)

    candidates = []
    for key, items in groups.items():
        count = len(items)
        if count < min_occurrences:
            continue
        reply_rate = sum(1 for i in items if i.replied) / count
        if reply_rate < _CANDIDATE_REPLY_RATE:
            continue  # recurring but nobody actually replies to it (e.g. bounce notifications) — not a real category
        matched = sum(1 for i in items if i.matched_reply_template_id is not None)
        coverage = matched / count
        if coverage >= max_coverage:
            continue
        candidates.append({
            "normalized_subject": key[:200],
            "example_subject": (items[0].subject or key).strip(),
            "count": count,
            "matched_count": matched,
            "coverage": round(coverage, 3),
        })

    return sorted(candidates, key=lambda c: c["count"], reverse=True)[:limit]


# Generic connector/greeting words filtered out of keyword-mining below —
# deliberately NOT filtering domain words like "request"/"update" since
# those are exactly the kind of signal worth surfacing as a keyword.
_MINE_STOPWORDS = {
    "the", "and", "for", "with", "from", "your", "this", "that", "have", "has", "will", "was", "were",
    "are", "not", "you", "our", "sir", "madam", "dear", "regards", "thanks", "thank", "hello", "subject",
    "mail", "email", "team", "please", "kindly",
}


def get_low_confidence_category_suggestions(min_matched: int = 2, max_avg_confidence: float = 0.5) -> list[dict]:
    """For each active reply category whose matched emails average low
    match confidence, mines a candidate keyword to add: the most frequent
    significant word across those matched emails' subjects that isn't
    already covered by the category's existing keyword list. Read-only —
    the category is only ever edited if a human approves the resulting
    Suggestion. This is what makes the agent 'self-improving': it revises
    its own existing categories based on how well they're actually
    matching, not just proposes brand-new ones."""
    with get_db() as db:
        templates = db.query(IncomingReplyTemplate).filter(IncomingReplyTemplate.is_active.is_(True)).all()
        results = []
        for tpl in templates:
            matched = (
                db.query(IncomingEmail)
                .filter(IncomingEmail.recipient_kind == "to", IncomingEmail.matched_reply_template_id == tpl.id)
                .all()
            )
            if len(matched) < min_matched:
                continue
            avg_conf = sum(m.match_confidence or 0 for m in matched) / len(matched)
            if avg_conf >= max_avg_confidence:
                continue

            existing_text = tpl.match_keywords.lower()
            word_counts: dict[str, int] = {}
            for m in matched:
                words = set(re.findall(r"[a-z]{4,}", (m.subject or "").lower()))
                for w in words - _MINE_STOPWORDS:
                    if w in existing_text:
                        continue
                    word_counts[w] = word_counts.get(w, 0) + 1
            if not word_counts:
                continue
            best_word, best_count = max(word_counts.items(), key=lambda kv: kv[1])
            if best_count < 2:
                continue
            results.append({
                "template_id": tpl.id, "category_name": tpl.category_name,
                "matched_count": len(matched), "avg_confidence": round(avg_conf, 3),
                "suggested_keyword": best_word, "keyword_hit_count": best_count,
            })
    return results


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
