"""
OPS CONTROL - Support Ticket Cog (DEPRECATED)

This module has been replaced by ticket_system.py.
The /support command is now handled by the TicketSystemCog
which provides a complete ticket system with modals, buttons,
and private ticket channels.

This file is kept for reference. It is no longer loaded by the bot.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.database import get_db
from bot.utils.helpers import utc_now_iso

logger = logging.getLogger("ops_control.cogs.support")

CATEGORIES = [
    app_commands.Choice(name="Installation", value="installation"),
    app_commands.Choice(name="Performance", value="performance"),
    app_commands.Choice(name="Account", value="account"),
    app_commands.Choice(name="Technical", value="technical"),
    app_commands.Choice(name="Other", value="other"),
]


class SupportCog(commands.Cog):
    """DEPRECATED: Support ticket system — use ticket_system.py instead."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="support-legacy",
        description="[DEPRECATED] Use /support in the ticket system instead.",
    )
    @app_commands.describe(
        category="Type of support needed",
        description="Describe your issue",
    )
    async def support(
        self,
        interaction: discord.Interaction,
        category: str,
        description: str,
    ) -> None:
        """DEPRECATED: Redirect to the new ticket system."""
        await interaction.response.send_message(
            "This command has been replaced. Use the Support Panel buttons "
            "or type /support to open the new ticket form.",
            ephemeral=True,
        )

    @support.autocomplete("category")
    async def category_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for support categories."""
        return [
            c for c in CATEGORIES if current.lower() in c.name.lower()
        ]


async def setup(bot: commands.Bot) -> None:
    logger.warning("SupportCog is deprecated — not loaded. Use ticket_system.py instead.")
    await bot.add_cog(SupportCog(bot))
