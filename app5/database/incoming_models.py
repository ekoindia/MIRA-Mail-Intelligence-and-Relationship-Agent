"""
Incoming-email ORM models (ADDITIVE — new tables only).

This module intentionally lives OUTSIDE `database/models.py` so the existing
schema file is never modified. It reuses the same declarative `Base`, so
`Base.metadata.create_all()` (called by `database.db.init_db`) will create
these new tables alongside the existing ones without altering any existing
table.

Tables added
------------
IncomingEmail        -> One received message (dedup-keyed by gmail_message_id)
IncomingAttachment   -> A file saved from an incoming message
ExtractedMetric      -> A key/value metric parsed from an incoming message body
IncomingProcessState -> Cursor/checkpoint for the Gmail poller (last history id)

Nothing here references or mutates the existing models; foreign keys point
only within this additive set. Existing outbound tables are untouched.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

# Reuse the SAME Base as the existing schema so create_all picks these up.
from database.models import Base


class IncomingStatus(str, enum.Enum):
    RECEIVED = "Received"          # fetched, not yet classified
    CLASSIFIED = "Classified"      # report type + level resolved
    EXTRACTED = "Extracted"        # metrics parsed
    ROUTED = "Routed"              # ack/routing draft created
    NEEDS_REVIEW = "Needs Review"  # low-confidence classification
    PROCESSED = "Processed"        # fully handled
    ERROR = "Error"


class IncomingEmail(Base):
    __tablename__ = "incoming_emails"

    id = Column(Integer, primary_key=True)

    # Dedup key — the immutable Gmail message id. UNIQUE guarantees a message
    # is never ingested twice even if the poller overlaps.
    gmail_message_id = Column(String(255), unique=True, nullable=False, index=True)
    gmail_thread_id = Column(String(255), nullable=True, index=True)

    sender = Column(String(320), nullable=True)
    subject = Column(String(998), nullable=True)
    snippet = Column(Text, nullable=True)
    body_text = Column(Text, nullable=True)
    received_at = Column(DateTime, nullable=True, index=True)

    # Classification results (nullable until classified)
    report_type = Column(String(100), nullable=True, index=True)   # e.g. "Loan Lead"
    level = Column(String(20), nullable=True)                      # Branch/RBO/AO/LHO/Corp
    lho_name = Column(String(255), nullable=True, index=True)      # resolved circle/LHO
    rbo_name = Column(String(255), nullable=True)
    classify_confidence = Column(Float, nullable=True)             # 0..1

    status = Column(Enum(IncomingStatus), default=IncomingStatus.RECEIVED, nullable=False, index=True)
    error = Column(Text, nullable=True)

    # Set when an acknowledgment / routing DRAFT has been created in Gmail.
    ack_draft_id = Column(String(255), nullable=True)

    # Whether the connected account has sent any message on this thread
    # (Gmail's own SENT label) — checked read-only, never set by drafting.
    # See services/gmail_service.thread_has_reply.
    replied = Column(Boolean, default=False, nullable=False)
    replied_at = Column(DateTime, nullable=True)

    # "to" = connected account is a direct recipient; "cc" = only cc'd;
    # "unknown" = neither header could be matched. See
    # services/gmail_service.classify_recipient_kind.
    recipient_kind = Column(String(10), nullable=True)

    # Best-matching IncomingReplyTemplate for this message, if any keyword
    # set matched — detection only, never used to auto-draft/send anything
    # yet. See services/incoming_service.classify_reply_template.
    matched_reply_template_id = Column(Integer, ForeignKey("incoming_reply_templates.id"), nullable=True)
    match_confidence = Column(Float, nullable=True)

    # Triage classification — "what kind of work (if any) does this mail
    # represent", independent of the reply-template match above.
    #   noise = needs no reply at all (marketing, calendar, bounces)
    #   info  = informational only (data pushes, report status)
    #   task  = a real request a human must action (limit approval, CSP code)
    #   other = no rule matched (the genuine long tail)
    # triage_intent is the specific bucket within the tier. Both are set by
    # services/incoming_service.classify_triage — plain keyword rules, no
    # model, so it's always inspectable. Detection only.
    triage_tier = Column(String(10), nullable=True, index=True)
    triage_intent = Column(String(60), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    processed_at = Column(DateTime, nullable=True)

    attachments = relationship(
        "IncomingAttachment", back_populates="email", cascade="all, delete-orphan"
    )
    metrics = relationship(
        "ExtractedMetric", back_populates="email", cascade="all, delete-orphan"
    )


class IncomingAttachment(Base):
    __tablename__ = "incoming_attachments"

    id = Column(Integer, primary_key=True)
    incoming_email_id = Column(Integer, ForeignKey("incoming_emails.id"), nullable=False)

    original_name = Column(String(500), nullable=False)
    stored_name = Column(String(500), nullable=False)
    stored_path = Column(String(1000), nullable=False)
    mime_type = Column(String(120), nullable=True)
    size_bytes = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)

    email = relationship("IncomingEmail", back_populates="attachments")


class ExtractedMetric(Base):
    __tablename__ = "extracted_metrics"

    id = Column(Integer, primary_key=True)
    incoming_email_id = Column(Integer, ForeignKey("incoming_emails.id"), nullable=False)

    metric_key = Column(String(100), nullable=False)    # e.g. "leads_generated"
    metric_value = Column(String(255), nullable=True)   # kept as text; parse on read
    created_at = Column(DateTime, default=datetime.utcnow)

    email = relationship("IncomingEmail", back_populates="metrics")

    __table_args__ = (
        UniqueConstraint("incoming_email_id", "metric_key", name="uq_metric_per_email"),
    )


class SentEmail(Base):
    """
    One message from the connected account's Gmail SENT folder — a
    read-only scan (`in:sent`), completely separate from this app's own
    automated report distribution (DistributionJob/EmailLog, shown under
    the Outgoing dashboard tab). This table captures EVERYTHING sent from
    the mailbox, whether via this app or manually, so it can answer "how
    much outgoing mail total, and what kind" for the connected account.

    category is a plain keyword-bucket classification (see
    services/sent_mail_service.classify_outgoing_category) — not an ML
    model. Detection/counting only; nothing here drafts or sends anything.
    """
    __tablename__ = "sent_emails"

    id = Column(Integer, primary_key=True)
    gmail_message_id = Column(String(255), unique=True, nullable=False, index=True)
    gmail_thread_id = Column(String(255), nullable=True)

    to_header = Column(String(998), nullable=True)
    subject = Column(String(998), nullable=True)
    snippet = Column(Text, nullable=True)
    body_text = Column(Text, nullable=True)
    sent_at = Column(DateTime, nullable=True, index=True)

    category = Column(String(30), nullable=True, index=True)
    category_confidence = Column(Float, nullable=True)

    # Whether the recipient replied back on this thread — mirror of
    # IncomingEmail.replied/replied_at but in the opposite direction (was
    # this OUTGOING message answered, not did WE answer). See
    # services/gmail_service.thread_has_incoming_reply.
    replied = Column(Boolean, nullable=True)
    replied_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)


class ExtractedTask(Base):
    """
    One unit of actual work parsed out of a task-tier incoming email —
    e.g. a single limit-approval ticket number, or a CSP/KO code needing
    action. The point is inversion: instead of scrolling 149 near-identical
    "please approve ticket N" emails, you work a list of N.

    One email can produce SEVERAL tasks (a subject like "...request 325588
    & 325589..." is genuinely two approvals), which is why the unique key
    is (incoming_email_id, identifier) rather than the email alone. Emails
    with no parseable identifier still get exactly one task row with
    identifier=NULL — the work is real even when it isn't numbered.

    Status is only ever changed by an explicit human action in the UI.
    Nothing here drafts, sends, or touches the mailbox: closing a task
    marks it done in this app and does not reply to or archive the email.
    """
    __tablename__ = "extracted_tasks"

    id = Column(Integer, primary_key=True)
    incoming_email_id = Column(Integer, ForeignKey("incoming_emails.id"), nullable=False, index=True)

    task_type = Column(String(60), nullable=False, index=True)   # mirrors IncomingEmail.triage_intent
    identifier = Column(String(60), nullable=True, index=True)   # ticket no / CSP code, if parseable
    identifier_kind = Column(String(20), nullable=True)          # "ticket" | "csp_code"

    # Denormalised so the queue renders without joining back to the email.
    subject = Column(String(500), nullable=True)
    sender = Column(String(320), nullable=True)
    received_at = Column(DateTime, nullable=True, index=True)

    status = Column(String(20), nullable=False, default="open", index=True)  # open/done/dismissed
    resolved_at = Column(DateTime, nullable=True)
    resolved_by_username = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("incoming_email_id", "identifier", name="uq_task_per_email_identifier"),
    )


class IncomingProcessState(Base):
    """Single-row checkpoint table for the Gmail poller."""
    __tablename__ = "incoming_process_state"

    id = Column(Integer, primary_key=True)
    last_history_id = Column(String(64), nullable=True)
    last_polled_at = Column(DateTime, nullable=True)
    is_enabled = Column(Boolean, default=True, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class IncomingReplyTemplate(Base):
    """
    A reusable reply for one recurring INCOMING request category (e.g.
    "terminal reset request", "CSP code allotment") — the incoming-mail
    counterpart to EmailTemplate (which is outbound-report-only, on the
    existing Templates page).

    match_keywords drives classify_reply_template (services/incoming_
    service.py): a plain, human-editable "if the subject/body contains any
    of these, this is a candidate match" list — not an ML model, so it's
    easy to see exactly why something matched. Detection/scoring only —
    this table is never read by anything that drafts or sends mail.
    """
    __tablename__ = "incoming_reply_templates"

    id = Column(Integer, primary_key=True)
    category_name = Column(String(150), unique=True, nullable=False)
    # Comma-separated, case-insensitive substrings checked against subject + body.
    match_keywords = Column(Text, nullable=False)
    subject_template = Column(String(500), nullable=False)
    body_template = Column(Text, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
