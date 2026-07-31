"""
OPS CONTROL - User Profile Cog

/profile -- View your OPS ROOM profile.
/profile-set -- Configure simulator, network, OPS ROOM version.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_db

logger = logging.getLogger("ops_control.cogs.profile")

SIMULATOR_CHOICES = [
    app_commands.Choice(name="MSFS 2020", value="MSFS2020"),
    app_commands.Choice(name="MSFS 2024", value="MSFS2024"),
    app_commands.Choice(name="X-Plane 11", value="XP11"),
    app_commands.Choice(name="X-Plane 12", value="XP12"),
    app_commands.Choice(name="P3D v5", value="P3Dv5"),
    app_commands.Choice(name="P3D v6", value="P3Dv6"),
    app_commands.Choice(name="Other", value="other"),
]

NETWORK_CHOICES = [
    app_commands.Choice(name="VATSIM", value="VATSIM"),
    app_commands.Choice(name="IVAO", value="IVAO"),
    app_commands.Choice(name="PilotEdge", value="PilotEdge"),
    app_commands.Choice(name="None", value="none"),
]


class ProfileCog(commands.Cog):
    """User profile management."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="profile",
        description="View your OPS ROOM profile.",
    )
    async def profile(self, interaction: discord.Interaction) -> None:
        """Show the user's profile."""
        db = await get_db()
        cursor = await db.execute("SELECT * FROM users WHERE id = ?", (interaction.user.id,))
        row = await cursor.fetchone()

        embed = discord.Embed(
            title=f"Profile -- {interaction.user.display_name}",
            color=0x6366F1,
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)

        if row:
            embed.add_field(name="Simulator", value=row["simulator"] or "Not set", inline=True)
            embed.add_field(name="Network", value=row["network"] or "Not set", inline=True)
            embed.add_field(name="OPS ROOM Version", value=row["opsroom_version"] or "Not set", inline=True)
            embed.add_field(name="Beta Tester", value="Yes" if row["beta_status"] else "No", inline=True)
            embed.add_field(name="First Seen", value=row["first_joined"][:10], inline=True)
        else:
            embed.description = "Profile not yet configured. Use /profile-set to configure your simulator and network."

        embed.set_footer(text="OPS ROOM Operations")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="profile-set",
        description="Set your simulator, network, and OPS ROOM version.",
    )
    @app_commands.describe(
        simulator="Your flight simulator",
        network="Your online network",
        opsroom_version="OPS ROOM version you use",
    )
    async def profile_set(
        self,
        interaction: discord.Interaction,
        simulator: str | None = None,
        network: str | None = None,
        opsroom_version: str | None = None,
    ) -> None:
        """Update profile details."""
        db = await get_db()
        from bot.utils.helpers import utc_now_iso

        updates: list[str] = []
        if simulator:
            updates.append(f"simulator = '{simulator}'")
        if network:
            updates.append(f"network = '{network}'")
        if opsroom_version:
            updates.append(f"opsroom_version = '{opsroom_version}'")

        if not updates:
            await interaction.response.send_message(
                "Provide at least one field to update.",
                ephemeral=True,
            )
            return

        updates.append(f"last_seen = '{utc_now_iso()}'")
        set_clause = ", ".join(updates)

        await db.execute(
            """
            INSERT OR IGNORE INTO users (id, username, display_name, first_joined, last_seen, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (interaction.user.id, interaction.user.name, interaction.user.display_name, utc_now_iso(), utc_now_iso()),
        )
        await db.execute(f"UPDATE users SET {set_clause} WHERE id = ?", (interaction.user.id,))
        await db.commit()

        await interaction.response.send_message(
            "Profile updated. Use /profile to view.",
            ephemeral=True,
        )

    @profile_set.autocomplete("simulator")
    async def sim_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return [c for c in SIMULATOR_CHOICES if current.lower() in c.name.lower()]

    @profile_set.autocomplete("network")
    async def net_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        return [c for c in NETWORK_CHOICES if current.lower() in c.name.lower()]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProfileCog(bot))
    logger.info("Profile cog loaded.")
