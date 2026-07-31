"""
OPS CONTROL - Support Ticket Cog

/support — Creates a private support thread or forum post.
Categories: Installation, Performance, Account, Technical, Other.
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
    """Support ticket system."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="support",
        description="Create a support ticket.",
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
        """Create a support ticket."""
        await interaction.response.defer(ephemeral=True)

        db = await get_db()
        now = utc_now_iso()
        cursor = await db.execute(
            """
            INSERT INTO tickets (user_id, username, category, description, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (interaction.user.id, interaction.user.display_name, category, description, now),
        )
        await db.commit()
        ticket_id = cursor.lastrowid

        # Post to support forum if configured
        thread_mention = ""
        if config.support_forum_channel_id and interaction.guild:
            try:
                forum = interaction.guild.get_channel(config.support_forum_channel_id)
                if forum and isinstance(forum, discord.ForumChannel):
                    cat_tag = next(
                        (_tag for _tag in forum.available_tags if _tag.name.lower() == category),
                        None,
                    )
                    tags = [cat_tag] if cat_tag else None
                    thread = await forum.create_thread(
                        name=f"[{category.upper()}] {interaction.user.display_name} — Support #{ticket_id}",
                        content=(
                            f"**Submitted by:** {interaction.user.mention}\n"
                            f"**Category:** {category}\n\n"
                            f"**Description:**\n{description}"
                        ),
                        applied_tags=tags,
                    )
                    thread_mention = f"\n📎 Support thread: {thread.mention}"

                    await db.execute(
                        "UPDATE tickets SET thread_id = ? WHERE id = ?",
                        (thread.id, ticket_id),
                    )
                    await db.commit()
            except Exception as exc:
                logger.warning("Failed to create support forum thread: %s", exc)

        await interaction.followup.send(
            f"✅ Support ticket **#{ticket_id}** created ({category}). "
            f"A staff member will assist you soon." + thread_mention,
            ephemeral=True,
        )
        logger.info("Support ticket #%s created by %s", ticket_id, interaction.user.name)

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
    await bot.add_cog(SupportCog(bot))
    logger.info("Support cog loaded.")
