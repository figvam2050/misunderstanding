"""
logging_config.py
=================
Centralized logging setup with rotating file handler and console output.
Call setup_logging() once at application startup.
"""
import logging
import logging.handlers
from pathlib import Path

import config


def setup_logging() -> None:
    """Configure root logger with rotating file + console handlers."""
    log_level = getattr(logging, config.LOG_LEVEL, logging.INFO)

    # Formatter shared by all handlers
    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # ── Rotating file handler (5 MB × 3 backups) ───────────────────────────
    log_path = Path(config.LOG_FILE)
    file_handler = logging.handlers.RotatingFileHandler(
        filename=log_path,
        maxBytes=5 * 1024 * 1024,  # 5 MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(log_level)

    # ── Console handler ─────────────────────────────────────────────────────
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(fmt)
    console_handler.setLevel(log_level)

    # ── Root logger ─────────────────────────────────────────────────────────
    root = logging.getLogger()
    root.setLevel(log_level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("websockets").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    logging.getLogger("streamlit").setLevel(logging.WARNING)
