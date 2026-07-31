"""
OPS CONTROL - Announce Cog

/announce -- [Admin] Send formatted announcements to the configured
announcement channel.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.database import get_db
from bot.utils.helpers import utc_now_iso
from bot.utils.permissions import require_owner_or_admin
from bot.services.audit import log_event
from bot.services.discord_log import log_announcement

logger = logging.getLogger("ops_control.cogs.announce")


class AnnounceCog(commands.Cog):
    """Announcement broadcast commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="announce",
        description="Send a formatted announcement to the OPS ROOM announcement channel.",
    )
    @app_commands.describe(
        title="Announcement title",
        content="Announcement body text",
        image="Optional image URL to attach to the embed",
        color="Embed color as hex (e.g. #ff0000) -- default: blue",
    )
    async def announce(
        self,
        interaction: discord.Interaction,
        title: str,
        content: str,
        image: str | None = None,
        color: str = "#3498db",
    ) -> None:
        """Send a rich announcement (owner/admin only)."""
        if not await require_owner_or_admin(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        target_channel_id = config.discord_announcement_channel
        if not target_channel_id:
            await interaction.followup.send(
                "DISCORD_ANNOUNCEMENT_CHANNEL is not configured. Set it in .env.",
                ephemeral=True,
            )
            return

        target_channel = interaction.guild.get_channel(target_channel_id) if interaction.guild else None
        if target_channel is None or not isinstance(target_channel, discord.TextChannel):
            await interaction.followup.send(
                f"Announcement channel (ID {target_channel_id}) not found or is not a text channel.",
                ephemeral=True,
            )
            return

        try:
            try:
                embed_color = int(color.lstrip("#"), 16)
            except ValueError:
                embed_color = 0x3498DB

            embed = discord.Embed(
                title=title,
                description=content,
                color=embed_color,
            )
            embed.set_author(
                name=f"OPS ROOM -- Announcement by {interaction.user.display_name}",
                icon_url=interaction.user.display_avatar.url,
            )

            if image:
                embed.set_image(url=image)

            embed.set_footer(text="OPS ROOM Operations")

            msg = await target_channel.send(embed=embed)

            db = await get_db()
            await db.execute(
                """
                INSERT INTO announcements (title, content, image_url, created_by, created_by_name, created_at, channel_id, message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (title, content, image, interaction.user.id, interaction.user.display_name, utc_now_iso(), target_channel_id, msg.id),
            )
            await db.commit()

            await log_event(
                "announce",
                user_id=interaction.user.id,
                username=interaction.user.display_name,
                guild_id=interaction.guild_id,  # type: ignore[arg-type]
                channel_id=target_channel_id,
                detail=f"Announcement sent: {title}",
            )

            # Discord log channel notification
            if isinstance(interaction.user, discord.Member):
                await log_announcement(
                    self.bot,
                    interaction.user,
                    title,
                    target_channel.name,
                )

            await interaction.followup.send(
                f"Announcement sent to {target_channel.mention}.",
                ephemeral=True,
            )
            logger.info("Announcement sent by %s to channel %s", interaction.user.name, target_channel_id)

        except Exception as e:
            logger.exception("Failed to send announcement")
            await interaction.followup.send(
                f"Failed to send announcement: {e}",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnnounceCog(bot))
    logger.info("Announce cog loaded.")
