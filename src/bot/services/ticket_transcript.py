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
import json
import logging
import re
from datetime import datetime, timezone
from io import BytesIO
from typing import TYPE_CHECKING, Any

import aiohttp
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
    close_reason: str | None = None,
    transcript_url: str | None = None,
) -> None:
    db = await get_db()
    await db.execute(
        """
        UPDATE tickets
        SET status = ?, closed_by = ?, closed_at = ?, updated_at = ?,
            transcript_status = ?, transcript_filename = ?,
            transcript_channel_id = ?, transcript_message_id = ?,
            transcript_dm_sent = ?, transcript_error = ?,
            close_reason = ?, transcript_url = ?
        WHERE id = ?
        """,
        (
            status, closed_by, closed_at, utc_now_iso(),
            transcript_status, transcript_filename,
            transcript_channel_id, transcript_message_id,
            transcript_dm_sent, transcript_error,
            close_reason, transcript_url,
            ticket_id,
        ),
    )
    await db.commit()


async def _post_transcript_to_admin_api(payload: dict[str, Any]) -> tuple[bool, str, str | None]:
    """POST a transcript payload to the admin API hosted-transcript endpoint.

    Returns (ok, message, transcript_url). transcript_url is the full public
    URL (e.g. https://opsroom.live/transcripts/{id}) on success, else None.
    """
    base_url = (config.admin_api_base_url or "").rstrip("/")
    token = config.admin_api_token or ""
    if not base_url or not token:
        return False, "Hosted transcripts disabled (ADMIN_API_BASE_URL / ADMIN_API_TOKEN not set)", None

    url = f"{base_url}/api/v1/transcripts/store"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status == 401 or resp.status == 403:
                    body = await _safe_body(resp)
                    return False, f"admin-api rejected the request (HTTP {resp.status}): {body}", None
                if resp.status >= 400:
                    body = await _safe_body(resp)
                    return False, f"admin-api error (HTTP {resp.status}): {body}", None
                try:
                    data = await resp.json()
                except Exception:
                    return False, "admin-api returned malformed JSON", None
                rel = data.get("url") or ""
                if not data.get("ok") or not rel:
                    return False, "admin-api did not return a transcript URL", None
                full_url = f"https://opsroom.live{rel}" if rel.startswith("/") else rel
                return True, "", full_url
    except aiohttp.ClientConnectionError as exc:
        return False, f"admin-api unreachable: {exc}", None
    except aiohttp.ServerTimeoutError as exc:
        return False, f"admin-api timeout: {exc}", None
    except Exception as exc:
        return False, f"admin-api POST failed: {exc}", None


