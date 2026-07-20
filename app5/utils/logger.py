"""Application-wide logging configuration."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from config import BASE_DIR, settings

_CONFIGURED = False


def setup_logging() -> None:
    """Idempotently configure root logging: rotating file + console."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_dir = BASE_DIR / settings.log_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "app.log"

    root = logging.getLogger()
    root.setLevel(settings.log_level.upper())

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=5)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Quiet down noisy third-party loggers
    for noisy in ("urllib3", "msal", "sqlalchemy.engine"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    setup_logging()
    return logging.getLogger(name)
