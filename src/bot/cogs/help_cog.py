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

        # -- Flight Operations --
        flight_commands = [
            ("/flightwatch <CALLSIGN>", "Track a VATSIM aircraft in real time"),
            ("/vatsim-status", "Current VATSIM network statistics"),
            ("/flight vatsim", "VATSIM network status (group)"),
            ("/flight opensky [icao24]", "OpenSky Network live aircraft states"),
            ("/flight simbrief <username>", "SimBrief flight plan by username"),
            ("/flight status", "Flight operations API health check"),
            ("/ops-status", "Flight operations dashboard"),
            ("/airport-status <ICAO>", "Aggregated airport status (VATSIM + NOTAM + weather)"),
            ("/vatsim-set <CID>", "Link your VATSIM CID for auto takeoff/landing posts"),
            ("/vatsim-unset", "Remove your linked VATSIM CID"),
        ]
        embed.add_field(
            name="Flight Operations & VATSIM",
            value="\n".join(f"`{cmd}` - {desc}" for cmd, desc in flight_commands),
            inline=False,
        )

        # -- SimBrief / Flight Planning --
        simbrief_commands = [
            ("/link-simbrief <username> [static_id]", "Link your Discord account to SimBrief"),
            ("/ofp [username]", "Fetch your latest Operational Flight Plan"),
            ("/randomroute", "Generate a realistic random flight route with SimBrief button"),
        ]
        embed.add_field(
            name="SimBrief & Flight Planning",
            value="\n".join(f"`{cmd}` - {desc}" for cmd, desc in simbrief_commands),
            inline=False,
        )

        # -- Weather & Briefing --
        weather_commands = [
            ("/metar <ICAO>", "Aviation weather observation report"),
            ("/weather metar <ICAO>", "Detailed METAR from NOAA"),
            ("/weather taf <ICAO>", "Terminal Area Forecast"),
            ("/atis <ICAO>", "VATSIM ATIS information"),
            ("/notam-external <ICAO>", "Active NOTAMs for an airport"),
            ("/sigmet", "Active aviation weather warnings"),
        ]
        embed.add_field(
            name="Weather & Briefing",
            value="\n".join(f"`{cmd}` - {desc}" for cmd, desc in weather_commands),
            inline=False,
        )

        # -- Community / Support --
        community_commands = [
            ("/rules", "View the community rules (owner-configurable)"),
            ("/support", "Create a support ticket"),
            ("/bug", "Report a bug in OPS ROOM"),
            ("/status", "Bot health, version, latency, and loaded modules"),
            ("/ping", "Check bot latency"),
            ("/profile", "View your user profile"),
            ("/profile-set", "Update your profile settings (simulator / network)"),
            ("/roles", "Select your simulator and network preferences"),
        ]
        embed.add_field(
            name="Community & Profile",
            value="\n".join(f"`{cmd}` - {desc}" for cmd, desc in community_commands),
            inline=False,
        )

        # -- Tools & Information --
        tools_commands = [
            ("/logbook", "View flight logbook"),
            ("/log-flight", "Log a completed flight"),
            ("/airport-add <ICAO> <type>", "Save an airport preference"),
            ("/airport-remove <ICAO>", "Remove a saved airport"),
            ("/preferences", "View notification preferences"),
            ("/preferences-set", "Update notification preferences"),
            ("/latest", "Latest OPS ROOM release"),
            ("/changelog", "Recent version changes"),
            ("/roadmap", "OPS ROOM development roadmap"),
            ("/help", "Display this command reference"),
        ]
        embed.add_field(
            name="Tools & Information",
            value="\n".join(f"`{cmd}` - {desc}" for cmd, desc in tools_commands),
            inline=False,
        )

        # -- Staff Commands --
        if is_staff:
            staff_commands = [
                ("/announce <title> <content>", "Send an announcement to the announcements channel"),
                ("/purge <amount>", "Delete messages in bulk"),
                ("/betatester add|remove <user>", "Manage beta tester roles"),
                ("/warn <user> <reason>", "Warn a user"),
                ("/kick <user> <reason>", "Kick a user"),
                ("/ban <user> <reason>", "Ban a user"),
                ("/unban <user_id>", "Unban a user by ID"),
                ("/timeout <user> <minutes> <reason>", "Timeout a user"),
                ("/untimeout <user>", "Remove a user's timeout"),
                ("/mute <user> [hours] [reason]", "Role-based mute (supports permanent)"),
                ("/unmute <user>", "Remove the Muted role"),
                ("/modcase <user>", "View a user's moderation history"),
                ("/notam add|list|remove", "Internal NOTAM management"),
            ]
            embed.add_field(
                name="Staff Commands",
                value="\n".join(f"`{cmd}` - {desc}" for cmd, desc in staff_commands),
                inline=False,
            )

        # -- Admin / Owner Commands --
        if is_owner:
            owner_commands = [
                ("/welcome", "Manually test welcome image generation"),
                ("/rolepanel", "Post the persistent reaction-role panel in this channel"),
                ("/setup-support-panel", "Create the persistent support panel in this channel"),
                ("/admin-health", "Detailed bot health report"),
                ("/admin-logs", "Recent audit log entries"),
                ("/admin-db-stats", "Database statistics"),
                ("/rules-set <content>", "Set the community rules (owner only)"),
                ("/rules-reset", "Restore the default community rules (owner only)"),
            ]
            embed.add_field(
                name="Admin / Owner Commands",
                value="\n".join(f"`{cmd}` - {desc}" for cmd, desc in owner_commands),
                inline=False,
            )

        embed.set_footer(text="OPS ROOM Operations Platform")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
    logger.info("Help cog loaded.")
