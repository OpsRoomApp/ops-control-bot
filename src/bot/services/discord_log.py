"""
OPS CONTROL - Discord Logging Service

Sends structured audit events to the configured Discord log channel.
Used for: bot startup/shutdown, user joins, command usage, ticket/bug
activity, admin actions, and announcements.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from bot.config import config

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger("ops_control.services.discord_log")

_log_channel: discord.TextChannel | None = None


def set_log_channel(channel: discord.TextChannel) -> None:
    """Cache the log channel reference for fast access."""
    global _log_channel
    _log_channel = channel


async def send_log(
    bot: commands.Bot,
    title: str,
    *,
    fields: list[tuple[str, str]] | None = None,
    color: int = 0x6B7280,
    detail: str | None = None,
) -> bool:
    """Send a structured log embed to the configured log channel.

    Args:
        bot: The bot instance (to fetch channel if not cached).
        title: Embed title.
        fields: Optional list of (name, value) pairs.
        color: Embed color hex.
        detail: Optional footer/detail text.

    Returns:
        True if the log was sent, False otherwise.
    """
    global _log_channel

    if not config.log_channel_id:
        return False

    if _log_channel is None:
        ch = bot.get_channel(config.log_channel_id)
        if ch and isinstance(ch, discord.TextChannel):
            _log_channel = ch
        else:
            logger.warning("Log channel %s not found", config.log_channel_id)
            return False

    embed = discord.Embed(title=title, color=color, timestamp=discord.utils.utcnow())

    if fields:
        for name, value in fields:
            embed.add_field(name=name, value=value, inline=name not in ("Title", "Description"))

    if detail:
        embed.set_footer(text=detail)

    try:
        await _log_channel.send(embed=embed)
        return True
    except Exception:
        logger.exception("Failed to send log embed to channel %s", config.log_channel_id)
        return False


# -- Convenience helpers for specific event types --


async def log_startup(bot: commands.Bot) -> None:
    await send_log(bot, "OPS CONTROL Online", color=0x059669, detail="Bot startup complete")


async def log_shutdown(bot: commands.Bot) -> None:
    await send_log(bot, "OPS CONTROL Offline", color=0xDC2626, detail="Bot shutting down")


async def log_member_join(
    bot: commands.Bot,
    user: discord.Member,
) -> None:
    await send_log(
        bot,
        "Member Joined",
        fields=[
            ("User", f"{user.mention} ({user.name})"),
            ("User ID", str(user.id)),
        ],
        color=0x059669,
    )


async def log_simbrief_link(
    bot: commands.Bot,
    user: discord.Member,
    username: str,
) -> None:
    await send_log(
        bot,
        "SimBrief Account Linked",
        fields=[
            ("User", f"{user.mention} ({user.name})"),
            ("SimBrief User", username),
        ],
        color=0xEA580C,
    )


async def log_ofp_request(
    bot: commands.Bot,
    user: discord.Member,
    sb_user: str,
) -> None:
    await send_log(
        bot,
        "OFP Requested",
        fields=[
            ("User", f"{user.mention} ({user.name})"),
            ("SimBrief User", sb_user),
        ],
        color=0xEA580C,
    )


async def log_announcement(
    bot: commands.Bot,
    user: discord.Member,
    title: str,
    channel_name: str,
) -> None:
    await send_log(
        bot,
        "Announcement Sent",
        fields=[
            ("Author", f"{user.mention} ({user.name})"),
            ("Channel", f"#{channel_name}"),
            ("Title", title),
        ],
        color=0x2563EB,
    )


async def log_ticket_created(
    bot: commands.Bot,
    user: discord.Member,
    ticket_id: int,
    subject: str,
    channel_mention: str,
) -> None:
    await send_log(
        bot,
        "Support Ticket Created",
        fields=[
            ("Ticket", f"#{ticket_id}"),
            ("Created By", f"{user.mention} ({user.name})"),
            ("Subject", subject),
            ("Channel", channel_mention),
        ],
        color=0x8B5CF6,
    )


async def log_ticket_closed(
    bot: commands.Bot,
    closer: discord.Member,
    ticket_id: int,
    creator_name: str,
) -> None:
    await send_log(
        bot,
        "Support Ticket Closed",
        fields=[
            ("Ticket", f"#{ticket_id}"),
            ("Closed By", f"{closer.mention} ({closer.name})"),
            ("Created By", creator_name),
        ],
        color=0xDC2626,
    )


async def log_bug_submitted(
    bot: commands.Bot,
    user: discord.Member,
    bug_id: int,
    title: str,
    channel_mention: str,
) -> None:
    await send_log(
        bot,
        "Bug Report Submitted",
        fields=[
            ("Bug", f"#{bug_id}"),
            ("Reporter", f"{user.mention} ({user.name})"),
            ("Title", title),
            ("Channel", channel_mention),
        ],
        color=0xF59E0B,
    )


async def log_purge(
    bot: commands.Bot,
    user: discord.Member,
    amount: int,
    channel_name: str,
) -> None:
    await send_log(
        bot,
        "Messages Purged",
        fields=[
            ("Executed By", f"{user.mention} ({user.name})"),
            ("Channel", f"#{channel_name}"),
            ("Messages", str(amount)),
        ],
        color=0xEF4444,
    )
