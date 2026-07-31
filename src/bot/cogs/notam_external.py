"""
OPS CONTROL - External NOTAM Cog

/notam-external ICAO -- Fetch active external NOTAMs for an airport.
Uses FAA NOTAM API where available.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.notam_service import fetch_notams

logger = logging.getLogger("ops_control.cogs.notam_external")


class NotamExternalCog(commands.Cog):
    """External aviation NOTAM retrieval."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="notam-external",
        description="Fetch active external NOTAMs for an ICAO airport.",
    )
    @app_commands.describe(icao="ICAO airport code (e.g. KLAX, EGLL)")
    async def notam_external(
        self, interaction: discord.Interaction, icao: str
    ) -> None:
        """Fetch external NOTAMs for the given ICAO code."""
        icao = icao.strip().upper()
        if len(icao) != 4:
            await interaction.response.send_message(
                "Invalid ICAO code. Provide a 4-letter identifier.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        try:
            notams = await fetch_notams(icao)
        except Exception:
            await interaction.followup.send(
                f"NOTAM data unavailable for {icao}.",
                ephemeral=True,
            )
            return

        if not notams:
            await interaction.followup.send(
                f"No active external NOTAMs found for {icao}.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"NOTAMs -- {icao}",
            color=0xEA580C,
        )

        for n in notams[:10]:
            value = f"{n.get('description', 'N/A')[:250]}"
            if len(n.get("description", "")) > 250:
                value += "..."
            embed.add_field(
                name=f"{n['identifier']} ({n['type']})",
                value=(
                    f"{value}\n"
                    f"Effective: {n['effective']}  |  Expires: {n['expiry']}\n"
                    f"Source: {n['source']}"
                ),
                inline=False,
            )

        embed.set_footer(text="Source: FAA NOTAM System")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NotamExternalCog(bot))
    logger.info("External NOTAM cog loaded.")
