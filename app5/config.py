"""
Central application configuration.

Loads settings from environment variables (via `.env`) and exposes them
as a single importable `settings` object. Keeping all configuration in
one place makes the rest of the codebase environment-agnostic.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _bool(key: str, default: str = "false") -> bool:
    return os.getenv(key, default).strip().lower() in {"1", "true", "yes", "on"}


def _int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except ValueError:
        return default


def _list(key: str, default: str) -> list[str]:
    raw = os.getenv(key, default)
    return [item.strip().lower() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # App
    app_name: str = os.getenv("APP_NAME", "Reporting Console")
    org_tagline: str = os.getenv("ORG_TAGLINE", "Report Distribution Operations")
    app_env: str = os.getenv("APP_ENV", "development")
    secret_key: str = os.getenv("SECRET_KEY", "dev-secret-key")
    session_timeout_minutes: int = _int("SESSION_TIMEOUT_MINUTES", 60)
    # Publicly reachable base URL for this backend (e.g. https://reports.yourcompany.com),
    # no trailing slash. Required for the email-open tracking pixel — recipients'
    # mail clients fetch it, so localhost/127.0.0.1 will never work here. Empty
    # (the default, e.g. while running only on localhost) disables pixel injection
    # entirely rather than embedding a dead URL in every sent email.
    public_base_url: str = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

    # Database
    db_engine: str = os.getenv("DB_ENGINE", "sqlite")
    postgres_host: str = os.getenv("POSTGRES_HOST", "localhost")
    postgres_port: int = _int("POSTGRES_PORT", 5432)
    postgres_db: str = os.getenv("POSTGRES_DB", "report_dashboard")
    postgres_user: str = os.getenv("POSTGRES_USER", "postgres")
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "postgres")
    sqlite_path: str = os.getenv("SQLITE_PATH", "data/app.db")

    # Microsoft Graph
    ms_graph_enabled: bool = _bool("MS_GRAPH_ENABLED", "false")
    ms_graph_tenant_id: str = os.getenv("MS_GRAPH_TENANT_ID", "")
    ms_graph_client_id: str = os.getenv("MS_GRAPH_CLIENT_ID", "")
    ms_graph_client_secret: str = os.getenv("MS_GRAPH_CLIENT_SECRET", "")
    ms_graph_sender_email: str = os.getenv("MS_GRAPH_SENDER_EMAIL", "")

    # SMTP
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = _int("SMTP_PORT", 587)
    smtp_use_tls: bool = _bool("SMTP_USE_TLS", "true")
    smtp_username: str = os.getenv("SMTP_USERNAME", "")
    smtp_password: str = os.getenv("SMTP_PASSWORD", "")
    smtp_from_name: str = os.getenv("SMTP_FROM_NAME", "Report Distribution System")

    # Uploads
    max_upload_size_mb: int = _int("MAX_UPLOAD_SIZE_MB", 25)
    allowed_report_extensions: list[str] = field(
        default_factory=lambda: _list("ALLOWED_REPORT_EXTENSIONS", ".pdf,.xlsx,.xls")
    )
    upload_dir: str = os.getenv("UPLOAD_DIR", "uploads/reports")
    master_upload_dir: str = os.getenv("MASTER_UPLOAD_DIR", "uploads/masters")

    # Batch / retry
    default_batch_size: int = _int("DEFAULT_BATCH_SIZE", 25)
    default_max_retries: int = _int("DEFAULT_MAX_RETRIES", 3)
    retry_backoff_seconds: int = _int("RETRY_BACKOFF_SECONDS", 30)

    # Send pacing (anti-throttling): emails go out in small batches with a
    # randomized pause between batches, rather than back-to-back, to look
    # less like a mass-mailing burst to Gmail's abuse detection.
    email_batch_size: int = _int("EMAIL_BATCH_SIZE", 3)
    email_pace_min_seconds: int = _int("EMAIL_PACE_MIN_SECONDS", 25)
    email_pace_max_seconds: int = _int("EMAIL_PACE_MAX_SECONDS", 31)

    # Daily automated fetch -> freshness-check -> send cycle for the
    # calling-sheet-driven reports. Off by default — this is a *standing*
    # automated behavior (real sends firing every day with no manual click,
    # subject to each report's existing Draft Only / Send Directly setting),
    # so it must be turned on deliberately once the operator is ready.
    autosend_enabled: bool = _bool("AUTOSEND_ENABLED", "false")
    autosend_fetch_time: str = os.getenv("AUTOSEND_FETCH_TIME", "10:15")
    autosend_send_time: str = os.getenv("AUTOSEND_SEND_TIME", "10:30")
    autosend_recheck_minutes: int = _int("AUTOSEND_RECHECK_MINUTES", 60)

    # Default admin bootstrap
    default_admin_username: str = os.getenv("DEFAULT_ADMIN_USERNAME", "admin")
    default_admin_password: str = os.getenv("DEFAULT_ADMIN_PASSWORD", "Admin@12345")
    default_admin_email: str = os.getenv("DEFAULT_ADMIN_EMAIL", "admin@yourcompany.com")

    # Logging
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_dir: str = os.getenv("LOG_DIR", "logs")

    @property
    def sqlalchemy_uri(self) -> str:
        if self.db_engine.lower() == "postgres":
            return (
                f"postgresql+psycopg2://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        sqlite_full_path = BASE_DIR / self.sqlite_path
        sqlite_full_path.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{sqlite_full_path}"


settings = Settings()

# Ensure runtime directories exist
for _dir in (settings.upload_dir, settings.master_upload_dir, settings.log_dir, "data"):
    (BASE_DIR / _dir).mkdir(parents=True, exist_ok=True)
