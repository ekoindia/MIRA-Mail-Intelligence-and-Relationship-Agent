"""
MIRA as the mail layer for the agent fleet. DRAFT ONLY.

An agent hands over facts and (sometimes) a file. Every word that reaches a
recipient is written here, in MIRA — the recipient, the subject, the body,
and the decision to draft rather than send. That split is deliberate: agents
generate reports, MIRA does the mailing, so mail behaviour stays in one
product instead of being reimplemented per agent.

HARD GUARANTEE: this module only ever calls gmail_service.create_outbound_draft,
never send_message — the same guarantee limit_forward_service.py and
incoming_ack_service.py carry. A human still has to press send.

NEW MAIL vs REPLY, per kind:
  bc_payout             -> a NEW standalone mail to whoever sent the
                           commission report. Explicitly NOT a reply on the
                           SBI thread, per instruction.
  bc_payout_rm_request  -> a NEW mail to the RM.
  reminders (both)      -> a real threaded reply on that mail's own thread,
                           with In-Reply-To/References set from the RFC
                           Message-ID so the recipient's client threads it.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from database.agent_mail_models import AgentMail, AgentMailKind
from database.db import get_db
from database.incoming_models import IncomingEmail
from services import gmail_service as gs
from services.incoming_service import _sender_email
from utils.logger import get_logger

logger = get_logger(__name__)

# Matches recipient_resolution_service.UNIVERSAL_CC — every automated mail in
# this app CCs the same address.
UNIVERSAL_CC = "sbikiosk@eko.co.in"

AGENT_UPLOAD_DIR = Path(__file__).resolve().parents[1] / "data" / "agent_uploads"


def _client():
    from services.gmail_auth import get_gmail_client

    service = get_gmail_client(interactive=False)
    if service is None:
        raise RuntimeError("Gmail is not connected — connect it in Settings first.")
    return service


def _signature(service) -> str:
    try:
        return gs.get_default_signature(service)
    except Exception:
        return ""


def _money(value) -> str:
    try:
        return f"{float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


# --------------------------------------------------------------------------
# Bodies — MIRA's wording, matching the app's existing mail design
# (table layout, inline styles only; email clients don't support flex/grid).
# --------------------------------------------------------------------------

def _payout_body(metadata: dict, signature: str) -> str:
    totals = metadata.get("totals") or {}
    period = metadata.get("period_label") or metadata.get("period_short") or "the period"
    pending = totals.get("pending_rows") or 0
    pending_note = ""
    if pending:
        pending_note = (
            f'<p style="margin:0 0 12px 0;">{pending} CSP(s) are listed on the '
            f"<b>Pending</b> sheet as their bank details are awaited; they are "
            f"excluded from the Final Upload sheet.</p>"
        )
    rows = [
        ("Total CSPs", totals.get("total_csps", 0)),
        ("To be paid by Eko", totals.get("final_upload_rows", 0)),
        ("Pending (details awaited)", pending),
        ("Total payable", _money(totals.get("total_payable"))),
    ]
    cells = "".join(
        f'<tr><td style="border:1px solid #d1d5db;padding:6px;background:#f3f4f6;"><b>{label}</b></td>'
        f'<td style="border:1px solid #d1d5db;padding:6px;">{value}</td></tr>'
        for label, value in rows
    )
    return f"""\
<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1f2937;">
  <p style="margin:0 0 12px 0;">Dear Sir/Madam,</p>
  <p style="margin:0 0 12px 0;">
    Please find attached the CSP Payout working for the month of <b>{period}</b>,
    prepared from the BC Commission Report shared by your office.
  </p>
  <table cellpadding="0" cellspacing="0" border="0"
         style="border-collapse:collapse;margin:0 0 14px 0;font-size:13px;">{cells}</table>
  {pending_note}
  <p style="margin:0 0 12px 0;">Kindly review and confirm.</p>
  <p style="margin:0 0 4px 0;">Regards,</p>
  {signature}
