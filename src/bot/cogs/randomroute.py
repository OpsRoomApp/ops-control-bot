"""
OPS CONTROL - Random Route Generator

/randomroute -- Generate a realistic aviation route suggestion.

Primary provider: Where2Fly API (when configured).
Fallback:         improved local database engine.

The result is an operational suggestion only — the suggested operator is
labelled "Suggested Operator / Suggested Callsign" and is never presented
as a confirmed real-world scheduled service.

Buttons:
    Open in SimBrief  -> prefilled dispatch.simbrief.com/options/custom URL
    Generate Another  -> reopens the route modal
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.database import get_db
from bot.services.audit import log_event
from bot.services.routes import (
    InvalidAircraft,
    InvalidDuration,
    InvalidICAO,
    NoRouteFound,
    generate_route,
)
from bot.services.routes.where2fly import parse_filters
from bot.services.simbrief_url import (
    build_simbrief_options_url,
    resolve_static_id,
)

logger = logging.getLogger("ops_control.cogs.randomroute")


class RouteModal(discord.ui.Modal, title="Random Flight Suggestion"):
    """Modal for route generation inputs."""

    aircraft = discord.ui.TextInput(
        label="Aircraft Type",
        placeholder="e.g. A320, B738, B777, A359, AT72",
        required=True,
        max_length=20,
    )
    duration = discord.ui.TextInput(
        label="Flight Duration",
        placeholder="e.g. 45m, 1h30, 2h 30m, 8 hours",
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
        placeholder="e.g. KJFK",
        required=False,
        max_length=4,
        min_length=0,
    )
    filters = discord.ui.TextInput(
        label="Optional Filters",
        placeholder="-windy +atc ifr rwy>6000 size=medium,large region=EU",
        required=False,
        max_length=200,
        min_length=0,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        aircraft_input = self.aircraft.value.strip()
        duration_input = self.duration.value.strip()
        origin_input = self.origin.value.strip() or None
        dest_input = self.destination.value.strip() or None
        filters = parse_filters(self.filters.value or "")

        try:
            route = await generate_route(
                aircraft_input, duration_input, origin_input, dest_input,
                filters=filters,
            )
        except InvalidAircraft as exc:
            await interaction.followup.send(
                f"{exc}. Examples: A320, B738, B777, A359, AT72.",
                ephemeral=True,
            )
            return
        except InvalidDuration as exc:
            await interaction.followup.send(
                f"{exc}. Examples: 45m, 1h30, 2h 30m, 8 hours.",
                ephemeral=True,
            )
            return
        except InvalidICAO as exc:
            await interaction.followup.send(
                f"{exc}. ICAO codes must be 4 letters, e.g. EDDF.",
                ephemeral=True,
            )
            return
        except NoRouteFound as exc:
            # Offer a "Generate Another" retry button.
            view = discord.ui.View()
            view.add_item(GenerateAnotherButton())
            await interaction.followup.send(
                f"No suitable route found: {exc}\n\n"
                "Try a different aircraft, duration, or leave origin/destination empty.",
                view=view,
                ephemeral=True,
            )
            return
        except Exception as exc:
            logger.exception("Route generation failed")
            await interaction.followup.send(
                f"Route generation failed: {exc}",
                ephemeral=True,
            )
            return

        # ---- Professional output embed ----
        try:
            embed = discord.Embed(
                title="Random Flight Suggestion",
                color=0x0EA5E9,
                timestamp=discord.utils.utcnow(),
            )
            for name, value in route.to_embed_fields():
                embed.add_field(name=name, value=value, inline=True)

            attribution = "Operational suggestion only — not a confirmed scheduled service."
            if route.powered_by:
                attribution = f"{route.powered_by} -- {route.route_source}"

            embed.set_footer(text=attribution)

            # ---- SimBrief button ----
            static_id = resolve_static_id(
                await self._linked_static_id(interaction.user.id),
                config.simbrief_static_id,
            )
            url = build_simbrief_options_url(
                airline=route.operator_icao or "OPS",
                fltnum=route.flight_number_digits or "001",
                orig=route.origin,
                dest=route.destination,
                basetype=route.aircraft_code,
                callsign=route.callsign,
                static_id=static_id,
            )

            view = discord.ui.View()
            view.add_item(
                discord.ui.Button(
                    label="Open in SimBrief",
                    style=discord.ButtonStyle.url,
                    url=url,
                )
            )
            view.add_item(GenerateAnotherButton())

            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        except Exception:
            logger.exception("Random route result rendering failed")
            try:
                await interaction.followup.send(
                    "Route generated, but the result could not be rendered. "
                    "Please try again or use a different aircraft/route.",
                    ephemeral=True,
                )
            except Exception:
                logger.exception("Failed to send error notice for random route")

        await log_event(
            "command",
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            guild_id=interaction.guild_id,  # type: ignore[arg-type]
            channel_id=interaction.channel_id,
            detail=(
                f"Random route: {route.flight_number} {route.origin}->{route.destination} "
                f"({route.route_source})"
            ),
        )

    @staticmethod
    async def _linked_static_id(discord_id: int) -> str | None:
        """Return the user's linked SimBrief static_id, if any."""
        try:
            db = await get_db()
            cursor = await db.execute(
                "SELECT static_id FROM simbrief_accounts WHERE discord_id = ?",
                (discord_id,),
            )
            row = await cursor.fetchone()
            return row["static_id"] if row else None
        except Exception:
            return None


class GenerateAnotherButton(discord.ui.Button):
    """Button that reopens the route generation modal."""

    def __init__(self) -> None:
        super().__init__(label="Generate Another", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(RouteModal())


class RandomRouteCog(commands.Cog):
    """Random route generation command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="randomroute",
        description="Generate a realistic aviation route suggestion.",
    )
    async def randomroute(self, interaction: discord.Interaction) -> None:
        """Open the route generation modal."""
        await interaction.response.send_modal(RouteModal())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RandomRouteCog(bot))
    logger.info("Random route cog loaded.")
