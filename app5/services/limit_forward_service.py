"""
Limit-approval forwarding workflow — REAL SEND, per explicit instruction
(this ran draft-only from 2026-08-14 as a supervised trial first; the
switch to sending was a deliberate decision after reviewing that trial's
drafts, not a default).

The real, observed process this automates
-----------------------------------------
1. A field colleague mails "Please approve my limit request <ticket#> ..."
2. Ganesh FORWARDS it to Priyanshu, keeping the original requester in Cc
3. Priyanshu replies "Approved." — which closes that ticket

Steps 1 and 3 are already detected (triage intents + ExtractedTask). This
module automates step 2's mechanics (now a real send, not a draft) and step
3's bookkeeping (always ran unconditionally, draft-only phase or not).

Recipients were not guessed — they were read off 15 real past forwards
fetched from Gmail, which were unanimous:
    To: Priyanshu Kumar <priyanshu.kumar@ekosupport.in>   15/15
    Cc: panditanil06 <panditanil06@gmail.com>             15/15
(and 75 of 76 forwards in the whole Sent folder went to that same To.)

The one thing standing between this and a real mistake is
_is_from_forward_target: since this now sends without a human in the loop,
that guard is load-bearing, not a nicety — see its own docstring.
"""
from __future__ import annotations

import html
import os
import random
import re
import time

from config import settings
from database.db import get_db
from database.incoming_models import ExtractedTask, IncomingEmail
from services import gmail_service as gs
from services.incoming_service import _sender_email, extract_task_identifiers
from utils.logger import get_logger

logger = get_logger(__name__)

# Overridable, but defaulted to the empirically-verified recipients above.
FORWARD_TO = os.getenv("LIMIT_FORWARD_TO", "priyanshu.kumar@ekosupport.in").strip().lower()
FORWARD_CC = os.getenv("LIMIT_FORWARD_CC", "panditanil06@gmail.com").strip().lower()

REQUEST_INTENT = "Limit Approval Request"
APPROVED_INTENT = "Limit Approval — Approved (close ticket)"

# Phrases that mark Priyanshu's reply as a real approval rather than a
# question or a "can't do this" — checked against the top of the body,
# before any quoted chain.
_APPROVAL_PHRASES = ("approved", "done", "completed", "processed")
_BODY_HEAD_CHARS = 220


def _body_head(body: str | None) -> str:
    """Top of the message only. Everything below a quote marker is the
    ORIGINAL request being quoted back — reading it would make every reply
    look like whatever the request said."""
    t = re.sub(r"<[^>]+>", " ", body or "")
    for marker in ("From:", "-----Original", "wrote:", "On Mon,", "On Tue,", "On Wed,",
                   "On Thu,", "On Fri,", "On Sat,", "On Sun,"):
        i = t.find(marker)
        if i > 0:
            t = t[:i]
    return re.sub(r"\s+", " ", t).strip()[:_BODY_HEAD_CHARS]


def _is_from_forward_target(sender: str | None) -> bool:
    """The one invariant that actually keeps this safe — and now the ONLY
    thing standing between a misclassification and a real, unreviewed send:
    there is no human checking a draft before it goes out anymore.

    Classification is keyword-based and WILL misfire — two of Priyanshu's
    own replies were observed landing in the request bucket because their
    subjects didn't match the expected shape. Forwarding those back to
    Priyanshu would create a loop. So rather than trusting the classifier,
    refuse outright to forward anything that came from the forward target.
    """
    return FORWARD_TO in (_sender_email(sender) or "").lower()


def _build_forward_body(row: IncomingEmail) -> str:
    orig = html.escape(row.body_text or "")
    return (
        "<p>Please approve.</p>"
        "<p>---------- Forwarded message ---------<br />"
        f"From: {html.escape(row.sender or '')}<br />"
        f"Date: {row.received_at or row.created_at}<br />"
        f"Subject: {html.escape(row.subject or '')}</p>"
        f"<div style=\"white-space:pre-wrap\">{orig}</div>"
    )


