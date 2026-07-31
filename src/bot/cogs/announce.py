"""
OPS CONTROL - Announce Cog

/announce -- [Admin] Send formatted announcements.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_db
from bot.utils.helpers import utc_now_iso
from bot.utils.permissions import require_owner_or_admin
from bot.services.audit import log_event

logger = logging.getLogger("ops_control.cogs.announce")


class AnnounceCog(commands.Cog):
    """Announcement broadcast commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="announce",
        description="Send a formatted announcement to the current channel.",
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
        """Send a rich announcement to the current channel (owner/admin only)."""
        if not await require_owner_or_admin(interaction):
            return

        await interaction.response.defer(ephemeral=True)

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

            msg = await interaction.channel.send(embed=embed)  # type: ignore[union-attr]

            db = await get_db()
            await db.execute(
                """
                INSERT INTO announcements (title, content, image_url, created_by, created_by_name, created_at, channel_id, message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (title, content, image, interaction.user.id, interaction.user.display_name, utc_now_iso(), interaction.channel_id, msg.id),
            )
            await db.commit()

            await log_event(
                "announce",
                user_id=interaction.user.id,
                username=interaction.user.display_name,
                guild_id=interaction.guild_id,  # type: ignore[arg-type]
                channel_id=interaction.channel_id,
                detail=f"Announcement sent: {title}",
            )

            await interaction.followup.send(
                "Announcement sent.",
                ephemeral=True,
            )
            logger.info("Announcement sent by %s in channel %s", interaction.user.name, interaction.channel_id)

        except Exception as e:
            logger.exception("Failed to send announcement")
            await interaction.followup.send(
                f"Failed to send announcement: {e}",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AnnounceCog(bot))
    logger.info("Announce cog loaded.")
