"""
OPS CONTROL - Bug Reporting Cog (DEPRECATED)

This module has been replaced by ticket_system.py.
The /bug command is now handled by the TicketSystemCog
which provides a complete bug report system with modals,
private ticket channels, and bug report notifications.

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

logger = logging.getLogger("ops_control.cogs.bugs")


class BugReportModal(discord.ui.Modal, title="Report a Bug (Legacy)"):
    """DEPRECATED: Use the new ticket system instead."""

    version = discord.ui.TextInput(
        label="OPS ROOM Version",
        placeholder="e.g. v0.24.106",
        required=True,
        max_length=50,
    )
    module = discord.ui.TextInput(
        label="Module / Area",
        placeholder="e.g. Black Box, Flight Planner",
        required=True,
        max_length=100,
    )
    description = discord.ui.TextInput(
        label="Description",
        placeholder="What happened?",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "This legacy bug form has been replaced. Use the Support Panel "
            "button or type /bug to open the new bug report form.",
            ephemeral=True,
        )


class BugCog(commands.Cog):
    """DEPRECATED: Bug reporting system — use ticket_system.py instead."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="bug-legacy",
        description="[DEPRECATED] Use /bug in the new ticket system.",
    )
    async def bug(self, interaction: discord.Interaction) -> None:
        """DEPRECATED: Redirect to the new ticket system."""
        await interaction.response.send_message(
            "This command has been replaced. Use the Support Panel buttons "
            "or type /bug to open the new bug report form.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    logger.warning("BugCog is deprecated — not loaded. Use ticket_system.py instead.")
    await bot.add_cog(BugCog(bot))
