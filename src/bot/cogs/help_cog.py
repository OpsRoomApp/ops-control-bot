"""
OPS CONTROL - Help Command

/help -- Display available commands based on user role.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config

logger = logging.getLogger("ops_control.cogs.help")

OWNER_ID = config.owner_user_id
MODERATOR_ROLE_ID = config.moderator_role_id


class HelpCog(commands.Cog):
    """Help command showing available bot commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="help",
        description="Display available OPS CONTROL commands.",
    )
    async def help(self, interaction: discord.Interaction) -> None:
        """Show command list based on user permissions."""

        is_owner = interaction.user.id == OWNER_ID
        is_admin = (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.administrator
        )
        is_mod = (
            isinstance(interaction.user, discord.Member)
            and MODERATOR_ROLE_ID
            and any(r.id == MODERATOR_ROLE_ID for r in interaction.user.roles)
        )
        is_staff = is_owner or is_admin or is_mod

        embed = discord.Embed(
            title="OPS CONTROL -- Command Reference",
            color=0x2563EB,
            timestamp=discord.utils.utcnow(),
        )

        # -- User Commands --
        user_commands = [
            ("/help", "Display this command reference"),
            ("/status", "Bot health, version, latency, and loaded modules"),
            ("/ping", "Check bot latency"),
            ("/link-simbrief <username>", "Link your Discord account to SimBrief"),
            ("/ofp [username]", "Fetch your latest Operational Flight Plan"),
            ("/randomroute", "Generate a realistic random flight route"),
            ("/metar <ICAO>", "Aviation weather observation report"),
            ("/weather metar <ICAO>", "Detailed METAR from NOAA"),
            ("/weather taf <ICAO>", "Terminal Area Forecast"),
            ("/atis <ICAO>", "VATSIM ATIS information"),
            ("/notam-external <ICAO>", "Active NOTAMs for an airport"),
            ("/sigmet", "Active aviation weather warnings"),
            ("/vatsim-status", "Current VATSIM network statistics"),
            ("/flightwatch <CALLSIGN>", "Track a VATSIM aircraft"),
            ("/ops-status", "Flight operations dashboard"),
            ("/airport-status <ICAO>", "Aggregated airport status"),
            ("/latest", "Latest OPS ROOM release"),
            ("/changelog", "Recent version changes"),
            ("/roadmap", "OPS ROOM development roadmap"),
            ("/profile", "View your user profile"),
            ("/profile-set", "Update your profile settings"),
            ("/logbook", "View flight logbook"),
            ("/log-flight", "Log a completed flight"),
            ("/airport-add <ICAO> <type>", "Save an airport preference"),
            ("/airport-remove <ICAO>", "Remove a saved airport"),
            ("/preferences", "View notification preferences"),
            ("/preferences-set", "Update notification preferences"),
            ("/roles", "Select your simulator and network preferences"),
        ]

        embed.add_field(
            name="Flight Operations",
            value="\n".join(f"`{cmd}` - {desc}" for cmd, desc in user_commands[:14]),
            inline=False,
        )
        embed.add_field(
            name="User Settings",
            value="\n".join(f"`{cmd}` - {desc}" for cmd, desc in user_commands[14:]),
            inline=False,
        )

        # -- Ticket / Support --
        embed.add_field(
            name="Support",
            value=(
                "`/support` - Create a support ticket\n"
                "`/bug` - Report a bug in OPS ROOM\n"
                "Use the Support Panel buttons in the support channel to create a ticket or report a bug with a guided form."
            ),
            inline=False,
        )

        # -- Staff Commands --
        if is_staff:
            staff_commands = [
                ("/announce <title> <content>", "Send an announcement to the announcements channel"),
                ("/purge <amount>", "Delete messages in bulk"),
                ("/betatester add|remove <user>", "Manage beta tester roles (Beta Coordinator / Moderator)"),
            ]
            embed.add_field(
                name="Staff Commands",
                value="\n".join(f"`{cmd}` - {desc}" for cmd, desc in staff_commands),
                inline=False,
            )

        # -- Owner Commands --
        if is_owner:
            owner_commands = [
                ("/welcome", "Manually test welcome image generation"),
                ("/admin-health", "Detailed bot health report"),
                ("/admin-logs", "Recent audit log entries"),
                ("/admin-db-stats", "Database statistics"),
                ("/notam add/list/remove", "Internal NOTAM management"),
            ]
            embed.add_field(
                name="Owner Commands",
                value="\n".join(f"`{cmd}` - {desc}" for cmd, desc in owner_commands),
                inline=False,
            )

        embed.set_footer(text="OPS ROOM Operations Platform")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
    logger.info("Help cog loaded.")
