"""
OPS CONTROL - Feedback / Feature Requests Cog

/feedback -- opens a modal; on submit creates a public forum thread in the
feedback forum (config.feedback_forum_channel_id, default 1522234516922433661)
so the community can see and react to the idea.

Feedback from the desktop app and website arrives via the admin-api
`feedback_new` pending action instead (pending_actions.py); this command is
the Discord-native submission path.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.services.audit import log_event

logger = logging.getLogger("ops_control.cogs.feedback")

FEEDBACK_FORUM_ID = config.feedback_forum_channel_id

_KIND_LABELS = {
    "feedback": "Feedback",
    "feature_request": "Feature Request",
}

_KIND_COLORS = {
    "feedback": 0x3B82F6,
    "feature_request": 0x8B5CF6,
}


class FeedbackModal(discord.ui.Modal, title="Submit Feedback"):  # type: ignore[misc]
    """Modal form for feedback / feature requests.

    Discord limits modals to 5 TextInput fields: Category, Title, Description.
    """

    category = discord.ui.TextInput(
        label="Category",
        placeholder="feedback or feature_request",
        required=True,
        max_length=20,
    )
    title = discord.ui.TextInput(
        label="Title",
        placeholder="Short summary of your idea or feedback",
        required=True,
        max_length=100,
    )
    description = discord.ui.TextInput(
        label="Description",
        placeholder="What would you like to see, or what did you like?",
        required=True,
        style=discord.TextStyle.paragraph,
        max_length=2000,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        raw_kind = self.category.value.strip().lower()
        kind = "feature_request" if "feature" in raw_kind else "feedback"
        title = self.title.value.strip()
        description = self.description.value.strip()

        forum_id = FEEDBACK_FORUM_ID
        if not forum_id:
            await interaction.followup.send(
                "The feedback forum is not configured yet. Contact the OPS ROOM team instead.",
                ephemeral=True,
            )
            return

        forum = interaction.guild.get_channel(forum_id) if interaction.guild else None
        if not forum or not isinstance(forum, discord.ForumChannel):
            await interaction.followup.send(
                "The feedback forum could not be found. Contact the OPS ROOM team instead.",
                ephemeral=True,
            )
            return

        kind_label = _KIND_LABELS.get(kind, "Feedback")
        thread_name = f"[{kind_label}] {title}"[:100]

        embed = discord.Embed(
            title=title,
            color=_KIND_COLORS.get(kind, 0x3B82F6),
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Type", value=kind_label, inline=True)
        embed.add_field(name="Submitted by", value=interaction.user.mention, inline=True)
        embed.add_field(name="Details", value=description[:1024], inline=False)
        embed.set_footer(text="Feedback is reviewed and routed from the admin panel")

        try:
            thread = await forum.create_thread(
                name=thread_name,
                content=f"{interaction.user.mention} submitted new **{kind_label.lower()}**: {title}",
                embed=embed,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("Failed to open feedback thread")
            await interaction.followup.send(
                f"Could not create the feedback thread ({type(exc).__name__}). Please try again later.",
                ephemeral=True,
            )
            return

        try:
            await log_event(
                "feedback_submitted",
                user_id=interaction.user.id,
                guild_id=interaction.guild_id,
                detail=f"kind={kind} title={title} thread_id={getattr(thread, 'id', '')}",
            )
        except Exception:
            pass

        thread_link = f"https://discord.com/channels/{interaction.guild_id}/{thread.id}" if getattr(thread, "id", None) else "the forum"
        await interaction.followup.send(
            f"Thanks for the feedback. Your {kind_label.lower()} is live in the forum: <#{forum.id}> ({thread_link})",
            ephemeral=True,
        )


class FeedbackCog(commands.Cog):
    """Public feedback and feature requests."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="feedback",
        description="Submit feedback or a feature request.",
    )
    async def feedback(self, interaction: discord.Interaction) -> None:
        """Open the feedback modal."""
        await interaction.response.send_modal(FeedbackModal())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(FeedbackCog(bot))
    logger.info("Feedback cog loaded.")
