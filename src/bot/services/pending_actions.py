"""
OPS CONTROL - Pending Action Dispatcher

Reliable, persistent background service that bridges the OPS ROOM admin
panel to Discord:

    Admin Panel -> Admin API -> SQLite pending_actions -> Bot Dispatcher -> Discord

The dispatcher polls the shared `pending_actions` table (the same SQLite
database used by the Admin API) and executes each action against Discord.

State machine:
    pending -> processing -> completed
    pending -> processing -> pending   (transient failure, bounded retry)
    pending -> processing -> failed    (terminal failure / retry exhausted)

Supported action types (canonical):
    announcement, scheduled_announcement,
    add_verified, remove_verified, add_beta, remove_beta,
    ticket_assign, ticket_close, ticket_reopen

Legacy action types (still processed, marked for migration):
    announce_dispatch  -> announcement
    beta_role_change   -> payload["action"] decides verified/beta add/remove
    ticket_state_change-> payload["action"] decides assign/close/reopen
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

import discord

from bot.config import config
from bot.database import get_db
from bot.utils.helpers import utc_now_iso

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger("ops_control.services.pending_actions")


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


async def process_pending_actions(bot: commands.Bot) -> int:
    """Process all currently-due pending actions.

    Claims each action atomically (pending -> processing) so duplicate
    processing is impossible even if two bot instances share the DB.

    Returns the number of actions processed (completed + failed terminal).
    """
    db = await get_db()
    now = utc_now_iso()

    # Recover rows stuck in 'processing' by a previously crashed process
    # (e.g. crash between claim and completion). Anything still in
    # 'processing' after a generous window is reclaimed for retry.
    # Uses julianday() because processing_started_at is ISO-8601 with a
    # timezone offset, which would otherwise never compare below SQLite's
    # space-separated datetime('now', ...) output.
    await db.execute(
        """
        UPDATE pending_actions
        SET status = 'pending',
            error = 'Recovered: previous attempt did not complete',
            processing_started_at = NULL
        WHERE status = 'processing'
          AND julianday(processing_started_at) < julianday('now', '-15 minutes')
        """
    )
    await db.commit()

    # Claim due actions in ascending ID order. Scheduled actions whose
    # scheduled_at is still in the future are left pending.
    cursor = await db.execute(
        """
        SELECT id, action_type, payload_json
        FROM pending_actions
        WHERE status = 'pending'
          AND (scheduled_at IS NULL OR scheduled_at <= ?)
        ORDER BY id ASC
        LIMIT 20
        """,
        (now,),
    )
    rows = await cursor.fetchall()

    processed = 0
    for row in rows:
        action_id = row["id"]
        action_type = row["action_type"]
        raw = row["payload_json"]

        # --- Atomic claim: pending -> processing, increment attempts ---
        claim = await db.execute(
            """
            UPDATE pending_actions
            SET status = 'processing',
                processing_started_at = ?,
                attempts = attempts + 1,
                error = NULL
            WHERE id = ? AND status = 'pending'
            """,
            (now, action_id),
        )
        await db.commit()
        if claim.rowcount == 0:
            # Another dispatcher instance claimed this action - skip it.
            logger.info(
                "Pending action %s claimed by another process - skipping", action_id
            )
            continue

        # Re-read to get the incremented attempt count.
        cursor2 = await db.execute(
            "SELECT attempts FROM pending_actions WHERE id = ?", (action_id,)
        )
        attempt_row = await cursor2.fetchone()
        attempts = attempt_row["attempts"] if attempt_row else 0

        logger.info("Processing pending action %s: %s", action_id, action_type)

        # --- Payload parsing (malformed payloads are terminal) ---
        try:
            payload: dict[str, Any] = json.loads(raw) if raw else {}
        except (json.JSONDecodeError, TypeError) as exc:
            await _mark_terminal(
                action_id, f"Malformed payload_json: {exc}"
            )
            processed += 1
            continue

        # --- Unknown action types are terminal (never retry) ---
        if action_type not in KNOWN_ACTION_TYPES:
            await _mark_terminal(
                action_id, f"Unknown action type: {action_type}"
            )
            processed += 1
            continue

        # --- Execute ---
        try:
            result = await _execute(bot, action_type, payload)
            await db.execute(
                """
                UPDATE pending_actions
                SET status = 'completed',
                    processed_at = ?,
                    error = NULL,
                    result_json = ?
                WHERE id = ?
                """,
                (utc_now_iso(), _safe_json(result), action_id),
            )
            await db.commit()
            processed += 1
            logger.info("Pending action %s completed", action_id)

        except Exception as exc:
            max_attempts = max(1, config.pending_action_max_attempts)
            if attempts >= max_attempts:
                await _mark_terminal(action_id, str(exc))
                processed += 1
            else:
                # Transient failure: revert to pending for a bounded retry.
                await db.execute(
                    """
                    UPDATE pending_actions
                    SET status = 'pending',
                        error = ?,
                        processing_started_at = NULL
                    WHERE id = ?
                    """,
                    (str(exc)[:500], action_id),
                )
                await db.commit()
                logger.warning(
                    "Retrying pending action %s (attempt %s/%s): %s",
                    action_id,
                    attempts,
                    max_attempts,
                    exc,
                )

    return processed


async def _mark_terminal(action_id: int, error: str) -> None:
    """Mark an action as failed (terminal). Never raises."""
    db = await get_db()
    try:
        await db.execute(
            """
            UPDATE pending_actions
            SET status = 'failed',
                processed_at = ?,
                error = ?
            WHERE id = ?
            """,
            (utc_now_iso(), error[:500], action_id),
        )
        await db.commit()
    except Exception:
        logger.exception("Failed to mark pending action %s as failed", action_id)
    logger.error("Pending action %s failed: %s", action_id, error)


def _safe_json(value: Any) -> str | None:
    """JSON-encode a result dict, falling back to None."""
    try:
        return json.dumps(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Action handlers
# ---------------------------------------------------------------------------

# Canonical + legacy action types the dispatcher understands. Anything not
# in this set is marked failed immediately (it can never succeed on retry).
KNOWN_ACTION_TYPES: frozenset[str] = frozenset({
    "announcement", "scheduled_announcement",
    "add_verified", "remove_verified", "add_beta", "remove_beta",
    "ticket_assign", "ticket_close", "ticket_reopen",
    "moderation_reverse",  # C2 -- appeal approval reverses ban/timeout
    "announce_dispatch", "beta_role_change", "ticket_state_change",
    "flight_event",  # community: takeoff/landing notification from the desktop app
    "roadmap_update",  # v0.26: roadmap publish from the admin panel
    "feedback_new",    # v0.26: feedback/feature-request forum thread
})


async def _execute(bot: commands.Bot, action_type: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Route a single action to its handler. Unknown types fail cleanly."""
    handlers: dict[str, Any] = {
        # Canonical announcement types
        "announcement": _dispatch_announcement,
        "scheduled_announcement": _dispatch_announcement,
        # Canonical beta role types
        "add_verified": lambda b, p: _dispatch_beta_role(b, p, "add_verified"),
        "remove_verified": lambda b, p: _dispatch_beta_role(b, p, "remove_verified"),
        "add_beta": lambda b, p: _dispatch_beta_role(b, p, "add_beta"),
        "remove_beta": lambda b, p: _dispatch_beta_role(b, p, "remove_beta"),
        # Canonical ticket types
        "ticket_assign": _dispatch_ticket_assign,
        "ticket_close": _dispatch_ticket_close,
        "ticket_reopen": _dispatch_ticket_reopen,
        # C2 -- appeal approval reverses a ban or timeout on Discord
        "moderation_reverse": _dispatch_moderation_reverse,
        # Community flight events (desktop app -> Discord)
        "flight_event": _dispatch_flight_event,
        # v0.26: roadmap publish + feedback forum threads (admin panel / app)
        "roadmap_update": _dispatch_roadmap,
        "feedback_new": _dispatch_feedback_new,
        # Legacy aliases (processed for backwards compatibility)
        "announce_dispatch": _dispatch_announcement,
        "beta_role_change": _dispatch_legacy_beta,
        "ticket_state_change": _dispatch_legacy_ticket,
    }
    handler = handlers.get(action_type)
    if handler is None:
        raise ValueError(f"Unknown action type: {action_type}")
    return await handler(bot, payload)


