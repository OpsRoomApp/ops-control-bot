"""OPS CONTROL - Community Flight Events Service

Dispatches takeoff/landing events from the OPS ROOM desktop app to Discord,
mirrors them into ``flight_logs`` for the leaderboard, generates the
post-landing PIREP card, and emits personal-best / milestone pings.

Privacy: only flight data is handled (callsign, aircraft, route, landing
metrics). The Discord user is resolved by the admin API into ``discord_id``
before the action is enqueued; nothing personal is collected here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

from bot.config import config
from bot.database import get_db
from bot.utils.helpers import utc_now_iso

if TYPE_CHECKING:
    from discord.ext import commands

logger = logging.getLogger("ops_control.services.community")

TAKEOFF_COLOR = 0x059669
LANDING_COLOR = 0x8B5CF6


def _f(value: Any) -> float | None:
    try:
        number = float(value)
        return number if number == number else None  # drop NaN
    except (TypeError, ValueError):
        return None


def _aircraft_label(payload: dict[str, Any]) -> str:
    aircraft = str(payload.get("aircraft") or "").strip()
    registration = str(payload.get("registration") or "").strip()
    if aircraft and registration:
        return f"{aircraft} · {registration}"
    return aircraft or registration or "N/A"


def _route_label(payload: dict[str, Any]) -> str:
    origin = str(payload.get("origin") or "").strip().upper()
    destination = str(payload.get("destination") or "").strip().upper()
    origin_name = str(payload.get("origin_name") or "").strip()
    destination_name = str(payload.get("destination_name") or "").strip()
    if origin and destination:
        label = f"{origin} → {destination}"
        if origin_name and origin_name not in ("N/A", ""):
            label = f"{origin} ({origin_name}) → {destination}"
        if destination_name and destination_name not in ("N/A", ""):
            label = f"{origin} ({origin_name}) → {destination} ({destination_name})"
        return label
    return str(payload.get("route") or "N/A")


async def _already_posted(flight_id: str, event_type: str) -> bool:
    """Return True if this flight+event already has a community_flights row."""
    if not flight_id:
        return False
    db = await get_db()
    cursor = await db.execute(
        "SELECT 1 FROM community_flights WHERE flight_id = ? AND event_type = ? LIMIT 1",
        (flight_id, event_type),
    )
    return await cursor.fetchone() is not None


async def _record_event(payload: dict[str, Any]) -> None:
    """Persist the event for de-duplication and analytics."""
    db = await get_db()
    await db.execute(
        """
        INSERT INTO community_flights
            (discord_id, flight_id, event_type, callsign, aircraft, registration,
             origin, origin_name, destination, destination_name, landing_rate,
             touchdown_g, touchdown_speed, duration_min, score, visibility, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(payload.get("discord_id") or 0),
            str(payload.get("flight_id") or ""),
            str(payload.get("event_type") or "unknown"),
            str(payload.get("callsign") or ""),
            str(payload.get("aircraft") or ""),
            str(payload.get("registration") or ""),
            str(payload.get("origin") or "").upper(),
            str(payload.get("origin_name") or ""),
            str(payload.get("destination") or "").upper(),
            str(payload.get("destination_name") or ""),
            _f(payload.get("landing_rate_fpm")),
            _f(payload.get("touchdown_g")),
            _f(payload.get("touchdown_speed_kts")),
            _f(payload.get("duration_min")),
            _f(payload.get("score")),
            str(payload.get("visibility") or "discord"),
            utc_now_iso(),
        ),
    )
    await db.commit()


