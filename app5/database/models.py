"""
SQLAlchemy ORM models for the Weekly Report Distribution Dashboard.

Schema overview
----------------
User               -> Admin/Operator accounts
Branch             -> Branch master (code, name, email, region)
LHO                -> Lead Head Office master (name, email, region)
ReportMaster       -> Configured report types + recipient rules
ReportUpload       -> An uploaded file batch tied to a ReportMaster
Recipient          -> Resolved recipient snapshot for a DistributionJob
DistributionJob    -> One "send" run for an uploaded report
EmailLog           -> Per-recipient delivery record (status, retries)
EmailTemplate      -> Reusable subject/body templates with variables
ScheduleConfig     -> Recurring send schedules
AuditLog           -> Immutable action trail (login, upload, send, edit)
AppSetting         -> Key/value runtime settings (batch size, theme, etc.)
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
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ----------------------------------------------------------------------
# Enums
# ----------------------------------------------------------------------
class UserRole(str, enum.Enum):
    ADMIN = "Admin"
    OPERATOR = "Operator"


class RecipientType(str, enum.Enum):
    BRANCH = "Branch"
    LHO = "LHO"
    BOTH = "Both"


class JobStatus(str, enum.Enum):
    DRAFT = "Draft"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    COMPLETED_WITH_ERRORS = "Completed With Errors"
    FAILED = "Failed"


class EmailStatus(str, enum.Enum):
    PENDING = "Pending"
    SENT = "Sent"
    FAILED = "Failed"
    RETRYING = "Retrying"


class ScheduleFrequency(str, enum.Enum):
    DAILY = "Daily"
    WEEKLY = "Weekly"
    MONTHLY = "Monthly"


# ----------------------------------------------------------------------
# Core tables
# ----------------------------------------------------------------------
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String(100), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(Enum(UserRole), nullable=False, default=UserRole.OPERATOR)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_login = Column(DateTime, nullable=True)


class Branch(Base):
    __tablename__ = "branches"

    id = Column(Integer, primary_key=True)
    branch_code = Column(String(50), unique=True, nullable=False, index=True)
    branch_name = Column(String(255), nullable=False)
    branch_email = Column(String(255), nullable=False)
    lho_id = Column(Integer, ForeignKey("lhos.id"), nullable=True)
    region = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    lho = relationship("LHO", back_populates="branches")


class LHO(Base):
    __tablename__ = "lhos"

    id = Column(Integer, primary_key=True)
    lho_name = Column(String(255), unique=True, nullable=False, index=True)
    lho_email = Column(String(255), nullable=False)
    region = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    branches = relationship("Branch", back_populates="lho")


class ReportMaster(Base):
    __tablename__ = "report_masters"

    id = Column(Integer, primary_key=True)
    report_name = Column(String(255), unique=True, nullable=False, index=True)
    description = Column(Text, nullable=True)
    recipient_type = Column(Enum(RecipientType), nullable=False, default=RecipientType.BOTH)
    default_template_id = Column(Integer, ForeignKey("email_templates.id"), nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Structured automation config (additive, nullable — old rows/pages that
    # don't set these keep working off `description` alone). Populated by
    # the reporting-framework seed and editable from the new web UI.
    # frequency: "Daily" / "Weekly" / "Monthly"
    frequency = Column(String(20), nullable=True)
    # org_levels: comma-separated OrgLevel values, e.g. "LHO,Corporate Center"
    org_levels = Column(String(300), nullable=True)
    # delivery_mode: "draft" (scheduled sends create Gmail drafts for manual
    # review) or "send" (scheduled sends go out directly, no review step).
    # NULL = fall back to the account-wide Gmail Connection setting.
    delivery_mode = Column(String(10), nullable=True)

    uploads = relationship("ReportUpload", back_populates="report_master")
    default_template = relationship("EmailTemplate")


class ReportUpload(Base):
    __tablename__ = "report_uploads"

    id = Column(Integer, primary_key=True)
    report_master_id = Column(Integer, ForeignKey("report_masters.id"), nullable=False)
    file_name = Column(String(500), nullable=False)
    stored_path = Column(String(1000), nullable=False)
    file_type = Column(String(10), nullable=False)  # pdf / xlsx / xls
    file_size_bytes = Column(Integer, nullable=False)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    uploaded_at = Column(DateTime, default=datetime.utcnow)

    report_master = relationship("ReportMaster", back_populates="uploads")
    uploader = relationship("User")
    jobs = relationship("DistributionJob", back_populates="upload")


class DistributionJob(Base):
    __tablename__ = "distribution_jobs"

    id = Column(Integer, primary_key=True)
    upload_id = Column(Integer, ForeignKey("report_uploads.id"), nullable=False)
    template_id = Column(Integer, ForeignKey("email_templates.id"), nullable=True)
    status = Column(Enum(JobStatus), default=JobStatus.DRAFT, nullable=False)
    total_recipients = Column(Integer, default=0)
    sent_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    is_scheduled_run = Column(Boolean, default=False)

    upload = relationship("ReportUpload", back_populates="jobs")
    template = relationship("EmailTemplate")
    creator = relationship("User")
    email_logs = relationship("EmailLog", back_populates="job")


class EmailLog(Base):
    __tablename__ = "email_logs"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("distribution_jobs.id"), nullable=False)
    recipient_name = Column(String(255), nullable=False)
    recipient_email = Column(String(255), nullable=False)
    recipient_type = Column(String(20), nullable=False)  # Branch / LHO
    branch_code = Column(String(50), nullable=True)
    lho_name = Column(String(255), nullable=True)
    status = Column(Enum(EmailStatus), default=EmailStatus.PENDING, nullable=False)
    attempt_count = Column(Integer, default=0)
    last_error = Column(Text, nullable=True)
    sent_via = Column(String(20), nullable=True)  # graph / smtp
    created_at = Column(DateTime, default=datetime.utcnow)
    sent_at = Column(DateTime, nullable=True)

    # Per-recipient overrides for "segmented" reports (e.g. each RBO gets
    # their own filtered CSP-wise attachment + computed metrics, rather than
    # the whole job sharing one attachment). NULL = use the job's shared
    # upload.stored_path and the standard Recipient/Branch/LHO context only,
    # exactly as before — fully backward compatible.
    attachment_override_path = Column(String(1000), nullable=True)
    context_override_json = Column(Text, nullable=True)

    # Comma-separated CC addresses for this specific send (e.g. Corporate
    # Center's fixed CC list). NULL/empty = no CC.
    cc_emails = Column(String(2000), nullable=True)

    # Open tracking: a random per-email token embedded in an invisible
    # pixel in the rendered body (see services/email_service.py). NULL
    # until the email is actually sent (token is generated at send time,
    # not at EmailLog creation, so unsent/failed rows never get one).
    tracking_token = Column(String(64), nullable=True, index=True)
    opened_at = Column(DateTime, nullable=True)
    open_count = Column(Integer, default=0)

    # Per-scheme (PMJDY/APY/PMSBY/PMJJBY) target/MTD/FTD + per-CSP rows,
    # snapshotted at send time (see report_aggregation_service.
    # build_csp_metric_breakdown) — the "click a metric card" detail page
    # (api/routers/report_detail.py) reads this instead of re-querying the
    # live sheet, so it always matches what this specific email said, even
    # if clicked much later. NULL for reports that don't have this card
    # layout, or emails sent before this feature existed.
    csp_breakdown_json = Column(Text, nullable=True)

    job = relationship("DistributionJob", back_populates="email_logs")


class EmailTemplate(Base):
    __tablename__ = "email_templates"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    subject = Column(String(500), nullable=False)
    body_html = Column(Text, nullable=False)
    is_default = Column(Boolean, default=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ScheduleConfig(Base):
    __tablename__ = "schedule_configs"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    report_master_id = Column(Integer, ForeignKey("report_masters.id"), nullable=False)
    frequency = Column(Enum(ScheduleFrequency), nullable=False)
    run_time = Column(String(5), nullable=False)  # "HH:MM" 24h
    day_of_week = Column(Integer, nullable=True)  # 0=Mon .. 6=Sun, for Weekly
    day_of_month = Column(Integer, nullable=True)  # 1-28, for Monthly
    is_active = Column(Boolean, default=True, nullable=False)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    report_master = relationship("ReportMaster")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    username = Column(String(100), nullable=True)
    action = Column(String(100), nullable=False)  # LOGIN, UPLOAD, SEND, EDIT, DELETE, RETRY
    entity_type = Column(String(100), nullable=True)
    entity_id = Column(String(50), nullable=True)
    details = Column(Text, nullable=True)
    ip_address = Column(String(50), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)


class AppSetting(Base):
    __tablename__ = "app_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
