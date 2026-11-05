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
                     body_html: str, signature_html: str | None, draft_id: str,
                     applied_rule_ids: list[int] | None = None) -> None:
    """Called right after an ack draft is created, inside the caller's
    session. `body_html` is the draft body as sent to Gmail minus the
    signature, i.e. the template with any learned rules applied. Never
    raises: a logging problem must not undo the draft's ack_draft_id and
    cause a second draft next cycle."""
    try:
        if db.query(ReplyDraftLog.id).filter(ReplyDraftLog.incoming_email_id == row.id).first():
            return
        db.add(ReplyDraftLog(
            incoming_email_id=row.id,
            gmail_thread_id=row.gmail_thread_id,
            gmail_draft_id=draft_id,
            template_id=tpl.id,
            template_category=template_category,
            drafted_text="\n".join(_sentences(_html_to_text(body_html))),
            signature_text="\n".join(_sentences(_html_to_text(signature_html))),
            drafted_at=datetime.utcnow(),
            applied_rules_json=json.dumps(applied_rule_ids or []),
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


_last_learn_run: datetime | None = None
_LEARN_EVERY = timedelta(minutes=30)


def refresh_reply_learning(get_signature=None) -> dict:
    """Poller entry point: log any older drafts, settle pending ones, and
    every 30 minutes learn new rules and check the active ones.
    `get_signature` is a callable returning the account's signature HTML."""
    global _last_learn_run
    backfilled = backfill_reply_draft_logs(get_signature)
    outcomes = check_reply_draft_outcomes()
    result = {"backfilled": backfilled, **outcomes}
    now = datetime.utcnow()
    if _last_learn_run is None or now - _last_learn_run >= _LEARN_EVERY:
        _last_learn_run = now
        result["switched_off"] = len(evaluate_learned_rules())
        result["learned"] = len(learn_reply_rules())
    return result


# ----------------------------------------------------------------------
# Part 2: learn from edits, apply to the next draft, measure, switch off
# ----------------------------------------------------------------------
LEARN_WINDOW = timedelta(days=90)
LEARN_MIN_SENT = 5          # sent replies for a template before anything is learned
LEARN_MIN_SUPPORT = 3       # a change must happen in at least this many different emails
LEARN_ALWAYS_RATE = 0.6     # ... and in this share of sent replies to apply always
COND_PRECISION = 0.8        # conditional: the change happens this often when the word is present
COND_RECALL = 0.8           # ... and the word is present this often when the change happens
EVAL_MIN_DRAFTS = 5         # sent drafts with a rule before judging it
SWITCH_OFF_UNDO_RATE = 0.4  # people undo the rule this often -> switch off
SWITCH_OFF_DROP = 0.15      # as-is rate falls this far below the baseline -> switch off

_DECIDED = ("sent_as_is", "edited")
_STOPWORDS = {
    "this", "that", "with", "from", "have", "your", "will", "please", "dear", "regards", "thanks",
    "thank", "sir", "madam", "kindly", "find", "attached", "attachment", "mail", "email", "date",
    "report", "sbi", "bank", "branch", "india", "state", "team", "also", "below", "above", "same",
    "been", "were", "they", "their", "there", "which", "would", "should", "into", "about", "subject",
}


def _learnable(phrase: str) -> bool:
    """Only generic wording is learned. A phrase with a number, an id or an
    address belongs to one email (ticket, CSP code, date, contact) and can't
    be reused in the next draft."""
    if re.search(r"\d", phrase) or "@" in phrase:
        return False
    return len(phrase.split()) >= 3


def _mail_words(subject: str | None, body: str | None) -> set[str]:
    text = f"{subject or ''} {(body or '')[:600]}".lower()
    return {w for w in re.findall(r"[a-z]{4,}", text) if w not in _STOPWORDS}


def _notify(fingerprint: str, title: str, description: str) -> None:
    """Tell the user on the Suggestions page (Incoming tab). Information
    only: switching a rule off is done from the Reply Drafts panel."""
    from services.suggestion_service import _upsert

    try:
        with get_db() as db:
            _upsert(
                db, category="incoming_learned_rule", title=title, description=description,
                severity="info", entity_type="LearnedReplyRule", entity_id=None,
                fingerprint=fingerprint, can_auto_fix=False,
            )
    except Exception:  # noqa: BLE001
        logger.exception("Could not post learned-rule notice")


def _rule_text(rule) -> str:
    when = f" when the mail mentions '{rule.condition_keyword}'" if rule.condition_keyword else " always"
    verb = "add" if rule.action == "add" else "leave out"
    return f"Rule for the '{rule.template_category}' draft — {verb}{when}: \"{rule.phrase}\""


def learn_reply_rules() -> list[int]:
    """Finds edits people make again and again and turns them into active
    rules. Returns the ids of rules created this run."""
    from database.incoming_models import LearnedReplyRule

    since = datetime.utcnow() - LEARN_WINDOW
    with get_db() as db:
        rows = (
            db.query(ReplyDraftLog, IncomingEmail.subject, IncomingEmail.body_text)
            .join(IncomingEmail, IncomingEmail.id == ReplyDraftLog.incoming_email_id)
            .filter(ReplyDraftLog.outcome.in_(_DECIDED), ReplyDraftLog.drafted_at >= since)
            .all()
        )
        items = [{
            "id": log.incoming_email_id, "template": log.template_category, "outcome": log.outcome,
            "added": json.loads(log.added_json) if log.added_json else [],
            "removed": json.loads(log.removed_json) if log.removed_json else [],
            "words": _mail_words(subject, body), "subject": subject,
        } for log, subject, body in rows]
        known = {
            (r.template_category, r.action, r.phrase_key, r.condition_keyword)
            for r in db.query(LearnedReplyRule).all()
        }

    by_template: dict[str, list[dict]] = {}
    for it in items:
        by_template.setdefault(it["template"] or "", []).append(it)

    created: list[int] = []
    for template, group in by_template.items():
        n = len(group)
        if not template or n < LEARN_MIN_SENT:
            continue
        baseline = sum(1 for g in group if g["outcome"] == "sent_as_is") / n
        for action, field in (("add", "added"), ("remove", "removed")):
            seen: dict[str, dict] = {}
            for g in group:
                for p in {p for p in g[field] if _learnable(p)}:
                    entry = seen.setdefault(_key(p), {"phrase": p, "emails": []})
                    entry["emails"].append(g)
            for key, entry in seen.items():
                support = len({g["id"] for g in entry["emails"]})
                if support < LEARN_MIN_SUPPORT:
                    continue
                condition, reason = None, None
                if support / n >= LEARN_ALWAYS_RATE:
                    done = "added this line" if action == "add" else "deleted this line"
                    reason = f"People {done} in {support} of the {n} '{template}' replies they sent."
                elif action == "add":
                    best = None
                    for w in set().union(*(g["words"] for g in entry["emails"])):
                        with_word = [g for g in group if w in g["words"]]
                        hits = sum(1 for g in entry["emails"] if w in g["words"])
                        precision, recall = hits / len(with_word), hits / support
                        if precision >= COND_PRECISION and recall >= COND_RECALL:
                            if best is None or precision + recall > best[1]:
                                best = (w, precision + recall, hits, len(with_word))
                    if best:
                        condition = best[0]
                        others = n - best[3]
                        reason = (
                            f"People added this line in {best[2]} of {best[3]} replies to mails that mention "
                            f"'{condition}', and in {support - best[2]} of {others} replies to mails that don't."
                        )
                if reason is None or (template, action, key, condition) in known:
                    continue
                with get_db() as db:
                    rule = LearnedReplyRule(
                        template_category=template, action=action, phrase=entry["phrase"], phrase_key=key,
                        condition_keyword=condition, support=support, opportunities=n, reason=reason,
                        evidence_json=json.dumps([
                            {"incomingEmailId": g["id"], "subject": g["subject"]} for g in entry["emails"][:10]
                        ]),
                        baseline_as_is_rate=round(baseline, 3), status="active",
                    )
                    db.add(rule)
                    db.flush()
                    created.append(rule.id)
                    text = _rule_text(rule)
                    fp = f"learned_rule:{rule.id}"
                known.add((template, action, key, condition))
                _notify(fp, "MIRA learned a change to an acknowledgment draft",
                        f"{text}. Why: {reason} It is already on for new drafts and switches itself off if "
                        f"drafts with it get edited more. Turn it off any time from Dashboard → Incoming → "
                        f"Reply Drafts.")
                logger.info("Learned reply rule %s: %s", rule.id, text)
    return created


def apply_learned_rules(db, template_category: str, body_html: str,
                        subject: str | None, body_text: str | None) -> tuple[str, list[int]]:
    """The draft body with every matching active rule applied. Additions go in
    as their own paragraph before the closing one; removals take the phrase
    out where it appears in the template text. Returns the rule ids used."""
    from database.incoming_models import LearnedReplyRule

    rules = (
        db.query(LearnedReplyRule)
        .filter(LearnedReplyRule.template_category == template_category, LearnedReplyRule.status == "active")
        .order_by(LearnedReplyRule.id)
        .all()
    )
    mail_text = f"{subject or ''} {(body_text or '')[:600]}".lower()
    html_out, applied = body_html, []
    for rule in rules:
        if rule.condition_keyword and rule.condition_keyword not in mail_text:
            continue
        if rule.action == "remove":
            pattern = re.compile(re.escape(rule.phrase), re.IGNORECASE)
            if pattern.search(html_out):
                html_out = re.sub(r"<p>\s*</p>", "", pattern.sub("", html_out, count=1))
                applied.append(rule.id)
        elif rule.phrase_key not in _key(_html_to_text(html_out)):
            para = f"<p>{html_lib.escape(rule.phrase)}</p>"
            last = html_out.lower().rfind("<p")
            html_out = html_out[:last] + para + html_out[last:] if last > 0 else html_out + para
            applied.append(rule.id)
    return html_out, applied


def evaluate_learned_rules() -> list[int]:
    """Judges each active rule on the drafts that carried it. Switched off
    when people keep undoing it, or when drafts get edited more than before.
    Returns the ids switched off this run."""
    from database.incoming_models import LearnedReplyRule

    switched: list[int] = []
    with get_db() as db:
        active = [r.id for r in db.query(LearnedReplyRule).filter(LearnedReplyRule.status == "active").all()]
    for rule_id in active:
        with get_db() as db:
            rule = db.query(LearnedReplyRule).get(rule_id)
            logs = (
                db.query(ReplyDraftLog)
                .filter(
                    ReplyDraftLog.template_category == rule.template_category,
                    ReplyDraftLog.drafted_at >= rule.activated_at,
                    ReplyDraftLog.outcome.in_(_DECIDED),
                    ReplyDraftLog.applied_rules_json.isnot(None),
                )
                .all()
            )
            carried = [lg for lg in logs if rule.id in json.loads(lg.applied_rules_json or "[]")]
            n = len(carried)
            rule.measured_drafts = n
            if n == 0:
                continue
            as_is = sum(1 for lg in carried if lg.outcome == "sent_as_is") / n
            rule.measured_as_is_rate = round(as_is, 3)
            if n < EVAL_MIN_DRAFTS:
                continue
            undo_field = "removed_json" if rule.action == "add" else "added_json"
            undone = sum(
                1 for lg in carried
                if any(rule.phrase_key in _key(p) or _key(p) in rule.phrase_key
                       for p in json.loads(getattr(lg, undo_field) or "[]"))
            )
            reason = None
            if undone / n >= SWITCH_OFF_UNDO_RATE:
                what = "removed this learned line" if rule.action == "add" else "put this line back"
                reason = f"People {what} in {undone} of {n} drafts that carried it."
            elif rule.baseline_as_is_rate is not None and as_is < rule.baseline_as_is_rate - SWITCH_OFF_DROP:
                reason = (
                    f"Drafts got worse with it: sent unchanged {round(as_is * 100)}% of the time, "
                    f"down from {round(rule.baseline_as_is_rate * 100)}% before."
                )
            if reason:
                rule.status = "switched_off"
                rule.switched_off_at = datetime.utcnow()
                rule.switched_off_reason = reason
                rule.switched_off_by = "auto-check"
                switched.append(rule.id)
                text = _rule_text(rule)
        if rule_id in switched:
            _notify(f"rule_off:{rule_id}", "MIRA switched off a learned draft change",
                    f"{text}. Switched off because: {reason}")
            logger.info("Switched off reply rule %s: %s", rule_id, reason)
    return switched


def switch_off_rule(rule_id: int, username: str) -> dict:
    from database.incoming_models import LearnedReplyRule
    from services.audit_service import log_action

    with get_db() as db:
        rule = db.query(LearnedReplyRule).get(rule_id)
        if rule is None:
            raise ValueError("Rule not found.")
        if rule.status != "active":
            raise ValueError("This rule is already off.")
        rule.status = "switched_off"
        rule.switched_off_at = datetime.utcnow()
        rule.switched_off_reason = "Switched off by hand."
        rule.switched_off_by = username
        log_action(db, "SWITCH_OFF_LEARNED_REPLY_RULE", username=username,
                   entity_type="LearnedReplyRule", entity_id=rule.id, details=_rule_text(rule))
        return {"id": rule.id, "status": rule.status}


def list_rules() -> list[dict]:
    from database.incoming_models import LearnedReplyRule
    from utils.helpers import utc_iso

    with get_db() as db:
        return [{
            "id": r.id, "template": r.template_category, "action": r.action, "phrase": r.phrase,
            "condition": r.condition_keyword, "reason": r.reason, "support": r.support,
            "status": r.status, "activatedAt": utc_iso(r.activated_at),
            "switchedOffAt": utc_iso(r.switched_off_at), "switchedOffReason": r.switched_off_reason,
            "switchedOffBy": r.switched_off_by, "baselineAsIsRate": r.baseline_as_is_rate,
            "measuredDrafts": r.measured_drafts, "measuredAsIsRate": r.measured_as_is_rate,
        } for r in db.query(LearnedReplyRule).order_by(LearnedReplyRule.activated_at.desc()).all()]


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
