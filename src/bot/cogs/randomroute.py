"""
OPS CONTROL - Random Route Generator

/randomroute -- Generate a realistic aviation route suggestion.

Primary provider: Where2Fly API (when configured).
Fallback:         local database engine (only when the API is down).

The preference panel maps 1:1 onto the Where2Fly /api/search parameters so
the generated route always comes from where2fly.today when it is enabled:

    Aircraft dropdown  -> codeletter (GA..JXL)
    Flight time        -> airtimeMin / airtimeMax
    Region dropdown    -> destinations.continents
    Conditions dropdown-> scores[] (weather + VATSIM filters)
    More options modal -> departure / arrival / metcondition /
                          destinationAirportSize / rwyLengthMin

Discord modals cannot host dropdowns (TextInput only), so the dropdowns live
in a View; the few free-form values (ICAO codes etc.) stay in a small modal.

Buttons:
    Open in SimBrief  -> prefilled dispatch.simbrief.com/options/custom URL
    Generate Another  -> reopens the preference panel

Attribution: "Powered by Where2Fly" is shown exactly once, as the clickable
hyperlink field (Discord footers do not render Markdown links). The locally
suggested operator/callsign are no longer displayed — they read as vague and
were never a claim of a real scheduled service.
"""

from __future__ import annotations

import logging
from typing import Any

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
    RouteResult,
    generate_route,
)
from bot.services.simbrief_url import (
    build_simbrief_options_url,
    resolve_static_id,
)

logger = logging.getLogger("ops_control.cogs.randomroute")

# ---------------------------------------------------------------------------
# Where2Fly option catalogs (values are the API's own codes)
# ---------------------------------------------------------------------------

# codeletter -> representative ICAO type used by resolve_aircraft / SimBrief.
CODE_LETTER_DEFAULTS: dict[str, str] = {
    "GA": "C172",
    "GAT": "BE36",
    "GTP": "AT72",
    "JS": "E190",
    "JM": "A320",
    "JML": "B763",
    "JL": "B77W",
    "JXL": "A388",
}

AIRCRAFT_OPTIONS: list[tuple[str, str, str]] = [
    ("GA", "Light GA — C172 / PA28", "Small pistons, short hops"),
    ("GAT", "Turbo GA — Bonanza / Caravan", "Fast pistons and light turboprops"),
    ("GTP", "Heavy Turboprop — AT72 / Q400 / PC-12", "Regional turboprops"),
    ("JS", "Regional Jet — CRJ / E190 / PC-24", "Regional jets"),
    ("JM", "Narrow Body — B737 / A320", "The classic airliner"),
    ("JML", "Mid Wide Body — B757 / B767", "Mid-size wide bodies"),
    ("JL", "Large Wide Body — B777 / B787 / A350", "Long-haul wide bodies"),
    ("JXL", "Super Heavy — B747 / A380", "The big ones"),
]

DURATION_OPTIONS: list[str] = [
    "30m", "45m", "1h", "1h30", "2h", "2h30", "3h",
    "4h", "5h", "6h", "8h", "10h", "12h",
]

REGION_OPTIONS: list[tuple[str, str]] = [
    ("ANY", "Anywhere"),
    ("EU", "Europe"),
    ("NA", "North America"),
    ("AS", "Asia"),
    ("AF", "Africa"),
    ("OC", "Oceania"),
    ("SA", "South America"),
]

# select value -> (Where2Fly score name, required(+1) / excluded(-1))
CONDITION_OPTIONS: list[tuple[str, str, str, int]] = [
    ("VATSIM_ATC:1", "Needs live ATC", "VATSIM_ATC", 1),
    ("VATSIM_EVENT:1", "VATSIM event airports", "VATSIM_EVENT", 1),
    ("VATSIM_POPULAR:1", "Popular on VATSIM", "VATSIM_POPULAR", 1),
    ("METAR_WINDY:-1", "Avoid strong wind", "METAR_WINDY", -1),
    ("METAR_GUSTS:-1", "Avoid gusty wind", "METAR_GUSTS", -1),
    ("METAR_CROSSWIND:-1", "Avoid big crosswinds", "METAR_CROSSWIND", -1),
    ("METAR_FOGGY:-1", "Avoid fog", "METAR_FOGGY", -1),
    ("METAR_THUNDERSTORM:-1", "Avoid storms", "METAR_THUNDERSTORM", -1),
    ("METAR_HEAVY_RAIN:-1", "Avoid heavy rain", "METAR_HEAVY_RAIN", -1),
    ("METAR_HEAVY_SNOW:-1", "Avoid heavy snow", "METAR_HEAVY_SNOW", -1),
]


