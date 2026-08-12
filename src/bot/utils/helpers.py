"""
OPS CONTROL - Utility Helpers

Shared helper functions used across the bot.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytz

from bot.config import config

logger = logging.getLogger("ops_control.utils.helpers")


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

async def resolve_text_channel(bot, channel_id: int):
    """Resolve a guild text channel by ID, cache-first with an API fallback.

    ``bot.get_channel`` only returns channels already present in the bot's
    internal cache. A channel the bot cannot see (a permission overwrite) or
    that lives in another guild is never cached, so we fall back to an explicit
    API fetch and log the precise failure reason.
    """
    import discord

    channel = bot.get_channel(channel_id)
    if isinstance(channel, discord.TextChannel):
        return channel

    try:
        channel = await bot.fetch_channel(channel_id)
    except discord.NotFound:
        logger.warning(
            "Channel %s not found (404): wrong ID, or bot is not a member of that guild",
            channel_id,
        )
        return None
    except discord.Forbidden:
        logger.warning(
            "Channel %s forbidden (403): bot lacks View Channel permission",
            channel_id,
        )
        return None
    except discord.HTTPException as exc:
        logger.warning("Channel %s fetch failed: %s", channel_id, exc)
        return None

    if isinstance(channel, discord.TextChannel):
        return channel

    logger.warning(
        "Channel %s is a %s, not a text channel",
        channel_id,
        type(channel).__name__,
    )
    return None

