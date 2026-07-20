"""
Org hierarchy ORM models (ADDITIVE — new tables only).

Lives outside `database/models.py` so the existing Branch/LHO schema is
never touched — the same pattern used by `incoming_models.py`. Reuses the
same declarative `Base`, so `Base.metadata.create_all()` (called by
`database.db.init_db`) picks these tables up automatically.

Design note
-----------
Real org hierarchies (Corporate Center / AO / RBO / LHO / Branch) don't
follow the same parent-child order at every organization. Rather than
hardcode an assumed order, `OrgUnit` is a single self-referencing table:
each row just names its own level and (optionally) its parent unit by
name, and the actual tree shape emerges from the uploaded data.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from database.models import Base


class OrgLevel(str, enum.Enum):
    CORP = "Corporate Center"
    AO = "AO"
    RBO = "RBO"
    LHO = "LHO"
    BRANCH = "Branch"


class OrgUnit(Base):
    __tablename__ = "org_units"

    id = Column(Integer, primary_key=True)
    level = Column(Enum(OrgLevel), nullable=False, index=True)
    unit_code = Column(String(50), nullable=True)
    unit_name = Column(String(255), nullable=False, index=True)
    email = Column(String(255), nullable=False)
    # Comma-separated CC addresses sent alongside every mail to this unit's
    # `email` (the To: address) — e.g. Corporate Center's fixed CC list.
    cc_emails = Column(String(2000), nullable=True)
    parent_id = Column(Integer, ForeignKey("org_units.id"), nullable=True)
    region = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    parent = relationship("OrgUnit", remote_side=[id], backref="children")

    __table_args__ = (
        UniqueConstraint("level", "unit_name", name="uq_org_unit_level_name"),
    )
