"""
Email delivery service.

Primary channel: the connected Gmail account (Gmail API), if connected.
Secondary channel: Microsoft Graph API (`/users/{sender}/sendMail`), using an
OAuth2 client-credentials app-only token.
Fallback channel: SMTP (used automatically if Gmail isn't connected, Graph
is disabled/fails, or a send call errors out).

Handles: attachment encoding, per-recipient template rendering, batch
sending with progress callback, and retry of failed EmailLog rows.
"""
from __future__ import annotations

import base64
import json
import random
import secrets
import smtplib
import time
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Callable

import requests
from sqlalchemy.orm import Session

from config import settings
from database.db import get_db
from database.models import AppSetting, DistributionJob, EmailLog, EmailStatus, EmailTemplate, JobStatus, ReportUpload
from services.audit_service import log_action
from services.report_aggregation_service import TEMPLATE_VARIABLE_DEFAULTS
from utils.helpers import (
    month_year_str,
    previous_date_str,
    render_email_body,
    render_template,
    today_str,
    utc_iso,
    week_end_str,
    week_number_str,
    week_start_str,
)
from utils.logger import get_logger

logger = get_logger(__name__)

GRAPH_TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"

_graph_token_cache: dict[str, tuple[str, float]] = {}


class EmailSendError(Exception):
    pass


def _inject_tracking_pixel(body_html: str, token: str) -> str:
    """Append an invisible 1x1 image whose fetch, by the recipient's mail
    client, is how we detect an "open" (see GET /api/track/{token}.png in
    api/routers/tracking.py). No-op if PUBLIC_BASE_URL isn't configured —
    embedding a localhost URL would never be reachable by a real recipient.
    """
    if not settings.public_base_url:
        return body_html
    pixel_url = f"{settings.public_base_url}/api/track/{token}.png"
    return f'{body_html}<img src="{pixel_url}" width="1" height="1" alt="" style="display:none" />'


# ----------------------------------------------------------------------
# Microsoft Graph
# ----------------------------------------------------------------------
def _get_graph_token() -> str:
    cached = _graph_token_cache.get("token")
    if cached and cached[1] > time.time() + 30:
        return cached[0]

    url = GRAPH_TOKEN_URL.format(tenant=settings.ms_graph_tenant_id)
    resp = requests.post(
        url,
        data={
            "client_id": settings.ms_graph_client_id,
            "client_secret": settings.ms_graph_client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=15,
    )
    resp.raise_for_status()
    payload = resp.json()
    token = payload["access_token"]
    expires_at = time.time() + payload.get("expires_in", 3600)
    _graph_token_cache["token"] = (token, expires_at)
    return token


def _send_via_graph(to_email: str, subject: str, body_html: str, attachment_path: str | None,
                     cc_emails: str | None = None) -> None:
    token = _get_graph_token()
    url = GRAPH_SEND_URL.format(sender=settings.ms_graph_sender_email)

    message: dict = {
        "subject": subject,
        "body": {"contentType": "HTML", "content": body_html},
        "toRecipients": [{"emailAddress": {"address": to_email}}],
    }
    if cc_emails:
        message["ccRecipients"] = [
            {"emailAddress": {"address": addr.strip()}} for addr in cc_emails.split(",") if addr.strip()
        ]

    if attachment_path:
        path = Path(attachment_path)
        content_bytes = path.read_bytes()
        message["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": path.name,
                "contentBytes": base64.b64encode(content_bytes).decode("utf-8"),
            }
        ]

    resp = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"message": message, "saveToSentItems": "true"},
        timeout=30,
    )
    if resp.status_code >= 300:
        raise EmailSendError(f"Graph API error {resp.status_code}: {resp.text[:300]}")