def create_forward_sends(max_items: int = 25, since=None) -> dict:
    """SEND the limit-approval forward directly to Priyanshu (Cc the
    original requester) for requests that haven't been forwarded yet.
    Idempotent: `ack_draft_id` (kept as the column name; holds the sent
    message id now, not a draft id) is set once sent, so re-running skips
    those rows.

    `since` bounds this to mail that ARRIVED after a given moment. The
    scheduled caller always passes it, so a first run after enabling this
    can never sweep up the whole historical backlog and blast it out at once.

    Real sends are paced (same EMAIL_PACE_MIN/MAX_SECONDS window
    email_service.py uses for report sends) rather than fired back-to-back,
    so a batch doesn't read as an automated burst to Gmail's abuse detection.
    """
    summary = {"candidates": 0, "sent": 0, "skipped_from_target": 0, "errors": 0}

    with get_db() as db:
        q = (
            db.query(IncomingEmail)
            .filter(
                IncomingEmail.recipient_kind == "to",
                IncomingEmail.triage_intent == REQUEST_INTENT,
                IncomingEmail.ack_draft_id.is_(None),
            )
        )
        if since is not None:
            q = q.filter(IncomingEmail.received_at >= since)
        rows = (
            q.order_by(IncomingEmail.received_at.desc())
            .limit(max_items * 3)   # over-fetch: some will be filtered by the guard
            .all()
        )
        candidates = [
            (r.id, r.gmail_thread_id, r.sender, r.subject, r.body_text,
             r.received_at or r.created_at)
            for r in rows
        ]

    # Apply the safety guard before touching Gmail at all.
    safe = []
    for cid, thread, sender, subject, body, received in candidates:
        if _is_from_forward_target(sender):
            summary["skipped_from_target"] += 1
            logger.info("Skipping %s — it is FROM the forward target (%s)", cid, FORWARD_TO)
            continue
        safe.append((cid, thread, sender, subject, body, received))
    safe = safe[:max_items]
    summary["candidates"] = len(safe)

    if not safe:
        logger.info("Limit forward sends: nothing to do. %s", summary)
        return summary

    try:
        service = gs.get_gmail_client()
    except Exception as exc:  # noqa: BLE001
        logger.error("Gmail client unavailable for forward sends: %s", exc)
        summary["error"] = str(exc)
        return summary

    for idx, (cid, thread, sender, subject, body, received) in enumerate(safe):
        try:
            with get_db() as db:
                row = db.query(IncomingEmail).get(cid)
                if row is None or row.ack_draft_id:
                    continue
                fwd_subject = subject or "(no subject)"
                if not fwd_subject.lower().startswith("fwd:"):
                    fwd_subject = f"Fwd: {fwd_subject}"
                sent_id = gs.send_message(
                    service,
                    to_email=FORWARD_TO,
                    subject=fwd_subject,
                    body_html=_build_forward_body(row),
                    attachment_path=None,
                    cc_emails=FORWARD_CC,
                )
                row.ack_draft_id = sent_id
                summary["sent"] += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("Forward send failed for incoming %s: %s", cid, exc)
            summary["errors"] += 1

        if idx < len(safe) - 1:
            pause = random.randint(settings.email_pace_min_seconds, settings.email_pace_max_seconds)
            time.sleep(pause)

    logger.info("Limit forward sends: %s", summary)
    return summary


def close_tickets_from_approvals() -> dict:
    """When Priyanshu replies "Approved.", close the matching open task(s).

    Matching is by ticket number parsed from the reply's subject (the reply
    quotes the original subject, which carries the ticket). Only closes
    tasks that are still open, and only when the reply body actually reads
    as an approval — a reply on the same thread asking a question must not
    close anything."""
    summary = {"approvals_seen": 0, "tickets_closed": 0, "no_match": 0, "not_an_approval": 0}

    with get_db() as db:
        approvals = (
            db.query(IncomingEmail)
            .filter(
                IncomingEmail.recipient_kind == "to",
                IncomingEmail.triage_intent == APPROVED_INTENT,
            )
            .all()
        )
        items = [(r.id, r.sender, r.subject, r.body_text) for r in approvals]

    for _id, sender, subject, body in items:
        summary["approvals_seen"] += 1
        head = _body_head(body).lower()
        if not any(p in head for p in _APPROVAL_PHRASES):
            summary["not_an_approval"] += 1
            continue
        idents = [i for i, kind in extract_task_identifiers(subject) if kind == "ticket"]
        if not idents:
            summary["no_match"] += 1
            continue
        with get_db() as db:
            for ident in idents:
                tasks = (
                    db.query(ExtractedTask)
                    .filter(ExtractedTask.identifier == ident, ExtractedTask.status == "open")
                    .all()
                )
                for t in tasks:
                    t.status = "done"
                    t.resolved_by_username = "auto (Priyanshu approved)"
                    from datetime import datetime
                    t.resolved_at = datetime.utcnow()
                    summary["tickets_closed"] += 1

    logger.info("Ticket auto-close from approvals: %s", summary)
    return summary