async def _safe_body(resp: aiohttp.ClientResponse) -> str:
    try:
        text = await resp.text()
        return text[:200]
    except Exception:
        return "(no body)"


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
    close_reason: str | None = None,
) -> dict[str, Any]:
    """Close a ticket channel with a full transcript workflow.

    Delivery order:
      1. Hosted transcript (POST to admin-api) -- preferred.
      2. Discord archive-channel upload -- fallback so a transcript is
         never silently lost if the website endpoint is unreachable.

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
        "transcript_url": None,
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
            close_reason=close_reason,
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

    # 3. Hosted transcript delivery (preferred, B1/C1).
    # POST the transcript payload to the admin API. On success the public
    # transcript link replaces the raw-HTML-upload-to-Discord behaviour.
    hosted_ok = False
    hosted_url: str | None = None
    hosted_error: str | None = None
    transcript_payload = {
        "ticket_id": ticket_id,
        "ticket_number": ticket_number,
        "creator_name": creator_name,
        "subject": subject or "No subject",
        "priority": priority,
        "assigned_staff": assigned_staff,
        "opened_at": opened_at,
        "closed_at": now_iso,
        "closed_by": closer.display_name or closer.name,
        "close_reason": close_reason,
        "channel_name": channel.name,
        "messages": messages,
    }
    hosted_ok, hosted_error, hosted_url = await _post_transcript_to_admin_api(transcript_payload)
    if hosted_ok and hosted_url:
        result["transcript_url"] = hosted_url
        logger.info("Ticket #%s transcript stored at %s", ticket_number, hosted_url)
    else:
        # Distinguish "unreachable" from "rejected" in the log.
        logger.warning(
            "Hosted transcript delivery failed for ticket #%s (%s) -- falling back to Discord upload",
            ticket_number,
            hosted_error,
        )

    # 4. Archive channel delivery (fallback and/or archive copy).
    # If hosted delivery succeeded we post a clean link embed instead of the
    # raw HTML dump. If hosted delivery failed AND an archive channel is
    # configured, fall back to uploading the HTML file so the transcript is
    # never silently lost. A missing archive channel is only fatal when
    # hosted delivery also failed.
    archive_channel = None
    archive_error = None
    if config.ticket_transcript_channel_id:
        archive_channel = bot.get_channel(config.ticket_transcript_channel_id)
        if not isinstance(archive_channel, discord.TextChannel):
            archive_channel = None
            archive_error = f"Archive channel {config.ticket_transcript_channel_id} not found"
            logger.warning("Transcript archive channel %s not found", config.ticket_transcript_channel_id)
    elif not hosted_ok:
        archive_error = "Transcript archive channel is not configured (TICKET_TRANSCRIPT_CHANNEL_ID)"

    if archive_error and not hosted_ok:
        result["error"] = archive_error
        await _update_ticket_metadata(
            ticket_id, status="open", closed_by=closer.id, closed_at=now_iso,
            transcript_status="failed", transcript_filename=filename,
            transcript_channel_id=config.ticket_transcript_channel_id or None,
            transcript_message_id=None, transcript_dm_sent=0,
            transcript_error=result["error"],
            close_reason=close_reason,
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
            if hosted_ok and hosted_url:
                archive_embed.add_field(
                    name="Transcript",
                    value=f"[View hosted transcript]({hosted_url}) · expires in {getattr(config, 'transcript_retention_days', 14)} days",
                    inline=False,
                )
                if close_reason:
                    archive_embed.add_field(name="Close Reason", value=close_reason[:1024], inline=False)
                msg = await archive_channel.send(embed=archive_embed)
            else:
                # Fallback: upload the raw HTML transcript.
                if close_reason:
                    archive_embed.add_field(name="Close Reason", value=close_reason[:1024], inline=False)
                msg = await archive_channel.send(embed=archive_embed, file=attachment)
            archive_message_id = msg.id
            result["archive_message_id"] = archive_message_id
        except Exception as exc:
            logger.exception("Failed to archive transcript")
            if not hosted_ok:
                result["error"] = f"Archive delivery failed: {exc}"
                await _update_ticket_metadata(
                    ticket_id, status="open", closed_by=closer.id, closed_at=now_iso,
                    transcript_status="failed", transcript_filename=filename,
                    transcript_channel_id=config.ticket_transcript_channel_id or None,
                    transcript_message_id=None, transcript_dm_sent=0,
                    transcript_error=result["error"],
                    close_reason=close_reason,
                )
                # Keep the channel; notify staff; allow retry.
                await _notify_staff_retry(channel, ticket_number, result["error"])
                return result
            # Hosted delivery already succeeded -- archive failure is logged
            # but not fatal (the transcript is durably stored on the website).
            logger.warning("Archive channel copy failed but hosted transcript exists for ticket #%s", ticket_number)

    # 5. DM the creator (non-fatal)
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
            if hosted_ok and hosted_url:
                dm_embed.add_field(
                    name="Transcript",
                    value=f"[View transcript]({hosted_url}) · expires in {getattr(config, 'transcript_retention_days', 14)} days",
                    inline=False,
                )
                await creator.send(embed=dm_embed)
            else:
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

    # 6. Finalize: mark closed + transcript delivered
    await _update_ticket_metadata(
        ticket_id, status="closed", closed_by=closer.id, closed_at=now_iso,
        transcript_status="delivered",
        transcript_filename=filename,
        transcript_channel_id=config.ticket_transcript_channel_id or None,
        transcript_message_id=archive_message_id,
        transcript_dm_sent=dm_sent,
        transcript_error=result["error"],
        close_reason=close_reason,
        transcript_url=hosted_url,
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
