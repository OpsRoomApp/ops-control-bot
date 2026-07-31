"""
OPS CONTROL - Welcome Cog

Automatic welcome image generation on member join.
/welcome -- [Owner] Manual welcome image test.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.services.welcome_image import WelcomeImageGenerator
from bot.services.audit import log_event
from bot.services.discord_log import log_member_join
from bot.database import get_db
from bot.utils.helpers import utc_now_iso
from bot.utils.permissions import require_owner

logger = logging.getLogger("ops_control.cogs.welcome")


class WelcomeCog(commands.Cog):
    """Welcome system for new members."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._generator: WelcomeImageGenerator | None = None

    @property
    def generator(self) -> WelcomeImageGenerator:
        if self._generator is None:
            self._generator = WelcomeImageGenerator()
        return self._generator

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        """Automatically generate and send welcome image to arrivals channel."""
        try:
            if member.guild.id != config.guild_id:
                return

            logger.info("Member joined: %s (ID: %s)", member.name, member.id)

            image_path = self.generator.generate(name=member.display_name)

            channel = self.bot.get_channel(config.arrivals_channel_id)
            if channel is None or not isinstance(channel, discord.TextChannel):
                logger.error("Arrivals channel %s not found", config.arrivals_channel_id)
                return

            msg = await channel.send(
                content=f"**{member.mention}** has arrived. Welcome to OPS ROOM.",
                file=discord.File(str(image_path)),
            )

            db = await get_db()
            await db.execute(
                """
                INSERT OR REPLACE INTO users (id, username, display_name, first_joined, last_seen, is_active)
                VALUES (?, ?, ?, COALESCE((SELECT first_joined FROM users WHERE id = ?), ?), ?, 1)
                """,
                (member.id, member.name, member.display_name, member.id, utc_now_iso(), utc_now_iso()),
            )
            await db.commit()

            await log_event(
                "join",
                user_id=member.id,
                username=f"{member.name}",
                guild_id=member.guild.id,
                channel_id=msg.channel.id,
                detail=f"Welcome image sent for {member.display_name}",
            )

            self.generator.cleanup(image_path)

            await log_member_join(self.bot, member)

        except Exception:
            logger.exception("Failed to process member join for %s", member.name)

    @app_commands.command(
        name="welcome",
        description="[Owner] Manually test welcome image generation.",
    )
    async def welcome_test(self, interaction: discord.Interaction) -> None:
        """Owner-only manual welcome image test."""
        if not await require_owner(interaction):
            return

        await interaction.response.defer(ephemeral=False)

        try:
            image_path = self.generator.generate(name=interaction.user.display_name)

            await interaction.followup.send(
                content=f"**Welcome test** for {interaction.user.mention}",
                file=discord.File(str(image_path)),
            )

            self.generator.cleanup(image_path)

            await log_event(
                "command",
                user_id=interaction.user.id,
                username=interaction.user.display_name,
                guild_id=interaction.guild_id,  # type: ignore[arg-type]
                channel_id=interaction.channel_id,
                detail="/welcome test command executed",
            )

        except Exception as e:
            logger.exception("Welcome test failed")
            await interaction.followup.send(
                f"Welcome test failed: {e}",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WelcomeCog(bot))
    logger.info("Welcome cog loaded.")