# ----------------------------------------------------------------------
# SMTP fallback
# ----------------------------------------------------------------------
def _send_via_smtp(to_email: str, subject: str, body_html: str, attachment_path: str | None,
                    cc_emails: str | None = None) -> None:
    cc_list = [a.strip() for a in (cc_emails or "").split(",") if a.strip()]

    msg = MIMEMultipart()
    msg["From"] = f"{settings.smtp_from_name} <{settings.smtp_username}>"
    msg["To"] = to_email
    if cc_list:
        msg["Cc"] = ", ".join(cc_list)
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html"))

    if attachment_path:
        path = Path(attachment_path)
        with open(path, "rb") as f:
            part = MIMEApplication(f.read(), Name=path.name)
        part["Content-Disposition"] = f'attachment; filename="{path.name}"'
        msg.attach(part)

    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as server:
        if settings.smtp_use_tls:
            server.starttls()
        server.login(settings.smtp_username, settings.smtp_password)
        server.sendmail(settings.smtp_username, [to_email, *cc_list], msg.as_string())


# ----------------------------------------------------------------------
# Gmail (primary channel when connected)
# ----------------------------------------------------------------------
def get_gmail_send_context() -> tuple[object | None, str, str]:
    """
    Resolve the Gmail client + send mode + signature ONCE per batch (not
    per email — building the API client, checking the token, and fetching
    the signature all have real overhead). Returns (client_or_None,
    send_mode, signature_html). send_mode is "direct_send" or "draft_only"
    (from the AppSetting saved in Settings -> Gmail Connection).
    signature_html is the connected account's own Gmail signature (Settings
    > Signature), appended to every automated email in place of a
    hardcoded one — "" if unavailable.
    """
    from services import gmail_auth, gmail_service

    try:
        client = gmail_auth.get_gmail_client(interactive=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Gmail client unavailable: %s", exc)
        client = None

    send_mode = "draft_only"
    with get_db() as db:
        row = db.query(AppSetting).filter(AppSetting.key == "gmail_send_mode").first()
        if row and row.value:
            send_mode = row.value

    signature = gmail_service.get_default_signature(client) if client is not None else ""

    return client, send_mode, signature


def _send_via_gmail(gmail_client, to_email: str, subject: str, body_html: str,
                    attachment_path: str | None, send_mode: str, cc_emails: str | None = None) -> str:
    from services import gmail_service

    if send_mode == "direct_send":
        gmail_service.send_message(gmail_client, to_email, subject, body_html, attachment_path, cc_emails)
        return "gmail"
    gmail_service.create_outbound_draft(gmail_client, to_email, subject, body_html, attachment_path, cc_emails)
    return "gmail_draft"


# ----------------------------------------------------------------------
# Unified send with fallback
# ----------------------------------------------------------------------
def send_single_email(
    to_email: str, subject: str, body_html: str, attachment_path: str | None,
    gmail_client: object | None = None, gmail_send_mode: str = "draft_only",
    cc_emails: str | None = None,
) -> str:
    """
    Try Gmail first (if a connected client is passed in), then Graph (if
    enabled), then fall back to SMTP. Returns the channel actually used.
    """
    if gmail_client is not None:
        try:
            return _send_via_gmail(gmail_client, to_email, subject, body_html, attachment_path, gmail_send_mode, cc_emails)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gmail send failed for %s, falling back: %s", to_email, exc)

    if settings.ms_graph_enabled:
        try:
            _send_via_graph(to_email, subject, body_html, attachment_path, cc_emails)
            return "graph"
        except Exception as exc:  # noqa: BLE001
            logger.warning("Graph send failed for %s, falling back to SMTP: %s", to_email, exc)

    _send_via_smtp(to_email, subject, body_html, attachment_path, cc_emails)
    return "smtp"


# ----------------------------------------------------------------------
# Batch processing for a DistributionJob
# ----------------------------------------------------------------------
def run_distribution_job(
    db: Session,
    job_id: int,
    batch_size: int | None = None,
    max_retries: int | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    force_draft: bool = False,
) -> DistributionJob:
    """
    Send all PENDING/RETRYING EmailLog rows for a job in batches.
    `progress_callback(done, total)` is invoked after each email for UI progress bars.
    `force_draft=True` always creates Gmail drafts for this run regardless
    of the report's stored Draft Only / Send Directly setting — used by the
    per-report "Test (Draft)" button, which must never actually send
    real mail no matter what the report is currently configured to do.
    """
    # Default batch size is the small anti-throttling pacing size (see the
    # sleep below), not the old bulk default_batch_size — callers that want
    # a bigger batch (e.g. a bulk one-off resend) can still pass batch_size
    # explicitly.
    batch_size = batch_size or settings.email_batch_size
    max_retries = max_retries or settings.default_max_retries

    job = db.query(DistributionJob).get(job_id)
    if not job:
        raise ValueError("Distribution job not found.")

    upload: ReportUpload = job.upload
    template: EmailTemplate | None = job.template

    subject_tpl = template.subject if template else "{{Report_Name}} - {{Date}}"
    body_tpl = template.body_html if template else (
        "<p>Dear {{Recipient_Name}},</p><p>Please find attached the {{Report_Name}} "
        "report dated {{Date}}.</p><p>Regards,<br/>Reports Team</p>"
    )

    pending_logs = (
        db.query(EmailLog)
        .filter(EmailLog.job_id == job_id, EmailLog.status.in_([EmailStatus.PENDING, EmailStatus.RETRYING]))
        .all()
    )

    job.status = JobStatus.IN_PROGRESS
    job.started_at = datetime.utcnow()
    db.flush()

    gmail_client, _, gmail_signature = get_gmail_send_context()
    # Per-report Draft Only / Send Directly choice (Reports page) is the
    # source of truth. Unset (never configured) defaults to draft_only —
    # NOT the legacy account-wide AppSetting, which can be left on
    # "direct_send" from old testing and would otherwise let an
    # unconfigured report send for real without anyone choosing that.
    if force_draft:
        gmail_send_mode = "draft_only"
    else:
        report_delivery_mode = upload.report_master.delivery_mode
        gmail_send_mode = {"draft": "draft_only", "send": "direct_send"}.get(
            report_delivery_mode, "draft_only"
        )

    total = len(pending_logs)
    done = 0

    # Week/month placeholders are the same for every recipient in a batch —
    # compute once rather than per-email.
    now = datetime.now()
    from services.festival_theme_service import get_festival_context

    shared_context = {
        # Defaults for every generic {{Variable}} the automated templates
        # reference — overridden below with real computed values when this
        # report has one (see report_aggregation_service.AGGREGATORS).
        # Without this, a report sharing a template with an automated one
        # but lacking its own aggregator (e.g. Re-KYC & Inoperative
        # Accounts) would send an email with raw, unrendered "{{Variable}}"
        # text instead of an honest "No data available".
        **TEMPLATE_VARIABLE_DEFAULTS,
        "Report_Name": upload.report_master.report_name,
        "Date": today_str(),
        "Previous_Date": previous_date_str(now),
        "Week_Number": week_number_str(now),
        "Week_Start": week_start_str(now),
        "Week_End": week_end_str(now),
        "Month_Year": month_year_str(now),
        # Purely decorative — see services/festival_theme_service.py. Empty
        # on every non-festival day; never affects report data.
        **get_festival_context(now.date()),
    }

    for i in range(0, total, batch_size):
        batch = pending_logs[i : i + batch_size]
        for log_row in batch:
            if not log_row.tracking_token:
                log_row.tracking_token = secrets.token_urlsafe(16)
            context = {
                "Recipient_Name": log_row.recipient_name,
                "Branch_Name": log_row.recipient_name if log_row.recipient_type == "Branch" else "",
                "RBO_Name": log_row.recipient_name if log_row.recipient_type == "RBO" else "",
                "AO_Name": log_row.recipient_name if log_row.recipient_type == "AO" else "",
                "LHO_Name": log_row.lho_name or (log_row.recipient_name if log_row.recipient_type == "LHO" else ""),
                "Corp_Name": log_row.recipient_name if log_row.recipient_type == "Corporate Center" else "",
                # Used to build the "click a metric card for CSP-wise detail"
                # links (see services/report_detail_service.py) — the token
                # doubles as the public detail page's only credential, same
                # as the tracking pixel above. Empty Base_URL (PUBLIC_BASE_URL
                # unset, e.g. local dev) means the {{#if Has_Public_Url}}
                # guard in the template hides the links rather than emitting
                # dead/localhost links a real recipient could never open.
                "Tracking_Token": log_row.tracking_token,
                "Base_URL": settings.public_base_url,
                "Has_Public_Url": bool(settings.public_base_url),
                **shared_context,
            }
            if log_row.context_override_json:
                # Segmented reports (e.g. per-RBO computed target/achievement
                # figures) override the generic context on a per-recipient basis.
                context.update(json.loads(log_row.context_override_json))
            subject = render_template(subject_tpl, context)
            body = render_email_body(body_tpl, context)
            if gmail_signature:
                # The template's own closing (e.g. "Regards, ...") is gone —
                # every automated email now ends with the connected
                # account's real Gmail signature instead of a hardcoded one.
                body += gmail_signature
            body = _inject_tracking_pixel(body, log_row.tracking_token)
            # "" is an explicit "no attachment" marker (segmented_distribution_service
            # uses it — the filled-in template body is the whole report, no
            # Excel file), distinct from NULL/unset which falls back to the
            # job's shared upload.
            if log_row.attachment_override_path == "":
                attachment_path = None
            else:
                attachment_path = log_row.attachment_override_path or upload.stored_path

            attempt = 0
            last_error = None
            sent_via = None
            success = False
            while attempt < max_retries and not success:
                attempt += 1
                try:
                    sent_via = send_single_email(
                        log_row.recipient_email, subject, body, attachment_path,
                        gmail_client=gmail_client, gmail_send_mode=gmail_send_mode,
                        cc_emails=log_row.cc_emails,
                    )
                    success = True
                except Exception as exc:  # noqa: BLE001
                    last_error = str(exc)
                    logger.error(
                        "Send failed (attempt %s/%s) to %s: %s",
                        attempt, max_retries, log_row.recipient_email, exc,
                    )
                    # Cap the in-loop backoff at 5s regardless of settings.retry_backoff_seconds:
                    # honoring a long backoff per-attempt here would stall the whole batch UI.
                    # settings.retry_backoff_seconds is intended for the delay between separate
                    # "Retry Failed" runs (see retry_failed_emails), not within a single job's loop.
                    time.sleep(min(settings.retry_backoff_seconds, 5))

            log_row.attempt_count += attempt
            if success:
                log_row.status = EmailStatus.SENT
                log_row.sent_at = datetime.utcnow()
                log_row.sent_via = sent_via
                job.sent_count += 1
            else:
                log_row.status = EmailStatus.FAILED
                log_row.last_error = last_error
                job.failed_count += 1

            done += 1
            db.flush()
            if progress_callback:
                progress_callback(done, total)

        # Pace batches with a randomized pause (not a fixed interval) so the
        # send pattern doesn't look like an automated burst to Gmail's abuse
        # detection. Only applies to real sends (direct_send) — drafts never
        # leave the account and have no abuse-detection exposure to pace
        # around, so drafting should run at full speed. Skipped after the
        # very last batch — nothing left to wait for.
        if i + batch_size < total and gmail_send_mode == "direct_send":
            pause = random.randint(settings.email_pace_min_seconds, settings.email_pace_max_seconds)
            logger.info("Pacing: waiting %ss before the next batch of %s emails.", pause, batch_size)
            time.sleep(pause)

    job.completed_at = datetime.utcnow()
    job.status = JobStatus.COMPLETED if job.failed_count == 0 else JobStatus.COMPLETED_WITH_ERRORS
    db.flush()

    log_action(
        db, "SEND_DISTRIBUTION", entity_type="DistributionJob", entity_id=job.id,
        details=f"sent={job.sent_count}, failed={job.failed_count}, total={job.total_recipients}",
    )
    return job


def retry_failed_emails(
    db: Session,
    job_id: int | None = None,
    email_log_ids: list[int] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
) -> dict:
    """Retry specific failed EmailLog rows, or all failed rows for a job."""
    query = db.query(EmailLog).filter(EmailLog.status == EmailStatus.FAILED)
    if job_id:
        query = query.filter(EmailLog.job_id == job_id)
    if email_log_ids:
        query = query.filter(EmailLog.id.in_(email_log_ids))

    rows = query.all()
    for row in rows:
        row.status = EmailStatus.RETRYING
    db.flush()

    affected_job_ids = {row.job_id for row in rows}
    results = {"retried": 0, "succeeded": 0, "failed": 0}
    for jid in affected_job_ids:
        job = db.query(DistributionJob).get(jid)
        job.failed_count = max(0, job.failed_count - len(
            [r for r in rows if r.job_id == jid]
        ))
        run_distribution_job(db, jid, progress_callback=progress_callback)

    results["retried"] = len(rows)
    return results


# ======================================================================
# READ LAYER (for Mail Activity dashboard) — outgoing counterpart to
# services/incoming_service.py's get_incoming_* helpers.
# ======================================================================
def get_outgoing_kpis() -> dict:
    with get_db() as db:
        total_sent = db.query(EmailLog).filter(EmailLog.status == EmailStatus.SENT).count()
        today_sent = db.query(EmailLog).filter(
            EmailLog.status == EmailStatus.SENT,
            EmailLog.sent_at >= datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0),
        ).count()
        failed = db.query(EmailLog).filter(EmailLog.status == EmailStatus.FAILED).count()
        return {"total_outgoing": total_sent, "outgoing_today": today_sent, "outgoing_failed": failed}


