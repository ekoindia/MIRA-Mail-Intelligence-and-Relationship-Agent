"""
Gmail INCOMING service (new inbound subsystem).

Scope
-----
This module reads operational report emails from a Gmail inbox, classifies
them (report type + org level + LHO/circle), extracts key metrics from the
body, files attachments to disk, and creates DRAFT acknowledgment/routing
emails. It writes only to the additive incoming_* tables.

Hard guarantees
---------------
* DRAFTS ONLY. This module never sends mail. It calls users.drafts.create,
  never users.messages.send. Outbound sending remains the sole job of the
  existing services/email_service.py (Microsoft Graph / SMTP), which is
  untouched.
* DEDUP BY GMAIL MESSAGE ID. Every message is keyed by its immutable Gmail
  id with a UNIQUE constraint; already-ingested ids are skipped.

Credentials
-----------
OAuth (client file, token cache, scopes) is shared with the outbound
sending channel via services/gmail_auth.py — see that module for the
"Connect Gmail" flow surfaced in Settings. This module never asks for
or stores your password — only the OAuth token Google issues.

Dependencies (add to requirements.txt):
    google-api-python-client
    google-auth
    google-auth-oauthlib
"""
from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Iterable

from config import BASE_DIR
from services.gmail_auth import get_gmail_client  # noqa: F401 (re-exported for callers of this module)
from utils.logger import get_logger

logger = get_logger(__name__)

# ----------------------------------------------------------------------
# Config (read from env with safe defaults; no secrets committed)
# ----------------------------------------------------------------------
GMAIL_USER_ID = os.getenv("GMAIL_USER_ID", "me")
INCOMING_ATTACH_DIR = Path(os.getenv("INCOMING_ATTACH_DIR", str(BASE_DIR / "data" / "incoming_attachments")))
# Gmail search query controlling what the poller pulls. Tune per your inbox.
GMAIL_QUERY = os.getenv("GMAIL_QUERY", "newer_than:14d -in:sent")


# ----------------------------------------------------------------------
# Classification vocab — derived from the reporting framework's structured
# subjects. Extend these lists as real inbox samples are observed.
# ----------------------------------------------------------------------
REPORT_KEYWORDS: dict[str, list[str]] = {
    "Social Security Scheme": ["social security", "sss", "pmjjby", "pmsby", "apy"],
    "Account Opening": ["account opening", "pmjdy", "new account"],
    "Re-KYC & Inoperative": ["re-kyc", "rekyc", "kyc", "inoperative", "reactivation"],
    "Loan Lead": ["loan lead", "lead generation", "leads converted", "loan leads"],
    "Server Issue": ["server issue", "downtime", "outage"],
    "CSP Camp": ["physical camp", "csp camp", "sssa", "enrollment camp"],
    "Inactive CSP": ["inactive csp", "inactive", "reactivated csp"],
    "DFS Slab": ["dfs", "incentive slab", "slab achievement"],
    "Income Impact": ["income impact", "csp score", "mom growth", "income streak"],
    "MoM Inputs": ["month-on-month", "mom growth input", "growth input"],
}

LEVEL_KEYWORDS: dict[str, list[str]] = {
    "Branch": ["branch"],
    "RBO": ["rbo", "regional business office"],
    "AO": ["administrative office", " ao "],
    "LHO": ["lho", "local head office", "circle"],
    "Corp": ["corporate", "corp centre", "corporate center"],
}

KNOWN_LHOS = ["Mumbai", "Delhi", "Kolkata", "Chennai", "Hyderabad", "Lucknow", "Patna"]