</div>"""


def _rm_request_body(payload: dict, signature: str, participant_count: int) -> str:
    """Two different asks can share one mail:

      supply  — we hold no bank details for this CSP at all
      confirm — we DO hold an account against the code, but something about
                the CSP changed since last month's payout (usually the name),
                so paying it risks sending money to whoever held the code
                before

    They render as separate sections because they need different answers.
    Mixing them into one table has the RM confirming details they were
    actually being asked to supply.

    When more than one RM is on the mail (their CSPs pooled into one thread
    so whoever is free can answer first), each row carries an "RM" column so
    a participant can tell their own rows apart from a colleague's — but the
    ask and the reply both live on the ONE shared thread.
    """
    period = payload.get("period_label") or "the current period"
    csps = payload.get("csps") or []
    supply = [c for c in csps if c.get("ask") != "confirm"]
    confirm = [c for c in csps if c.get("ask") == "confirm"]
    multi = participant_count > 1

    rm_header = '<th style="border:1px solid #d1d5db;padding:6px;text-align:left;">RM</th>' if multi else ""
    rm_cell = (
        lambda c: f'<td style="border:1px solid #d1d5db;padding:6px;">{c.get("owner_rm") or ""}</td>'
        if multi
        else ""
    )

    cells = "".join(
        f"""
      <tr>
        <td style="border:1px solid #d1d5db;padding:6px;">{c.get('csp_code','')}</td>
        <td style="border:1px solid #d1d5db;padding:6px;">
          {c.get('csp_name') or ''}
          {f'<div style="font-size:11px;color:#8a6410;">{c["changed"]}</div>' if c.get('changed') else ''}
        </td>
        <td style="border:1px solid #d1d5db;padding:6px;">{c.get('circle_name') or ''}</td>
        {rm_cell(c)}
        <td style="border:1px solid #d1d5db;padding:6px;text-align:right;">{_money(c.get('amount_on_hold'))}</td>
        <td style="border:1px solid #d1d5db;padding:6px;"></td>
        <td style="border:1px solid #d1d5db;padding:6px;"></td>
        <td style="border:1px solid #d1d5db;padding:6px;"></td>
        <td style="border:1px solid #d1d5db;padding:6px;"></td>
      </tr>"""
        for c in supply
    )

    confirm_rows = "".join(
        f"""
      <tr>
        <td style="border:1px solid #d1d5db;padding:6px;">{c.get('csp_code','')}</td>
        <td style="border:1px solid #d1d5db;padding:6px;">{c.get('csp_name') or ''}</td>
        {rm_cell(c)}
        <td style="border:1px solid #d1d5db;padding:6px;">{c.get('changed') or ''}</td>
        <td style="border:1px solid #d1d5db;padding:6px;">{c.get('known_account') or ''}</td>
        <td style="border:1px solid #d1d5db;padding:6px;text-align:right;">{_money(c.get('amount_on_hold'))}</td>
      </tr>"""
        for c in confirm
    )

    supply_block = ""
    if supply:
        supply_block = (
            f'<p style="margin:0 0 12px 0;">For the following {len(supply)} CSP(s) we do '
            f"not hold PAN or bank account details at all. Kindly fill the blank columns "
            f"below and reply to this mail.</p>"
            f'<table cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;font-size:13px;margin:0 0 14px 0;">'
            f'<tr style="background:#f3f4f6;">'
            f'<th style="border:1px solid #d1d5db;padding:6px;text-align:left;">CSP Code</th>'
            f'<th style="border:1px solid #d1d5db;padding:6px;text-align:left;">CSP Name</th>'
            f'<th style="border:1px solid #d1d5db;padding:6px;text-align:left;">Circle</th>'
            f"{rm_header}"
            f'<th style="border:1px solid #d1d5db;padding:6px;text-align:right;">Amount on hold</th>'
            f'<th style="border:1px solid #d1d5db;padding:6px;text-align:left;">PAN</th>'
            f'<th style="border:1px solid #d1d5db;padding:6px;text-align:left;">Beneficiary Name</th>'
            f'<th style="border:1px solid #d1d5db;padding:6px;text-align:left;">Account No</th>'
            f'<th style="border:1px solid #d1d5db;padding:6px;text-align:left;">IFSC</th>'
            f"</tr>{cells}</table>"
        )

    confirm_block = ""
    if confirm:
        confirm_block = (
            f'<p style="margin:18px 0 12px 0;">For the following {len(confirm)} CSP(s) we '
            f"already hold an account number, but their details have changed since last "
            f"month's payout. Please <b>confirm whether the account below still belongs to "
            f"the CSP named in this month's report</b> before we release the payment "
            f"&mdash; if it has changed, kindly share the new details.</p>"
            f'<table cellpadding="0" cellspacing="0" border="0" '
            f'style="border-collapse:collapse;font-size:13px;margin:0 0 14px 0;">'
            f'<tr style="background:#fdf3d8;">'
            f'<th style="border:1px solid #d1d5db;padding:6px;text-align:left;">CSP Code</th>'
            f'<th style="border:1px solid #d1d5db;padding:6px;text-align:left;">Name in this report</th>'
            f"{rm_header}"
            f'<th style="border:1px solid #d1d5db;padding:6px;text-align:left;">What changed</th>'
            f'<th style="border:1px solid #d1d5db;padding:6px;text-align:left;">Account we hold</th>'
            f'<th style="border:1px solid #d1d5db;padding:6px;text-align:right;">Amount on hold</th>'
            f"</tr>{confirm_rows}</table>"
        )

    if multi:
        greeting = "Team"
        pooled_note = (
            '<p style="margin:0 0 12px 0;">This is one mail covering all of you together — '
            "whoever is free first, please reply on this thread with your rows filled in; "
            "no need to wait for the others.</p>"
        )
    else:
        greeting = payload.get("rm_name") or "Sir/Madam"
        pooled_note = ""

    return f"""\
