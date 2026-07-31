"""
OPS CONTROL - Purge Command

/purge -- [Admin/Moderator] Delete messages in bulk.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.services.discord_log import log_purge
from bot.services.audit import log_event

logger = logging.getLogger("ops_control.cogs.purge")

MODERATOR_ROLE_ID = config.moderator_role_id
OWNER_ID = config.owner_user_id


class PurgeCog(commands.Cog):
    """Message purge command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="purge",
        description="Delete a number of messages from this channel.",
    )
    @app_commands.describe(amount="Number of messages to delete (1-100)")
    async def purge(self, interaction: discord.Interaction, amount: int) -> None:
        """Purge messages (admin, owner, or moderator)."""
        # Permission check: Owner or Moderator role only.
        if interaction.user.id != OWNER_ID and isinstance(interaction.user, discord.Member):
            has_mod_role = MODERATOR_ROLE_ID and any(
                r.id == MODERATOR_ROLE_ID for r in interaction.user.roles
            )
            if not has_mod_role:
                await interaction.response.send_message(
                    "This command is restricted to the bot owner and the Moderator role.",
                    ephemeral=True,
                )
                return

        amount = max(1, min(amount, 100))
        await interaction.response.defer(ephemeral=True)

        try:
            deleted = await interaction.channel.purge(limit=amount)  # type: ignore[union-attr]
            count = len(deleted)

            await interaction.followup.send(
                f"Deleted {count} messages.",
                ephemeral=True,
            )

            await log_event(
                "purge",
                user_id=interaction.user.id,
                username=interaction.user.display_name,
                guild_id=interaction.guild_id,  # type: ignore[arg-type]
                channel_id=interaction.channel_id,
                detail=f"Purged {count} messages",
            )

            if isinstance(interaction.user, discord.Member):
                await log_purge(
                    self.bot,
                    interaction.user,
                    count,
                    interaction.channel.name if hasattr(interaction.channel, 'name') else "unknown",  # type: ignore[union-attr]
                )

        except Exception as e:
            logger.exception("Purge failed")
            await interaction.followup.send(
                f"Purge failed: {e}",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PurgeCog(bot))
    logger.info("Purge cog loaded.")
