"""
OPS CONTROL - Operations Dashboard Cog

/ops-status -- VATSIM network overview with regional breakdown.
/airport-status ICAO -- Airport traffic, controllers, weather aggregate.
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from bot.api import fetch_vatsim_data
from bot.services.noaa_weather import fetch_noaa_metar, fetch_noaa_taf
from bot.services.notam_service import fetch_notams

logger = logging.getLogger("ops_control.cogs.ops_dashboard")


class OpsDashboardCog(commands.Cog):
    """Flight operations dashboard."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ----------------------------------------------------------------
    # /ops-status
    # ----------------------------------------------------------------

    @app_commands.command(
        name="ops-status",
        description="VATSIM network status with regional breakdown.",
    )
    async def ops_status(self, interaction: discord.Interaction) -> None:
        """Show VATSIM network overview."""
        await interaction.response.defer()

        try:
            data = await fetch_vatsim_data()
        except Exception:
            await interaction.followup.send(
                "VATSIM network data unavailable.",
                ephemeral=True,
            )
            return

        pilots = data.get("pilots", [])
        controllers = data.get("controllers", [])

        # Regional breakdown
        regions = _classify_regions(pilots + controllers)

        embed = discord.Embed(
            title="VATSIM Network Status",
            color=0x059669,
        )

        embed.add_field(name="Pilots Online", value=str(len(pilots)), inline=True)
        embed.add_field(name="Controllers Online", value=str(len(controllers)), inline=True)
        embed.add_field(name="Total Connections", value=str(len(pilots) + len(controllers)), inline=True)

        # Regional
        for region, count in sorted(regions.items(), key=lambda x: -x[1]):
            embed.add_field(name=region, value=str(count), inline=True)

        embed.set_footer(text="Source: VATSIM Data API")
        await interaction.followup.send(embed=embed)

    # ----------------------------------------------------------------
    # /airport-status
    # ----------------------------------------------------------------

    @app_commands.command(
        name="airport-status",
        description="Airport operations status: traffic, controllers, weather.",
    )
    @app_commands.describe(icao="ICAO airport code")
    async def airport_status(
        self, interaction: discord.Interaction, icao: str
    ) -> None:
        """Aggregate airport status: VATSIM traffic, controllers, METAR, TAF."""
        icao = icao.strip().upper()
        if len(icao) != 4:
            await interaction.response.send_message(
                "Invalid ICAO code. Provide a 4-letter identifier.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        # Fetch all data in parallel
        try:
            vatsim_data = await fetch_vatsim_data()
        except Exception:
            vatsim_data = None

        try:
            metar = await fetch_noaa_metar(icao)
        except Exception:
            metar = None

        try:
            taf = await fetch_noaa_taf(icao)
        except Exception:
            taf = None

        try:
            notams = await fetch_notams(icao)
        except Exception:
            notams = []

        # VATSIM traffic at this airport
        departures = 0
        arrivals = 0
        local_controllers = []

        if vatsim_data:
            for p in vatsim_data.get("pilots", []):
                plan = p.get("flight_plan") or {}
                if plan.get("departure", "").upper() == icao:
                    departures += 1
                if plan.get("arrival", "").upper() == icao:
                    arrivals += 1

            for c in vatsim_data.get("controllers", []):
                callsign = c.get("callsign", "").upper()
                if callsign.startswith(icao):
                    local_controllers.append({
                        "callsign": callsign,
                        "frequency": c.get("frequency", "N/A"),
                        "name": c.get("name", "N/A"),
                    })

        embed = discord.Embed(
            title=f"Airport Status -- {icao}",
            color=0x0891B2,
        )

        # VATSIM traffic
        if vatsim_data:
            embed.add_field(name="Departures (VATSIM)", value=str(departures), inline=True)
            embed.add_field(name="Arrivals (VATSIM)", value=str(arrivals), inline=True)
            embed.add_field(name="Traffic Total", value=str(departures + arrivals), inline=True)

            if local_controllers:
                ctrl_text = "\n".join(
                    f"{c['callsign']} ({c['frequency']})" for c in local_controllers[:5]
                )
                embed.add_field(name="Controllers", value=ctrl_text, inline=False)
            else:
                embed.add_field(name="Controllers", value="None online", inline=False)

        # METAR
        if metar:
            embed.add_field(
                name="METAR",
                value=f"```{metar.get('raw_text', 'N/A')[:500]}```" if metar.get("raw_text") != "N/A" else "N/A",
                inline=False,
            )
            embed.add_field(
                name="Wind / Vis / Temp",
                value=(
                    f"Wind: {metar['wind_dir']}/{metar['wind_speed']}kt | "
                    f"Vis: {metar['visibility']}SM | "
                    f"Temp: {metar['temperature']}C"
                ),
                inline=False,
            )

        # TAF summary
        if taf:
            embed.add_field(
                name="TAF",
                value=(
                    f"Valid: {taf['valid_from']} to {taf['valid_to']}\n"
                    f"```{taf.get('raw_text', 'N/A')[:400]}```"
                ) if taf.get("raw_text") != "N/A" else "N/A",
                inline=False,
            )

        if not vatsim_data and not metar and not taf:
            embed.description = "No data available for this airport."

        # NOTAMs
        if notams:
            notam_text = "\n".join(
                f"{n['identifier']}: {n.get('description', 'N/A')[:150]}"
                for n in notams[:5]
            )
            embed.add_field(name=f"NOTAMs ({len(notams)})", value=notam_text[:1024], inline=False)

        embed.set_footer(text="Sources: VATSIM / NOAA")
        await interaction.followup.send(embed=embed)


def _classify_regions(connections: list[dict[str, Any]]) -> dict[str, int]:
    """Categorize VATSIM connections by rough geographic region."""
    regions: dict[str, int] = {}
    for conn in connections:
        lat = conn.get("latitude")
        lon = conn.get("longitude")
        if lat is None or lon is None:
            continue

        if lon is not None and lon < -20 and lat > 20:
            region = "North America"
        elif lon is not None and -20 <= lon <= 60 and lat > 25:
            region = "Europe"
        elif lon is not None and lon > 60:
            region = "Asia / Oceania"
        elif lat is not None and lat < 20 and lon is not None and lon < -30:
            region = "South America"
        else:
            region = "Other Regions"

        regions[region] = regions.get(region, 0) + 1
    return regions


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(OpsDashboardCog(bot))
    logger.info("Ops Dashboard cog loaded.")
