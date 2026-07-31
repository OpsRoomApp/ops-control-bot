"""
OPS CONTROL - Status Cog

/status -- Bot health, version, latency, UTC time, loaded modules.
/ping -- Bot latency check.
"""

from __future__ import annotations

import logging
import platform
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("ops_control.cogs.status")

VERSION = "1.4.0"


class StatusCog(commands.Cog):
    """Bot health and status commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="status",
        description="Display OPS CONTROL bot status and health.",
    )
    async def status(self, interaction: discord.Interaction) -> None:
        """Respond with current bot status information."""
        latency_ms = round(self.bot.latency * 1000, 1)

        cogs = list(self.bot.cogs.keys())
        cogs.sort()

        embed = discord.Embed(
            title="OPS CONTROL -- System Status",
            color=0x059669,
            timestamp=datetime.now(timezone.utc),
        )

        embed.add_field(name="Version", value=VERSION, inline=True)
        embed.add_field(name="Latency", value=f"{latency_ms}ms", inline=True)
        embed.add_field(name="Python", value=platform.python_version(), inline=True)

        now_utc = datetime.now(timezone.utc)
        embed.add_field(
            name="UTC Time",
            value=f"{now_utc.strftime('%H:%M:%S')}\n{now_utc.strftime('%d %b %Y')}",
            inline=True,
        )
        embed.add_field(name="discord.py", value=discord.__version__, inline=True)
        embed.add_field(name="Guild ID", value=str(self.bot.user.id if self.bot.user else "N/A"), inline=True)

        embed.add_field(
            name=f"Modules ({len(cogs)})",
            value="\n".join(f"  {c}" for c in cogs) if cogs else "None",
            inline=False,
        )

        embed.set_footer(text="OPS ROOM Operations Platform")
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="ping",
        description="Check bot latency.",
    )
    async def ping(self, interaction: discord.Interaction) -> None:
        """Simple ping/pong latency check."""
        latency_ms = round(self.bot.latency * 1000, 1)
        await interaction.response.send_message(
            f"Pong -- {latency_ms}ms",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatusCog(bot))
    logger.info("Status cog loaded.")