<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1f2937;">
  <p style="margin:0 0 12px 0;">Dear {greeting},</p>
  <p style="margin:0 0 12px 0;">
    While preparing the CSP payout for <b>{period}</b>, {len(csps)} CSP(s)
    could not be released. Their commission is on hold until we hear back.
  </p>
  {pooled_note}
  {supply_block}{confirm_block}
  <p style="margin:0 0 12px 0;">
    Please keep the CSP Code against each row unchanged so the details can be
    mapped correctly.
  </p>
  <p style="margin:0 0 4px 0;">Regards,</p>
  {signature}
</div>"""


def _reminder_body(mail: AgentMail) -> str:
    if mail.kind == AgentMailKind.BC_PAYOUT_RM_REQUEST:
        ask = (
            "the PAN and bank details requested below are still awaited, and the "
            "commission payout for those CSPs is on hold until we receive them"
        )
    else:
        ask = "kindly review the CSP Payout working shared below and confirm at your convenience"
    return f"""\
<div style="font-family:Arial,Helvetica,sans-serif;font-size:14px;color:#1f2937;">
  <p style="margin:0 0 12px 0;">Dear Sir/Madam,</p>
  <p style="margin:0 0 12px 0;">Gentle reminder on the mail below &mdash; {ask}.</p>
  <p style="margin:0 0 4px 0;">Regards,</p>
