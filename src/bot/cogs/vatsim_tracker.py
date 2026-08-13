"""
OPS CONTROL - VATSIM Flight Tracker

/vatsim-set <CID> -- Link your VATSIM CID to your Discord account.
/vatsim-unset -- Remove your linked VATSIM CID.

A background loop polls the VATSIM data feed every
VATSIM_TRACKER_POLL_SECONDS (default 60). For every linked CID it detects
takeoff (ground -> airborne) and landing (airborne -> ground / left the feed)
and posts a flight-watch style embed to VATSIM_TRACKER_CHANNEL_ID
(default 1533447716359639131).

State is persisted per CID in the vatsim_tracking table so restarts do not
re-notify and a single feed fetch serves every linked user each cycle.
"""

from __future__ import annotations

import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.api import fetch_vatsim_pilots_by_cids
from bot.config import config
from bot.database import get_db
from bot.utils.helpers import resolve_text_channel, utc_now_iso
from bot.services.audit import log_event

logger = logging.getLogger("ops_control.vatsim_tracker")

DEFAULT_TRACKER_CHANNEL_ID = 1533447716359639131

# A pilot is "airborne" when the feed does not flag on_ground and the
# altitude is a meaningful AGL climb. VATSIM exposes altitude in MSL ft, so
# a raw absolute threshold false-positives at high-elevation fields (EDDS
# ~1,276 ft: a ground aircraft during pushback reads 1,289 ft and passes a
# 100 ft check whenever the client's on_ground flag is momentarily unset).
#
# Instead we self-calibrate: the last-seen on-ground altitude is the field
# reference, and airborne requires climbing ~100 ft AGL above it. When no
# ground reference exists yet (first sighting / never reported on_ground),
# require a real climb signature: altitude well above any civil field plus
# meaningful groundspeed.
AIRBORNE_AGL_FT = 100
AIRBORNE_UNKNOWN_FLOOR_FT = 1500
AIRBORNE_MIN_GROUNDSPEED_KT = 60


def _is_airborne(pilot: dict, prev: dict | None) -> bool:
    """MSL-agnostic airborne check (see module constants for rationale)."""
    altitude = int(pilot.get("altitude") or 0)
    if bool(pilot.get("on_ground")):
        return False
    reference = None
    if prev is not None:
        reference = prev.get("ground_ref_alt") or prev.get("altitude")
    if reference is not None and int(reference) > 0:
        return (altitude - int(reference)) > AIRBORNE_AGL_FT
    groundspeed = int(pilot.get("groundspeed") or 0)
    return altitude > AIRBORNE_UNKNOWN_FLOOR_FT and groundspeed > AIRBORNE_MIN_GROUNDSPEED_KT


def evaluate_tracker_state(
    prev: dict | None,
    pilot: dict | None,
    now_iso: str,
) -> tuple[str, dict]:
    """Decide the tracker event for one CID on one poll cycle.

    Returns (event, new_state) where event is one of:
        "takeoff"  - was on ground (or unknown) and is now airborne
        "landing"  - was airborne and is now on ground / absent from the feed
        "none"     - no state change worth announcing

    Pure function so it can be unit tested without Discord.
    """
    prev_airborne = bool(prev and prev.get("airborne"))
    prev_callsign = (prev or {}).get("callsign") or "N/A"

    if pilot is None:
        # Absent from the feed. If we were airborne, treat as landing.
        if prev_airborne:
            new_state = {
                "callsign": prev_callsign,
                "airborne": 0,
                "altitude": (prev or {}).get("altitude") or 0,
                "ground_ref_alt": (prev or {}).get("ground_ref_alt"),
                "departure": (prev or {}).get("departure") or "N/A",
                "arrival": (prev or {}).get("arrival") or "N/A",
                "aircraft": (prev or {}).get("aircraft") or "N/A",
                "last_seen": now_iso,
            }
            return "landing", new_state
        new_state = {
            "callsign": prev_callsign,
            "airborne": 0,
            "altitude": (prev or {}).get("altitude") or 0,
            "ground_ref_alt": (prev or {}).get("ground_ref_alt"),
            "departure": (prev or {}).get("departure") or "N/A",
            "arrival": (prev or {}).get("arrival") or "N/A",
            "aircraft": (prev or {}).get("aircraft") or "N/A",
            "last_seen": now_iso,
        }
        return "none", new_state

    altitude = int(pilot.get("altitude") or 0)
    airborne = _is_airborne(pilot, prev)
    # Keep the last on-ground MSL altitude as the field reference so the AGL
    # check works at ANY airport elevation (and survives a bot restart).
    ground_ref_alt = (prev or {}).get("ground_ref_alt") if prev else None
    if bool(pilot.get("on_ground")):
        ground_ref_alt = altitude

    new_state = {
        "callsign": str(pilot.get("callsign") or "N/A"),
        "airborne": int(airborne),
        "altitude": altitude,
        "ground_ref_alt": ground_ref_alt,
        "departure": str(pilot.get("departure") or "N/A"),
        "arrival": str(pilot.get("arrival") or "N/A"),
        "aircraft": str(pilot.get("aircraft") or "N/A"),
        "last_seen": now_iso,
    }

    if airborne and not prev_airborne:
        return "takeoff", new_state
    if not airborne and prev_airborne:
        return "landing", new_state
    return "none", new_state


