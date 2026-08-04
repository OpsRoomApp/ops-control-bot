"""
OPS CONTROL - ATIS Cog

/atis ICAO -- VATSIM ATIS for an airport.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.api import fetch_vatsim_atis

logger = logging.getLogger("ops_control.cogs.atis")


class AtisCog(commands.Cog):
    """VATSIM ATIS retrieval commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="atis",
        description="VATSIM ATIS for an ICAO airport.",
    )
    @app_commands.describe(icao="ICAO airport code (e.g. EDDL, KJFK)")
    async def atis(self, interaction: discord.Interaction, icao: str) -> None:
        """Fetch ATIS from VATSIM for the given ICAO code."""
        icao = icao.strip().upper()

        if len(icao) != 4:
            await interaction.response.send_message(
                "Invalid ICAO code. Provide a 4-letter identifier.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        try:
            data = await fetch_vatsim_atis(icao)
        except Exception:
            await interaction.followup.send(
                f"ATIS unavailable for {icao}. VATSIM data may be down.",
                ephemeral=True,
            )
            return

        if data is None:
            await interaction.followup.send(
                f"No ATIS available for {icao} on VATSIM right now. "
                "The airport may be uncontrolled or no ATIS is being broadcast.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"ATIS -- {data['airport']}",
            color=0x059669,
            description=f"```{data['atis_message']}```" if data.get("atis_message") else None,
        )
        embed.add_field(name="Type", value=data.get("atis_type", "ATIS"), inline=True)
        embed.add_field(name="ATIS Code", value=data.get("atis_code", "N/A"), inline=True)
        embed.add_field(name="Controller", value=data.get("name", "N/A"), inline=True)
        embed.add_field(name="CID", value=str(data.get("cid", "N/A")), inline=True)
        embed.set_footer(text="Source: VATSIM")

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AtisCog(bot))
    logger.info("ATIS cog loaded.")