</div>"""


# --------------------------------------------------------------------------
# Raising mail
# --------------------------------------------------------------------------

def _new_ref() -> str:
    return uuid.uuid4().hex


def create_payout_mail(metadata: dict, workbook_path: str | Path, agent_name: str = "bc-payout-agent") -> dict:
    """Draft the CSP Payout mail.

    Recipient is resolved HERE from the source message id the agent quoted —
    the agent never names a recipient. A NEW standalone mail: no threadId, no
    In-Reply-To, per the instruction that this must not land as a reply on
    the SBI thread.
    """
    source_message_id = metadata.get("source_message_id")
    to_email = ""
    with get_db() as db:
        row = (
            db.query(IncomingEmail)
            .filter(IncomingEmail.gmail_message_id == source_message_id)
            .first()
        )
        if row is not None:
            to_email = _sender_email(row.sender)
    if not to_email:
        raise ValueError(
            "Could not resolve the commission report's sender — unknown source_message_id."
        )

    period_short = metadata.get("period_short") or metadata.get("period_label") or ""
    subject = f"CSP Payout Report for the month of {period_short}".strip()

    service = _client()
    body = _payout_body(metadata, _signature(service))

    # DRAFT ONLY — never gs.send_message.
    draft_id = gs.create_outbound_draft(
        service,
        to_email=to_email,
        subject=subject,
        body_html=body,
        attachment_path=str(workbook_path),
        cc_emails=UNIVERSAL_CC,
    )
    thread_id, message_id = _resolve_draft(service, draft_id)

    mail_ref = _new_ref()
    with get_db() as db:
        db.add(
            AgentMail(
                mail_ref=mail_ref,
                kind=AgentMailKind.BC_PAYOUT,
                agent_name=agent_name,
                agent_ref=str(metadata.get("run_id")),
                to_email=to_email,
                cc_emails=UNIVERSAL_CC,
                subject=subject,
                attachment_name=Path(workbook_path).name,
                attachment_path=str(workbook_path),
                draft_id=draft_id,
                thread_id=thread_id,
                message_id=message_id,
            )
        )
        db.commit()

    logger.info("Drafted agent payout mail %s to %s (draft %s).", mail_ref, to_email, draft_id)
    return {"mail_ref": mail_ref, "to": to_email, "subject": subject, "thread_id": thread_id}


def create_rm_request_mail(payload: dict, agent_name: str = "bc-payout-agent") -> dict:
    """Draft one RM-details request.

    Accepts EITHER a single `rm_email` or a list `rm_emails` — the latter
    puts every recipient in the same To line of the SAME mail/thread, on
    instruction: rather than one separate mail per RM, pool everyone whose
    CSPs are outstanding into one thread so whichever of them is free first
    can reply, instead of waiting on a specific person.
    """
    emails_raw = payload.get("rm_emails") or (
        [payload["rm_email"]] if payload.get("rm_email") else []
    )
    emails = sorted({e.strip().lower() for e in emails_raw if e and "@" in e})
    if not emails:
        raise ValueError("rm_emails (or rm_email) is required and must contain at least one address.")
    to_email = ", ".join(emails)

    csps = payload.get("csps") or []
    # The subject has to match what is actually being asked. A confirmation
    # request titled "PAN & bank details required" reads as a different job
    # and invites the wrong reply.
    wants_confirm = any(c.get("ask") == "confirm" for c in csps)
    wants_supply = any(c.get("ask") != "confirm" for c in csps)
    if wants_confirm and wants_supply:
        what = "Bank details & confirmation required"
    elif wants_confirm:
        what = "Please confirm CSP bank details"
    else:
        what = "PAN & bank details required"
    subject = (
        f"{what} for {len(csps)} CSP(s) — payout "
        f"{payload.get('period_label') or ''}".strip()
    )

    service = _client()
    body = _rm_request_body(payload, _signature(service), participant_count=len(emails))

    # DRAFT ONLY — never gs.send_message.
    draft_id = gs.create_outbound_draft(
        service,
        to_email=to_email,
        subject=subject,
        body_html=body,
        cc_emails=UNIVERSAL_CC,
    )
    thread_id, message_id = _resolve_draft(service, draft_id)

    mail_ref = _new_ref()
    with get_db() as db:
        db.add(
            AgentMail(
                mail_ref=mail_ref,
                kind=AgentMailKind.BC_PAYOUT_RM_REQUEST,
                agent_name=agent_name,
                agent_ref=str(payload.get("request_id")),
                to_email=to_email,
                cc_emails=UNIVERSAL_CC,
                subject=subject,
                draft_id=draft_id,
                thread_id=thread_id,
                message_id=message_id,
            )
        )
        db.commit()

    logger.info(
        "Drafted agent RM request %s to %s (%d recipient(s), draft %s).",
        mail_ref, to_email, len(emails), draft_id,
    )
    return {"mail_ref": mail_ref, "to": to_email, "subject": subject, "thread_id": thread_id}


def cancel_mail(mail_ref: str) -> dict:
    """Delete a not-yet-sent draft, so a stale ask can be replaced by a
    fresh, consolidated one.

    Refuses to touch anything that has actually been SENT — cancelling a
    real, delivered mail is not this function's job, and silently deleting
    a draft that already went out (which cannot happen once sent, since a
    sent message is no longer a draft) would be the wrong kind of "cancel."
    Deleting an already-deleted draft is treated as success: the end state
    the caller wants (no live draft) already holds.
    """
    with get_db() as db:
        mail = db.query(AgentMail).filter(AgentMail.mail_ref == mail_ref).one_or_none()
        if mail is None:
            raise ValueError(f"Unknown mail_ref {mail_ref!r}.")
        if mail.sent_at is not None:
            raise ValueError("This mail has already been sent — it cannot be cancelled.")
        draft_id = mail.draft_id

    if draft_id:
        service = _client()
        try:
            service.users().drafts().delete(userId="me", id=draft_id).execute()
            logger.info("Cancelled agent mail %s (deleted draft %s).", mail_ref, draft_id)
        except Exception as exc:
            if "not found" in str(exc).lower() or "404" in str(exc):
                logger.info("Agent mail %s: draft %s was already gone.", mail_ref, draft_id)
            else:
                raise

    return {"mail_ref": mail_ref, "cancelled": True}


def _resolve_draft(service, draft_id: str) -> tuple[str | None, str | None]:
    try:
        draft = service.users().drafts().get(userId="me", id=draft_id).execute()
    except Exception:
        return None, None
    message = draft.get("message") or {}
    return message.get("threadId"), message.get("id")


# --------------------------------------------------------------------------
# Status + reminders
# --------------------------------------------------------------------------

def mail_status(mail_ref: str) -> dict:
    """Refresh one agent mail's state from its Gmail thread, then report it.

    `sent_at` comes from the thread, never from when the draft was made:
    under a draft-only policy the only honest "sent" signal is a human
    having actually sent it.
    """
    with get_db() as db:
        mail = db.query(AgentMail).filter(AgentMail.mail_ref == mail_ref).one_or_none()
        if mail is None:
            raise ValueError(f"Unknown mail_ref {mail_ref!r}.")
        draft_id, thread_id, message_id = mail.draft_id, mail.thread_id, mail.message_id

    service = _client()
    if not thread_id and draft_id:
        thread_id, message_id = _resolve_draft(service, draft_id)

    # A person deleting an unwanted draft is a normal thing to do, and it
    # leaves the thread gone too. Detect that explicitly and report it as
    # `missing`, rather than letting the mail sit in "handed off, no news"
    # forever — the agent uses this to put the run back to Ready so a
    # corrected version can be raised.
    missing = False
    thread = None
    if thread_id:
        try:
            thread = service.users().threads().get(userId="me", id=thread_id, format="full").execute()
        except Exception as exc:
            if "not found" in str(exc).lower() or "404" in str(exc):
                missing = True
                logger.info("Agent mail %s: draft/thread no longer exists (deleted).", mail_ref)
            else:
                logger.warning("Could not read agent mail thread %s: %s", thread_id, exc)

        if thread is not None:
            our_sent, reply = None, None
            for message in thread.get("messages", []):
                labels = set(message.get("labelIds") or [])
                if "DRAFT" in labels:
                    continue
                if "SENT" in labels and our_sent is None:
                    our_sent = message
                    continue
                if our_sent is not None and "SENT" not in labels:
                    reply = message
                    break

            with get_db() as db:
                mail = db.query(AgentMail).filter(AgentMail.mail_ref == mail_ref).one()
                mail.thread_id = thread_id
                if message_id:
                    mail.message_id = message_id
                if our_sent is not None and mail.sent_at is None:
                    mail.sent_at = datetime.utcfromtimestamp(int(our_sent.get("internalDate", 0)) / 1000)
                if reply is not None and mail.replied_at is None:
                    headers = {
                        h["name"].lower(): h["value"]
                        for h in reply.get("payload", {}).get("headers", [])
                    }
                    mail.replied_at = datetime.utcfromtimestamp(int(reply.get("internalDate", 0)) / 1000)
                    mail.reply_from = headers.get("from")
                    mail.reply_snippet = reply.get("snippet")
                db.commit()

    with get_db() as db:
        mail = db.query(AgentMail).filter(AgentMail.mail_ref == mail_ref).one()
        return {
            "mail_ref": mail.mail_ref,
            "kind": mail.kind.value,
            "to": mail.to_email,
            "subject": mail.subject,
            "thread_id": mail.thread_id,
            "drafted": bool(mail.draft_id),
            "sent_at": mail.sent_at.isoformat() + "Z" if mail.sent_at else None,
            "replied_at": mail.replied_at.isoformat() + "Z" if mail.replied_at else None,
            "reply_from": mail.reply_from,
            "reply_snippet": mail.reply_snippet,
            "reminder_count": mail.reminder_count or 0,
            "last_reminder_at": mail.last_reminder_at.isoformat() + "Z" if mail.last_reminder_at else None,
            # True when the draft (and its thread) no longer exist — somebody
            # deleted it. Never true once the mail has actually been sent.
            "missing": missing and mail.sent_at is None,
        }


def _rfc_message_id(service, gmail_message_id: str | None, thread_id: str) -> str | None:
    """The RFC `Message-ID` header of our own message on the thread. That
    header — not Gmail's opaque id — is what makes a reply thread correctly
    in the recipient's own mail client."""
    try:
        thread = service.users().threads().get(
            userId="me", id=thread_id, format="metadata", metadataHeaders=["Message-ID"]
        ).execute()
    except Exception:
        return None
    messages = thread.get("messages", [])
    target = next((m for m in messages if gmail_message_id and m.get("id") == gmail_message_id), None)
    if target is None:
        target = next((m for m in reversed(messages) if "SENT" in set(m.get("labelIds") or [])), None)
    if target is None:
        return None
    headers = {h["name"].lower(): h["value"] for h in target.get("payload", {}).get("headers", [])}
    return headers.get("message-id")


