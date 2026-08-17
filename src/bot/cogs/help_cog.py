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
            title="OPS CONTROL - everything you can do here",
            description=(
                "Hey! Here's what I can do, grouped by what it's for. "
                "**< >** means you type a value (like an ICAO code); "
                "**[]** means it's optional. "
                "Got a question I can't answer? `/support` opens a ticket."
            ),
            color=0x2563EB,
            timestamp=discord.utils.utcnow(),
        )

        # -- Flight Operations --
        flight_commands = [
            ("/flightwatch <CALLSIGN>", "Watch a VATSIM aircraft live"),
            ("/vatsim-status", "How busy is the network right now"),
            ("/flight vatsim", "VATSIM network status (group)"),
            ("/flight opensky [icao24]", "Live aircraft from OpenSky"),
            ("/flight simbrief <username>", "Pull a SimBrief flight plan"),
            ("/flight status", "Flight ops API health check"),
            ("/ops-status", "Flight operations dashboard"),
            ("/airport-status <ICAO>", "One-stop airport snapshot: VATSIM + NOTAMs + weather"),
            ("/vatsim-set <CID>", "Link your VATSIM CID (auto takeoff/landing posts)"),
            ("/vatsim-unset", "Unlink your VATSIM CID"),
            ("/vatsim-linked", "Show your linked VATSIM CID"),
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
            ("/randomroute", "Spin up a random route - pick aircraft, time, region, conditions"),
        ]
        embed.add_field(
            name="SimBrief & Flight Planning",
            value="\n".join(f"`{cmd}` - {desc}" for cmd, desc in simbrief_commands),
            inline=False,
        )

        # -- Weather & Briefing --
        weather_commands = [
            ("/metar <ICAO>", "Quick METAR for an airport"),
            ("/weather metar <ICAO>", "Detailed NOAA METAR"),
            ("/weather taf <ICAO>", "Terminal Area Forecast"),
            ("/atis <ICAO>", "Live VATSIM ATIS text"),
            ("/notam-external <ICAO>", "Current NOTAMs for an airport"),
            ("/notams icao|geo|fdc|checklist|search", "Live FAA NMS NOTAMs - by airport, area, TFRs/FDCs, checklists, free text"),
            ("/sigmet", "Active aviation weather advisories"),
        ]
        embed.add_field(
            name="Weather & Briefing",
            value="\n".join(f"`{cmd}` - {desc}" for cmd, desc in weather_commands),
            inline=False,
        )

        # -- Community / Support --
        community_commands = [
            ("/rules", "The house rules"),
            ("/support", "Open a support ticket"),
            ("/bug", "Report an OPS ROOM bug"),
            ("/feedback", "Submit feedback or a feature request"),
            ("/status", "Bot health, version, latency, loaded modules"),
            ("/ping", "Latency check"),
            ("/profile", "Your user profile"),
            ("/profile-set", "Update profile (simulator / network)"),
            ("/roles", "Pick your simulator and network roles"),
            ("/leaderboard [period] [sort]", "Community flight leaderboard (hours, flights, landing rate)"),
            ("/flight-visibility <discord|public|hidden>", "Choose where your flights appear"),
            ("/link-app", "Get a one-time pairing code for the desktop app"),
        ]
        embed.add_field(
            name="Community & Profile",
            value="\n".join(f"`{cmd}` - {desc}" for cmd, desc in community_commands),
            inline=False,
        )

        # -- Tools & Information --
        tools_commands = [
            ("/logbook", "Your flight log"),
            ("/log-flight", "Log a flight you just finished"),
            ("/airport-add <ICAO> <type>", "Save an airport preference"),
            ("/airport-remove <ICAO>", "Drop a saved airport"),
            ("/preferences", "Your notification preferences"),
            ("/preferences-set", "Change your notification preferences"),
            ("/latest", "Newest OPS ROOM release"),
            ("/changelog", "What changed in recent versions"),
            ("/roadmap", "What's coming down the pipe"),
            ("/help", "This list again"),
        ]
        embed.add_field(
            name="Tools & Information",
            value="\n".join(f"`{cmd}` - {desc}" for cmd, desc in tools_commands),
            inline=False,
        )

        # -- Staff Commands --
        if is_staff:
            staff_commands = [
                ("/announce <title> <content>", "Post an announcement to the announcements channel"),
                ("/purge <amount>", "Bulk-delete messages"),
                ("/betatester add|remove <user>", "Manage beta tester roles"),
                ("/warn <user> <reason>", "Warn a user"),
                ("/kick <user> <reason>", "Kick a user"),
                ("/ban <user> <reason>", "Ban a user"),
                ("/unban <user_id>", "Unban a user by ID"),
                ("/timeout <user> <minutes> <reason>", "Timeout a user"),
                ("/untimeout <user>", "Remove a user's timeout"),
                ("/mute <user> [hours] [reason]", "Role-based mute (supports permanent)"),
                ("/unmute <user>", "Remove the Muted role"),
                ("/modcase <user>", "A user's moderation history"),
                ("/notam add|list|remove", "Internal NOTAM management"),
                ("/verify-setup", "Post or refresh the persistent Verify button"),
                ("/scambait-warning", "Post the restricted-channel warning in the scambait channel"),
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

        embed.set_footer(text="Stuck on something? /support gets you a human. - OPS ROOM")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(HelpCog(bot))
    logger.info("Help cog loaded.")
