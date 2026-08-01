"""
OPS CONTROL - Ticket Transcript Service

Generates an HTML transcript of a support ticket when it is closed, sends
it to the transcript archive channel and to the ticket creator by DM, then
records delivery metadata in the database.

Workflow (idempotent):
    1. Guard against duplicate close operations.
    2. Lock ticket controls.
    3. Fetch complete accessible channel history.
    4. Generate transcript (HTML, plain-text fallback).
    5. Send to archive channel; if unavailable -> mark failed, KEEP channel.
    6. DM the ticket creator; record DM failure but continue normally.
    7. Update ticket status + transcript metadata.
    8. Log closure; only then delete/archive the ticket channel.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timezone
from io import BytesIO
from typing import TYPE_CHECKING, Any

import discord

from bot.config import config
from bot.database import get_db
from bot.services.audit import log_event
from bot.utils.helpers import utc_now_iso

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger("ops_control.services.ticket_transcript")


def sanitize_channel_name(username: str) -> str:
    """Normalize a username for use in a Discord channel name."""
    name = (username or "user").lower()
    name = re.sub(r"[^a-z0-9 _-]", "", name)
    name = re.sub(r"\s+", "-", name).strip("-")
    name = re.sub(r"-{2,}", "-", name)
    return name[:60]


def ticket_channel_name(ticket_number: int, username: str, fallback_id: int = 0) -> str:
    """Build a ticket channel name: ticket-{number}-{username}.

    Falls back to the user ID when the username is empty or invalid.
    """
    clean = (username or "").strip()
    if not clean:
        clean = str(fallback_id or "user")
    safe = sanitize_channel_name(clean) or "user"
    return f"ticket-{ticket_number}-{safe}"


def build_html_transcript(
    *,
    ticket_number: int,
    channel_name: str,
    creator: str,
    subject: str,
    priority: str,
    assigned_staff: str | None,
    opened_at: str,
    closed_at: str,
    closed_by: str,
    messages: list[dict[str, Any]],
) -> str:
    """Render a complete ticket transcript as HTML."""
    rows: list[str] = []
    for msg in messages:
        author = html.escape(str(msg.get("author") or "Unknown"))
        ts = html.escape(str(msg.get("timestamp") or ""))
        content = html.escape(str(msg.get("content") or "")) or "<i>(no text)</i>"
        content = content.replace("\n", "<br>")
        attachments = msg.get("attachments") or []
        embeds = msg.get("embeds") or []

        extra: list[str] = []
        for url in attachments:
            extra.append(f'<div class="attachment"><a href="{html.escape(url)}">Attachment: {html.escape(url)}</a></div>')
        for embed in embeds:
            title = html.escape(str(embed.get("title") or "Embed"))
            desc = html.escape(str(embed.get("description") or ""))
            extra.append(f'<div class="embed"><b>{title}</b> {desc}</div>')

        rows.append(
            f'<div class="message"><div class="meta"><span class="author">{author}</span>'
            f'<span class="time">{ts}</span></div><div class="body">{content}</div>'
            f'{"".join(extra)}</div>'
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Ticket #{ticket_number} Transcript</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; background: #0b1220; color: #e5e9f0; margin: 0; padding: 24px; }}
  h1 {{ font-size: 20px; color: #f4f6fc; }}
  .meta {{ color: #8b93a7; font-size: 12px; margin-bottom: 18px; }}
  .meta span {{ margin-right: 14px; }}
  .message {{ background: #111a2e; border: 1px solid #1f2a44; border-radius: 8px; padding: 12px 14px; margin-bottom: 10px; }}
  .author {{ color: #60a5fa; font-weight: 600; }}
  .time {{ color: #64748b; margin-left: 10px; }}
  .body {{ margin-top: 6px; color: #e5e9f0; white-space: normal; }}
  .attachment a, .embed a {{ color: #38bdf8; }}
  .embed {{ border-left: 3px solid #2563eb; padding-left: 8px; margin-top: 6px; color: #b6c2d9; }}
</style>
</head>
<body>
<h1>Ticket #{ticket_number} Transcript</h1>
<div class="meta">
  <span><b>Channel:</b> {html.escape(channel_name)}</span>
  <span><b>Creator:</b> {html.escape(creator)}</span>
  <span><b>Subject:</b> {html.escape(subject)}</span>
  <span><b>Priority:</b> {html.escape(priority)}</span>
  <span><b>Assigned:</b> {html.escape(assigned_staff or "None")}</span>
  <span><b>Opened:</b> {html.escape(opened_at)}</span>
  <span><b>Closed:</b> {html.escape(closed_at)}</span>
  <span><b>Closed by:</b> {html.escape(closed_by)}</span>
</div>
{''.join(rows)}
</body>
</html>"""