# Metric field patterns per report type. Values captured as text; parsed on read.
METRIC_PATTERNS: dict[str, dict[str, str]] = {
    "_common": {
        "target_vs_achievement": r"target\s*vs\.?\s*achievement\s*[:\-]?\s*([\d.,%]+)",
        "mtd": r"\bMTD\b[^:\n]*[:\-]?\s*([\d.,%]+)",
        "ftd": r"\bFTD\b[^:\n]*[:\-]?\s*([\d.,%]+)",
    },
    "Loan Lead": {
        "leads_generated": r"leads?\s*generated\s*[:\-]?\s*([\d.,]+)",
        "converted": r"converted\s*[:\-]?\s*([\d.,]+)",
        "active_csps": r"active\s+lead[- ]generating\s+csps?\s*[:\-]?\s*([\d.,]+)",
        "non_responders": r"non[- ]responders?\s*[:\-]?\s*([\d.,]+)",
    },
    "Server Issue": {
        "downtime_incidents": r"downtime\s+incidents?\s*[:\-]?\s*([\d.,]+)",
        "downtime_hours": r"cumulative\s+downtime\s+hours?\s*[:\-]?\s*([\d.,]+)",
    },
    "CSP Camp": {
        "camps_held": r"camps?\s+held\s*[:\-]?\s*([\d.,]+)",
        "accounts_serviced": r"accounts?\s+serviced\s*[:\-]?\s*([\d.,]+)",
        "enrollments": r"enrollments?\s+generated\s*[:\-]?\s*([\d.,]+)",
    },
    "Inactive CSP": {
        "total_inactive": r"total\s+inactive\s+csps?\s*[:\-]?\s*([\d.,]+)",
        "newly_inactive": r"newly\s+inactive[^:\n]*[:\-]?\s*([\d.,]+)",
        "reactivated": r"reactivated\s*[:\-]?\s*([\d.,]+)",
    },
}


# ----------------------------------------------------------------------
# Data holders
# ----------------------------------------------------------------------
@dataclass
class ParsedMessage:
    gmail_message_id: str
    gmail_thread_id: str | None
    sender: str | None
    subject: str | None
    snippet: str | None
    body_text: str | None
    received_at: datetime | None
    attachments: list[dict] = field(default_factory=list)  # {name, mime, data(bytes)}
    to_header: str | None = None
    cc_header: str | None = None


@dataclass
class Classification:
    report_type: str | None
    level: str | None
    lho_name: str | None
    rbo_name: str | None
    confidence: float