async def _dispatch_announcement(bot: commands.Bot, payload: dict[str, Any]) -> dict[str, Any]:
    """Send an announcement embed to a Discord channel.

    Payload: title, content, channel_id, optional embed_color / image_url /
    announcement_id / scheduled_at. When scheduled_at is in the future the
    claim query keeps the row pending until it is due.
    """
    title = str(payload.get("title", "")).strip()
    content = str(payload.get("content", "")).strip()
    channel_id = int(payload.get("channel_id", 0) or 0)
    embed_color = payload.get("embed_color") or "#2563EB"
    image_url = payload.get("image_url")

    if not title or not content or not channel_id:
        raise ValueError("Missing title, content, or channel_id in announcement payload")

    try:
        color = int(str(embed_color).lstrip("#"), 16)
    except ValueError:
        color = 0x2563EB

    channel = bot.get_channel(channel_id)
    if not channel or not isinstance(channel, discord.TextChannel):
        raise ValueError(f"channel not found: {channel_id}")

    embed = discord.Embed(title=title, description=content, color=color)
    embed.set_author(name="OPS ROOM Operations")
    embed.set_footer(text="Announced via OPS ROOM Admin Panel")
    if image_url:
        embed.set_image(url=image_url)

    msg = await channel.send(embed=embed)
    logger.info("Announcement sent to #%s", channel.name)

    result: dict[str, Any] = {"message_id": msg.id, "channel_id": channel_id}

    # Reflect status back into discord_announcements for the admin panel.
    announcement_id = payload.get("announcement_id")
    if announcement_id:
        db = await get_db()
        await db.execute(
            """
            UPDATE discord_announcements
            SET status = 'completed', announced_at = ?, channel_id = ?
            WHERE id = ?
            """,
            (utc_now_iso(), channel_id, int(announcement_id)),
        )
        await db.commit()

    return result


