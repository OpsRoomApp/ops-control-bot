"""
OPS CONTROL - Desktop Integration Events API

API layer for OPS ROOM Desktop App to Discord integration.

Architecture:
    OPS ROOM Desktop App --authenticated--> /api/events --stored--> events table

Supported events:
    flight_started, flight_completed, flight_cancelled,
    crash_detected, update_available, telemetry_snapshot
"""

from __future__ import annotations

import json
import logging
from typing import Any

from bot.database import get_db
from bot.utils.helpers import utc_now_iso

logger = logging.getLogger("ops_control.api.events")

_AUTH_TOKENS: set[str] = set()


def configure_auth(tokens: list[str]) -> None:
    """Register valid API tokens for the events endpoint."""
    global _AUTH_TOKENS
    _AUTH_TOKENS = set(tokens)


EVENT_HANDLERS: dict[str, str] = {
    "flight_started": "User started a new flight in the simulator.",
    "flight_completed": "User completed a flight (parking brake set at gate).",
    "flight_cancelled": "User cancelled an active flight.",
    "crash_detected": "OPS ROOM detected a simulator crash or unexpected exit.",
    "update_available": "A new OPS ROOM version is available.",
    "telemetry_snapshot": "Periodic telemetry snapshot from active flight.",
}


async def process_event(
    user_id: int,
    event_type: str,
    callsign: str | None = None,
    aircraft: str | None = None,
    route: str | None = None,
    version: str | None = None,
    payload: dict[str, Any] | None = None,
) -> bool:
    """Process an incoming event from the OPS ROOM Desktop App.

    Stores timestamp, user, callsign, aircraft, route, version,
    and structured payload in the events table.
    """
    try:
        db = await get_db()
        payload_json = json.dumps(payload) if payload else None

        await db.execute(
            """
            INSERT INTO events
                (user_id, event_type, callsign, aircraft, route, version, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (user_id, event_type, callsign, aircraft, route, version, payload_json, utc_now_iso()),
        )
        await db.commit()
        logger.info(
            "Event processed: %s [%s -> %s] user=%s",
            event_type, callsign or "-", route or "-", user_id,
        )
        return True

    except Exception:
        logger.exception("Failed to process event: %s", event_type)
        return False


def get_supported_events() -> dict[str, str]:
    """Return supported event types and descriptions."""
    return dict(EVENT_HANDLERS)
