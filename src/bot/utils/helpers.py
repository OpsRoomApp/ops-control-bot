"""
OPS CONTROL - Utility Helpers

Shared helper functions used across the bot.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytz

from bot.config import config


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def fmt_ts(iso_string: str, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format an ISO timestamp into a human-friendly string."""
    try:
        dt = datetime.fromisoformat(iso_string)
        return dt.strftime(fmt)
    except (ValueError, TypeError):
        return iso_string


def format_duration(seconds: float) -> str:
    """Convert seconds into a human-readable duration string."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m {secs}s"


def get_tz():
    """Return the configured timezone object."""
    return pytz.timezone(config.timezone)
