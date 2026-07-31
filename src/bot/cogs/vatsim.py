"""
OPS CONTROL - VATSIM Expansion Cog

/vatsim-status -- VATSIM network status.
/flightwatch -- Track a VATSIM aircraft by callsign.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.api import fetch_vatsim_online_count, fetch_vatsim_flight
from bot.services.audit import log_event

logger = logging.getLogger("ops_control.cogs.vatsim")


class VatsimCog(commands.Cog):
    """VATSIM network and flight tracking."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="vatsim-status",
        description="VATSIM network status.",
    )
    async def vatsim_status(self, interaction: discord.Interaction) -> None:
        """Display VATSIM network statistics."""
        await interaction.response.defer()

        try:
            data = await fetch_vatsim_online_count()
        except Exception as exc:
            logger.warning("VATSIM API unavailable: %s", exc)
            await interaction.followup.send(
                "VATSIM data is currently unavailable.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="VATSIM Network Status", color=0x059669)
        embed.add_field(name="Pilots Online", value=str(data["pilots"]), inline=True)
        embed.add_field(name="Controllers Online", value=str(data["controllers"]), inline=True)
        embed.add_field(name="ATIS Active", value=str(data["atis"]), inline=True)
        embed.add_field(name="Total Connections", value=str(data["pilots"] + data["controllers"]), inline=True)
        embed.set_footer(text="Source: VATSIM Data API")

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="flightwatch",
        description="Track a specific aircraft on VATSIM by callsign.",
    )
    @app_commands.describe(callsign="Aircraft callsign (e.g. BAW123, DAL456)")
    async def flightwatch(self, interaction: discord.Interaction, callsign: str) -> None:
        """Track a VATSIM aircraft by callsign."""
        callsign = callsign.strip().upper()
        await interaction.response.defer()

        try:
            flight = await fetch_vatsim_flight(callsign)
        except Exception as exc:
            logger.warning("VATSIM flightwatch failed: %s", exc)
            await log_event(
                "api_failure",
                user_id=interaction.user.id,
                username=interaction.user.display_name,
                guild_id=interaction.guild_id,  # type: ignore[arg-type]
                channel_id=interaction.channel_id,
                detail=f"VATSIM flightwatch failure: {exc}",
            )
            await interaction.followup.send(
                "Could not retrieve flight data. VATSIM may be unavailable.",
                ephemeral=True,
            )
            return

        if flight is None:
            await interaction.followup.send(
                f"No aircraft found with callsign {callsign} on VATSIM.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(title=f"Flight Watch -- {flight['callsign']}", color=0x059669)
        embed.add_field(name="Pilot", value=flight["name"], inline=True)
        embed.add_field(name="Aircraft", value=flight["aircraft"], inline=True)
        embed.add_field(name="Route", value=f"{flight['departure']} - {flight['arrival']}", inline=True)

        lat = flight.get("latitude")
        lon = flight.get("longitude")
        if lat is not None and lon is not None:
            embed.add_field(name="Position", value=f"{lat:.4f}, {lon:.4f}", inline=True)
        embed.add_field(name="Altitude", value=f"{flight.get('altitude', 'N/A')} ft", inline=True)
        embed.add_field(name="Speed", value=f"{flight.get('groundspeed', 'N/A')} kt", inline=True)

        if flight.get("route") and flight["route"] != "N/A":
            embed.add_field(name="Route", value=f"```{flight['route'][:500]}```", inline=False)

        embed.set_footer(text="Source: VATSIM")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(VatsimCog(bot))
    logger.info("VATSIM expansion cog loaded.")
