"""
Reply-draft learning loop, part 1: record and measure.

Every acknowledgment draft this app creates for an incoming email is logged
with its exact text (ReplyDraftLog). Once the connected account's Sent folder
(SentEmail, filled by sent_mail_service.scan_sent_mail) holds a reply on the
same thread, the two are compared sentence by sentence and the draft gets an
outcome: sent as-is, edited (with the sentences added and removed), replied
separately, or not used. Per-template rates show how often a draft is good
enough to send untouched.

Read-only towards Gmail and the templates: nothing here drafts, sends or
changes a template. Learning from the edits (proposing template changes for
human approval) is a later step built on these logs.
"""
from __future__ import annotations

import difflib
import html as html_lib
import json
import re
from datetime import datetime, timedelta

from database.db import get_db
from database.incoming_models import IncomingEmail, IncomingReplyTemplate, ReplyDraftLog, SentEmail
from utils.logger import get_logger

logger = get_logger(__name__)

AS_IS_THRESHOLD = 0.95      # this similar or more: sent without meaningful change
SEPARATE_THRESHOLD = 0.30   # below this: a different reply was written instead
NOT_USED_AFTER = timedelta(days=7)
_CLOCK_SKEW = timedelta(minutes=5)

OUTCOMES = ("pending", "sent_as_is", "edited", "replied_separately", "not_used")


# ----------------------------------------------------------------------
# Text normalisation
# ----------------------------------------------------------------------
def _html_to_text(value: str | None) -> str:
    t = value or ""
    t = re.sub(r"(?i)<br\s*/?>", "\n", t)
    t = re.sub(r"(?i)</(p|div|li|tr|h[1-6])>", "\n\n", t)
    t = re.sub(r"<[^>]+>", " ", t)
    return html_lib.unescape(t)


def _strip_quoted(text: str) -> str:
    """Keep only the new reply. Gmail's plain text quotes the original
    below an 'On <date> ... wrote:' line (sometimes wrapped over two or
    three lines) or as '>'-prefixed lines."""
    lines = (text or "").splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith(">") or s.startswith("From:") or "-----Original" in s:
            return "\n".join(lines[:i])
        if re.match(r"^On\s.+", s) and "wrote:" in " ".join(lines[i:i + 3]):
            return "\n".join(lines[:i])
    return text or ""


def _sentences(text: str) -> list[str]:
    """Paragraphs, then sentences inside each. Sent plain text hard-wraps
    lines mid-sentence, so lines alone can't be compared."""
    out: list[str] = []
    for para in re.split(r"\n\s*\n", text or ""):
        flat = " ".join(para.split()).replace("*", "").strip()
        if not flat:
            continue
        for s in re.split(r"(?<=[.!?])\s+", flat):
            s = s.strip()
            if _key(s):
                out.append(s)
    return out


