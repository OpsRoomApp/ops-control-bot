"""
OPS CONTROL - SIGMET Cog

/sigmet -- Active aviation weather warnings.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.notam_service import fetch_sigmets

logger = logging.getLogger("ops_control.cogs.sigmet")


class SigmetCog(commands.Cog):
    """SIGMET aviation weather warnings."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="sigmet",
        description="Active aviation weather warnings (SIGMET).",
    )
    async def sigmet(self, interaction: discord.Interaction) -> None:
        """Fetch active SIGMETs."""
        await interaction.response.defer()

        try:
            sigmets = await fetch_sigmets()
        except Exception:
            await interaction.followup.send(
                "SIGMET data unavailable. The weather service may be down.",
                ephemeral=True,
            )
            return

        if not sigmets:
            await interaction.followup.send(
                "No active SIGMET warnings at this time.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="Active SIGMET Warnings",
            color=0xDC2626,
            description=f"{len(sigmets)} active warning(s)",
        )

        for s in sigmets[:10]:
            embed.add_field(
                name=f"{s['id']} -- {s['type']}",
                value=(
                    f"{s['description'][:250]}\n"
                    f"Valid: {s['valid_from']} to {s['valid_to']}"
                ),
                inline=False,
            )

        embed.set_footer(text="Source: NOAA Aviation Weather Center")
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SigmetCog(bot))
    logger.info("SIGMET cog loaded.")
