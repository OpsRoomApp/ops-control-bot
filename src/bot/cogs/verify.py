"""
OPS CONTROL - Member Verification Gate

New members join with no roles (or the Unverified role). A persistent
"Verify Me" button lives in the verification channel; clicking it grants
the member role, removes the unverified role, and logs the event.

/verify-setup -- [Admin] Post (or refresh) the persistent verify button
                 message in the configured verification channel.
"""

from __future__ import annotations

import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.database import get_db
from bot.services.audit import log_event
from bot.utils.helpers import utc_now_iso
from bot.utils.permissions import is_staff

logger = logging.getLogger("ops_control.cogs.verify")

VERIFY_MESSAGE_KEY = "verify_message"


class VerifyView(discord.ui.View):
    """Persistent verify button (survives restarts via bot.add_view)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Verify Me",
        style=discord.ButtonStyle.success,
        custom_id="verify:grant",
    )
    async def verify_click(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button,
    ) -> None:
        if not interaction.guild:
            return

        role = None
        if config.verify_member_role_id:
            role = interaction.guild.get_role(config.verify_member_role_id)
        if role is None:
            return await interaction.response.send_message(
                "Verification is not configured (VERIFY_MEMBER_ROLE_ID missing). "
                "Please contact staff.",
                ephemeral=True,
            )

        if role in interaction.user.roles:
            return await interaction.response.send_message(
                "You are already verified. Welcome aboard!",
                ephemeral=True,
            )

        try:
            await interaction.user.add_roles(role, reason="Verified via verification channel")
        except discord.Forbidden:
            return await interaction.response.send_message(
                "I could not grant the member role (permission/hierarchy issue). "
                "Please contact staff.",
                ephemeral=True,
            )
        except discord.HTTPException:
            return await interaction.response.send_message(
                "Something went wrong while verifying. Please try again or contact staff.",
                ephemeral=True,
            )

        if config.verify_unverified_role_id:
            unverified = interaction.guild.get_role(config.verify_unverified_role_id)
            if unverified and unverified in interaction.user.roles:
                try:
                    await interaction.user.remove_roles(unverified, reason="Verified")
                except (discord.Forbidden, discord.HTTPException):
                    pass

        await interaction.response.send_message(
            "You are verified! Full server access granted.",
            ephemeral=True,
        )
        await log_event(
            "verify",
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            guild_id=interaction.guild.id,
            channel_id=interaction.channel_id,
            detail="Member verified via verification channel button",
        )


class Verify(commands.Cog):
    """Member verification gate (persistent button + setup command)."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @app_commands.command(
        name="verify-setup",
        description="[Admin] Post or refresh the persistent Verify button in the verification channel",
    )
    async def verify_setup_cmd(self, interaction: discord.Interaction) -> None:
        if not await is_staff(interaction):
            return await interaction.response.send_message(
                "Insufficient permissions.", ephemeral=True
            )
        if not config.verify_channel_id:
            return await interaction.response.send_message(
                "VERIFY_CHANNEL_ID is not configured. Cannot post verify message.",
                ephemeral=True,
            )
        if not config.verify_member_role_id:
            return await interaction.response.send_message(
                "VERIFY_MEMBER_ROLE_ID is not configured. Cannot set up verification.",
                ephemeral=True,
            )
        channel = interaction.guild.get_channel(config.verify_channel_id)
        if channel is None or not isinstance(channel, discord.TextChannel):
            return await interaction.response.send_message(
                f"Verify channel <#{config.verify_channel_id}> not found in this guild.",
                ephemeral=True,
            )

        embed = discord.Embed(
            title="Verify to Unlock OPS ROOM",
            description=(
                "Welcome! Click the button below to verify your account and "
                "receive the Member role with full server access."
            ),
            color=discord.Color.green(),
        )
        view = VerifyView()

        await interaction.response.defer(ephemeral=True)

        # Refresh the existing button message when possible, otherwise post a new one.
        stored = await self._stored_message(interaction.guild.id)
        message = None
        if stored:
            try:
                ch = interaction.guild.get_channel(stored.get("channel_id") or 0)
                if ch is not None and isinstance(ch, discord.TextChannel):
                    old = await ch.fetch_message(stored.get("message_id") or 0)
                    await old.edit(embed=embed, view=view)
                    message = old
            except (discord.NotFound, discord.HTTPException):
                message = None

        if message is None:
            message = await channel.send(embed=embed, view=view)

        await self._store_message(interaction.guild.id, channel.id, message.id)
        await log_event(
            "command",
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            guild_id=interaction.guild.id,
            channel_id=interaction.channel_id,
            detail="Verify button message posted/refreshed",
        )
        await interaction.followup.send(
            f"Verify button is live in <#{config.verify_channel_id}>.",
            ephemeral=True,
        )


    # ------------------------------------------------------------------
    # Persistence helpers (guild_settings table)
    # ------------------------------------------------------------------

    async def _stored_message(self, guild_id: int) -> dict | None:
        db = await get_db()
        cur = await db.execute(
            "SELECT value FROM guild_settings WHERE guild_id = ? AND key = ?",
            (guild_id, VERIFY_MESSAGE_KEY),
        )
        row = await cur.fetchone()
        if not row:
            return None
        try:
            data = json.loads(row["value"])
        except (ValueError, TypeError):
            return None
        return data if isinstance(data, dict) else None

    async def _store_message(self, guild_id: int, channel_id: int, message_id: int) -> None:
        db = await get_db()
        await db.execute(
            """
            INSERT INTO guild_settings (guild_id, key, value, updated_by, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(guild_id, key)
            DO UPDATE SET value = excluded.value,
                          updated_by = excluded.updated_by,
                          updated_at = excluded.updated_at
            """,
            (
                guild_id,
                VERIFY_MESSAGE_KEY,
                json.dumps({"channel_id": channel_id, "message_id": message_id}),
                None,
                utc_now_iso(),
            ),
        )
        await db.commit()


async def setup(bot: commands.Bot):
    await bot.add_cog(Verify(bot))
