"""
Weekly report snapshot ORM model (ADDITIVE).

Same pattern as report_source_models.py — reuses the same declarative
Base so create_all() picks this table up without touching any existing
table.

One row per recipient per drafted weekly report run — a direct copy of
the exact computed report data that went into that recipient's drafted
email (EmailLog.context_override_json), not a fresh re-fetch of the
Calling Sheet. The growth report's exact template/metrics aren't defined
yet, so the full context is kept as JSON rather than split into typed
columns — whatever the eventual comparison needs can be pulled out of
the blob once the template arrives.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import Column, Date, DateTime, Index, Integer, String, Text

from database.models import Base


class WeeklyReportSnapshot(Base):
    __tablename__ = "weekly_report_snapshots"

    id = Column(Integer, primary_key=True)
    report_date = Column(Date, nullable=False)
    level = Column(String(20), nullable=False)  # RBO / LHO / Corporate Center / Branch
    recipient_name = Column(String(255), nullable=False)
    recipient_email = Column(String(255), nullable=False)
    source_job_id = Column(Integer, nullable=True)  # DistributionJob.id this was copied from
    context_json = Column(Text, nullable=False)  # exact EmailLog.context_override_json drafted
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_weekly_snapshot_date_recipient", "report_date", "recipient_email"),
    )
