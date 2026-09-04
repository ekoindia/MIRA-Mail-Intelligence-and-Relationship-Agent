"""
Mail raised on behalf of an external agent (ADDITIVE — new table only).

MIRA is the mail layer for the agent fleet: an agent supplies facts and a
file, MIRA writes the mail, drafts it, and tracks what happens to it. This
table is the handle MIRA gives back — an agent never sees a Gmail draft id
or thread id, it only ever quotes a `mail_ref`.

Same convention as the other additive model modules: lives outside
database/models.py, reuses the same declarative Base so create_all() picks
it up without touching the existing schema.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Column, DateTime, Enum, Integer, String, Text

from database.models import Base


class AgentMailKind(str, enum.Enum):
    BC_PAYOUT = "bc_payout"
    BC_PAYOUT_RM_REQUEST = "bc_payout_rm_request"


class AgentMail(Base):
    __tablename__ = "agent_mails"

    id = Column(Integer, primary_key=True)

    # The opaque handle handed back to the agent.
    mail_ref = Column(String(64), unique=True, nullable=False, index=True)
    kind = Column(Enum(AgentMailKind), nullable=False, index=True)

    # Which agent asked, and its own id for the thing — kept for tracing a
    # mail back to the run that caused it.
    agent_name = Column(String(80), nullable=True)
    agent_ref = Column(String(120), nullable=True, index=True)

    to_email = Column(String(320), nullable=True)
    cc_emails = Column(String(998), nullable=True)
    subject = Column(String(998), nullable=True)
    attachment_name = Column(String(500), nullable=True)
    attachment_path = Column(String(1000), nullable=True)

    # DRAFT ONLY. draft_id is a Gmail draft; sent_at is set only by
    # observing on the thread that a human actually sent it.
    draft_id = Column(String(255), nullable=True)
    thread_id = Column(String(255), nullable=True, index=True)
    message_id = Column(String(255), nullable=True)
    sent_at = Column(DateTime, nullable=True)

    replied_at = Column(DateTime, nullable=True)
    reply_from = Column(String(320), nullable=True)
    reply_snippet = Column(Text, nullable=True)

    reminder_count = Column(Integer, default=0)
    last_reminder_at = Column(DateTime, nullable=True)
    reminder_draft_id = Column(String(255), nullable=True)

    error = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
