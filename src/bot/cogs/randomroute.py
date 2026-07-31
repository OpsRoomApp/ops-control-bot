"""
OPS CONTROL - Random Route Generator

/randomroute -- Generate a realistic random aviation flight.

Opens a modal for aircraft type and flight duration (with optional
origin/destination), then generates a route using the bundled airport
and airline databases and offers an "Open in SimBrief" button.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.database import get_db
from bot.services.routegen import (
    generate_route,
    build_simbrief_url,
    resolve_aircraft,
)
from bot.services.audit import log_event

logger = logging.getLogger("ops_control.cogs.randomroute")


class RouteModal(discord.ui.Modal, title="OPS CONTROL Random Route"):
    """Modal for route generation inputs."""

    aircraft = discord.ui.TextInput(
        label="Aircraft Type",
        placeholder="e.g. A320, B738, B777",
        required=True,
        max_length=20,
    )
    duration = discord.ui.TextInput(
        label="Flight Duration",
        placeholder="e.g. 45 minutes, 2 hours, 8 hours",
        required=True,
        max_length=20,
    )
    origin = discord.ui.TextInput(
        label="Origin ICAO (optional)",
        placeholder="e.g. EDDF",
        required=False,
        max_length=4,
        min_length=0,
    )
    destination = discord.ui.TextInput(
        label="Destination ICAO (optional)",
        placeholder="e.g. LIRF",
        required=False,
        max_length=4,
        min_length=0,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        aircraft_input = self.aircraft.value.strip()
        duration_input = self.duration.value.strip()
        origin_input = self.origin.value.strip() or None
        dest_input = self.destination.value.strip() or None

        if not resolve_aircraft(aircraft_input):
            await interaction.followup.send(
                f"Unknown aircraft type: `{aircraft_input}`. "
                "Examples: A320, B738, B777, A359, DH8D.",
                ephemeral=True,
            )
            return

        try:
            route = generate_route(aircraft_input, duration_input, origin_input, dest_input)
        except Exception as exc:
            logger.exception("Route generation failed")
            await interaction.followup.send(
                f"Route generation failed: {exc}",
                ephemeral=True,
            )
            return

        if not route:
            await interaction.followup.send(
                "Could not generate a route. Check the aircraft type and try again.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="OPS CONTROL Random Route",
            color=0x0EA5E9,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Aircraft", value=route["aircraft"], inline=True)
        embed.add_field(name="Flight", value=route["flight"], inline=True)
        embed.add_field(name="Route", value=route["route"], inline=True)
        embed.add_field(name="Distance", value=f"{route['distance_nm']} NM", inline=True)
        embed.add_field(name="Estimated Time", value=route["flight_time"], inline=True)
        embed.add_field(name="Airline", value=route["airline"], inline=True)
        embed.add_field(name="Callsign", value=route["callsign"], inline=True)
        embed.add_field(
            name="Endpoints",
            value=(
                f"{route['origin']} ({route['origin_name']})\n"
                f"{route['destination']} ({route['destination_name']})"
            ),
            inline=False,
        )
        embed.set_footer(text=f"Cruise speed estimate: {route['speed_kts']} kts")

        # SimBrief button: use linked account if available, else config defaults
        username = None
        static_id = None
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT simbrief_user, static_id FROM simbrief_accounts WHERE discord_id = ?",
                (interaction.user.id,),
            )
            row = await cursor.fetchone()
            if row:
                username = row["simbrief_user"]
                static_id = row["static_id"]
        except Exception:
            pass

        if not username:
            username = config.simbrief_user_id
        if not static_id:
            static_id = config.simbrief_static_id

        url = build_simbrief_url(
            route["aircraft_code"],
            route["origin"],
            route["destination"],
            username,
            static_id,
        )

        view = discord.ui.View()
        view.add_item(
            discord.ui.Button(
                label="Open in SimBrief",
                style=discord.ButtonStyle.url,
                url=url,
            )
        )

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

        await log_event(
            "command",
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            guild_id=interaction.guild_id,  # type: ignore[arg-type]
            channel_id=interaction.channel_id,
            detail=f"Random route: {route['flight']} {route['route']}",
        )


class RandomRouteCog(commands.Cog):
    """Random route generation command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="randomroute",
        description="Generate a realistic random flight route.",
    )
    async def randomroute(self, interaction: discord.Interaction) -> None:
        """Open the route generation modal."""
        await interaction.response.send_modal(RouteModal())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RandomRouteCog(bot))
    logger.info("Random route cog loaded.")
