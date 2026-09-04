"""
Database engine + session management.

Tries PostgreSQL first (if configured). If the connection fails for any
reason (server down, bad credentials, driver missing), it transparently
falls back to a local SQLite file so the app never hard-crashes on
startup because of a database outage.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from config import BASE_DIR, settings

logger = logging.getLogger(__name__)

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None
ACTIVE_DB_BACKEND = "unknown"


def _build_sqlite_engine() -> Engine:
    global ACTIVE_DB_BACKEND
    sqlite_path = BASE_DIR / settings.sqlite_path
    sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{sqlite_path}",
        # timeout: how long a connection waits for a write lock to clear
        # before raising "database is locked" — this app has a background
        # APScheduler thread doing its own writes (audit logs, autosend
        # state) every minute, fully independent of request threads, so
        # brief write overlaps with a real HTTP request are expected, not
        # exceptional. The sqlite3 driver's own default (5s) wasn't enough
        # — a real login request 500'd with "database is locked" during
        # this session. 30s comfortably covers a batch write without
        # masking a genuinely stuck lock.
        connect_args={"check_same_thread": False, "timeout": 30},
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        # WAL lets readers proceed while a write is in progress (the
        # default rollback-journal mode blocks everyone during a write) —
        # directly reduces how often the scheduler thread and a request
        # thread contend for the same lock in the first place.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

    ACTIVE_DB_BACKEND = "sqlite"
    return engine


def get_engine() -> Engine:
    """Return a lazily-created, process-wide SQLAlchemy engine."""
    global _engine, ACTIVE_DB_BACKEND
    if _engine is not None:
        return _engine

    if settings.db_engine.lower() == "postgres":
        try:
            candidate = create_engine(
                settings.sqlalchemy_uri,
                pool_pre_ping=True,
                pool_size=10,
                max_overflow=20,
                pool_recycle=1800,
            )
            # Fail fast if Postgres is unreachable.
            with candidate.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            _engine = candidate
            ACTIVE_DB_BACKEND = "postgres"
            logger.info("Connected to PostgreSQL.")
        except Exception as exc:  # noqa: BLE001
            logger.warning("PostgreSQL unavailable (%s). Falling back to SQLite.", exc)
            _engine = _build_sqlite_engine()
    else:
        _engine = _build_sqlite_engine()

    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        # expire_on_commit=False: pages routinely read ORM object attributes
        # after their `with get_db()` block has committed and closed (each
        # Streamlit interaction re-runs the whole script top to bottom, so
        # there's no long-lived session to keep attributes fresh against).
        # Without this, any such access raises DetachedInstanceError.
        _SessionLocal = sessionmaker(
            bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False
        )
    return _SessionLocal


@contextmanager
def get_db() -> Iterator[Session]:
    """Context-managed DB session. Commits on success, rolls back on error."""
    session: Session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _run_lightweight_migrations() -> None:
    """
    Add-column-if-missing for a handful of nullable columns added after the
    tables already existed on disk — `create_all()` only creates missing
    *tables*, never alters existing ones. This app has no Alembic setup, so
    for purely additive nullable columns a guarded `ALTER TABLE` is enough;
    each entry is a no-op once the column exists.
    """
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    migrations = [
        ("report_masters", "frequency", "VARCHAR(20)"),
        ("report_masters", "org_levels", "VARCHAR(300)"),
        ("report_sources", "source_type", "VARCHAR(20)"),
        ("report_sources", "google_sheet_id", "VARCHAR(255)"),
        ("report_sources", "google_sheet_tab", "VARCHAR(255)"),
        ("auto_distribution_schedules", "fetch_time", "VARCHAR(5)"),
        ("auto_distribution_schedules", "last_fetch_at", "DATETIME"),
        ("auto_distribution_schedules", "next_fetch_at", "DATETIME"),
        ("email_logs", "attachment_override_path", "VARCHAR(1000)"),
        ("email_logs", "context_override_json", "TEXT"),
        ("report_masters", "delivery_mode", "VARCHAR(10)"),
        ("email_logs", "cc_emails", "VARCHAR(2000)"),
        ("org_units", "cc_emails", "VARCHAR(2000)"),
        ("email_logs", "tracking_token", "VARCHAR(64)"),
        ("email_logs", "opened_at", "DATETIME"),
        ("email_logs", "open_count", "INTEGER"),
        ("incoming_emails", "replied", "BOOLEAN"),
        ("incoming_emails", "replied_at", "DATETIME"),
        ("incoming_emails", "recipient_kind", "VARCHAR(10)"),
        ("incoming_emails", "matched_reply_template_id", "INTEGER"),
        ("incoming_emails", "match_confidence", "FLOAT"),
        ("sent_emails", "replied", "BOOLEAN"),
        ("sent_emails", "replied_at", "DATETIME"),
        ("incoming_emails", "triage_tier", "VARCHAR(10)"),
        ("incoming_emails", "triage_intent", "VARCHAR(60)"),
        ("email_logs", "csp_breakdown_json", "TEXT"),
    ]

    with engine.begin() as conn:
        for table, column, col_type in migrations:
            if table not in existing_tables:
                continue  # brand-new install: create_all() already included the column
            existing_cols = {c["name"] for c in inspector.get_columns(table)}
            if column not in existing_cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"))
                logger.info("Migration: added %s.%s", table, column)

        if "email_logs" in existing_tables:
            conn.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_email_logs_tracking_token ON email_logs (tracking_token)"
            ))


def _drop_stale_empty_tables() -> None:
    """
    `create_all()` can't fix a column whose NOT NULL constraint changed
    (SQLite has no ALTER COLUMN for that) — `report_sources.base_url` was
    NOT NULL when the table first shipped, then relaxed to nullable to
    support Google-Sheet sources. If the table is still empty, it's safe
    to drop and let create_all() recreate it with the current, correct
    constraints rather than requiring a real migration for zero rows.
    """
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    if "report_sources" not in inspector.get_table_names():
        return

    base_url_col = next((c for c in inspector.get_columns("report_sources") if c["name"] == "base_url"), None)
    if base_url_col is None or base_url_col["nullable"]:
        return  # already correct (or column missing entirely — create_all handles that)

    with engine.begin() as conn:
        (count,) = conn.execute(text("SELECT COUNT(*) FROM report_sources")).fetchone()
        if count == 0:
            conn.execute(text("DROP TABLE report_sources"))
            logger.info("Migration: dropped empty stale report_sources table (stale NOT NULL base_url) for recreation.")
        else:
            logger.warning(
                "report_sources.base_url is still NOT NULL but the table has %d row(s) — "
                "leave as-is and migrate manually if Google Sheet sources start failing to insert.",
                count,
            )


def init_db() -> None:
    """Create all tables and seed a default admin user if none exists."""
    from database.models import Base, User
    from utils.security import hash_password

    # Additive: register the incoming-email, org-hierarchy and report-source
    # tables on the same metadata so create_all() builds them alongside
    # (never altering) the existing schema.
    import database.agent_mail_models  # noqa: F401
    import database.incoming_models  # noqa: F401
    import database.org_models  # noqa: F401
    import database.report_source_models  # noqa: F401
    import database.snapshot_models  # noqa: F401
    import database.suggestion_models  # noqa: F401

    _drop_stale_empty_tables()
    Base.metadata.create_all(bind=get_engine())
    _run_lightweight_migrations()

    with get_db() as db:
        has_user = db.query(User).first()
        if not has_user:
            admin = User(
                username=settings.default_admin_username,
                email=settings.default_admin_email,
                password_hash=hash_password(settings.default_admin_password),
                role="Admin",
                is_active=True,
            )
            db.add(admin)
            logger.info("Seeded default admin user '%s'.", admin.username)