class VatsimTracker(commands.Cog):
    """Auto takeoff/landing notifications for linked VATSIM CIDs."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._poll_task: tasks.Loop | None = None

    async def cog_load(self) -> None:
        self._session = aiohttp.ClientSession()
        if not config.vatsim_tracker_channel_id:
            logger.info("VATSIM_TRACKER_CHANNEL_ID not set - tracker disabled")
            return
        self._poll_task = tasks.loop(seconds=config.vatsim_tracker_poll_seconds)(self._poll)
        self._poll_task.start()
        logger.info(
            "VATSIM tracker started: channel=%s poll=%ss",
            config.vatsim_tracker_channel_id,
            config.vatsim_tracker_poll_seconds,
        )

    async def cog_unload(self) -> None:
        if self._poll_task:
            self._poll_task.cancel()
        if self._session:
            await self._session.close()

    # -- Commands -----------------------------------------------------------

    @app_commands.command(
        name="vatsim-set",
        description="Link your VATSIM CID so the bot posts your takeoffs and landings.",
    )
    @app_commands.describe(cid="Your VATSIM CID (numeric)")
    async def vatsim_set(self, interaction: discord.Interaction, cid: str) -> None:
        """Link the user's Discord account to a VATSIM CID."""
        cid = cid.strip()
        if not cid.isdigit():
            await interaction.response.send_message(
                "Your VATSIM CID must be a numeric ID (e.g. 1293090).",
                ephemeral=True,
            )
            return

        db = await get_db()
        await db.execute(
            """
            INSERT INTO vatsim_links (discord_id, vatsim_cid, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(discord_id)
            DO UPDATE SET vatsim_cid = excluded.vatsim_cid,
                          updated_at = excluded.updated_at
            """,
            (interaction.user.id, int(cid), utc_now_iso(), utc_now_iso()),
        )
        await db.commit()

        await log_event(
            "command",
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            guild_id=interaction.guild_id,  # type: ignore[arg-type]
            channel_id=interaction.channel_id,
            detail=f"VATSIM CID linked: {cid}",
        )

        await interaction.response.send_message(
            f"VATSIM CID **{cid}** linked. Takeoffs and landings will be posted to the "
            "tracking channel automatically.",
            ephemeral=True,
        )

    @app_commands.command(
        name="vatsim-unset",
        description="Remove your linked VATSIM CID.",
    )
    async def vatsim_unset(self, interaction: discord.Interaction) -> None:
        """Unlink the user's VATSIM CID."""
        db = await get_db()
        await db.execute(
            "DELETE FROM vatsim_links WHERE discord_id = ?",
            (interaction.user.id,),
        )
        await db.commit()
        await interaction.response.send_message(
            "VATSIM CID link removed.",
            ephemeral=True,
        )

    @app_commands.command(
        name="vatsim-linked",
        description="Show your linked VATSIM CID.",
    )
    async def vatsim_linked(self, interaction: discord.Interaction) -> None:
        """Show the user's linked CID (if any)."""
        db = await get_db()
        cursor = await db.execute(
            "SELECT vatsim_cid FROM vatsim_links WHERE discord_id = ?",
            (interaction.user.id,),
        )
        row = await cursor.fetchone()
        if row:
            await interaction.response.send_message(
                f"Your VATSIM CID is **{row['vatsim_cid']}**.",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                "No VATSIM CID linked. Use /vatsim-set to link one.",
                ephemeral=True,
            )

    # -- Background loop -----------------------------------------------------

    async def _poll(self) -> None:
        try:
            db = await get_db()
            cursor = await db.execute("SELECT vatsim_cid FROM vatsim_links")
            rows = await cursor.fetchall()
            if not rows:
                return

            cids = {str(row["vatsim_cid"]) for row in rows}
            try:
                pilots = await fetch_vatsim_pilots_by_cids(cids)
            except Exception as exc:
                logger.warning("VATSIM tracker feed fetch failed: %s", exc)
                return

            channel = await resolve_text_channel(self.bot, config.vatsim_tracker_channel_id)
            if channel is None:
                return

            now_iso = utc_now_iso()
            for row in rows:
                cid = str(row["vatsim_cid"])
                pilot = pilots.get(cid)
                cursor = await db.execute(
                    "SELECT callsign, airborne, altitude, ground_ref_alt, departure, arrival, aircraft FROM vatsim_tracking WHERE vatsim_cid = ?",
                    (int(cid),),
                )
                prev_row = await cursor.fetchone()
                prev = dict(prev_row) if prev_row else None

                event, new_state = evaluate_tracker_state(prev, pilot, now_iso)

                await db.execute(
                    """
                    INSERT INTO vatsim_tracking
                        (vatsim_cid, callsign, airborne, altitude, ground_ref_alt, departure, arrival, aircraft, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(vatsim_cid)
                    DO UPDATE SET callsign = excluded.callsign,
                                  airborne = excluded.airborne,
                                  altitude = excluded.altitude,
                                  ground_ref_alt = excluded.ground_ref_alt,
                                  departure = excluded.departure,
                                  arrival = excluded.arrival,
                                  aircraft = excluded.aircraft,
                                  last_seen = excluded.last_seen
                    """,
                    (
                        int(cid),
                        new_state["callsign"],
                        new_state["airborne"],
                        new_state.get("altitude") or 0,
                        new_state.get("ground_ref_alt"),
                        new_state["departure"],
                        new_state["arrival"],
                        new_state["aircraft"],
                        new_state["last_seen"],
                    ),
                )
                await db.commit()

                if event == "takeoff":
                    await self._post_takeoff(channel, cid, pilot, new_state)
                elif event == "landing":
                    await self._post_landing(channel, cid, new_state)
        except Exception:
            logger.exception("VATSIM tracker poll failed")

    async def _post_takeoff(
        self,
        channel: discord.TextChannel,
        cid: str,
        pilot: dict | None,
        state: dict,
    ) -> None:
        embed = discord.Embed(
            title=f"TAKEOFF -- {state['callsign']}",
            color=0x059669,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Pilot", value=f"VATSIM CID {cid}", inline=True)
        embed.add_field(name="Aircraft", value=state["aircraft"], inline=True)
        embed.add_field(
            name="Route",
            value=f"{state['departure']} -> {state['arrival']}",
            inline=True,
        )
        lat = pilot.get("latitude") if pilot else None
        lon = pilot.get("longitude") if pilot else None
        if lat is not None and lon is not None:
            embed.add_field(name="Position", value=f"{lat:.4f}, {lon:.4f}", inline=True)
        altitude = pilot.get("altitude") if pilot else None
        groundspeed = pilot.get("groundspeed") if pilot else None
        embed.add_field(
            name="Altitude",
            value=f"{altitude if altitude is not None else 'N/A'} ft",
            inline=True,
        )
        embed.add_field(
            name="Speed",
            value=f"{groundspeed if groundspeed is not None else 'N/A'} kt",
            inline=True,
        )
        embed.set_footer(text="Source: VATSIM Tracker")
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            logger.warning("VATSIM tracker cannot send to channel %s", channel.id)

    async def _post_landing(
        self,
        channel: discord.TextChannel,
        cid: str,
        state: dict,
    ) -> None:
        embed = discord.Embed(
            title=f"LANDING -- {state['callsign']}",
            color=0x8B5CF6,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Pilot", value=f"VATSIM CID {cid}", inline=True)
        embed.add_field(name="Aircraft", value=state["aircraft"], inline=True)
        embed.add_field(
            name="Route",
            value=f"{state['departure']} -> {state['arrival']}",
            inline=True,
        )
        embed.set_footer(text="Source: VATSIM Tracker")
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            logger.warning("VATSIM tracker cannot send to channel %s", channel.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VatsimTracker(bot))
    logger.info("VATSIM tracker cog loaded.")
