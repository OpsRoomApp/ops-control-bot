"""
OPS CONTROL - Beta Tester Management

/betatester add <user> -- Grant Verified Tester + Public Beta roles.
/betatester remove <user> -- Revoke tester roles.

Permissions:
- Owner: full access
- Beta Coordinator role: can assign beta tester
- Moderator role: can assign beta tester
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.database import get_db
from bot.utils.helpers import utc_now_iso
from bot.services.audit import log_event
from bot.services.discord_log import send_log

logger = logging.getLogger("ops_control.cogs.betatester")

VERIFIED_TESTER_ROLE = config.verified_tester_role_id
PUBLIC_BETA_ROLE = config.public_beta_role_id
BETA_COORDINATOR_ROLE = config.beta_coordinator_role_id
MODERATOR_ROLE = config.moderator_role_id


class BetaTesterCog(commands.Cog):
    """Beta tester role management."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _can_manage(self, member: discord.Member) -> bool:
        """Owner has full access; Beta Coordinator and Moderator can assign."""
        if member.id == config.owner_user_id:
            return True
        if member.guild_permissions.administrator:
            return True
        manager_roles = (BETA_COORDINATOR_ROLE, MODERATOR_ROLE)
        return any(rid and any(r.id == rid for r in member.roles) for rid in manager_roles)

    async def _tester_roles(self, guild: discord.Guild) -> tuple[discord.Role | None, discord.Role | None]:
        verified = guild.get_role(VERIFIED_TESTER_ROLE) if VERIFIED_TESTER_ROLE else None
        public_beta = guild.get_role(PUBLIC_BETA_ROLE) if PUBLIC_BETA_ROLE else None
        return verified, public_beta

    @app_commands.command(
        name="betatester",
        description="Manage beta tester status for a user.",
    )
    @app_commands.describe(action="add or remove", user="The Discord user to manage")
    async def betatester(
        self,
        interaction: discord.Interaction,
        action: str,
        user: discord.Member,
    ) -> None:
        """Add or remove beta tester roles and update the database."""
        if not isinstance(interaction.user, discord.Member) or not self._can_manage(interaction.user):
            await interaction.response.send_message(
                "You need the Beta Coordinator, Moderator, or Owner role to manage beta testers.",
                ephemeral=True,
            )
            return

        action = action.lower()
        if action not in ("add", "remove"):
            await interaction.response.send_message(
                "Action must be 'add' or 'remove'.",
                ephemeral=True,
            )
            return

        verified, public_beta = await self._tester_roles(interaction.guild)  # type: ignore[arg-type]
        roles_to_change = [r for r in (verified, public_beta) if r is not None]

        if not roles_to_change:
            await interaction.response.send_message(
                "VERIFIED_TESTER_ROLE_ID and/or PUBLIC_BETA_ROLE_ID are not configured in .env.",
                ephemeral=True,
            )
            return

        try:
            if action == "add":
                await user.add_roles(*roles_to_change, reason=f"Beta tester assigned by {interaction.user.display_name}")
                beta_status = 1
                detail = f"Beta tester roles granted to {user.display_name}"
            else:
                await user.remove_roles(*roles_to_change, reason=f"Beta tester revoked by {interaction.user.display_name}")
                beta_status = 0
                detail = f"Beta tester roles revoked from {user.display_name}"

            # Update database
            db = await get_db()
            await db.execute(
                """
                INSERT OR IGNORE INTO users (id, username, display_name, first_joined, last_seen, is_active)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (user.id, user.name, user.display_name, utc_now_iso(), utc_now_iso()),
            )
            await db.execute(
                "UPDATE users SET beta_status = ?, last_seen = ? WHERE id = ?",
                (beta_status, utc_now_iso(), user.id),
            )
            await db.commit()

            role_names = ", ".join(r.name for r in roles_to_change)
            await interaction.response.send_message(
                f"{user.mention}: **{action.upper()}** - {role_names}",
                ephemeral=False,
            )

            await log_event(
                "betatester",
                user_id=interaction.user.id,
                username=interaction.user.display_name,
                guild_id=interaction.guild_id,  # type: ignore[arg-type]
                channel_id=interaction.channel_id,
                detail=detail,
            )

            await send_log(
                self.bot,
                "Beta Tester Updated",
                fields=[
                    ("User", f"{user.mention} ({user.name})"),
                    ("Action", action),
                    ("Roles", role_names),
                    ("Updated By", interaction.user.mention),
                ],
                color=0x0EA5E9,
            )

        except Exception as e:
            logger.exception("Failed to manage beta tester")
            await interaction.response.send_message(
                f"Failed to update beta tester: {e}",
                ephemeral=True,
            )

    @betatester.autocomplete("action")
    async def action_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        return [
            app_commands.Choice(name="add", value="add"),
            app_commands.Choice(name="remove", value="remove"),
        ]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(BetaTesterCog(bot))
    logger.info("Beta tester cog loaded.")
