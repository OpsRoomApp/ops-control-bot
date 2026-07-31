"""
OPS CONTROL - Audit Logging Service

Writes structured audit entries to the database `logs` table.
Used by cogs to record command executions, joins, and system events.
"""

from __future__ import annotations

import logging
from typing import Any

from bot.database import get_db
from bot.utils.helpers import utc_now_iso

logger = logging.getLogger("ops_control.services.audit")


async def log_event(
    event_type: str,
    *,
    user_id: int | None = None,
    username: str | None = None,
    guild_id: int | None = None,
    channel_id: int | None = None,
    detail: str | None = None,
) -> None:
    """
    Record an audit event in the database.

    Args:
        event_type: One of 'command', 'join', 'error', 'api_failure', 'announce', 'notam'.
        user_id: Discord user ID of the actor.
        username: Human-readable name of the actor.
        guild_id: Guild where the event occurred.
        channel_id: Channel where the event occurred.
        detail: Human-readable description of the event.
    """
    try:
        db = await get_db()
        await db.execute(
            """
            INSERT INTO logs (event_type, user_id, username, guild_id, channel_id, detail, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_type,
                user_id,
                username,
                guild_id,
                channel_id,
                detail,
                utc_now_iso(),
            ),
        )
        await db.commit()
    except Exception:
        logger.exception("Failed to write audit log entry: %s", event_type)
