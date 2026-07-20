"""
External report-source + automated distribution ORM models (ADDITIVE).

Lives outside `database/models.py`, same pattern as `incoming_models.py`
and `org_models.py` — reuses the same declarative `Base` so `create_all()`
picks these tables up without touching any existing table.

ReportSource            -> Config for a REST API that serves a report file
ReportSourceRun         -> One fetch attempt log (manual "Test Fetch" or scheduled)
AutoDistributionSchedule -> Recurring "download from ReportSource, then send" job
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
from sqlalchemy.orm import relationship

from database.models import Base, ScheduleFrequency


class SourceType(str, enum.Enum):
    REST_API = "REST API"
    GOOGLE_SHEET = "Google Sheet"


class AuthType(str, enum.Enum):
    NONE = "None"
    API_KEY = "API Key Header"
    BEARER = "Bearer Token"
    BASIC = "Basic Auth"


class HttpMethod(str, enum.Enum):
    GET = "GET"
    POST = "POST"


class RunStatus(str, enum.Enum):
    SUCCESS = "Success"
    FAILED = "Failed"


class ReportSource(Base):
    __tablename__ = "report_sources"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), unique=True, nullable=False)
    report_master_id = Column(Integer, ForeignKey("report_masters.id"), nullable=False)

    source_type = Column(Enum(SourceType), default=SourceType.REST_API, nullable=False)

    # -- REST_API fields (nullable: unused when source_type == GOOGLE_SHEET) --
    base_url = Column(String(1000), nullable=True)
    http_method = Column(Enum(HttpMethod), default=HttpMethod.GET, nullable=False)
    # Path/query appended to base_url. Supports {date}, {yesterday}, {week}, {month}
    # placeholders, optionally with a strftime spec e.g. {date:%d-%m-%Y}.
    endpoint_path_template = Column(String(1000), nullable=True)
    auth_type = Column(Enum(AuthType), default=AuthType.NONE, nullable=False)
    auth_header_name = Column(String(100), nullable=True)  # e.g. "X-API-Key" (API_KEY) or username (BASIC)
    auth_secret = Column(String(1000), nullable=True)      # API key / bearer token / password
    # Local filename to save/attach the downloaded report as. Same placeholders as above.
    filename_template = Column(String(500), nullable=True)

    # -- GOOGLE_SHEET fields (nullable: unused when source_type == REST_API) --
    # The spreadsheet id (the long id segment in the sheet's URL, not the full URL).
    google_sheet_id = Column(String(255), nullable=True)
    # Tab/sheet name within the spreadsheet, e.g. "Daily CSP Calling Sheet".
    google_sheet_tab = Column(String(255), nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    report_master = relationship("ReportMaster")
    runs = relationship("ReportSourceRun", back_populates="source", cascade="all, delete-orphan")


class ReportSourceRun(Base):
    __tablename__ = "report_source_runs"

    id = Column(Integer, primary_key=True)
    report_source_id = Column(Integer, ForeignKey("report_sources.id"), nullable=False)
    run_at = Column(DateTime, default=datetime.utcnow)
    status = Column(Enum(RunStatus), nullable=False)
    resolved_filename = Column(String(500), nullable=True)
    stored_path = Column(String(1000), nullable=True)
    report_upload_id = Column(Integer, ForeignKey("report_uploads.id"), nullable=True)
    error = Column(Text, nullable=True)
    triggered_by = Column(String(50), default="manual")  # manual / schedule

    source = relationship("ReportSource", back_populates="runs")


class AutoDistributionSchedule(Base):
    __tablename__ = "auto_distribution_schedules"

    id = Column(Integer, primary_key=True)
    name = Column(String(255), nullable=False)
    report_source_id = Column(Integer, ForeignKey("report_sources.id"), nullable=False)
    template_id = Column(Integer, ForeignKey("email_templates.id"), nullable=True)

    # Recipient resolution against org_units (see database/org_models.py).
    # Comma-separated OrgLevel.value entries, e.g. "LHO,Corporate Center" — a
    # single report is often sent to several levels at once (per the
    # reporting-framework matrix), so this is a list, not a single level.
    org_level = Column(String(300), nullable=False)
    # Comma-separated OrgUnit ids to restrict to specific units, or NULL = all active units of the selected level(s).
    org_unit_ids = Column(String(2000), nullable=True)

    frequency = Column(Enum(ScheduleFrequency), nullable=False)
    # Send time — kept as `run_time` for backward compatibility with the
    # column already on disk. "HH:MM" 24h.
    run_time = Column(String(5), nullable=False)
    day_of_week = Column(Integer, nullable=True)
    day_of_month = Column(Integer, nullable=True)

    # Fetch time — independent of send time, so the source can be refreshed
    # (e.g. 11:40) well before the send goes out (e.g. 12:00), rather than
    # fetching and sending at the same instant. Nullable: a schedule created
    # before this existed just fetches immediately before it sends.
    fetch_time = Column(String(5), nullable=True)
    last_fetch_at = Column(DateTime, nullable=True)
    next_fetch_at = Column(DateTime, nullable=True)

    is_active = Column(Boolean, default=True, nullable=False)
    last_run_at = Column(DateTime, nullable=True)
    next_run_at = Column(DateTime, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    report_source = relationship("ReportSource")
    template = relationship("EmailTemplate")