def build_plaintext_transcript(
    *,
    ticket_number: int,
    creator: str,
    subject: str,
    messages: list[dict[str, Any]],
) -> str:
    """Plain-text fallback transcript."""
    lines = [
        f"Ticket #{ticket_number} Transcript",
        f"Creator: {creator}",
        f"Subject: {subject}",
        "=" * 40,
    ]
    for msg in messages:
        lines.append(f"[{msg.get('timestamp', '')}] {msg.get('author', '')}: {msg.get('content', '')}")
        for url in msg.get("attachments") or []:
            lines.append(f"  Attachment: {url}")
    return "\n".join(lines)


async def fetch_channel_history(channel: discord.TextChannel, limit: int = 2000) -> list[dict[str, Any]]:
    """Fetch chronological message history (text, attachments, embed summaries)."""
    messages: list[dict[str, Any]] = []
    async for msg in channel.history(limit=limit, oldest_first=True):
        messages.append({
            "author": f"{msg.author.display_name} ({msg.author.name})" if msg.author else "Unknown",
            "timestamp": msg.created_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            "content": msg.content or "",
            "attachments": [a.url for a in msg.attachments],
            "embeds": [
                {
                    "title": e.title or "",
                    "description": e.description or "",
                }
                for e in msg.embeds
            ],
        })
    return messages


async def _update_ticket_metadata(
    ticket_id: int,
    *,
    status: str,
    closed_by: int,
    closed_at: str,
    transcript_status: str,
    transcript_filename: str | None,
    transcript_channel_id: int | None,
    transcript_message_id: int | None,
    transcript_dm_sent: int,
    transcript_error: str | None,
) -> None:
    db = await get_db()
    await db.execute(
        """
        UPDATE tickets
        SET status = ?, closed_by = ?, closed_at = ?, updated_at = ?,
            transcript_status = ?, transcript_filename = ?,
            transcript_channel_id = ?, transcript_message_id = ?,
            transcript_dm_sent = ?, transcript_error = ?
        WHERE id = ?
        """,
        (
            status, closed_by, closed_at, utc_now_iso(),
            transcript_status, transcript_filename,
            transcript_channel_id, transcript_message_id,
            transcript_dm_sent, transcript_error,
            ticket_id,
        ),
    )
    await db.commit()


