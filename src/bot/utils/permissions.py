"""
OPS CONTROL - Permission Utilities (app_commands)

Shared permission checks and guard functions for slash commands.
All cogs should use these for consistent access control.
"""

from __future__ import annotations

import discord
from discord import app_commands

from bot.config import config


async def is_staff(interaction_or_message: discord.Interaction | discord.Message) -> bool:
    """Return True when the user is bot owner, a guild admin, or holds any
    staff role (OPS CONTROL / Moderator / Support Dispatch).

    Accepts either an Interaction or a Message so it can guard both slash
    commands and on_message automod listeners.
    """
    user = getattr(interaction_or_message, "user", None) or getattr(interaction_or_message, "author", None)
    if user is None:
        return False
    if user.id == config.owner_user_id:
        return True
    member = user if isinstance(user, discord.Member) else None
    if member is None:
        # Try resolving the author against the guild when available.
        guild = getattr(interaction_or_message, "guild", None)
        if guild is not None:
            member = guild.get_member(user.id)
    if member is not None and member.guild_permissions.administrator:
        return True
    staff_role_ids = {
        r for r in (
            config.ops_control_role_id,
            config.moderator_role_id,
            config.support_dispatch_role_id,
        ) if r
    }
    if not staff_role_ids:
        return False
    return any(r.id in staff_role_ids for r in member.roles) if member is not None else False


async def require_owner(interaction: discord.Interaction) -> bool:
    """
    Ensure the interaction user is the bot owner.

    Sends an ephemeral error message and returns False if not.
    Callers should `return` early on False.

    Usage:
        if not await require_owner(interaction):
            return
    """
    if interaction.user.id == config.owner_user_id:
        return True

    # Only send message if we haven't already responded
    if not interaction.response.is_done():
        await interaction.response.send_message(
            "This command is restricted to the bot owner.",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(
            "This command is restricted to the bot owner.",
            ephemeral=True,
        )
    return False


async def require_owner_or_admin(interaction: discord.Interaction) -> bool:
    """
    Ensure the interaction user is the bot owner or a guild admin.

    Sends an ephemeral error message and returns False if not.

    Usage:
        if not await require_owner_or_admin(interaction):
            return
    """
    # Bot owner always passes
    if interaction.user.id == config.owner_user_id:
        return True

    # Guild admins pass
    if (
        isinstance(interaction.user, discord.Member)
        and interaction.user.guild_permissions.administrator
    ):
        return True

    if not interaction.response.is_done():
        await interaction.response.send_message(
            "You need Administrator permissions or must be the bot owner.",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(
            "You need Administrator permissions or must be the bot owner.",
            ephemeral=True,
        )
    return False