async def _dispatch_roadmap(bot: commands.Bot, payload: dict[str, Any]) -> dict[str, Any]:
    """Post a roadmap update embed to the roadmap channel.

    Payload: sprint, revision, planned / in_progress / completed (title
    lists), optional channel_id. The channel falls back to
    config.discord_roadmap_channel_id when not supplied.
    """
    sprint = str(payload.get("sprint") or "").strip()
    revision = int(payload.get("revision") or 0)
    planned = [str(x) for x in (payload.get("planned") or []) if str(x).strip()]
    in_progress = [str(x) for x in (payload.get("in_progress") or []) if str(x).strip()]
    completed = [str(x) for x in (payload.get("completed") or []) if str(x).strip()]

    channel_id = int(payload.get("channel_id") or 0) or config.discord_roadmap_channel_id
    if not channel_id:
        raise ValueError("No roadmap channel configured")
    channel = bot.get_channel(channel_id)
    if not channel or not isinstance(channel, discord.TextChannel):
        raise ValueError(f"roadmap channel not found: {channel_id}")

    def _field(title_list: list[str]) -> str:
        text = "\n".join(f"- {item}" for item in title_list) or "- None"
        return text[:1024]

    embed = discord.Embed(
        title="OPS ROOM Roadmap Updated",
        color=0x059669,
        description=f"Current Sprint: {sprint or 'Unknown'}" + (f" · revision {revision}" if revision else ""),
    )
    # Sections are sent In Progress -> Planned -> Completed (active work
    # first, finished work last).
    embed.add_field(name="In Progress", value=_field(in_progress), inline=False)
    embed.add_field(name="Planned", value=_field(planned), inline=False)
    embed.add_field(name="Completed", value=_field(completed), inline=False)
    embed.set_footer(text="Pushed from the OPS ROOM admin panel")

    msg = await channel.send(embed=embed)
    logger.info("Roadmap update posted to #%s (revision %s)", channel.name, revision)
    return {"message_id": msg.id, "channel_id": channel_id, "revision": revision}


