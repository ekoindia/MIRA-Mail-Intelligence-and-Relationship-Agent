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


class IncomingProcessState(Base):
    """Single-row checkpoint table for the Gmail poller."""
    __tablename__ = "incoming_process_state"

    id = Column(Integer, primary_key=True)
    last_history_id = Column(String(64), nullable=True)
    last_polled_at = Column(DateTime, nullable=True)
    is_enabled = Column(Boolean, default=True, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