def get_outgoing_by_lho(since: datetime | None = None) -> list[dict]:
    """Per-LHO outgoing (sent) counts. Only recipients resolved at LHO level carry lho_name."""
    from sqlalchemy import func
    with get_db() as db:
        q = db.query(EmailLog.lho_name, func.count(EmailLog.id)).filter(
            EmailLog.status == EmailStatus.SENT, EmailLog.lho_name.isnot(None),
        )
        if since:
            q = q.filter(EmailLog.sent_at >= since)
        rows = q.group_by(EmailLog.lho_name).all()
    return [{"LHO": name, "Outgoing Emails": cnt} for name, cnt in rows]


def get_outgoing_by_level(level: str, since: datetime | None = None) -> list[dict]:
    """Per-unit outgoing (sent) counts for any org level (LHO/RBO/AO/Branch/Corporate Center)."""
    from sqlalchemy import func
    with get_db() as db:
        q = db.query(EmailLog.recipient_name, func.count(EmailLog.id)).filter(
            EmailLog.status == EmailStatus.SENT, EmailLog.recipient_type == level,
        )
        if since:
            q = q.filter(EmailLog.sent_at >= since)
        rows = q.group_by(EmailLog.recipient_name).all()
    return [{"name": name, "outgoing": cnt} for name, cnt in rows]


def get_recent_outgoing(limit: int = 15, since: datetime | None = None) -> list[dict]:
    with get_db() as db:
        q = db.query(EmailLog)
        if since:
            q = q.filter(EmailLog.created_at >= since)
        rows = q.order_by(EmailLog.created_at.desc()).limit(limit).all()
        return [
            {
                "Timestamp": utc_iso(r.sent_at or r.created_at),
                "Recipient": r.recipient_name,
                "Email": r.recipient_email,
                "LHO": r.lho_name or "—",
                "Status": r.status.value if r.status else "—",
                "Via": r.sent_via or "—",
            }
            for r in rows
        ]
