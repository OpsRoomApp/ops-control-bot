"""
OPS CONTROL - Bug Reporting Cog

/bug — Opens a Discord Modal for structured bug reports.
Creates a database entry and posts to a forum channel if configured.
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

PRIORITY_LABELS = {
    "critical": "🚨 Critical",
    "high": "🔴 High",
    "normal": "🟡 Normal",
    "low": "🟢 Low",
}


class BugReportModal(discord.ui.Modal, title="Report a Bug"):
    """Modal form for structured bug reports."""

    version = discord.ui.TextInput(
        label="OPS ROOM Version",
        placeholder="e.g. v0.24.106",
        required=True,
        max_length=50,
    )
    simulator = discord.ui.TextInput(
        label="Simulator",
        placeholder="e.g. MSFS 2020, MSFS 2024, X-Plane 12",
        required=False,
        max_length=50,
    )
    aircraft = discord.ui.TextInput(
        label="Aircraft",
        placeholder="e.g. PMDG 737-800, Fenix A320",
        required=False,
        max_length=100,
    )
    module = discord.ui.TextInput(
        label="Module / Area",
        placeholder="e.g. Black Box, Flight Planner, Camera Bridge",
        required=True,
        max_length=100,
    )
    description = discord.ui.TextInput(
        label="Description",
        placeholder="What happened? Describe the bug clearly.",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        """Handle form submission."""
        await interaction.response.defer(ephemeral=True)

        db = await get_db()
        now = utc_now_iso()
        cursor = await db.execute(
            """
            INSERT INTO bugs (reporter_id, reporter_name, version, simulator, aircraft,
                             module, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                interaction.user.id,
                interaction.user.display_name,
                self.version.value.strip(),
                self.simulator.value.strip() or None,
                self.aircraft.value.strip() or None,
                self.module.value.strip(),
                self.description.value.strip(),
                now,
            ),
        )
        await db.commit()
        bug_id = cursor.lastrowid

        # Post to bug forum channel if configured
        thread_mention = ""
        if config.bug_forum_channel_id and interaction.guild:
            try:
                forum = interaction.guild.get_channel(config.bug_forum_channel_id)
                if forum and isinstance(forum, discord.ForumChannel):
                    tags = [_tag for _tag in forum.available_tags if _tag.name.lower() in ("bug", "report")]
                    thread = await forum.create_thread(
                        name=f"Bug #{bug_id}: {self.module.value.strip()} — {self.version.value.strip()}",
                        content=(
                            f"**Reporter:** {interaction.user.mention}\n"
                            f"**Version:** {self.version.value.strip()}\n"
                            f"**Simulator:** {self.simulator.value.strip() or 'N/A'}\n"
                            f"**Aircraft:** {self.aircraft.value.strip() or 'N/A'}\n"
                            f"**Module:** {self.module.value.strip()}\n\n"
                            f"**Description:**\n{self.description.value.strip()}"
                        ),
                        applied_tags=tags if tags else None,
                    )
                    thread_mention = f"\n📎 Forum thread: {thread.mention}"

                    # Update DB with thread ID
                    await db.execute(
                        "UPDATE bugs SET thread_id = ? WHERE id = ?",
                        (thread.id, bug_id),
                    )
                    await db.commit()
            except Exception as exc:
                logger.warning("Failed to create bug forum thread: %s", exc)

        await interaction.followup.send(
            f"✅ Bug report **#{bug_id}** submitted. Thank you!" + thread_mention,
            ephemeral=True,
        )
        logger.info("Bug #%s reported by %s", bug_id, interaction.user.name)


class BugCog(commands.Cog):
    """Bug reporting system."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="bug",
        description="Report a bug in OPS ROOM.",
    )
    async def bug(self, interaction: discord.Interaction) -> None:
        """Open the bug report modal."""
        await interaction.response.send_modal(BugReportModal())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BugCog(bot))
    logger.info("Bug cog loaded.")
