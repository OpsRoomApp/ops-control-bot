"""
OPS CONTROL - Role Selection System

/roles -- Let users select their simulator, network, and tester status
using Discord select menus. Updates the user's profile in the database
and optionally assigns Discord roles when configured.
/rolepanel -- [Admin] Post the persistent reaction-role panel (v0.25.55 / B4)

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
        await interaction.response.send_message(f"Simulator set to **{value}**.", ephemeral=True)

    @discord.ui.select(
        placeholder="Select your network",
        min_values=1,
        max_values=1,
        options=NETWORK_OPTIONS,
    )
    async def network_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
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
        await interaction.response.send_message(f"Network set to **{value}**.", ephemeral=True)

    @discord.ui.select(
        placeholder="Beta program (opt-in)",
        min_values=1,
        max_values=1,
        options=TESTER_OPTIONS,
    )
    async def tester_select(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        choice = select.values[0]
        public_beta = config.public_beta_role_id
        if not public_beta:
            await interaction.response.send_message("PUBLIC_BETA_ROLE_ID is not configured.", ephemeral=True)
            return
        if not isinstance(interaction.user, discord.Member) or not interaction.guild:
            await interaction.response.send_message("Must be used inside the server.", ephemeral=True)
            return
        role = interaction.guild.get_role(public_beta)
        if role is None:
            await interaction.response.send_message("Public Beta role not found.", ephemeral=True)
            return
        try:
            if choice == "public_beta":
                if role not in interaction.user.roles:
                    await interaction.user.add_roles(role, reason="Self-service Public Beta opt-in")
                await interaction.response.send_message("You are now opted into Public Beta.", ephemeral=True)
            else:
                if role in interaction.user.roles:
                    await interaction.user.remove_roles(role, reason="Self-service Public Beta opt-out")
                await interaction.response.send_message("You have left Public Beta.", ephemeral=True)
        except Exception as exc:
            logger.exception("Failed to update beta role")
            await interaction.response.send_message(f"Could not update beta role: {exc}", ephemeral=True)


# v0.25.55 (B4) -- Persistent reaction-role panel that survives bot restarts
class PersistentRolePanel(discord.ui.View):
    """A persistent, restart-safe role-selection panel posted via /rolepanel."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="MSFS 2020", style=discord.ButtonStyle.secondary,
                       custom_id="role:msfs2020", row=0)
    async def msfs2020_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "MSFS2020")

    @discord.ui.button(label="MSFS 2024", style=discord.ButtonStyle.secondary,
                       custom_id="role:msfs2024", row=0)
    async def msfs2024_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "MSFS2024")

    @discord.ui.button(label="VATSIM", style=discord.ButtonStyle.success,
                       custom_id="role:vatsim", row=1)
    async def vatsim_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "VATSIM")

    @discord.ui.button(label="IVAO", style=discord.ButtonStyle.success,
                       custom_id="role:ivao", row=1)
    async def ivao_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "IVAO")

    @discord.ui.button(label="Public Beta", style=discord.ButtonStyle.primary,
                       custom_id="role:publicbeta", row=2)
    async def publicbeta_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._toggle(interaction, "Public Beta")

    async def _toggle(self, interaction: discord.Interaction, label: str):
        if not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("Unknown user.", ephemeral=True)
        role = discord.utils.get(interaction.guild.roles, name=label)
        if role is None:
            return await interaction.response.send_message(f"Role '{label}' not found.", ephemeral=True)
        has_role = role in interaction.user.roles
        try:
            if has_role:
                await interaction.user.remove_roles(role)
                await interaction.response.send_message(f"Removed {label}.", ephemeral=True)
            else:
                await interaction.user.add_roles(role)
                await interaction.response.send_message(f"Added {label}!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I cannot manage that role.", ephemeral=True)


class RolesCog(commands.Cog):
    """Role and profile selection system."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="rolepanel",
        description="[Admin] Post the persistent reaction-role panel in this channel.",
    )
    @app_commands.default_permissions(administrator=True)
    async def rolepanel(self, interaction: discord.Interaction) -> None:
        """Post the persistent role selection panel (v0.25.55 / B4)."""
        embed = discord.Embed(
            title="OPS ROOM - Select Your Roles",
            description=(
                "Click a button below to join or leave a role.\n\n"
                "**Simulators:** MSFS 2020 | MSFS 2024\n"
                "**Networks:** VATSIM | IVAO\n"
                "**Testing:** Public Beta"
            ),
            color=0x6366F1,
        )
        embed.set_footer(text="OPS ROOM Role Panel")
        await interaction.channel.send(embed=embed, view=PersistentRolePanel())
        await interaction.response.send_message("Role panel posted!", ephemeral=True)

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
        await interaction.response.send_message(embed=embed, view=RoleSelectView(), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RolesCog(bot))
    logger.info("Roles cog loaded.")
