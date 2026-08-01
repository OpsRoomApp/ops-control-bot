"""
OPS CONTROL - Flight Operations Cog

/flight vatsim -- VATSIM network status.
/flight opensky -- OpenSky live aircraft.
/flight simbrief -- SimBrief flight plan.
/flight status -- API health check.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.api import (
    fetch_vatsim_online_count,
    fetch_opensky_states,
    fetch_simbrief_flightplan,
)
from bot.config import config
from bot.services.audit import log_event

logger = logging.getLogger("ops_control.cogs.flight_ops")


class FlightOpsCog(commands.Cog):
    """Flight operations and tracking."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    flight_group = app_commands.Group(
        name="flight",
        description="Flight operations and tracking.",
    )

    @flight_group.command(
        name="vatsim",
        description="VATSIM network status.",
    )
    async def flight_vatsim(self, interaction: discord.Interaction) -> None:
        """Query VATSIM for current online pilot count."""
        await interaction.response.defer()

        try:
            data = await fetch_vatsim_online_count()
        except Exception as exc:
            logger.warning("VATSIM API unavailable: %s", exc)
            await log_event(
                "api_failure",
                user_id=interaction.user.id,
                username=interaction.user.display_name,
                guild_id=interaction.guild_id,  # type: ignore[arg-type]
                channel_id=interaction.channel_id,
                detail=f"VATSIM API failure: {exc}",
            )
            await interaction.followup.send(
                "VATSIM data is currently unavailable.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="VATSIM Network", color=0x059669)
        embed.add_field(name="Online Pilots", value=str(data.get("pilots", "N/A")), inline=True)
        embed.add_field(name="Online Controllers", value=str(data.get("controllers", "N/A")), inline=True)
        embed.add_field(name="ATIS Active", value=str(data.get("atis", "N/A")), inline=True)
        embed.set_footer(text="Source: VATSIM Data API")
        await interaction.followup.send(embed=embed)

    @flight_group.command(
        name="opensky",
        description="OpenSky Network live flight data.",
    )
    @app_commands.describe(icao24="ICAO24 transponder code (hex)")
    async def flight_opensky(
        self, interaction: discord.Interaction, icao24: str | None = None,
    ) -> None:
        """Query OpenSky Network for aircraft state vectors."""
        await interaction.response.defer()

        try:
            states = await fetch_opensky_states(icao24)
        except Exception as exc:
            logger.warning("OpenSky API unavailable: %s", exc)
            await log_event(
                "api_failure",
                user_id=interaction.user.id,
                username=interaction.user.display_name,
                guild_id=interaction.guild_id,  # type: ignore[arg-type]
                channel_id=interaction.channel_id,
                detail=f"OpenSky API failure: {exc}",
            )
            await interaction.followup.send(
                "OpenSky Network data is currently unavailable.",
                ephemeral=True,
            )
            return

        if not states:
            await interaction.followup.send(
                "No aircraft data found matching your query.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="OpenSky Network -- Aircraft", color=0x0E7490)

        for ac in states[:5]:
            callsign = ac.get("callsign", "N/A").strip() or "N/A"
            origin = ac.get("origin_country", "N/A")
            alt = ac.get("baro_altitude", "N/A")
            vel = ac.get("velocity", "N/A")
            embed.add_field(
                name=f"{callsign} ({ac.get('icao24', 'N/A')})",
                value=f"Country: {origin}\nAltitude: {alt} m\nVelocity: {vel} m/s",
                inline=True,
            )

        embed.set_footer(text="Source: OpenSky Network")
        await interaction.followup.send(embed=embed)

    @flight_group.command(
        name="simbrief",
        description="SimBrief flight plan by username.",
    )
    @app_commands.describe(username="SimBrief username")
    async def flight_simbrief(
        self, interaction: discord.Interaction, username: str | None = None,
    ) -> None:
        """Fetch a SimBrief flight plan (public XML fetcher, no API key)."""
        if not username:
            await interaction.response.send_message(
                "Provide a SimBrief username.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        try:
            plan = await fetch_simbrief_flightplan(username)
        except Exception as exc:
            logger.warning("SimBrief API unavailable: %s", exc)
            await log_event(
                "api_failure",
                user_id=interaction.user.id,
                username=interaction.user.display_name,
                guild_id=interaction.guild_id,  # type: ignore[arg-type]
                channel_id=interaction.channel_id,
                detail=f"SimBrief API failure: {exc}",
            )
            await interaction.followup.send(
                "SimBrief data is currently unavailable.",
                ephemeral=True,
            )
            return

        if plan is None:
            await interaction.followup.send(
                f"No active flight plan found for user {username}.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(title=f"SimBrief -- {plan['callsign']}", color=0xEA580C)
        embed.add_field(name="Aircraft", value=plan["aircraft"], inline=True)
        embed.add_field(name="Route", value=f"{plan['origin']} - {plan['destination']}", inline=True)
        embed.add_field(name="ETE", value=plan.get("ete", "N/A"), inline=True)
        embed.add_field(name="Fuel", value=plan.get("fuel", "N/A"), inline=True)
        embed.set_footer(text="Source: SimBrief API")
        await interaction.followup.send(embed=embed)

    @flight_group.command(
        name="status",
        description="Flight operations module health.",
    )
    async def flight_status(self, interaction: discord.Interaction) -> None:
        """Health check for flight ops API integrations."""
        await interaction.response.defer(ephemeral=True)

        checks: list[tuple[str, bool, str]] = []

        try:
            await fetch_vatsim_online_count()
            checks.append(("VATSIM", True, "Online"))
        except Exception as e:
            checks.append(("VATSIM", False, str(e)))

        try:
            await fetch_opensky_states()
            checks.append(("OpenSky", True, "Online"))
        except Exception as e:
            checks.append(("OpenSky", False, str(e)))

        try:
            result = await fetch_simbrief_flightplan()
            checks.append(("SimBrief", True, "Online" if result is not None else "No data"))
        except Exception as e:
            checks.append(("SimBrief", False, str(e)))

        lines: list[str] = []
        for name, ok, detail in checks:
            status_mark = "PASS" if ok else "FAIL"
            lines.append(f"{status_mark}  {name}: {detail}")

        embed = discord.Embed(
            title="Flight Operations -- API Status",
            description="\n".join(lines),
            color=0x2563EB,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FlightOpsCog(bot))
    logger.info("Flight Ops cog loaded.")
