"""
Suggestion ORM model (ADDITIVE).

Same pattern as snapshot_models.py / report_source_models.py — reuses the
same declarative Base so create_all() picks this table up without touching
any existing table.

One row per detected condition (e.g. a digest template's default_template_id
FK resolving to nothing, a same-day duplicate draft batch). Detection is
always read-only and scheduled; a row only ever gets mutated by an explicit
user Approve/Dismiss action (see services/suggestion_service.py).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from database.models import Base


class Suggestion(Base):
    __tablename__ = "suggestions"

    id = Column(Integer, primary_key=True)
    category = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    severity = Column(String(10), nullable=False, default="info")  # info / warning / critical
    entity_type = Column(String(50), nullable=True)
    entity_id = Column(Integer, nullable=True)
    # Stable dedupe key (e.g. "template_fk_null:42") so a re-scan doesn't
    # pile up duplicate pending rows for the same still-open condition.
    fingerprint = Column(String(255), nullable=False, unique=True)
    can_auto_fix = Column(Boolean, nullable=False, default=False)
    # Human-readable + structured description of what Approve will do;
    # JSON-encoded text, not a typed column — action shapes vary by category.
    proposed_action_json = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pending")  # pending/applied/dismissed/failed
    detected_at = Column(DateTime, default=datetime.utcnow)
    resolved_at = Column(DateTime, nullable=True)
    resolved_by_id = Column(Integer, nullable=True)
    resolved_by_username = Column(String(100), nullable=True)
    result_detail = Column(Text, nullable=True)
