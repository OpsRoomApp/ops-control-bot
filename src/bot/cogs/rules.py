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

DEFAULT_RULES = """
**1. Be respectful.**
No harassment, hate speech, discrimination, or personal attacks. Treat others how you'd want to be treated.

**2. Keep it family-friendly.**
No NSFW, gore, or otherwise inappropriate content anywhere in the server.

**3. No spam or self-promotion.**
No unsolicited DMs, link dumping, or advertising other servers, products, or services without staff approval.

**4. No piracy.**
No discussion or distribution of cracked, pirated, or otherwise illegal software or addons.

**5. Follow Discord's rules.**
You must comply with Discord's Terms of Service and Community Guidelines at all times.

**6. Keep channels on-topic.**
Use the right channel for the right subject. Support and bug reports go in their dedicated channels, not general chat.

**7. No doxxing.**
Do not share anyone's personal information without their explicit consent — including your own.

**8. No begging.**
Do not ask for free products, keys, roles, or perks, and do not DM staff for them.

**9. English in public channels.**
Keep public chat in English so everyone can participate. Other languages are welcome in DMs.

**10. Staff have the final say.**
Moderator decisions are final. If you disagree, use the appeal process — do not argue in public channels.

**11. Verify to participate.**
Complete verification in the #verify channel to unlock full server access.

**12. Report, don't retaliate.**
If someone breaks a rule, report it to staff. Do not escalate or engage in public arguments.

━━━━━━━━━━━━━━━━━━━━━━━━━
*Breaking these rules may result in a warning, mute, timeout, or ban at staff discretion.*
To appeal a moderation action: https://opsroom.live/appeal
"""


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
            title=":clipboard: SERVER RULES",
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