async def _dispatch_feedback_new(bot: commands.Bot, payload: dict[str, Any]) -> dict[str, Any]:
    """Open a feedback/feature-request forum thread from the admin/app pipeline.

    Payload: feedback_id, kind, title, description, optional contact and
    forum_channel_id. The forum falls back to config.feedback_forum_channel_id.
    """
    feedback_id = str(payload.get("feedback_id") or "")
    kind = str(payload.get("kind") or "feedback").strip().lower()
    title = str(payload.get("title") or "Untitled").strip()
    description = str(payload.get("description") or "").strip()
    contact = str(payload.get("contact") or "").strip()

    forum_id = int(payload.get("forum_channel_id") or 0) or config.feedback_forum_channel_id
    if not forum_id:
        raise ValueError("No feedback forum configured")
    forum = bot.get_channel(forum_id)
    if not forum or not isinstance(forum, discord.ForumChannel):
        raise ValueError(f"feedback forum not found: {forum_id}")

    kind_label = {
        "feedback": "Feedback",
        "feature_request": "Feature Request",
        "bug": "Bug Report",
    }.get(kind, "Feedback")
    thread_name = f"[{kind_label}] {title}"[:100]

    embed = discord.Embed(title=title, color=0x3B82F6, timestamp=discord.utils.utcnow())
    embed.add_field(name="Type", value=kind_label, inline=True)
    embed.add_field(name="Submitted", value="via OPS ROOM", inline=True)
    embed.add_field(name="Details", value=(description or "-")[:1024], inline=False)
    if contact:
        embed.add_field(name="Contact", value=contact[:100], inline=False)
    if feedback_id:
        embed.set_footer(text=f"{feedback_id} · feedback is reviewed and routed from the admin panel")

    thread = await forum.create_thread(
        name=thread_name,
        content=f"New **{kind_label.lower()}** submitted.",
        embed=embed,
    )
    logger.info("Feedback thread opened in %s: %s", forum.name, thread_name)
    return {"thread_id": getattr(thread, "id", None), "forum_id": forum_id, "feedback_id": feedback_id}


async def _dispatch_beta_role(
    bot: commands.Bot,
    payload: dict[str, Any],
    action: str,
) -> None:
    """Apply a fine-grained beta role change for one member."""
    discord_id = int(payload.get("discord_id", 0) or 0)
    if not discord_id:
        raise ValueError("Missing discord_id")

    guild = bot.get_guild(config.guild_id)
    if not guild:
        raise ValueError(f"Guild {config.guild_id} not found")

    member = guild.get_member(discord_id)
    if not member:
        try:
            member = await guild.fetch_member(discord_id)
        except Exception:
            raise ValueError(f"Member {discord_id} not found in guild")

    role_id = config.verified_tester_role_id if "verified" in action else config.public_beta_role_id
    if not role_id:
        raise ValueError(f"Role not configured for action {action}")

    role = guild.get_role(role_id)
    if not role:
        raise ValueError(f"Role {role_id} not found in guild")

    reason = f"Beta role {action} via admin panel"
    if action.startswith("add"):
        if role not in member.roles:
            await member.add_roles(role, reason=reason)
            logger.info("Added role %s to %s", role.name, member.name)
    else:
        if role in member.roles:
            await member.remove_roles(role, reason=reason)
            logger.info("Removed role %s from %s", role.name, member.name)