async def _mirror_flight_log(payload: dict[str, Any]) -> int | None:
    """Mirror a landing into flight_logs (feeds /logbook + leaderboard)."""
    db = await get_db()
    username = str(payload.get("username") or "pilot")
    cursor = await db.execute(
        """
        INSERT INTO flight_logs
            (user_id, username, callsign, aircraft, departure, arrival, route,
             duration_min, landing_rate, score, registration, touchdown_g,
             visibility, submitted_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(payload.get("discord_id") or 0),
            username,
            str(payload.get("callsign") or ""),
            str(payload.get("aircraft") or ""),
            str(payload.get("origin") or "").upper(),
            str(payload.get("destination") or "").upper(),
            str(payload.get("route") or ""),
            _f(payload.get("duration_min")),
            _f(payload.get("landing_rate_fpm")),
            _f(payload.get("score")),
            str(payload.get("registration") or ""),
            _f(payload.get("touchdown_g")),
            str(payload.get("visibility") or "discord"),
            utc_now_iso(),
        ),
    )
    await db.commit()
    cursor = await db.execute("SELECT last_insert_rowid() AS id")
    row = await cursor.fetchone()
    return int(row["id"]) if row else None


async def dispatch_flight_event(bot: commands.Bot, payload: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a single takeoff/landing event (idempotent per flight+event)."""
    if not config.community_enabled:
        return {"ok": True, "skipped": "community disabled"}

    event_type = str(payload.get("event_type") or "").lower()
    if event_type not in ("takeoff", "landing"):
        raise ValueError(f"Unsupported flight event type: {event_type!r}")

    flight_id = str(payload.get("flight_id") or "")
    if await _already_posted(flight_id, event_type):
        logger.info("flight_event %s/%s already posted — skipping", flight_id, event_type)
        return {"ok": True, "skipped": "already_posted"}

    await _record_event(payload)

    # Post to the flights channel (or DM the user if no channel is set).
    channel = bot.get_channel(config.flights_channel_id)
    if channel is None or not isinstance(channel, discord.TextChannel):
        logger.warning("Flights channel %s not found", config.flights_channel_id)
        channel = None

    if event_type == "takeoff":
        embed = _takeoff_embed(payload)
        target = channel
    else:
        embed = _landing_embed(payload)
        target = channel
        await _mirror_flight_log(payload)

    if target is not None:
        try:
            await target.send(embed=embed)
        except discord.Forbidden:
            logger.warning("Cannot send flight event to channel %s", target.id)
        except discord.HTTPException:
            logger.exception("Failed to send flight event embed")

    # Post-landing extras: PIREP card DM + milestone/personal-best ping.
    if event_type == "landing":
        await _send_pirep_dm(bot, payload)
        await _send_milestone_ping(bot, payload)

    return {"ok": True, "event": event_type}


def _takeoff_embed(payload: dict[str, Any]) -> discord.Embed:
    embed = discord.Embed(
        title=f"✈️ Takeoff — {str(payload.get('callsign') or 'N/A')}",
        color=TAKEOFF_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.add_field(name="Route", value=_route_label(payload), inline=False)
    embed.add_field(name="Aircraft", value=_aircraft_label(payload), inline=True)
    if payload.get("origin") and payload.get("destination"):
        embed.add_field(
            name="Distance",
            value=f"{payload.get('distance_nm')} NM" if payload.get("distance_nm") else "—",
            inline=True,
        )
    embed.set_footer(text="OPS ROOM Flight Watch")
    return embed


def _landing_embed(payload: dict[str, Any]) -> discord.Embed:
    rate = _f(payload.get("landing_rate_fpm"))
    gforce = _f(payload.get("touchdown_g"))
    speed = _f(payload.get("touchdown_speed_kts"))

    lines = [
        f"**Route:** {_route_label(payload)}",
        f"**Aircraft:** {_aircraft_label(payload)}",
    ]
    metrics: list[str] = []
    if rate is not None:
        metrics.append(f"Landing rate **{rate:.0f} fpm**")
    if gforce is not None:
        metrics.append(f"{gforce:.2f} G")
    if speed is not None:
        metrics.append(f"{speed:.0f} kt")
    if metrics:
        lines.append("**" + " · ".join(metrics) + "**")

    embed = discord.Embed(
        title=f"🛬 Landing — {str(payload.get('callsign') or 'N/A')}",
        description="\n".join(lines),
        color=LANDING_COLOR,
        timestamp=discord.utils.utcnow(),
    )
    embed.set_footer(text="OPS ROOM Flight Watch")
    return embed


async def _send_pirep_dm(bot: commands.Bot, payload: dict[str, Any]) -> None:
    """DM the user a compact PIREP card after landing (best-effort)."""
    discord_id = int(payload.get("discord_id") or 0)
    if discord_id <= 0:
        return
    try:
        user = await bot.fetch_user(discord_id)
    except Exception:
        return
    if user is None:
        return
    try:
        await user.send(embed=_landing_embed(payload))
    except Exception:
        logger.debug("PIREP DM to %s failed (DMs closed?)", discord_id)


async def _send_milestone_ping(bot: commands.Bot, payload: dict[str, Any]) -> None:
    """Ping the user on milestone flight counts and personal-best landings."""
    discord_id = int(payload.get("discord_id") or 0)
    if discord_id <= 0:
        return

    db = await get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) AS n FROM flight_logs WHERE user_id = ?", (discord_id,)
    )
    row = await cursor.fetchone()
    total = int(row["n"]) if row else 0

    rate = _f(payload.get("landing_rate_fpm"))
    milestone = None
    if total in (1, 5, 10, 25, 50, 100, 250, 500, 1000):
        milestone = f"🎖️ **{total}th flight logged!**"

    personal_best = None
    if rate is not None:
        # "best" = softest landing = highest (closest to zero) fpm value.
        cursor = await db.execute(
            "SELECT MAX(landing_rate) AS best FROM flight_logs "
            "WHERE user_id = ? AND landing_rate IS NOT NULL AND id < (SELECT MAX(id) FROM flight_logs WHERE user_id = ?)",
            (discord_id, discord_id),
        )
        row = await cursor.fetchone()
        best = _f(row["best"]) if row else None
        if best is None or rate > best:
            personal_best = f"🟢 New personal best: **{rate:.0f} fpm**"

    if not milestone and not personal_best:
        return
    try:
        user = await bot.fetch_user(discord_id)
        if user:
            lines = [line for line in (milestone, personal_best) if line]
            await user.send("\n".join(lines))
    except Exception:
        logger.debug("Milestone ping to %s failed", discord_id)
