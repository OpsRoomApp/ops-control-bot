"""
OPS CONTROL - Permission Utilities (app_commands)

Shared permission checks and guard functions for slash commands.
All cogs should use these for consistent access control.
"""

from __future__ import annotations

import discord
from discord import app_commands

from bot.config import config


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
            "⛔ This command is restricted to the bot owner.",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(
            "⛔ This command is restricted to the bot owner.",
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
            "⛔ You need Administrator permissions or must be the bot owner.",
            ephemeral=True,
        )
    else:
        await interaction.followup.send(
            "⛔ You need Administrator permissions or must be the bot owner.",
            ephemeral=True,
        )
    return False