async def _dispatch_legacy_beta(bot: commands.Bot, payload: dict[str, Any]) -> None:
    """Legacy beta_role_change -> route based on payload['action']."""
    action = str(payload.get("action", "add")).lower()
    mapped = {
        "add_verified": "add_verified",
        "remove_verified": "remove_verified",
        "add_beta": "add_beta",
        "remove_beta": "remove_beta",
        "add": "add_verified",  # legacy coarse add = both roles
        "remove": "remove_verified",
    }.get(action, action)
    if mapped in ("add", "remove"):
        raise ValueError(f"Unknown beta action: {action}")
    await _dispatch_beta_role(bot, payload, mapped)


async def _dispatch_moderation_reverse(bot: commands.Bot, payload: dict[str, Any]) -> dict[str, Any]:
    """Reverse a moderation action after an appeal is approved (C2).

    Payload: discord_id, reverse_action ('ban'|'timeout'|'mute'), appeal_id,
    optional resolution. Unbans the user or clears their timeout on Discord.
    A ban is the only action that must be reversed explicitly -- timeouts
    naturally expire, but an approved appeal should clear them immediately.
    """
    discord_id = int(payload.get("discord_id", 0) or 0)
    if not discord_id:
        raise ValueError("Missing discord_id in moderation_reverse payload")

    guild = bot.get_guild(config.guild_id)
    if not guild:
        raise ValueError(f"Guild {config.guild_id} not found")

    reverse_action = str(payload.get("reverse_action") or "ban").lower()
    appeal_id = payload.get("appeal_id")
    resolution = str(payload.get("resolution") or "Appeal approved")

    result: dict[str, Any] = {"discord_id": discord_id, "reverse_action": reverse_action}

    if reverse_action == "ban" or reverse_action == "unban":
        try:
            ban_entry = await guild.fetch_ban(discord.Object(id=discord_id))
            await guild.unban(ban_entry.user, reason=f"Appeal #{appeal_id} approved: {resolution}")
            result["unbanned"] = True
            logger.info("Appeal #%s: unbanned %s", appeal_id, discord_id)
        except discord.NotFound:
            result["unbanned"] = False
            result["note"] = "No ban found (already unbanned)"
        except discord.Forbidden as exc:
            raise ValueError(f"Cannot unban {discord_id}: missing permissions ({exc})")

    elif reverse_action in ("timeout", "untimeout"):
        member = guild.get_member(discord_id)
        if member is None:
            try:
                member = await guild.fetch_member(discord_id)
            except Exception:
                member = None
        if member is not None:
            await member.timeout(None, reason=f"Appeal #{appeal_id} approved: {resolution}")
            result["timeout_cleared"] = True
            logger.info("Appeal #%s: cleared timeout for %s", appeal_id, discord_id)
        else:
            result["timeout_cleared"] = False
            result["note"] = "Member not in guild (timeout cleared automatically on leave)"

    elif reverse_action == "mute":
        member = guild.get_member(discord_id)
        if member is not None and config.muted_role_id:
            role = guild.get_role(config.muted_role_id)
            if role and role in member.roles:
                await member.remove_roles(role, reason=f"Appeal #{appeal_id} approved: {resolution}")
                result["mute_removed"] = True
        db = await get_db()
        await db.execute(
            "UPDATE moderation_cases SET active=0 WHERE user_id=? AND action_type='MUTE' AND active=1",
            (discord_id,),
        )
        await db.commit()
        result["mute_removed"] = result.get("mute_removed", False) or True

    else:
        raise ValueError(f"Unknown reverse_action: {reverse_action}")

    return result


async def _dispatch_ticket_assign(bot: commands.Bot, payload: dict[str, Any]) -> None:
    """Admin-initiated ticket assignment (DB + optional channel note)."""
    ticket_id = int(payload.get("ticket_id", 0) or 0)
    assigned_to = int(payload.get("assigned_to", 0) or 0)
    if not ticket_id:
        raise ValueError("Missing ticket_id")

    db = await get_db()
    await db.execute(
        "UPDATE tickets SET assigned_to = ?, updated_at = ? WHERE id = ?",
        (assigned_to or None, utc_now_iso(), ticket_id),
    )
    await db.commit()
    logger.info("Ticket %s assigned to %s via admin panel", ticket_id, assigned_to or "nobody")


