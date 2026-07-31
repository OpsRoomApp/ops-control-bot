"""
OPS CONTROL - Logging Configuration

Configures structured logging to both console and rotating file.
Logs startup, commands, errors, member joins, and API failures.
"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from bot.config import config


LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# Max log file size: 5 MB; keep 3 backups
MAX_BYTES = 5 * 1024 * 1024
BACKUP_COUNT = 3


def setup_logging() -> logging.Logger:
    """
    Configure and return the root OPS CONTROL logger.

    - Console handler (stdout) for real-time visibility.
    - Rotating file handler for persistent audit trail.
    """
    log_path = Path(config.log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger("ops_control")
    root_logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))

    # Prevent duplicate handlers on hot-reload
    if root_logger.handlers:
        return root_logger

    # --- Formatters ---
    detailed = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    console_fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S"
    )

    # --- Console handler ---
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(console_fmt)
    root_logger.addHandler(console)

    # --- File handler ---
    file_handler = RotatingFileHandler(
        str(log_path),
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(detailed)
    root_logger.addHandler(file_handler)

    # Silence overly verbose third-party loggers
    for noisy in ("discord.gateway", "discord.http", "aiosqlite", "aiohttp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    root_logger.info("Logging initialised — log file: %s", log_path)
    return root_logger


# Convenience accessor
def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"ops_control.{name}")
