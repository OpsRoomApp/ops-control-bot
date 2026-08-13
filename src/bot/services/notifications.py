"""
OPS CONTROL - Notification Dispatch Service

Handles automated announcement dispatch for:
- New OPS ROOM releases
- NOTAM alerts for saved airports
- VATSIM event notifications

Professional aviation operations style - no emojis, clean formatting.
"""

from __future__ import annotations

import logging
from typing import Any

import discord

from bot.config import config
from bot.database import get_db
from bot.utils.helpers import utc_now_iso

logger = logging.getLogger("ops_control.services.notifications")


async def dispatch_release_announcement(
    bot,
    version: str,
    codename: str,
    release_date: str,
    changes: str,
    download_url: str,
) -> bool:
    """Post a release announcement to the configured announcement channel."""
    if not config.discord_announcement_channel:
        logger.info("No announcement channel configured; skipping release dispatch")
        return False

    channel = bot.get_channel(config.discord_announcement_channel)
    if not channel or not isinstance(channel, discord.TextChannel):
        logger.warning("Announcement channel %s not found", config.discord_announcement_channel)
        return False

    embed = discord.Embed(
        title=f"OPS ROOM {version} Released",
        description=(
            f"**Codename:** {codename}\n"
            f"**Release Date:** {release_date}\n\n"
            f"{changes[:1500]}"
        ),
        color=0x2563EB,
    )
    embed.add_field(name="Download", value=f"[opsroom.live/downloads]({download_url})", inline=False)
    embed.set_footer(text="OPS ROOM Release System")

    try:
        await channel.send(embed=embed)

        # Log
        db = await get_db()
        await db.execute(
            """
            INSERT INTO discord_announcements (title, content, channel_id, announced_at)
            VALUES (?, ?, ?, ?)
            """,
            (f"OPS ROOM {version}", changes, config.discord_announcement_channel, utc_now_iso()),
        )
        await db.commit()

        logger.info("Release announcement dispatched: %s", version)
        return True
    except Exception:
        logger.exception("Failed to dispatch release announcement")
        return False


async def notify_user_airport_alert(
    bot,
    user_id: int,
    icao: str,
    notam_detail: str,
) -> bool:
    """Send a DM to a user about a NOTAM affecting their saved airport."""
    try:
        user = await bot.fetch_user(user_id)
        if user:
            embed = discord.Embed(
                title=f"NOTAM Alert - {icao}",
                description=notam_detail[:2000],
                color=0xF59E0B,
            )
            embed.set_footer(text="OPS ROOM Operations Alert")
            await user.send(embed=embed)
            return True
    except Exception:
        logger.exception("Failed to send airport alert to user %s", user_id)
    return False