async def _dispatch_ticket_close(bot: commands.Bot, payload: dict[str, Any]) -> None:
    """Admin-initiated ticket close (DB status + optional channel post)."""
    ticket_id = int(payload.get("ticket_id", 0) or 0)
    if not ticket_id:
        raise ValueError("Missing ticket_id")

    db = await get_db()
    await db.execute(
        "UPDATE tickets SET status = 'closed', closed_at = ?, updated_at = ? WHERE id = ?",
        (utc_now_iso(), utc_now_iso(), ticket_id),
    )
    await db.commit()

    channel_id = payload.get("channel_id")
    if channel_id:
        channel = bot.get_channel(int(channel_id))
        if channel and isinstance(channel, discord.TextChannel):
            embed = discord.Embed(
                title="Ticket Closed",
                description=f"Ticket **#{ticket_id}** was closed from the admin panel.",
                color=0xDC2626,
            )
            await channel.send(embed=embed)
    logger.info("Ticket %s closed via admin panel", ticket_id)


async def _dispatch_ticket_reopen(bot: commands.Bot, payload: dict[str, Any]) -> None:
    """Admin-initiated ticket reopen (DB status)."""
    ticket_id = int(payload.get("ticket_id", 0) or 0)
    if not ticket_id:
        raise ValueError("Missing ticket_id")

    db = await get_db()
    await db.execute(
        "UPDATE tickets SET status = 'open', updated_at = ? WHERE id = ?",
        (utc_now_iso(), ticket_id),
    )
    await db.commit()
    logger.info("Ticket %s reopened via admin panel", ticket_id)


async def _dispatch_legacy_ticket(bot: commands.Bot, payload: dict[str, Any]) -> None:
    """Legacy ticket_state_change -> route based on payload['action']."""
    action = str(payload.get("action", "")).lower()
    if action == "close":
        await _dispatch_ticket_close(bot, payload)
    elif action == "reopen":
        await _dispatch_ticket_reopen(bot, payload)
    elif action == "assign":
        await _dispatch_ticket_assign(bot, payload)
    else:
        # Legacy no-op: the admin panel already updated the DB directly.
        logger.info("Legacy ticket_state_change with action=%r (no-op)", action)


async def _dispatch_flight_event(bot: commands.Bot, payload: dict[str, Any]) -> dict[str, Any]:
    """Post a takeoff/landing event to the community flights channel.

    Payload comes from the OPS ROOM desktop app (via the admin API). It is
    flight-data only: callsign, aircraft, route, and landing metrics. The
    Discord user is resolved by the admin API into ``discord_id`` before the
    action is enqueued, so the bot never stores or sends anything personal.

    Landing events are also mirrored into ``flight_logs`` so they feed the
    leaderboard and ``/logbook``. Takeoff/landing de-duplication is keyed on
    ``flight_id`` + ``event_type`` so a retried action never double-posts.
    """
    from bot.services.community import dispatch_flight_event
    return await dispatch_flight_event(bot, payload)


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------


async def pending_actions_loop(bot: commands.Bot) -> None:
    """Poll and dispatch pending actions indefinitely until the bot closes."""
    interval = max(2, config.pending_action_poll_seconds)
    logger.info("Pending action dispatcher started (interval: %ss)", interval)

    while not bot.is_closed():
        try:
            count = await process_pending_actions(bot)
            if count:
                logger.info("Dispatched %d pending actions", count)
        except asyncio.CancelledError:
            logger.info("Pending action dispatcher stopped.")
            raise
        except Exception:
            logger.exception("Error in pending actions loop - continuing")

        # Poll in short slices so shutdown is responsive.
        for _ in range(interval):
            if bot.is_closed():
                break
            await asyncio.sleep(1)
