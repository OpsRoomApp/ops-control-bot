"""
OPS CONTROL - Role Selection System

/roles -- Let users select their simulator, network, and tester status
using Discord select menus. Updates the user's profile in the database
and optionally assigns Discord roles when configured.

Discord onboarding note: the native onboarding flow is managed in the
Discord server settings (Server Settings > Onboarding). The /roles
command here provides a self-service alternative without requiring
manual Discord configuration.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.database import get_db
from bot.utils.helpers import utc_now_iso

logger = logging.getLogger("ops_control.cogs.roles")

SIMULATOR_OPTIONS = [
    discord.SelectOption(label="MSFS 2020", value="MSFS2020", description="Microsoft Flight Simulator 2020"),
    discord.SelectOption(label="MSFS 2024", value="MSFS2024", description="Microsoft Flight Simulator 2024"),
    discord.SelectOption(label="X-Plane 12", value="XP12", description="X-Plane 12"),
    discord.SelectOption(label="X-Plane 11", value="XP11", description="X-Plane 11"),
    discord.SelectOption(label="P3D v5", value="P3Dv5", description="Prepar3D v5"),
    discord.SelectOption(label="P3D v6", value="P3Dv6", description="Prepar3D v6"),
]

NETWORK_OPTIONS = [
    discord.SelectOption(label="VATSIM", value="VATSIM", description="Online ATC network"),
    discord.SelectOption(label="IVAO", value="IVAO", description="International Virtual Aviation Organisation"),
    discord.SelectOption(label="PilotEdge", value="PilotEdge", description="PilotEdge ATC network"),
    discord.SelectOption(label="None / Offline", value="none", description="No online network"),
]

# Tester opt-in: Public Beta is self-serve; Verified Tester is staff-granted via /betatester.
TESTER_OPTIONS = [
    discord.SelectOption(label="Join Public Beta", value="public_beta", description="Self-assign the Public Beta role (opt-in)"),
    discord.SelectOption(label="Leave Public Beta", value="leave_beta", description="Remove the Public Beta role"),
]


class RoleSelectView(discord.ui.View):
    """View with simulator, network, and tester select menus."""

    def __init__(self) -> None:
        super().__init__(timeout=180)

    @discord.ui.select(
        placeholder="Select your simulator",
        min_values=1,
        max_values=1,
        options=SIMULATOR_OPTIONS,
    )
    async def simulator_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        """Update the user's simulator in the database."""
        value = select.values[0]
        db = await get_db()
        await db.execute(
            """
            INSERT OR IGNORE INTO users (id, username, display_name, first_joined, last_seen, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (interaction.user.id, interaction.user.name, interaction.user.display_name, utc_now_iso(), utc_now_iso()),
        )
        await db.execute(
            "UPDATE users SET simulator = ?, last_seen = ? WHERE id = ?",
            (value, utc_now_iso(), interaction.user.id),
        )
        await db.commit()
        await interaction.response.send_message(
            f"Simulator set to **{value}**.", ephemeral=True
        )

    @discord.ui.select(
        placeholder="Select your network",
        min_values=1,
        max_values=1,
        options=NETWORK_OPTIONS,
    )
    async def network_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        """Update the user's network in the database."""
        value = select.values[0]
        db = await get_db()
        await db.execute(
            """
            INSERT OR IGNORE INTO users (id, username, display_name, first_joined, last_seen, is_active)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            (interaction.user.id, interaction.user.name, interaction.user.display_name, utc_now_iso(), utc_now_iso()),
        )
        await db.execute(
            "UPDATE users SET network = ?, last_seen = ? WHERE id = ?",
            (value, utc_now_iso(), interaction.user.id),
        )
        await db.commit()
        await interaction.response.send_message(
            f"Network set to **{value}**.", ephemeral=True
        )

    @discord.ui.select(
        placeholder="Beta program (opt-in)",
        min_values=1,
        max_values=1,
        options=TESTER_OPTIONS,
    )
    async def tester_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        """Self-service Public Beta role opt-in/opt-out."""
        choice = select.values[0]
        public_beta = config.public_beta_role_id

        if not public_beta:
            await interaction.response.send_message(
                "PUBLIC_BETA_ROLE_ID is not configured on this server.",
                ephemeral=True,
            )
            return

        if not isinstance(interaction.user, discord.Member) or not interaction.guild:
            await interaction.response.send_message(
                "This command must be used inside the server.",
                ephemeral=True,
            )
            return

        role = interaction.guild.get_role(public_beta)
        if role is None:
            await interaction.response.send_message(
                "The Public Beta role was not found on this server.",
                ephemeral=True,
            )
            return

        try:
            if choice == "public_beta":
                if role not in interaction.user.roles:
                    await interaction.user.add_roles(role, reason="Self-service Public Beta opt-in")
                await interaction.response.send_message(
                    "You are now opted into the Public Beta program.",
                    ephemeral=True,
                )
            else:
                if role in interaction.user.roles:
                    await interaction.user.remove_roles(role, reason="Self-service Public Beta opt-out")
                await interaction.response.send_message(
                    "You have left the Public Beta program.",
                    ephemeral=True,
                )
        except Exception as exc:
            logger.exception("Failed to update beta role")
            await interaction.response.send_message(
                f"Could not update beta role: {exc}",
                ephemeral=True,
            )


class RolesCog(commands.Cog):
    """Role and profile selection system."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="roles",
        description="Select your simulator, network, and testing preferences.",
    )
    async def roles(self, interaction: discord.Interaction) -> None:
        """Open the role selection panel."""
        embed = discord.Embed(
            title="OPS ROOM Preferences",
            description=(
                "Select your simulator, network, and testing preferences below. "
                "These settings update your OPS ROOM profile.\n\n"
                "**Verified Tester** is granted by staff via /betatester; "
                "Public Beta can be self-selected here."
            ),
            color=0x6366F1,
        )
        embed.set_footer(text="OPS ROOM Operations Platform")
        await interaction.response.send_message(
            embed=embed,
            view=RoleSelectView(),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RolesCog(bot))
    logger.info("Roles cog loaded.")
