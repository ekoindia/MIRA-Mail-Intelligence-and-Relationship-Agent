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


class DailyLoanLeadSnapshot(Base):
    """
    One row per CSP per day — that CSP's Loan Lead MTD count and per-type
    breakdown as of that day, captured once daily regardless of which (if
    any) recipients actually got a drafted/sent email that day. Global,
    not per-recipient: services/loan_lead_approval_service.py filters this
    down to one branch's own CSPs when computing "new leads since the last
    snapshot" for that branch's report.

    Unlike WeeklyReportSnapshot (a frozen copy of what a specific drafted
    email said), this is captured independently of any send/draft outcome
    — so a branch with zero new leads today (and therefore no drafted
    email at all, see drop_zero_new_lead_recipients) still gets today's
    real counts stored as tomorrow's comparison baseline.
    """
    __tablename__ = "daily_loan_lead_snapshots"

    id = Column(Integer, primary_key=True)
    snapshot_date = Column(Date, nullable=False)
    csp_code = Column(String(50), nullable=False)
    csp_name = Column(String(255), nullable=True)
    branch_name = Column(String(255), nullable=True)
    branch_code = Column(String(50), nullable=True)
    mtd_count = Column(Integer, nullable=False, default=0)
    # {"Agri Loan": 3, "Personal Loan": 5, ...} for this CSP as of this day.
    type_counts_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_daily_ll_snapshot_date_csp", "snapshot_date", "csp_code", unique=True),
    )