def _condition_meta(value: str) -> tuple[str, str, int]:
    """Look up (score name, label, sign) for a condition select value."""
    for v, label, name, sign in CONDITION_OPTIONS:
        if v == value:
            return name, label, sign
    return "", "", 0


def _build_filters(
    *,
    region: str | None,
    weather: str | None,
    airport_sizes: list[str],
    runway_min: str | None,
    conditions: list[str],
) -> dict[str, Any]:
    """Assemble the Where2Fly /api/search filter dict from the panel inputs."""
    filters: dict[str, Any] = {}

    if region and region != "ANY":
        filters["destinations"] = {
            "continents": [region],
            "countries": None,
            "states": None,
        }

    if weather and weather != "ANY":
        filters["metcondition"] = weather

    if airport_sizes:
        # The API's canonical values (verified from the server source).
        filters["destinationAirportSize"] = airport_sizes

    if runway_min and runway_min != "ANY":
        filters["rwyLengthMin"] = int(runway_min)

    if conditions:
        scores: dict[str, int] = {}
        for value in conditions:
            name, _label, sign = _condition_meta(value)
            if name:
                scores[name] = sign
        if scores:
            filters["scores"] = scores

    return filters


# ---------------------------------------------------------------------------
# Option modals
# ---------------------------------------------------------------------------


class AirportModal(discord.ui.Modal, title="More route options (all optional)"):
    """Extra free-form options that don't fit dropdowns (ICAO codes etc.)."""

    origin = discord.ui.TextInput(
        label="Origin ICAO", placeholder="e.g. EDDF", required=False, max_length=4
    )
    destination = discord.ui.TextInput(
        label="Destination ICAO", placeholder="e.g. KJFK", required=False, max_length=4
    )
    weather = discord.ui.TextInput(
        label="Weather", placeholder="IFR or VFR (leave empty for any)",
        required=False, max_length=4,
    )
    airport_sizes = discord.ui.TextInput(
        label="Airport sizes", placeholder="e.g. medium,large (small/medium/large)",
        required=False, max_length=40,
    )
    runway_min = discord.ui.TextInput(
        label="Min runway length (ft)", placeholder="e.g. 6000 (optional)",
        required=False, max_length=6,
    )

    def __init__(self, view: "RoutePreferencesView") -> None:
        super().__init__()
        self._view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self._view.origin = (self.origin.value or "").strip().upper() or None
        self._view.destination = (self.destination.value or "").strip().upper() or None
        weather = (self.weather.value or "").strip().upper()
        self._view.weather = weather if weather in ("IFR", "VFR") else None
        sizes = [
            s.strip()
            for s in (self.airport_sizes.value or "").split(",")
            if s.strip() in ("small", "medium", "large")
        ]
        self._view.airport_sizes = [f"{s}_airport" for s in sizes]
        runway = (self.runway_min.value or "").strip()
        self._view.runway_min = runway if runway.isdigit() else None

        await interaction.response.defer(ephemeral=True)
        embed = self._view.summary_embed()
        if self._view.message_id is not None:
            try:
                await interaction.followup.edit(
                    self._view.message_id, embed=embed, view=self._view
                )
                return
            except (discord.HTTPException, discord.NotFound):
                logger.debug("Could not edit prefs message", exc_info=True)
        await interaction.edit_original_response(embed=embed, view=self._view)


# ---------------------------------------------------------------------------
# Preferences view (dropdowns = Where2Fly API params)
# ---------------------------------------------------------------------------


