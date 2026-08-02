"""
OPS CONTROL - Rules Cog

/rules -- Post the OPS ROOM community rules to the current channel (public).
/rules-set <content> -- [Owner] Set the community rules (stored per guild).
/rules-reset -- [Owner] Restore the default rules template.

Rules are stored in guild_settings (key = "rules") so every member sees the
same canonical text and only the bot owner can change it.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_db
from bot.utils.helpers import utc_now_iso
from bot.utils.permissions import require_owner
from bot.services.audit import log_event

logger = logging.getLogger("ops_control.cogs.rules")

DEFAULT_RULES = (
    "**OPS ROOM Community Guidelines**\n\n"
    "As a community, we strive to protect the members using our service. "
    "To ensure the protection and enjoyment of all users we have laid out "
    "some guidelines that shall be adhered to at all times while using the service.\n"
    "--------------------\n"
    "**A. Be Respectful**\n"
    "Treat every member with respect. Harassment, name-calling, swearing at, or "
    "denigrating members — including staff — is not allowed under any circumstances. "
    "Remember that this is a hobby and volunteers give their free time.\n"
    "--------------------\n"
    "**B. Appropriate Posting**\n"
    "Keep content in the appropriate designated channels. Sending messages rapidly, "
    "malicious links, piracy links, or inappropriate content is not allowed. "
    "Constructive and respectful debates are welcome; arguing is not.\n"
    "--------------------\n"
    "**C. Languages**\n"
    "English shall be the language for communication on the server to ensure everyone "
    "feels included in discussions, whether on voice or in text channels.\n"
    "--------------------\n"
    "**D. Political & Religious Topics**\n"
    "Under no circumstances are political & religious topics allowed to be discussed "
    "on the server.\n"
    "--------------------\n"
    "**E. Roles and Mentions**\n"
    "Mentions within the server are to be kept at the bare minimum, including staff "
    "members. Roles are assigned via the bot's role panel.\n"
    "--------------------\n"
    "**F. Discord Terms of Service**\n"
    "All members must abide by the Terms of Service and Community Guidelines set by "
    "Discord Inc.\n"
    "--------------------\n"
    "**G. Enforcement**\n"
    "These rules are enforced at all times by the OPS ROOM team. Depending on the "
    "severity of an infraction it can escalate for further review, and repeat or "
    "severe offences may result in a timeout, mute, or ban."
)


class RulesCog(commands.Cog):
    """Community rules display and management."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _load_rules(self, guild_id: int | None) -> str:
        db = await get_db()
        cursor = await db.execute(
            "SELECT value FROM guild_settings WHERE guild_id = ? AND key = 'rules'",
            (guild_id or 0,),
        )
        row = await cursor.fetchone()
        return row["value"] if row else DEFAULT_RULES

    @app_commands.command(
        name="rules",
        description="View the OPS ROOM community rules.",
    )
    async def rules(self, interaction: discord.Interaction) -> None:
        """Post the community rules to the channel (visible to everyone)."""
        content = await self._load_rules(interaction.guild_id)
        embed = discord.Embed(
            title="OPS ROOM -- Community Rules",
            description=content,
            color=0x2563EB,
        )
        embed.set_footer(text="OPS ROOM Operations | Maintained by the owner")
        await interaction.response.defer(ephemeral=True)
        if interaction.channel is not None:
            await interaction.channel.send(embed=embed)
        await interaction.followup.send(
            "Community rules posted to this channel.",
            ephemeral=True,
        )

    @app_commands.command(
        name="rules-set",
        description="[Owner] Set the OPS ROOM community rules.",
    )
    @app_commands.describe(content="The full rules text to post")
    async def rules_set(self, interaction: discord.Interaction, content: str) -> None:
        """Save custom rules (owner only)."""
        if not await require_owner(interaction):
            return

        guild_id = interaction.guild_id
        await interaction.response.defer(ephemeral=True)

        db = await get_db()
        await db.execute(
            """
            INSERT INTO guild_settings (guild_id, key, value, updated_by, updated_at)
            VALUES (?, 'rules', ?, ?, ?)
            ON CONFLICT(guild_id, key)
            DO UPDATE SET value = excluded.value,
                          updated_by = excluded.updated_by,
                          updated_at = excluded.updated_at
            """,
            (guild_id, content, interaction.user.id, utc_now_iso()),
        )
        await db.commit()

        await log_event(
            "command",
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            guild_id=guild_id,  # type: ignore[arg-type]
            channel_id=interaction.channel_id,
            detail="Community rules updated",
        )

        await interaction.followup.send(
            "Community rules updated. Members can view them with /rules.",
            ephemeral=True,
        )
        logger.info("Rules updated by %s in guild %s", interaction.user.id, guild_id)

    @app_commands.command(
        name="rules-reset",
        description="[Owner] Restore the default community rules.",
    )
    async def rules_reset(self, interaction: discord.Interaction) -> None:
        """Delete custom rules and restore the default template (owner only)."""
        if not await require_owner(interaction):
            return

        guild_id = interaction.guild_id
        await interaction.response.defer(ephemeral=True)

        db = await get_db()
        await db.execute(
            "DELETE FROM guild_settings WHERE guild_id = ? AND key = 'rules'",
            (guild_id,),
        )
        await db.commit()

        await log_event(
            "command",
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            guild_id=guild_id,  # type: ignore[arg-type]
            channel_id=interaction.channel_id,
            detail="Community rules reset to default",
        )

        await interaction.followup.send(
            "Community rules restored to the default template.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RulesCog(bot))
    logger.info("Rules cog loaded.")