# ----------------------------------------------------------------------
# Auth + client — see services/gmail_auth.get_gmail_client (imported above)
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# Fetch + parse
# ----------------------------------------------------------------------
def list_message_ids(service, query: str | None = None, max_results: int = 100) -> list[str]:
    query = query if query is not None else GMAIL_QUERY
    ids: list[str] = []
    page_token = None
    while True:
        resp = (
            service.users()
            .messages()
            .list(userId=GMAIL_USER_ID, q=query, maxResults=min(max_results, 100), pageToken=page_token)
            .execute()
        )
        ids.extend(m["id"] for m in resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token or len(ids) >= max_results:
            break
    return ids[:max_results]


def _decode_part(data: str) -> bytes:
    return base64.urlsafe_b64decode(data.encode("utf-8"))


def _walk_parts(payload) -> tuple[str, list[dict]]:
    """Return (plain_text_body, attachments[]) from a Gmail payload tree."""
    body_text = ""
    attachments: list[dict] = []

    def recurse(part):
        nonlocal body_text
        mime = part.get("mimeType", "")
        filename = part.get("filename") or ""
        body = part.get("body", {})

        if filename:  # attachment part
            attachments.append({
                "name": filename,
                "mime": mime,
                "attachment_id": body.get("attachmentId"),
                "size": body.get("size", 0),
            })
        elif mime == "text/plain" and body.get("data"):
            body_text += _decode_part(body["data"]).decode("utf-8", errors="replace")
        elif mime == "text/html" and body.get("data") and not body_text:
            html = _decode_part(body["data"]).decode("utf-8", errors="replace")
            body_text += re.sub(r"<[^>]+>", " ", html)

        for sub in part.get("parts", []) or []:
            recurse(sub)

    recurse(payload)
    return body_text.strip(), attachments


def _utc_received_at(msg: dict, headers: dict) -> datetime | None:
    """Naive UTC receive time for a message.

    Prefers Gmail's own `internalDate` (epoch ms — unambiguous, and what
    thread_has_reply already compares against). Falls back to the Date
    header, CONVERTED to UTC rather than merely stripped of its offset.

    The strip-without-convert this replaces silently produced a column with
    two different meanings: mail from a +0530 sender stored correct IST wall
    time, while mail from a +0000 sender stored UTC — so an email that
    really arrived 12:54 IST displayed as 07:24, 5.5 hours early. Everything
    else in this app stores naive UTC (datetime.utcnow), so UTC is the one
    convention worth having here.
    """
    if msg.get("internalDate"):
        try:
            return datetime.utcfromtimestamp(int(msg["internalDate"]) / 1000)
        except Exception:  # noqa: BLE001
            pass
    if headers.get("date"):
        try:
            dt = parsedate_to_datetime(headers["date"])
            if dt is None:
                return None
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt  # already naive; assume UTC
        except Exception:  # noqa: BLE001
            return None
    return None


def fetch_message(service, message_id: str) -> ParsedMessage:
    msg = service.users().messages().get(userId=GMAIL_USER_ID, id=message_id, format="full").execute()
    payload = msg.get("payload", {})
    headers = {h["name"].lower(): h["value"] for h in payload.get("headers", [])}

    body_text, attach_meta = _walk_parts(payload)

    # Download attachment bytes
    attachments = []
    for a in attach_meta:
        data_bytes = b""
        if a.get("attachment_id"):
            att = (
                service.users().messages().attachments()
                .get(userId=GMAIL_USER_ID, messageId=message_id, id=a["attachment_id"])
                .execute()
            )
            if att.get("data"):
                data_bytes = _decode_part(att["data"])
        attachments.append({"name": a["name"], "mime": a["mime"], "data": data_bytes})

    received_at = _utc_received_at(msg, headers)

    return ParsedMessage(
        gmail_message_id=msg["id"],
        gmail_thread_id=msg.get("threadId"),
        sender=headers.get("from"),
        subject=headers.get("subject"),
        snippet=msg.get("snippet"),
        body_text=body_text,
        received_at=received_at,
        attachments=attachments,
        to_header=headers.get("to"),
        cc_header=headers.get("cc"),
    )


def fetch_message_headers(service, message_id: str) -> tuple[str | None, str | None]:
    """Lightweight (To, Cc) header fetch — no body/attachments — for
    backfilling recipient_kind on already-ingested rows without repeating
    a full fetch_message() call."""
    msg = (
        service.users().messages()
        .get(userId=GMAIL_USER_ID, id=message_id, format="metadata", metadataHeaders=["To", "Cc"])
        .execute()
    )
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    return headers.get("to"), headers.get("cc")


def fetch_message_id_header(service, message_id: str) -> str | None:
    """Lightweight fetch of the RFC `Message-ID` header (distinct from
    Gmail's own opaque message id) — needed as the In-Reply-To/References
    value when building a real threaded reply. No body/attachments work."""
    msg = (
        service.users().messages()
        .get(userId=GMAIL_USER_ID, id=message_id, format="metadata", metadataHeaders=["Message-ID"])
        .execute()
    )
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    return headers.get("message-id")


# Extra addresses this mailbox legitimately receives on besides its own
# login address — shared team aliases, distribution groups, etc. Comma
# separated in GMAIL_ACCOUNT_ALIASES.
#
# This matters more than it looks: without it, every message delivered via
# a team alias matches neither To nor Cc, lands in "unknown", and is then
# silently EXCLUDED from all the direct-mail analysis (triage, subject
# patterns, automation candidates — all scoped to recipient_kind == "to").
# A real 60-message sample of this mailbox's "unknown" bucket was 98%
# team-alias mail, not genuinely-unaddressable mail.
GMAIL_ACCOUNT_ALIASES = [
    a.strip().lower() for a in os.getenv("GMAIL_ACCOUNT_ALIASES", "").split(",") if a.strip()
]


def classify_recipient_kind(to_header: str | None, cc_header: str | None, account_email: str | None) -> str:
    """"to" if the connected account (or one of its configured aliases) is a
    direct recipient, "cc" if it only appears in Cc, "unknown" if neither
    header could be matched (e.g. no address resolved, or a Bcc-only
    delivery). To wins over Cc when both match — being directly addressed
    is the stronger signal."""
    needles = list(GMAIL_ACCOUNT_ALIASES)
    if account_email:
        needles.append(account_email.strip().lower())
    if not needles:
        return "unknown"

    to_l, cc_l = (to_header or "").lower(), (cc_header or "").lower()
    if any(n in to_l for n in needles):
        return "to"
    if any(n in cc_l for n in needles):
        return "cc"
    return "unknown"


# ----------------------------------------------------------------------
# Reply detection — read-only, no draft/send involved.
# ----------------------------------------------------------------------
def thread_has_reply(service, thread_id: str, after: datetime | None = None) -> tuple[bool, datetime | None]:
    """True + the send time of the earliest GENUINE reply on this thread —
    a SENT-labeled message strictly after `after` (the incoming message's
    received_at). Without `after`, falls back to "any SENT message exists"
    — kept only for callers that don't have a timestamp to compare against.

    Passing `after` matters: a thread can carry a SENT message from BEFORE
    the incoming message arrived (e.g. a bounce/delay notice replying to an
    email you originally sent) — counting that as "we replied to this"
    is a false positive. internalDate (Gmail's own delivery timestamp,
    epoch ms) is used in preference to the Date header, since the header
    is client-supplied and less reliable for ordering."""
    if not thread_id:
        return False, None
    thread = (
        service.users().threads()
        .get(userId=GMAIL_USER_ID, id=thread_id, format="metadata", metadataHeaders=["Date"])
        .execute()
    )
    genuine_replies: list[datetime] = []
    for msg in thread.get("messages", []):
        if "SENT" not in (msg.get("labelIds") or []):
            continue
        sent_at = None
        if msg.get("internalDate"):
            try:
                sent_at = datetime.utcfromtimestamp(int(msg["internalDate"]) / 1000)
            except Exception:  # noqa: BLE001
                sent_at = None
        if sent_at is None:
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            if headers.get("date"):
                try:
                    sent_at = parsedate_to_datetime(headers["date"])
                    if sent_at and sent_at.tzinfo:
                        sent_at = sent_at.replace(tzinfo=None)
                except Exception:  # noqa: BLE001
                    sent_at = None
        if sent_at is None:
            continue
        if after is not None and sent_at <= after:
            continue  # sent before the incoming message — not a reply to it
        genuine_replies.append(sent_at)
    if not genuine_replies:
        return False, None
    return True, min(genuine_replies)


def thread_has_incoming_reply(service, thread_id: str, after: datetime | None = None) -> tuple[bool, datetime | None]:
    """Mirror of thread_has_reply for the opposite direction: given a
    message WE sent, checks whether the recipient replied back — a message
    on the same thread that does NOT carry Gmail's own SENT label (i.e. it
    arrived from someone else), with a timestamp strictly after `after`
    (the sent message's own send time). Same internalDate-preferred timing
    logic as thread_has_reply, for the same reliability reason."""
    if not thread_id:
        return False, None
    thread = (
        service.users().threads()
        .get(userId=GMAIL_USER_ID, id=thread_id, format="metadata", metadataHeaders=["Date"])
        .execute()
    )
    genuine_replies: list[datetime] = []
    for msg in thread.get("messages", []):
        if "SENT" in (msg.get("labelIds") or []):
            continue  # one of our own outgoing messages, not a reply
        received_at = None
        if msg.get("internalDate"):
            try:
                received_at = datetime.utcfromtimestamp(int(msg["internalDate"]) / 1000)
            except Exception:  # noqa: BLE001
                received_at = None
        if received_at is None:
            headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
            if headers.get("date"):
                try:
                    received_at = parsedate_to_datetime(headers["date"])
                    if received_at and received_at.tzinfo:
                        received_at = received_at.replace(tzinfo=None)
                except Exception:  # noqa: BLE001
                    received_at = None
        if received_at is None:
            continue
        if after is not None and received_at <= after:
            continue  # received before we sent it — not a reply to this message
        genuine_replies.append(received_at)
    if not genuine_replies:
        return False, None
    return True, min(genuine_replies)


# ----------------------------------------------------------------------
# Classify
# ----------------------------------------------------------------------
def classify(subject: str | None, body: str | None, sender: str | None = None) -> Classification:
    text = f"{subject or ''} \n {body or ''}".lower()

    # Report type — score by keyword hits, subject-weighted.
    best_report, best_score = None, 0
    subj = (subject or "").lower()
    for report, kws in REPORT_KEYWORDS.items():
        score = 0
        for kw in kws:
            if kw in subj:
                score += 2
            elif kw in text:
                score += 1
        if score > best_score:
            best_report, best_score = report, score

    # Level
    level = None
    for lvl, kws in LEVEL_KEYWORDS.items():
        if any(kw in text for kw in kws):
            level = lvl
            break

    # LHO / circle
    lho = next((name for name in KNOWN_LHOS if name.lower() in text), None)

    # RBO token from structured subject: "RBO <name>"
    rbo = None
    m = re.search(r"\bRBO\s+([A-Za-z0-9 ._-]{2,40})", subject or "", re.IGNORECASE)
    if m:
        rbo = m.group(1).strip(" .-_")

    # Confidence: crude but useful for NEEDS_REVIEW routing.
    confidence = 0.0
    if best_report:
        confidence += min(best_score / 4.0, 0.6)
    if level:
        confidence += 0.2
    if lho:
        confidence += 0.2
    confidence = round(min(confidence, 1.0), 2)

    return Classification(best_report, level, lho, rbo, confidence)


# ----------------------------------------------------------------------
# Extract metrics
# ----------------------------------------------------------------------
def extract_metrics(report_type: str | None, body: str | None) -> dict[str, str]:
    if not body:
        return {}
    out: dict[str, str] = {}
    patterns = dict(METRIC_PATTERNS.get("_common", {}))
    if report_type and report_type in METRIC_PATTERNS:
        patterns.update(METRIC_PATTERNS[report_type])
    for key, pat in patterns.items():
        m = re.search(pat, body, re.IGNORECASE)
        if m:
            out[key] = m.group(1).strip()
    return out


# ----------------------------------------------------------------------
# File attachments
# ----------------------------------------------------------------------
def save_attachment(report_type: str | None, lho: str | None, received: datetime | None,
                    name: str, data: bytes) -> dict:
    safe_report = (report_type or "Unclassified").replace(" ", "_").replace("/", "-")
    safe_lho = (lho or "Unknown").replace(" ", "_")
    date_str = (received or datetime.utcnow()).strftime("%d-%b-%Y")
    folder = INCOMING_ATTACH_DIR / safe_report / safe_lho / date_str
    folder.mkdir(parents=True, exist_ok=True)

    ext = Path(name).suffix
    stored_name = f"{safe_report}_{safe_lho}_{date_str}{ext}"
    # Avoid clobbering multiple attachments in one mail.
    target = folder / stored_name
    counter = 1
    while target.exists():
        target = folder / f"{safe_report}_{safe_lho}_{date_str}_{counter}{ext}"
        counter += 1

    target.write_bytes(data or b"")
    return {
        "original_name": name,
        "stored_name": target.name,
        "stored_path": str(target),
        "size_bytes": len(data or b""),
    }


# ----------------------------------------------------------------------
# Draft acknowledgment (DRAFT ONLY — never sends)
# ----------------------------------------------------------------------
def create_ack_draft(service, to_email: str, subject: str, body_text: str,
                     thread_id: str | None = None) -> str:
    from email.mime.text import MIMEText

    mime = MIMEText(body_text)
    mime["To"] = to_email
    mime["Subject"] = subject
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("utf-8")
    message: dict = {"raw": raw}
    if thread_id:
        message["threadId"] = thread_id

    draft = (
        service.users().drafts()
        .create(userId=GMAIL_USER_ID, body={"message": message})
        .execute()
    )
    return draft.get("id", "")


# ----------------------------------------------------------------------
# Outbound send (direct send + attachment-capable drafts) — used by
# services/email_service.py for automated report distribution via the
# connected Gmail account.
# ----------------------------------------------------------------------
def get_default_signature(service) -> str:
    """
    The connected account's own configured signature (Gmail Settings >
    General > Signature), fetched via the Send-As settings API. Returns ""
    if unavailable (missing scope, no signature configured, API error) —
    callers should treat that as "no signature to append", not an error.
    """
    try:
        resp = service.users().settings().sendAs().list(userId=GMAIL_USER_ID).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch Gmail signature: %s", exc)
        return ""

    send_as_list = resp.get("sendAs", [])
    match = next((s for s in send_as_list if s.get("isDefault")), None) or (
        send_as_list[0] if send_as_list else None
    )
    return (match or {}).get("signature") or ""


def build_mime_message(
    to_email: str, subject: str, body_html: str, attachment_path: str | None = None,
    cc_emails: str | None = None, in_reply_to: str | None = None, references: str | None = None,
):
    """`in_reply_to`/`references` take the ORIGINAL message's RFC
    `Message-ID` header value (not Gmail's own opaque message id) —
    setting these is what makes the recipient's own mail client (Outlook,
    not just Gmail) thread this as a reply on the original conversation
    instead of a new, unrelated email."""
    from email.mime.application import MIMEApplication
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    mime = MIMEMultipart()
    mime["To"] = to_email
    if cc_emails:
        mime["Cc"] = cc_emails
    mime["Subject"] = subject
    if in_reply_to:
        mime["In-Reply-To"] = in_reply_to
    if references:
        mime["References"] = references
    mime.attach(MIMEText(body_html, "html"))

    if attachment_path:
        path = Path(attachment_path)
        with open(path, "rb") as f:
            part = MIMEApplication(f.read(), Name=path.name)
        part["Content-Disposition"] = f'attachment; filename="{path.name}"'
        mime.attach(part)

    return mime


def send_message(
    service, to_email: str, subject: str, body_html: str, attachment_path: str | None = None,
    cc_emails: str | None = None,
) -> str:
    """Send an email directly via the Gmail API. Returns the sent message id."""
    mime = build_mime_message(to_email, subject, body_html, attachment_path, cc_emails)
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("utf-8")
    sent = service.users().messages().send(userId=GMAIL_USER_ID, body={"raw": raw}).execute()
    return sent.get("id", "")


def create_outbound_draft(
    service, to_email: str, subject: str, body_html: str, attachment_path: str | None = None,
    cc_emails: str | None = None, in_reply_to: str | None = None, references: str | None = None,
    thread_id: str | None = None,
) -> str:
    """Create a Gmail draft (with optional attachment) instead of sending
    directly. Returns the draft id. Pass `thread_id` (Gmail's own thread
    id) plus `in_reply_to`/`references` (the original message's RFC
    Message-ID) together to make this a real threaded reply rather than a
    new standalone email — thread_id groups it correctly in the sender's
    own Gmail view, the headers make the recipient's client thread it too."""
    mime = build_mime_message(to_email, subject, body_html, attachment_path, cc_emails, in_reply_to, references)
    raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("utf-8")
    message_body: dict = {"raw": raw}
    if thread_id:
        message_body["threadId"] = thread_id
    draft = service.users().drafts().create(userId=GMAIL_USER_ID, body={"message": message_body}).execute()
    return draft.get("id", "")