class RoutePreferencesView(discord.ui.View):
    """Dropdown panel for /randomroute, mapping to Where2Fly API parameters.

    Discord action rows are one-select-per-row, so the four most impactful
    params get dropdowns; free-form values live in the \"More options\" modal.
    """

    def __init__(self, *, timeout: float | None = 600) -> None:
        super().__init__(timeout=timeout)
        self.message_id: int | None = None
        self.origin: str | None = None
        self.destination: str | None = None
        self.weather: str | None = None
        self.airport_sizes: list[str] = []
        self.runway_min: str | None = None

        self.aircraft = discord.ui.Select(
            placeholder="Aircraft category (required)",
            options=[
                discord.SelectOption(label=label, value=value, description=desc)
                for value, label, desc in AIRCRAFT_OPTIONS
            ],
            min_values=1,
            max_values=1,
            row=0,
        )
        self.aircraft.callback = self._on_select

        self.duration = discord.ui.Select(
            placeholder="Flight time (required)",
            options=[
                discord.SelectOption(label=d, value=d, description=f"~{d} en route")
                for d in DURATION_OPTIONS
            ],
            min_values=1,
            max_values=1,
            row=1,
        )
        self.duration.callback = self._on_select

        self.region = discord.ui.Select(
            placeholder="Region (optional)",
            options=[
                discord.SelectOption(label=label, value=value)
                for value, label in REGION_OPTIONS
            ],
            min_values=0,
            max_values=1,
            row=2,
        )
        self.region.callback = self._on_select

        self.conditions = discord.ui.Select(
            placeholder="Weather / ATC conditions (optional)",
            options=[
                discord.SelectOption(label=label, value=value)
                for value, label, _name, _sign in CONDITION_OPTIONS
            ],
            min_values=0,
            max_values=len(CONDITION_OPTIONS),
            row=3,
        )
        self.conditions.callback = self._on_select

        self.add_item(self.aircraft)
        self.add_item(self.duration)
        self.add_item(self.region)
        self.add_item(self.conditions)

        more = discord.ui.Button(
            label="More options",
            style=discord.ButtonStyle.secondary,
            row=4,
            custom_id="randomroute_more",
        )
        more.callback = self._open_more_options
        self.add_item(more)

        generate = discord.ui.Button(
            label="Generate Route",
            style=discord.ButtonStyle.primary,
            row=4,
            custom_id="randomroute_generate",
        )
        generate.callback = self._generate
        self.add_item(generate)

    # -- callbacks ------------------------------------------------------

    async def _on_select(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

    async def _open_more_options(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AirportModal(self))

    def summary_embed(self) -> discord.Embed:
        """Live summary of the current preferences."""
        lines = [
            f"**Aircraft:** {self.aircraft.values[0] if self.aircraft.values else '— not chosen yet —'}",
            f"**Flight time:** {self.duration.values[0] if self.duration.values else '— not chosen yet —'}",
            f"**Region:** {self.region.values[0] if self.region.values else 'Anywhere'}",
        ]
        if self.conditions.values:
            labels = [_condition_meta(v)[1] for v in self.conditions.values]
            lines.append(f"**Conditions:** {', '.join(labels)}")
        if self.origin or self.destination:
            lines.append(f"**Airports:** {self.origin or '?'} → {self.destination or '?'}")
        if self.weather:
            lines.append(f"**Weather:** {self.weather}")
        if self.airport_sizes:
            sizes = [s.replace("_airport", "") for s in self.airport_sizes]
            lines.append(f"**Airport sizes:** {', '.join(sizes)}")
        if self.runway_min:
            lines.append(f"**Min runway:** {int(self.runway_min):,} ft")
        return discord.Embed(
            title="Random route — pick your preferences",
            description="\n".join(lines),
            color=0x0EA5E9,
        )

    # -- generation ------------------------------------------------------

    async def _generate(self, interaction: discord.Interaction) -> None:
        if not (self.aircraft.values and self.duration.values):
            await interaction.response.send_message(
                "Pick an aircraft and a flight time first — those are required.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        aircraft_input = CODE_LETTER_DEFAULTS.get(self.aircraft.values[0], "A320")
        duration_input = self.duration.values[0]

        filters = _build_filters(
            region=self.region.values[0] if self.region.values else None,
            weather=self.weather,
            airport_sizes=self.airport_sizes,
            runway_min=self.runway_min,
            conditions=list(self.conditions.values),
        )

        try:
            route = await generate_route(
                aircraft_input,
                duration_input,
                self.origin,
                self.destination,
                filters=filters,
            )
        except InvalidAircraft as exc:
            await interaction.followup.send(
                f"{exc}. Try a different aircraft category.", ephemeral=True
            )
            return
        except InvalidDuration as exc:
            await interaction.followup.send(
                f"{exc}. Pick one of the flight-time options.", ephemeral=True
            )
            return
        except InvalidICAO as exc:
            await interaction.followup.send(
                f"{exc}. ICAO codes must be 4 letters, e.g. EDDF.", ephemeral=True
            )
            return
        except NoRouteFound as exc:
            view = discord.ui.View()
            view.add_item(GenerateAnotherButton())
            await interaction.followup.send(
                f"No suitable route found: {exc}\n\n"
                "Try a different aircraft, flight time, or drop some filters.",
                view=view,
                ephemeral=True,
            )
            return
        except Exception as exc:
            logger.exception("Route generation failed")
            await interaction.followup.send(
                f"Route generation failed: {exc}", ephemeral=True
            )
            return

        try:
            await self._show_result(interaction, route)
        except Exception:
            logger.exception("Random route result rendering failed")
            try:
                await interaction.followup.send(
                    "Route generated, but the result could not be rendered. "
                    "Please try again or loosen a filter.",
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
                f"Random route: {route.origin}->{route.destination} "
                f"({route.route_source})"
            ),
        )

    async def _show_result(
        self, interaction: discord.Interaction, route: RouteResult
    ) -> None:
        """Render the result embed + buttons, replacing the preference panel."""
        embed = build_result_embed(route)

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

        # Replace the source message (the preference panel) in place so the
        # ephemeral thread does not pile up messages. Fall back to sending a
        # new message when the source cannot be edited.
        source_id = self.message_id
        if interaction.message is not None:
            source_id = interaction.message.id
        if source_id is not None:
            try:
                await interaction.followup.edit(
                    source_id, embed=embed, view=view
                )
                return
            except (discord.HTTPException, discord.NotFound):
                logger.debug("Could not replace source message", exc_info=True)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

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


def build_result_embed(route: RouteResult) -> discord.Embed:
    """Build the /randomroute result embed.

    The suggested operator/callsign are intentionally not shown; the
    Where2Fly attribution appears exactly once, as the clickable hyperlink
    field (embed footers do not render Markdown links).
    """
    embed = discord.Embed(
        title="Random Flight Suggestion",
        color=0x0EA5E9,
        timestamp=discord.utils.utcnow(),
    )
    for name, value in route.to_embed_fields():
        embed.add_field(name=name, value=value, inline=True)

    embed.set_footer(
        text="Operational suggestion only \u2014 not a confirmed scheduled service."
    )
    if route.powered_by_url:
        embed.add_field(
            name="Powered by",
            value=f"[{route.powered_by}]({route.powered_by_url})",
            inline=False,
        )
    return embed


class GenerateAnotherButton(discord.ui.Button):
    """Button that reopens the route preference panel."""

    def __init__(self) -> None:
        super().__init__(label="Generate Another", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        view = RoutePreferencesView()
        msg = await interaction.response.edit_message(
            embed=view.summary_embed(), view=view
        )
        if msg is not None:
            view.message_id = msg.id


class RandomRouteCog(commands.Cog):
    """Random route generation command."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="randomroute",
        description="Generate a realistic aviation route suggestion.",
    )
    async def randomroute(self, interaction: discord.Interaction) -> None:
        """Open the route preference panel."""
        view = RoutePreferencesView()
        msg = await interaction.response.send_message(
            embed=view.summary_embed(), view=view, ephemeral=True
        )
        if msg is not None:
            view.message_id = msg.id


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RandomRouteCog(bot))
    logger.info("Random route cog loaded.")