def create_reminder(mail_ref: str) -> dict:
    """Draft ONE reminder as a real threaded reply.

    Refuses when the original was never actually sent, or when it has
    already been answered — nudging someone about a mail still sitting in
    Drafts, or one they have already replied to, would be wrong.
    """
    status = mail_status(mail_ref)
    if not status.get("sent_at"):
        return {"reminder_created": False, "reason": "original has not been sent yet"}
    if status.get("replied_at"):
        return {"reminder_created": False, "reason": "already replied"}

    with get_db() as db:
        mail = db.query(AgentMail).filter(AgentMail.mail_ref == mail_ref).one()
        thread_id, message_id, to_email = mail.thread_id, mail.message_id, mail.to_email
        subject, kind = mail.subject, mail.kind
        snapshot = mail
        db.expunge(snapshot)

    if not thread_id:
        return {"reminder_created": False, "reason": "no thread to reply on"}

    service = _client()
    rfc_id = _rfc_message_id(service, message_id, thread_id)

    # DRAFT ONLY — a reminder is still a draft a human must send.
    draft_id = gs.create_outbound_draft(
        service,
        to_email=to_email,
        subject=f"Reminder: {subject}",
        body_html=_reminder_body(snapshot),
        cc_emails=UNIVERSAL_CC,
        in_reply_to=rfc_id,
        references=rfc_id,
        thread_id=thread_id,
    )

    with get_db() as db:
        mail = db.query(AgentMail).filter(AgentMail.mail_ref == mail_ref).one()
        mail.reminder_count = (mail.reminder_count or 0) + 1
        mail.last_reminder_at = datetime.utcnow()
        mail.reminder_draft_id = draft_id
        db.commit()
        count = mail.reminder_count

    logger.info("Drafted reminder on thread %s for agent mail %s.", thread_id, mail_ref)
    return {"reminder_created": True, "reminder_count": count, "draft_id": draft_id}
