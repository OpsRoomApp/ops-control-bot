"""
OPS CONTROL - Moderation Suite (v0.25.55)

Core moderation commands (warn/kick/ban/unban/timeout/mute) with
case history tracking, automod event listeners, and appeal system
integration.

All actions are logged to mod_log_channel and stored in moderation_cases.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.config import config
from bot.database import get_db
from bot.utils.permissions import is_staff

logger = logging.getLogger("ops_control.moderation")

# -- Automod defaults (overridable via automod_config table) --
AUTOMOD_DEFAULTS: dict[str, dict[str, Any]] = {
    "spam": {
        "enabled": True,
        "action": "warn",
        "threshold": 5,  # messages per 5-second window
    },
    "excessive_mentions": {
        "enabled": True,
        "action": "timeout",
        "threshold": 8,  # mentions per message
    },
    "link_filter": {
        "enabled": False,
        "action": "delete",
        "threshold": None,
        "config_json": '{"allowlist":[],"blocklist":[]}',
    },
    "excessive_caps": {
        "enabled": True,
        "action": "warn",
        "threshold": 0.7,  # fraction of message that is uppercase
    },
}


class CloseAppealModal(discord.ui.Modal, title="Resolve Appeal"):
    """Modal for staff to approve/deny an appeal."""

    resolution = discord.ui.TextInput(
        label="Resolution note",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=500,
        placeholder="Reason for approval/denial...",
    )

    def __init__(self, appeal_id: int, action: str):
        super().__init__()
        self.appeal_id = appeal_id
        self.action = action  # "approved" or "denied"

    async def on_submit(self, interaction: discord.Interaction):
        db = await get_db()
        now = datetime.now(timezone.utc).isoformat()
        await db.execute(
            "UPDATE appeals SET status=?, reviewed_by=?, reviewed_at=?, resolution=? WHERE id=?",
            (self.action, interaction.user.id, now, str(self.resolution), self.appeal_id),
        )
        await db.commit()
        await interaction.response.send_message(
            f"Appeal #{self.appeal_id} {self.action}.", ephemeral=True
        )


class Moderation(commands.Cog):
    """Full moderation suite: commands, automod, logging, appeals."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._spam_tracker: dict[int, list[float]] = {}  # user_id -> [timestamps]
        self._last_release_tag: str | None = None

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------

    @app_commands.command(name="warn", description="Warn a user")
    @app_commands.describe(user="User to warn", reason="Reason for warning")
    async def warn_cmd(
        self, interaction: discord.Interaction, user: discord.Member, reason: str
    ):
        if not await is_staff(interaction):
            return await interaction.response.send_message(
                "Insufficient permissions.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=False)
        try:
            await self._log_action(interaction, "WARN", user, reason)
            try:
                await user.send(f"You have been warned in {interaction.guild.name}: {reason}")
            except (discord.Forbidden, discord.HTTPException):
                pass
            await interaction.followup.send(
                f"Warned {user.mention}: {reason}", ephemeral=False
            )
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"Failed to record warning for {user.mention}: {exc}",
                ephemeral=True,
            )

    @app_commands.command(name="kick", description="Kick a user")
    @app_commands.describe(user="User to kick", reason="Reason for kicking")
    async def kick_cmd(
        self, interaction: discord.Interaction, user: discord.Member, reason: str
    ):
        if not await is_staff(interaction):
            return await interaction.response.send_message(
                "Insufficient permissions.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=False)
        try:
            try:
                await user.send(f"You have been kicked from {interaction.guild.name}: {reason}")
            except (discord.Forbidden, discord.HTTPException):
                pass
            await user.kick(reason=reason)
            await self._log_action(interaction, "KICK", user, reason)
            await interaction.followup.send(
                f"Kicked {user}: {reason}", ephemeral=False
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"Failed to kick {user}: the bot needs the **Kick Members** "
                "permission and the target must be below the bot in the role "
                "hierarchy.",
                ephemeral=True,
            )
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"Failed to kick {user}: {exc}", ephemeral=True
            )

    @app_commands.command(name="ban", description="Ban a user")
    @app_commands.describe(
        user="User to ban",
        reason="Reason for banning",
        delete_message_days="Days of messages to delete (0-7)",
    )
    async def ban_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str,
        delete_message_days: int = 0,
    ):
        if not await is_staff(interaction):
            return await interaction.response.send_message(
                "Insufficient permissions.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=False)
        try:
            days = max(0, min(7, int(delete_message_days)))
            appeal_link = config.appeal_form_url or "https://opsroom.live/appeal"
            dm_msg = (
                f"You have been banned from {interaction.guild.name}.\n"
                f"Reason: {reason}\n\n"
                f"To appeal, visit: {appeal_link}"
            )
            try:
                await user.send(dm_msg)
            except (discord.Forbidden, discord.HTTPException):
                pass
            await user.ban(reason=reason, delete_message_days=days)
            await self._log_action(interaction, "BAN", user, reason)
            await interaction.followup.send(
                f"Banned {user}: {reason}", ephemeral=False
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"Failed to ban {user}: the bot needs the **Ban Members** "
                "permission and the target must be below the bot in the role "
                "hierarchy.",
                ephemeral=True,
            )
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"Failed to ban {user}: {exc}", ephemeral=True
            )

    @app_commands.command(name="unban", description="Unban a user by ID")
    @app_commands.describe(user_id="Discord user ID to unban", reason="Reason for unbanning")
    async def unban_cmd(
        self, interaction: discord.Interaction, user_id: str, reason: str = "Appeal approved"
    ):
        if not await is_staff(interaction):
            return await interaction.response.send_message(
                "Insufficient permissions.", ephemeral=True
            )
        try:
            uid = int(user_id)
            entry = await interaction.guild.fetch_ban(discord.Object(id=uid))
            await interaction.guild.unban(entry.user, reason=reason)
            await self._log_action(interaction, "UNBAN", entry.user, reason)
            await interaction.response.send_message(
                f"Unbanned user ID {uid}: {reason}", ephemeral=False
            )
        except discord.NotFound:
            await interaction.response.send_message(
                f"No ban found for user ID {user_id}.", ephemeral=True
            )
        except ValueError:
            await interaction.response.send_message(
                "Invalid user ID.", ephemeral=True
            )

    @app_commands.command(name="timeout", description="Timeout a user")
    @app_commands.describe(
        user="User to timeout",
        duration_minutes="Duration in minutes",
        reason="Reason for timeout",
    )
    async def timeout_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        duration_minutes: int,
        reason: str,
    ):
        if not await is_staff(interaction):
            return await interaction.response.send_message(
                "Insufficient permissions.", ephemeral=True
            )
        # Acknowledge immediately: the API call, DB write and DM below can
        # exceed Discord's 3-second interaction window.
        await interaction.response.defer(ephemeral=False)
        try:
            until = discord.utils.utcnow() + timedelta(minutes=max(1, min(40320, duration_minutes)))
            await user.timeout(until, reason=reason)
            await self._log_action(interaction, "TIMEOUT", user, reason, expires_at=until.isoformat())
            try:
                await user.send(
                    f"You have been timed out in {interaction.guild.name} "
                    f"for {duration_minutes} minutes: {reason}"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            await interaction.followup.send(
                f"Timed out {user.mention} for {duration_minutes} min: {reason}",
                ephemeral=False,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"Failed to timeout {user.mention}: the bot needs the "
                "**Moderate Members** permission and the target must be below "
                "the bot in the role hierarchy.",
                ephemeral=True,
            )
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"Failed to timeout {user.mention}: {exc}", ephemeral=True
            )

    @app_commands.command(name="untimeout", description="Remove a user's timeout")
    @app_commands.describe(user="User to remove timeout from")
    async def untimeout_cmd(
        self, interaction: discord.Interaction, user: discord.Member
    ):
        if not await is_staff(interaction):
            return await interaction.response.send_message(
                "Insufficient permissions.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=False)
        try:
            await user.timeout(None, reason="Timeout removed by staff")
            await self._log_action(interaction, "UNTIMEOUT", user, "Timeout removed")
            await interaction.followup.send(
                f"Removed timeout from {user.mention}.", ephemeral=False
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"Failed to remove timeout from {user.mention}: the bot needs "
                "the **Moderate Members** permission.",
                ephemeral=True,
            )
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"Failed to remove timeout: {exc}", ephemeral=True
            )

    @app_commands.command(name="mute", description="Role-based mute (supports permanent / long durations)")
    @app_commands.describe(
        user="User to mute",
        reason="Reason for muting",
        duration_minutes="Duration in minutes (omit for permanent)",
    )
    async def mute_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str,
        duration_minutes: int = 0,
    ):
        """Apply the Muted role.

        Discord's native timeout caps at 28 days. A role-based mute covers
        permanent mutes and any duration beyond that cap, so /mute always
        uses the role (expiry tracked in moderation_cases.expires_at and
        enforced by a background loop) rather than a native timeout.
        """
        if not await is_staff(interaction):
            return await interaction.response.send_message(
                "Insufficient permissions.", ephemeral=True
            )
        role_id = config.muted_role_id
        if not role_id:
            return await interaction.response.send_message(
                "MUTED_ROLE_ID is not configured. Cannot mute.", ephemeral=True
            )
        role = interaction.guild.get_role(role_id)
        if role is None:
            return await interaction.response.send_message(
                "Muted role not found in this guild.", ephemeral=True
            )
        if role in user.roles:
            return await interaction.response.send_message(
                f"{user.mention} is already muted.", ephemeral=False
            )
        await interaction.response.defer(ephemeral=False)
        try:
            await user.add_roles(role, reason=reason)
            expires_at = None
            if duration_minutes and duration_minutes > 0:
                expires_at = (discord.utils.utcnow() + timedelta(minutes=duration_minutes)).isoformat()
            await self._log_action(
                interaction, "MUTE", user, reason, expires_at=expires_at
            )
            try:
                duration_text = (
                    f"for {duration_minutes} minutes" if duration_minutes and duration_minutes > 0
                    else "permanently"
                )
                await user.send(
                    f"You have been muted {duration_text} in {interaction.guild.name}: {reason}"
                )
            except (discord.Forbidden, discord.HTTPException):
                pass
            await interaction.followup.send(
                f"Muted {user.mention} {('for ' + str(duration_minutes) + ' min') if duration_minutes and duration_minutes > 0 else 'permanently'}: {reason}",
                ephemeral=False,
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"Failed to mute {user.mention}: the bot needs **Manage Roles** "
                "permission and the target must be below the bot in the role "
                "hierarchy.",
                ephemeral=True,
            )
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"Failed to mute {user.mention}: {exc}", ephemeral=True
            )

    @app_commands.command(name="unmute", description="Remove the Muted role from a user")
    @app_commands.describe(user="User to unmute")
    async def unmute_cmd(
        self, interaction: discord.Interaction, user: discord.Member
    ):
        if not await is_staff(interaction):
            return await interaction.response.send_message(
                "Insufficient permissions.", ephemeral=True
            )
        await interaction.response.defer(ephemeral=False)
        try:
            role_id = config.muted_role_id
            if role_id:
                role = interaction.guild.get_role(role_id)
                if role and role in user.roles:
                    await user.remove_roles(role, reason="Unmuted by staff")
            # Mark any active MUTE cases inactive.
            db = await get_db()
            await db.execute(
                "UPDATE moderation_cases SET active=0 WHERE user_id=? AND action_type='MUTE' AND active=1",
                (user.id,),
            )
            await db.commit()
            await self._log_action(interaction, "UNMUTE", user, "Mute removed")
            await interaction.followup.send(
                f"Unmuted {user.mention}.", ephemeral=False
            )
        except discord.Forbidden:
            await interaction.followup.send(
                f"Failed to unmute {user.mention}: the bot needs **Manage Roles** "
                "permission.",
                ephemeral=True,
            )
        except discord.HTTPException as exc:
            await interaction.followup.send(
                f"Failed to unmute {user.mention}: {exc}", ephemeral=True
            )

    @app_commands.command(name="modcase", description="View a user's moderation history")
    @app_commands.describe(user="User to look up")
    async def modcase_cmd(
        self, interaction: discord.Interaction, user: discord.Member
    ):
        if not await is_staff(interaction):
            return await interaction.response.send_message(
                "Insufficient permissions.", ephemeral=True
            )
        db = await get_db()
        cursor = await db.execute(
            "SELECT action_type, reason, moderator_id, created_at, active "
            "FROM moderation_cases WHERE user_id=? ORDER BY created_at DESC LIMIT 25",
            (user.id,),
        )
        rows = await cursor.fetchall()
        if not rows:
            return await interaction.response.send_message(
                f"No moderation history for {user}.", ephemeral=True
            )
        embed = discord.Embed(
            title=f"Moderation History - {user}",
            color=discord.Color.gold(),
        )
        for row in rows:
            mod = interaction.guild.get_member(row["moderator_id"])
            mod_name = str(mod) if mod else f"ID:{row['moderator_id']}"
            active = " [ACTIVE]" if row["active"] else ""
            embed.add_field(
                name=f"{row['action_type']}{active} - {row['created_at'][:16]}",
                value=f"Reason: {row['reason']}\nBy: {mod_name}",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------
    # Scambait channel
    # ------------------------------------------------------------------

    @app_commands.command(
        name="scambait-warning",
        description="[Admin] Post the restricted-channel warning notice in the scambait channel",
    )
    async def scambait_warning_cmd(self, interaction: discord.Interaction):
        if not await is_staff(interaction):
            return await interaction.response.send_message(
                "Insufficient permissions.", ephemeral=True
            )
        if not config.scambait_channel_id:
            return await interaction.response.send_message(
                "SCAMBAIT_CHANNEL_ID is not configured. Cannot post warning.",
                ephemeral=True,
            )
        channel = interaction.guild.get_channel(config.scambait_channel_id)
        if channel is None:
            return await interaction.response.send_message(
                "Scambait channel not found in this guild.", ephemeral=True
            )
        try:
            await channel.send(embed=self._scambait_warning_embed(interaction.guild))
        except discord.Forbidden:
            return await interaction.response.send_message(
                f"Failed to post warning: the bot cannot send messages in <#{config.scambait_channel_id}>.",
                ephemeral=True,
            )
        await interaction.response.send_message(
            f"Warning posted in <#{config.scambait_channel_id}>.", ephemeral=True
        )

    async def _scambait_act(self, message: discord.Message):
        """Soft-ban (timeout) anyone who posts in the restricted scambait channel."""
        guild = message.guild
        member = message.author
        minutes = max(1, int(config.scambait_timeout_minutes or 60))
        until = discord.utils.utcnow() + timedelta(minutes=minutes)
        reason = (
            f"Sent a message in the restricted channel #{message.channel.name} "
            "(scambait)"
        )
        try:
            await message.delete()
        except (discord.Forbidden, discord.HTTPException):
            pass
        try:
            await member.timeout(until, reason=reason)
        except (discord.Forbidden, discord.HTTPException):
            pass
        # Moderation case + mod-log embed.
        await self._log_action_raw(
            guild, "SCAMBAIT_TIMEOUT", member, reason,
            moderator_id=self.bot.user.id or 0,
            expires_at=until.isoformat(),
        )
        # DM the user with the warning and the appeal link.
        appeal = config.appeal_form_url or "https://opsroom.live/appeal"
        try:
            await member.send(
                f"You sent a message in **#{message.channel.name}** on "
                f"{guild.name}, which is a **restricted channel**. "
                f"Do not send messages there."
                + chr(10) * 2
                + f"Your ability to send messages in this server has been temporarily "
                f"suspended for {minutes} minute(s)."
                + chr(10) * 2
                + f"If you believe this is a mistake, appeal here: {appeal}"
            )
        except (discord.Forbidden, discord.HTTPException):
            pass

    def _scambait_warning_embed(self, guild: discord.Guild) -> discord.Embed:
        appeal = config.appeal_form_url or "https://opsroom.live/appeal"
        embed = discord.Embed(
            title="Restricted Channel - Do Not Send Messages",
            description=(
                "This channel is monitored. **Any message posted here results "
                "in an automatic suspension of your ability to send messages "
                "in this server.**"
                + chr(10) * 2
                + f"If you believe this was a mistake, appeal here: {appeal}"
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text=guild.name)
        return embed

    # ------------------------------------------------------------------
    # Automod (on_message event)
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        if await is_staff(message):
            return

        # Scambait channel: any message from a normal member here
        # triggers an instant soft-ban (timeout) with a warning and appeal link.
        if config.scambait_channel_id and message.channel.id == config.scambait_channel_id:
            await self._scambait_act(message)
            return

        # Spam detection
        await self._check_spam(message)
        # Excessive mentions
        await self._check_mentions(message)
        # Excessive caps
        await self._check_caps(message)

    async def _check_spam(self, message: discord.Message):
        cfg = await self._automod_config("spam")
        if not cfg.get("enabled"):
            return
        threshold = cfg.get("threshold", 5)
        uid = message.author.id
        now = datetime.now(timezone.utc).timestamp()
        self._spam_tracker.setdefault(uid, []).append(now)
        # Prune old entries (> 5 sec)
        self._spam_tracker[uid] = [
            t for t in self._spam_tracker[uid] if now - t < 5.0
        ]
        if len(self._spam_tracker[uid]) >= threshold:
            await self._automod_act(message, "spam", "Rapid message spam detected")

    async def _check_mentions(self, message: discord.Message):
        cfg = await self._automod_config("excessive_mentions")
        if not cfg.get("enabled"):
            return
        threshold = cfg.get("threshold", 8)
        mentions = len(message.mentions) + len(message.role_mentions)
        if mentions >= threshold:
            await self._automod_act(message, "excessive_mentions", f"{mentions} mentions in one message")

    async def _check_caps(self, message: discord.Message):
        cfg = await self._automod_config("excessive_caps")
        if not cfg.get("enabled"):
            return
        threshold = cfg.get("threshold", 0.7)
        text = "".join(c for c in message.content if c.isalpha())
        if len(text) < 10:
            return
        caps_ratio = sum(1 for c in text if c.isupper()) / len(text)
        if caps_ratio >= threshold:
            await self._automod_act(message, "excessive_caps", f"{caps_ratio:.0%} uppercase")

    async def _automod_act(self, message: discord.Message, rule: str, detail: str):
        cfg = await self._automod_config(rule)
        action = cfg.get("action", "warn")
        if action == "delete":
            try:
                await message.delete()
            except discord.Forbidden:
                pass
        elif action == "timeout":
            try:
                until = discord.utils.utcnow() + timedelta(minutes=15)
                await message.author.timeout(until, reason=f"Automod: {rule} - {detail}")
            except discord.Forbidden:
                pass
        elif action == "warn":
            await self._log_action_raw(
                message.guild, "AUTOMOD_WARN", message.author,
                f"[{rule}] {detail}", moderator_id=self.bot.user.id or 0,
            )

    async def _automod_config(self, rule: str) -> dict[str, Any]:
        db = await get_db()
        cursor = await db.execute(
            "SELECT enabled, action, threshold, config_json FROM automod_config WHERE rule_key=?",
            (rule,),
        )
        row = await cursor.fetchone()
        if row:
            return {
                "enabled": bool(row["enabled"]),
                "action": row["action"],
                "threshold": row["threshold"],
                "config_json": row["config_json"],
            }
        return dict(AUTOMOD_DEFAULTS.get(rule, {}))

    # ------------------------------------------------------------------
    # Action logging
    # ------------------------------------------------------------------

    async def _log_action(
        self,
        interaction: discord.Interaction,
        action_type: str,
        target: discord.Member | discord.User,
        reason: str,
        expires_at: str | None = None,
    ):
        await self._log_action_raw(
            interaction.guild,
            action_type,
            target,
            reason,
            moderator_id=interaction.user.id,
            expires_at=expires_at,
        )

    async def _log_action_raw(
        self,
        guild: discord.Guild,
        action_type: str,
        target: discord.Member | discord.User,
        reason: str,
        moderator_id: int,
        expires_at: str | None = None,
    ):
        now = datetime.now(timezone.utc).isoformat()
        db = await get_db()
        await db.execute(
            "INSERT INTO moderation_cases(user_id,guild_id,action_type,reason,moderator_id,created_at,expires_at,active) VALUES(?,?,?,?,?,?,?,1)",
            (target.id, guild.id, action_type, reason, moderator_id, now, expires_at),
        )
        await db.commit()

        # Post to mod-log channel
        channel_id = config.mod_log_channel_id
        if channel_id:
            channel = guild.get_channel(channel_id)
            if channel and isinstance(channel, discord.TextChannel):
                embed = discord.Embed(
                    title=f"Moderation: {action_type}",
                    description=f"**Target:** {target} ({target.id})\n**Reason:** {reason}\n**By:** <@{moderator_id}>",
                    color=discord.Color.red(),
                    timestamp=datetime.now(timezone.utc),
                )
                if expires_at:
                    embed.add_field(name="Expires", value=expires_at[:16])
                try:
                    await channel.send(embed=embed)
                except discord.Forbidden:
                    pass


async def setup(bot: commands.Bot):
    await bot.add_cog(Moderation(bot))