def _key(sentence: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", sentence.lower())


def _words(text: str) -> list[tuple[str, str]]:
    """(original word, match key) pairs; punctuation-only tokens dropped."""
    out = []
    for w in (text or "").replace("*", " ").split():
        k = _key(w)
        if k:
            out.append((w, k))
    return out


def _cut_signature(words: list[tuple[str, str]], signature_text: str) -> list[tuple[str, str]]:
    """Drop the signature block from a sent reply. Its spacing changes in
    plain text ("9821946240Operation"), so match on its leading characters
    with all spacing and punctuation removed, not word by word."""
    sig = _key(signature_text)[:15]
    if len(sig) < 6:
        return words
    joined, starts = "", []
    for _, k in words:
        starts.append(len(joined))
        joined += k
    pos = joined.find(sig)
    if pos < 0:
        return words
    for i, s in enumerate(starts):
        if s + len(words[i][1]) > pos:
            return words[:i]
    return words


def _phrase(words: list[tuple[str, str]]) -> str:
    return " ".join(w for w, _ in words).strip()


def compare(drafted_text: str, sent_reply_text: str, signature_text: str = "") -> dict:
    """Word-level comparison of what was drafted vs what was sent, so it
    doesn't matter where either side breaks lines or paragraphs. Returns the
    phrases added to and removed from the draft. The signature is ignored."""
    draft = _words(drafted_text)
    sent = _cut_signature(_words(sent_reply_text), signature_text)
    dk, sk = [k for _, k in draft], [k for _, k in sent]
    matcher = difflib.SequenceMatcher(None, dk, sk, autojunk=False)
    ratio = matcher.ratio() if (dk or sk) else 1.0
    added, removed = [], []
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op in ("insert", "replace") and _phrase(sent[j1:j2]):
            added.append(_phrase(sent[j1:j2]))
        if op in ("delete", "replace") and _phrase(draft[i1:i2]):
            removed.append(_phrase(draft[i1:i2]))
    if ratio >= AS_IS_THRESHOLD:
        outcome = "sent_as_is"
    elif ratio < SEPARATE_THRESHOLD:
        outcome = "replied_separately"
    else:
        outcome = "edited"
    return {"similarity": round(ratio, 3), "added": added, "removed": removed, "outcome": outcome}


# ----------------------------------------------------------------------
# Recording
# ----------------------------------------------------------------------
def record_ack_draft(db, row: IncomingEmail, tpl: IncomingReplyTemplate, template_category: str,
                     signature_html: str | None, draft_id: str) -> None:
    """Called right after an ack draft is created, inside the caller's
    session. Never raises: a logging problem must not undo the draft's
    ack_draft_id and cause a second draft next cycle."""
    try:
        if db.query(ReplyDraftLog.id).filter(ReplyDraftLog.incoming_email_id == row.id).first():
            return
        db.add(ReplyDraftLog(
            incoming_email_id=row.id,
            gmail_thread_id=row.gmail_thread_id,
            gmail_draft_id=draft_id,
            template_id=tpl.id,
            template_category=template_category,
            drafted_text="\n".join(_sentences(_html_to_text(tpl.body_template))),
            signature_text="\n".join(_sentences(_html_to_text(signature_html))),
            drafted_at=datetime.utcnow(),
        ))
    except Exception:  # noqa: BLE001
        logger.exception("Could not record reply draft log for incoming %s", row.id)


def backfill_reply_draft_logs(get_signature=None, ack_categories: list[str] | None = None) -> int:
    """Logs drafts made before this feature existed, reconstructing the
    drafted text from today's template (marked backfilled). Where a template
    has a "— Clear" variant, the variant closest to the sent reply is used."""
    from services.incoming_ack_service import ACK_INTENTS

    categories = ack_categories or list(ACK_INTENTS)
    created = 0
    with get_db() as db:
        templates = db.query(IncomingReplyTemplate).all()
        logged = {i for (i,) in db.query(ReplyDraftLog.incoming_email_id).all()}
        rows = (
            db.query(IncomingEmail)
            .filter(IncomingEmail.ack_draft_id.isnot(None), IncomingEmail.triage_intent.in_(categories))
            .all()
        )
        todo = [
            (r.id, r.gmail_thread_id, r.ack_draft_id, r.triage_intent, r.received_at)
            for r in rows if r.id not in logged
        ]
        tpl_by_intent = {
            intent: [t for t in templates if t.category_name == intent
                     or (t.category_name.startswith(intent) and "clear" in t.category_name.lower())]
            for intent in categories
        }
        tpl_data = {intent: [(t.id, t.category_name, t.body_template) for t in ts] for intent, ts in tpl_by_intent.items()}

    if not todo:
        return 0
    # Fetched only when there is something to log (a Gmail call on the server).
    sig_text = "\n".join(_sentences(_html_to_text(get_signature() if get_signature else None)))
    for iid, thread, draft_id, intent, received in todo:
        candidates = tpl_data.get(intent) or []
        if not candidates:
            continue
        with get_db() as db:
            sent = _first_sent_on_thread(db, thread, received) if thread else None
            best = candidates[0]
            if sent is not None and len(candidates) > 1:
                reply = _strip_quoted(sent.body_text or "")
                best = max(candidates, key=lambda c: compare(_html_to_text(c[2]), reply, sig_text)["similarity"])
            db.add(ReplyDraftLog(
                incoming_email_id=iid, gmail_thread_id=thread, gmail_draft_id=draft_id,
                template_id=best[0], template_category=best[1],
                drafted_text="\n".join(_sentences(_html_to_text(best[2]))),
                signature_text=sig_text, drafted_at=received, backfilled=True,
            ))
            created += 1
    return created


# ----------------------------------------------------------------------
# Outcome detection
# ----------------------------------------------------------------------
def _first_sent_on_thread(db, thread_id: str | None, after: datetime | None):
    if not thread_id:
        return None
    q = db.query(SentEmail).filter(SentEmail.gmail_thread_id == thread_id)
    if after is not None:
        q = q.filter(SentEmail.sent_at >= after - _CLOCK_SKEW)
    return q.order_by(SentEmail.sent_at.asc()).first()


def check_reply_draft_outcomes(max_items: int = 300) -> dict:
    """Settles pending logs against the Sent folder. Commits per row so a
    long run can't hold a write lock against the pollers."""
    summary = {o: 0 for o in OUTCOMES if o != "pending"}
    now = datetime.utcnow()
    with get_db() as db:
        ids = [i for (i,) in db.query(ReplyDraftLog.id)
               .filter(ReplyDraftLog.outcome == "pending").limit(max_items).all()]
    for log_id in ids:
        with get_db() as db:
            log = db.query(ReplyDraftLog).get(log_id)
            if log is None or log.outcome != "pending":
                continue
            sent = _first_sent_on_thread(db, log.gmail_thread_id, log.drafted_at)
            if sent is None:
                # The Sent scan only covers recent mail. If Gmail says the
                # thread was replied to, the reply exists but isn't scanned:
                # stay pending rather than wrongly call the draft unused.
                replied = db.query(IncomingEmail.replied).filter(IncomingEmail.id == log.incoming_email_id).scalar()
                if not replied and log.drafted_at and now - log.drafted_at > NOT_USED_AFTER:
                    log.outcome = "not_used"
                    log.checked_at = now
                    summary["not_used"] += 1
                continue
            reply = _strip_quoted(sent.body_text or "")
            result = compare(log.drafted_text or "", reply, log.signature_text or "")
            log.outcome = result["outcome"]
            log.similarity = result["similarity"]
            log.added_json = json.dumps(result["added"])
            log.removed_json = json.dumps(result["removed"])
            log.sent_gmail_message_id = sent.gmail_message_id
            log.sent_at = sent.sent_at
            log.sent_text = "\n".join(_sentences(reply))
            log.checked_at = now
            summary[result["outcome"]] += 1
    if any(summary.values()):
        logger.info("Reply draft outcomes settled: %s", summary)
    return summary


def refresh_reply_learning(get_signature=None) -> dict:
    """Poller entry point: log any older drafts, then settle pending ones.
    `get_signature` is a callable returning the account's signature HTML."""
    backfilled = backfill_reply_draft_logs(get_signature)
    outcomes = check_reply_draft_outcomes()
    return {"backfilled": backfilled, **outcomes}


# ----------------------------------------------------------------------
# Read models for the dashboard
# ----------------------------------------------------------------------
def summary_by_template() -> dict:
    with get_db() as db:
        rows = db.query(ReplyDraftLog.template_category, ReplyDraftLog.outcome).all()
    by: dict[str, dict] = {}
    for category, outcome in rows:
        b = by.setdefault(category or "(unknown)", {"template": category or "(unknown)", **{o: 0 for o in OUTCOMES}})
        b[outcome] = b.get(outcome, 0) + 1
    templates = []
    for b in by.values():
        decided = b["sent_as_is"] + b["edited"] + b["replied_separately"]
        b["total"] = sum(b[o] for o in OUTCOMES)
        b["asIsRate"] = round(b["sent_as_is"] / decided, 3) if decided else None
        templates.append(b)
    templates.sort(key=lambda t: -t["total"])
    totals = {o: sum(t[o] for t in templates) for o in OUTCOMES}
    decided = totals["sent_as_is"] + totals["edited"] + totals["replied_separately"]
    return {
        "templates": templates,
        "totals": {**totals, "total": sum(totals.values()),
                   "asIsRate": round(totals["sent_as_is"] / decided, 3) if decided else None},
    }


def detail(template: str | None = None, outcome: str | None = None, page: int = 1, page_size: int = 20) -> dict:
    from utils.helpers import utc_iso

    with get_db() as db:
        q = (
            db.query(ReplyDraftLog, IncomingEmail.subject, IncomingEmail.sender)
            .join(IncomingEmail, IncomingEmail.id == ReplyDraftLog.incoming_email_id)
        )
        if template:
            q = q.filter(ReplyDraftLog.template_category == template)
        if outcome:
            q = q.filter(ReplyDraftLog.outcome == outcome)
        total = q.count()
        items = (
            q.order_by(ReplyDraftLog.drafted_at.desc())
            .offset((page - 1) * page_size).limit(page_size).all()
        )
        rows = [{
            "id": log.id,
            "subject": subject,
            "sender": sender,
            "template": log.template_category,
            "outcome": log.outcome,
            "similarity": log.similarity,
            "draftedAt": utc_iso(log.drafted_at),
            "sentAt": utc_iso(log.sent_at),
            "backfilled": log.backfilled,
            "added": json.loads(log.added_json) if log.added_json else [],
            "removed": json.loads(log.removed_json) if log.removed_json else [],
            "draftedText": log.drafted_text,
            "sentText": log.sent_text,
        } for log, subject, sender in items]
    return {"total": total, "page": page, "pageSize": page_size, "rows": rows}