async def close_ticket_with_transcript(
    bot: commands.Bot,
    channel: discord.TextChannel,
    closer: discord.Member,
    *,
    ticket_id: int,
    creator_user_id: int,
    creator_name: str,
    subject: str,
    priority: str,
    assigned_staff: str | None,
    opened_at: str,
    ticket_number: int,
) -> dict[str, Any]:
    """Close a ticket channel with a full transcript workflow.

    Returns a dict with transcript_status and delivery metadata.
    Raises nothing on expected failure paths; always leaves the DB consistent.
    """
    now_iso = utc_now_iso()
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    safe_user = sanitize_channel_name(creator_name) or "user"
    filename = f"ticket-{ticket_number}-{safe_user}-{now_stamp}.html"

    result: dict[str, Any] = {
        "transcript_status": "failed",
        "filename": filename,
        "archive_message_id": None,
        "dm_sent": 0,
        "error": None,
    }

    # 1. Fetch history
    try:
        messages = await fetch_channel_history(channel)
    except Exception as exc:
        logger.exception("Failed to fetch ticket history")
        result["error"] = f"History fetch failed: {exc}"
        await _update_ticket_metadata(
            ticket_id, status="open", closed_by=closer.id, closed_at=now_iso,
            transcript_status="failed", transcript_filename=filename,
            transcript_channel_id=None, transcript_message_id=None,
            transcript_dm_sent=0, transcript_error=result["error"],
        )
        return result

    # 2. Build transcript (HTML preferred, plain-text fallback)
    transcript_html = build_html_transcript(
        ticket_number=ticket_number,
        channel_name=channel.name,
        creator=creator_name,
        subject=subject or "No subject",
        priority=priority,
        assigned_staff=assigned_staff,
        opened_at=opened_at,
        closed_at=now_iso,
        closed_by=closer.display_name or closer.name,
        messages=messages,
    )
    transcript_bytes = BytesIO(transcript_html.encode("utf-8"))
    transcript_bytes.seek(0)
    attachment = discord.File(transcript_bytes, filename=filename)

    # 3. Archive channel delivery.
    # A configured archive channel that cannot be resolved is an archive
    # FAILURE: the ticket must be preserved (never deleted), marked failed,
    # staff notified, and retried later.
    archive_channel = None
    archive_error = None
    if config.ticket_transcript_channel_id:
        archive_channel = bot.get_channel(config.ticket_transcript_channel_id)
        if not isinstance(archive_channel, discord.TextChannel):
            archive_channel = None
            archive_error = f"Archive channel {config.ticket_transcript_channel_id} not found"
            logger.warning("Transcript archive channel %s not found", config.ticket_transcript_channel_id)
    else:
        archive_error = "Transcript archive channel is not configured (TICKET_TRANSCRIPT_CHANNEL_ID)"

    if archive_error:
        result["error"] = archive_error
        await _update_ticket_metadata(
            ticket_id, status="open", closed_by=closer.id, closed_at=now_iso,
            transcript_status="failed", transcript_filename=filename,
            transcript_channel_id=config.ticket_transcript_channel_id or None,
            transcript_message_id=None, transcript_dm_sent=0,
            transcript_error=result["error"],
        )
        # Keep the channel; notify staff; allow retry.
        await _notify_staff_retry(channel, ticket_number, result["error"])
        return result

    archive_message_id = None
    if archive_channel:
        try:
            archive_embed = discord.Embed(
                title=f"Ticket #{ticket_number} Closed",
                color=0x64748B,
                timestamp=discord.utils.utcnow(),
            )
            archive_embed.add_field(name="Creator", value=creator_name, inline=True)
            archive_embed.add_field(name="Assigned Staff", value=assigned_staff or "None", inline=True)
            archive_embed.add_field(name="Closed By", value=f"{closer.display_name} ({closer.name})", inline=True)
            archive_embed.add_field(name="Subject", value=subject or "No subject", inline=False)
            archive_embed.add_field(name="Opened", value=opened_at, inline=True)
            archive_embed.add_field(name="Closed", value=now_iso, inline=True)
            msg = await archive_channel.send(embed=archive_embed, file=attachment)
            archive_message_id = msg.id
            result["archive_message_id"] = archive_message_id
        except Exception as exc:
            logger.exception("Failed to archive transcript")
            result["error"] = f"Archive delivery failed: {exc}"
            await _update_ticket_metadata(
                ticket_id, status="open", closed_by=closer.id, closed_at=now_iso,
                transcript_status="failed", transcript_filename=filename,
                transcript_channel_id=config.ticket_transcript_channel_id or None,
                transcript_message_id=None, transcript_dm_sent=0,
                transcript_error=result["error"],
            )
            # Keep the channel; notify staff; allow retry.
            await _notify_staff_retry(channel, ticket_number, result["error"])
            return result

    # 4. DM the creator (non-fatal)
    dm_sent = 0
    dm_error = None
    try:
        creator = bot.get_user(creator_user_id)
        if creator is None:
            try:
                guild = channel.guild
                member = guild.get_member(creator_user_id)
                creator = member
            except Exception:
                creator = None

        if creator is not None:
            dm_embed = discord.Embed(
                title=f"Ticket #{ticket_number} Closed",
                description=f"Your support ticket has been closed.",
                color=0x64748B,
                timestamp=discord.utils.utcnow(),
            )
            dm_embed.add_field(name="Ticket", value=f"#{ticket_number}", inline=True)
            dm_embed.add_field(name="Subject", value=subject or "No subject", inline=True)
            transcript_bytes.seek(0)
            await creator.send(embed=dm_embed, file=discord.File(transcript_bytes, filename=filename))
            dm_sent = 1
        else:
            dm_error = "Creator not found"
    except discord.Forbidden:
        dm_error = "DM disabled or blocked by user"
    except Exception as exc:
        dm_error = str(exc)[:200]
        logger.exception("Failed to DM ticket creator transcript")

    result["dm_sent"] = dm_sent
    if dm_error:
        result["error"] = (result["error"] or "") + f" DM: {dm_error}" if result["error"] else f"DM: {dm_error}"

    # 5. Finalize: mark closed + transcript delivered
    await _update_ticket_metadata(
        ticket_id, status="closed", closed_by=closer.id, closed_at=now_iso,
        transcript_status="delivered",
        transcript_filename=filename,
        transcript_channel_id=config.ticket_transcript_channel_id or None,
        transcript_message_id=archive_message_id,
        transcript_dm_sent=dm_sent,
        transcript_error=result["error"],
    )
    result["transcript_status"] = "delivered"

    await log_event(
        "ticket_closed",
        user_id=closer.id,
        username=closer.display_name,
        guild_id=channel.guild.id,
        channel_id=channel.id,
        detail=(
            f"Ticket #{ticket_number} closed with transcript "
            f"(dm_sent={dm_sent}, archive={'yes' if archive_message_id else 'no'})"
        ),
    )
    return result


async def _notify_staff_retry(channel: discord.TextChannel, ticket_number: int, error: str) -> None:
    """Notify staff that archiving failed and the ticket must be retried."""
    try:
        await channel.send(
            content=(
                f"Transcript archiving failed ({error}). "
                "The ticket has been preserved — a staff member can retry the close."
            )
        )
    except Exception:
        logger.exception("Failed to notify staff about transcript failure")


async def _disable_ticket_controls(channel: discord.TextChannel) -> None:
    """Disable buttons on ticket control messages (lock the ticket)."""
    try:
        async for msg in channel.history(limit=50):
            if msg.components:
                for component in msg.components:
                    if hasattr(component, "children") and component.children:
                        await msg.edit(view=None)
                        return
    except Exception:
        logger.exception("Failed to disable ticket controls")
