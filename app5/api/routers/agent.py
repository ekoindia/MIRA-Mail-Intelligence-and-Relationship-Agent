"""
MIRA's agent-facing surface.

MIRA owns the mailbox; report-generating agents don't. An agent reads
incoming mail through here, pulls the reference data MIRA already
integrates (the Calling Sheet's CSP -> RM map), and hands back a finished
file for MIRA to mail. It never receives a Gmail id, and it cannot send.

Auth is a single shared token in the `X-Agent-Token` header, compared
against `AGENT_SERVICE_TOKEN`. An unset token disables the whole surface
rather than opening it.
"""
from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from config import settings
from database.db import get_db
from database.incoming_models import IncomingAttachment, IncomingEmail
from services import agent_mail_service as mail_svc
from utils.helpers import utc_iso
from utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/agent", tags=["agent"])

APP_ROOT = Path(__file__).resolve().parents[2]


def require_agent(x_agent_token: str | None = Header(default=None)):
    if not settings.agent_service_token:
        raise HTTPException(
            status_code=503,
            detail="Agent API is disabled — set AGENT_SERVICE_TOKEN in MIRA's environment to enable it.",
        )
    if x_agent_token != settings.agent_service_token:
        raise HTTPException(status_code=401, detail="Invalid or missing agent token.")
    return True


@router.get("/ping", dependencies=[Depends(require_agent)])
def ping():
    return {"service": "mira", "role": "mail-layer", "ok": True}


# --------------------------------------------------------------------------
# Incoming — read-only
# --------------------------------------------------------------------------

def _attachment_json(row: IncomingAttachment) -> dict:
    return {
        # Composite id: the download endpoint uses the prefix to decide
        # whether to serve a file MIRA already stored or fetch one live
        # from Gmail. Agents treat it as opaque.
        "attachment_id": f"stored:{row.id}",
        "name": row.original_name,
        "mime_type": row.mime_type,
        "size_bytes": row.size_bytes,
    }


@router.get("/incoming/messages", dependencies=[Depends(require_agent)])
def incoming_messages(
    intent: str | None = None,
    limit: int = Query(50, le=300),
):
    """Ingested inbox messages, optionally filtered by triage intent.

    Only messages that actually carry an attachment are returned — every
    agent consuming this wants the file, and a subject-only match is noise.
    """
    with get_db() as db:
        query = db.query(IncomingEmail)
        if intent:
            query = query.filter(IncomingEmail.triage_intent == intent)
        emails = query.order_by(IncomingEmail.received_at.desc()).limit(limit).all()
        if not emails:
            return []
        ids = [e.id for e in emails]
        attachments: dict[int, list] = {}
        for row in db.query(IncomingAttachment).filter(
            IncomingAttachment.incoming_email_id.in_(ids)
        ):
            attachments.setdefault(row.incoming_email_id, []).append(_attachment_json(row))

        return [
            {
                "message_id": e.gmail_message_id,
                "thread_id": e.gmail_thread_id,
                "sender": e.sender,
                "subject": e.subject,
                "received_at": utc_iso(e.received_at),
                "attachments": attachments.get(e.id, []),
            }
            for e in emails
            if attachments.get(e.id)
        ]


@router.get("/incoming/attachments/{attachment_id}", dependencies=[Depends(require_agent)])
def download_attachment(attachment_id: str):
    """Serve an attachment, whether MIRA stored it at ingestion or it has to
    be fetched live from Gmail (a reply's attachment, which the incoming
    pipeline doesn't necessarily store)."""
    if attachment_id.startswith("stored:"):
        try:
            row_id = int(attachment_id.split(":", 1)[1])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Malformed attachment id.") from exc
        with get_db() as db:
            row = db.get(IncomingAttachment, row_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Attachment not found.")
            path = Path(row.stored_path)
            if not path.is_absolute():
                path = APP_ROOT / row.stored_path
            if not path.exists():
                raise HTTPException(status_code=404, detail="Attachment file is missing on disk.")
            return FileResponse(
                path,
                filename=row.original_name,
                media_type=row.mime_type or "application/octet-stream",
            )

    if attachment_id.startswith("gmail:"):
        try:
            _, message_id, gmail_attachment_id = attachment_id.split(":", 2)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Malformed attachment id.") from exc
        from services.gmail_auth import get_gmail_client

        service = get_gmail_client(interactive=False)
        if service is None:
            raise HTTPException(status_code=503, detail="Gmail is not connected.")
        try:
            blob = service.users().messages().attachments().get(
                userId="me", messageId=message_id, id=gmail_attachment_id
            ).execute()
        except Exception as exc:
            raise HTTPException(status_code=404, detail=f"Could not fetch attachment: {exc}") from exc
        return Response(
            content=base64.urlsafe_b64decode(blob["data"]),
            media_type="application/octet-stream",
        )

    raise HTTPException(status_code=400, detail="Unknown attachment id scheme.")


@router.get("/incoming/replies", dependencies=[Depends(require_agent)])
def incoming_replies(thread_id: str, after: str | None = None):
    """Inbound messages on a thread — i.e. everything that is NOT ours.

    Returned oldest-first so "the first reply after we sent" is simply the
    first element. Bodies are decoded here so the agent never needs a Gmail
    client of its own.
    """
    from services.gmail_auth import get_gmail_client

    service = get_gmail_client(interactive=False)
    if service is None:
        raise HTTPException(status_code=503, detail="Gmail is not connected.")

    try:
        thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
    except Exception as exc:
        raise HTTPException(status_code=404, detail=f"Could not read thread: {exc}") from exc

    cutoff = None
    if after:
        try:
            parsed = datetime.fromisoformat(after.replace("Z", "+00:00"))
            cutoff = parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            cutoff = None

    def walk(part):
        yield part
        for sub in part.get("parts", []) or []:
            yield from walk(sub)

    out = []
    for message in thread.get("messages", []):
        labels = set(message.get("labelIds") or [])
        if "DRAFT" in labels or "SENT" in labels:
            continue
        received = datetime.utcfromtimestamp(int(message.get("internalDate", 0)) / 1000)
        if cutoff and received <= cutoff:
            continue

        headers = {h["name"].lower(): h["value"] for h in message.get("payload", {}).get("headers", [])}
        texts, attachments = [], []
        for part in walk(message.get("payload", {})):
            filename = part.get("filename") or ""
            body = part.get("body") or {}
            if filename:
                if body.get("attachmentId"):
                    attachments.append(
                        {
                            "attachment_id": f"gmail:{message['id']}:{body['attachmentId']}",
                            "name": filename,
                            "mime_type": part.get("mimeType"),
                            "size_bytes": body.get("size"),
                        }
                    )
                continue
            if part.get("mimeType") in ("text/plain", "text/html") and body.get("data"):
                try:
                    texts.append(base64.urlsafe_b64decode(body["data"]).decode("utf-8", errors="replace"))
                except Exception:
                    pass

        out.append(
            {
                "message_id": message.get("id"),
                "sender": headers.get("from"),
                "received_at": utc_iso(received),
                "body_text": "\n".join(texts),
                "snippet": message.get("snippet"),
                "attachments": attachments,
            }
        )

    out.sort(key=lambda m: m["received_at"] or "")
    return out


# --------------------------------------------------------------------------
# Reference data MIRA already integrates
# --------------------------------------------------------------------------

@router.get("/csp/rm-map", dependencies=[Depends(require_agent)])
def csp_rm_map():
    """CSP code -> RM email, from the live Calling Sheet.

    Exposed so agents don't each carry their own Sheets credentials. A
    failure returns an empty map rather than a 500: the caller treats "no
    RM known" as a visible gap, which is better than a dead pipeline.
    """
    try:
        from services import calling_sheet_service as css

        df = css.load_calling_sheet()
    except Exception as exc:
        logger.warning("Calling Sheet unavailable for rm-map: %s", exc)
        return {}
    if "csp_code" not in df.columns or "rm_email" not in df.columns:
        logger.warning("Calling Sheet has no csp_code/rm_email columns.")
        return {}
    out: dict[str, str] = {}
    for code, email in zip(df["csp_code"], df["rm_email"]):
        code_s = str(code or "").strip()
        email_s = str(email or "").strip().lower()
        if code_s and "@" in email_s:
            out[code_s] = email_s
    return out


# --------------------------------------------------------------------------
# Mail — MIRA writes and drafts it. Never sends.
# --------------------------------------------------------------------------

@router.post("/mail/payout", dependencies=[Depends(require_agent)])
async def mail_payout(workbook: UploadFile = File(...), metadata: str = Form(...)):
    """Take a finished payout workbook plus its facts, and draft the mail.

    The agent supplies no wording and no recipient — MIRA resolves the
    recipient from the source message and writes the mail itself.
    """
    try:
        meta = json.loads(metadata)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="metadata must be valid JSON.") from exc

    mail_svc.AGENT_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    dest = mail_svc.AGENT_UPLOAD_DIR / (workbook.filename or "payout.xlsx")
    dest.write_bytes(await workbook.read())

    try:
        return mail_svc.create_payout_mail(meta, dest)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/mail/rm-request", dependencies=[Depends(require_agent)])
def mail_rm_request(payload: dict):
    try:
        return mail_svc.create_rm_request_mail(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/mail/status", dependencies=[Depends(require_agent)])
def mail_status(mail_ref: str):
    try:
        return mail_svc.mail_status(mail_ref)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class RemindPayload(BaseModel):
    mail_ref: str


@router.post("/mail/remind", dependencies=[Depends(require_agent)])
def mail_remind(payload: RemindPayload):
    try:
        return mail_svc.create_reminder(payload.mail_ref)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


class CancelPayload(BaseModel):
    mail_ref: str


@router.post("/mail/cancel", dependencies=[Depends(require_agent)])
def mail_cancel(payload: CancelPayload):
    """Delete a not-yet-sent draft, so a stale ask can be replaced.

    404 for an unknown mail_ref, 409 if the mail was already sent — that one
    is deliberate: a sent mail cannot be un-sent by deleting a draft that no
    longer exists, so refusing is safer than silently reporting success.
    """
    try:
        return mail_svc.cancel_mail(payload.mail_ref)
    except ValueError as exc:
        status = 409 if "already been sent" in str(exc) else 404
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
