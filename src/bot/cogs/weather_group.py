"""
OPS CONTROL - Weather Group Cog

Professional aviation weather commands.
/weather metar ICAO - NOAA METAR data
/weather taf ICAO - NOAA TAF forecast
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.noaa_weather import fetch_noaa_metar, fetch_noaa_taf

logger = logging.getLogger("ops_control.cogs.weather_group")


class WeatherGroupCog(commands.Cog):
    """Aviation weather commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    weather_group = app_commands.Group(
        name="weather",
        description="Aviation weather data from NOAA.",
    )

    # ----------------------------------------------------------------
    # /weather metar
    # ----------------------------------------------------------------

    @weather_group.command(
        name="metar",
        description="METAR report for an ICAO airport.",
    )
    @app_commands.describe(icao="ICAO airport code (e.g. EDDL, KJFK)")
    async def weather_metar(self, interaction: discord.Interaction, icao: str) -> None:
        """Fetch NOAA METAR for the given ICAO code."""
        icao = icao.strip().upper()
        if len(icao) != 4:
            await interaction.response.send_message(
                "Invalid ICAO code. Provide a 4-letter identifier (e.g. EDDL).",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        try:
            data = await fetch_noaa_metar(icao)
        except Exception:
            await interaction.followup.send(
                f"METAR data unavailable for {icao}. The weather service may be down.",
                ephemeral=True,
            )
            return

        if data is None:
            await interaction.followup.send(
                f"No METAR report available for {icao}.",
                ephemeral=True,
            )
            return

        station = f" ({data['station_name']})" if data.get("station_name") else ""
        embed = discord.Embed(
            title=f"METAR -- {data['icao']}{station}",
            color=0x2563EB,
            description=f"```{data['raw_text']}```" if data["raw_text"] != "N/A" else None,
        )

        if data["wind_dir"] != "N/A" or data["wind_speed"] != "N/A":
            wind = f"{data['wind_dir']}/{data['wind_speed']}"
            if data.get("wind_gust") and data["wind_gust"] != "N/A":
                wind += f"G{data['wind_gust']}"
            wind += " kt"
            embed.add_field(name="Wind", value=wind, inline=True)

        vis = data["visibility"]
        embed.add_field(name="Visibility", value=f"{vis} SM" if vis != "N/A" else "N/A", inline=True)

        clouds = data.get("clouds", [])
        cloud_str = ", ".join(
            f"{c['cover']} {c['base_ft']}ft" for c in clouds
        ) if clouds else "N/A"
        embed.add_field(name="Clouds", value=cloud_str, inline=True)

        temp = data["temperature"]
        dew = data["dewpoint"]
        embed.add_field(name="Temperature", value=f"{temp}C" if temp != "N/A" else "N/A", inline=True)
        embed.add_field(name="Dewpoint", value=f"{dew}C" if dew != "N/A" else "N/A", inline=True)
        embed.add_field(name="Altimeter", value=f"{data['pressure']} inHg" if data["pressure"] != "N/A" else "N/A", inline=True)

        embed.add_field(name="Flight Category", value=data["flight_category"].upper(), inline=True)
        embed.add_field(name="Observed", value=data["obs_time"], inline=True)
        embed.add_field(name="Elevation", value=f"{data['elevation']} ft" if data["elevation"] != "N/A" else "N/A", inline=True)

        embed.set_footer(text="Source: NOAA Aviation Weather Center")

        await interaction.followup.send(embed=embed)

    # ----------------------------------------------------------------
    # /weather taf
    # ----------------------------------------------------------------

    @weather_group.command(
        name="taf",
        description="TAF forecast for an ICAO airport.",
    )
    @app_commands.describe(icao="ICAO airport code (e.g. EDDL, KJFK)")
    async def weather_taf(self, interaction: discord.Interaction, icao: str) -> None:
        """Fetch NOAA TAF for the given ICAO code."""
        icao = icao.strip().upper()
        if len(icao) != 4:
            await interaction.response.send_message(
                "Invalid ICAO code. Provide a 4-letter identifier.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        try:
            data = await fetch_noaa_taf(icao)
        except Exception:
            await interaction.followup.send(
                f"TAF data unavailable for {icao}.",
                ephemeral=True,
            )
            return

        if data is None:
            await interaction.followup.send(
                f"No TAF forecast available for {icao}.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"TAF -- {data['icao']}",
            color=0x7C3AED,
            description=f"```{data['raw_text']}```" if data["raw_text"] != "N/A" else None,
        )

        embed.add_field(name="Issued", value=data["issue_time"], inline=True)
        embed.add_field(name="Valid From", value=data["valid_from"], inline=True)
        embed.add_field(name="Valid To", value=data["valid_to"], inline=True)

        for i, fcst in enumerate(data.get("forecast", [])[:4]):
            clouds = ", ".join(
                f"{c['cover']} {c['base_ft']}ft" for c in fcst.get("clouds", [])
            ) or "N/A"
            fcst_text = (
                f"Wind: {fcst['wind_dir']}/{fcst['wind_speed']}kt | "
                f"Vis: {fcst['visibility']}SM\n"
                f"Clouds: {clouds}"
            )
            label = f"Forecast {i+1}: {fcst.get('time_from', 'N/A')} - {fcst.get('time_to', 'N/A')}"
            if fcst.get("change"):
                label = f"{fcst['change']}: {fcst.get('time_from', 'N/A')}"
            embed.add_field(name=label, value=fcst_text, inline=False)

        embed.set_footer(text="Source: NOAA Aviation Weather Center")

        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WeatherGroupCog(bot))
    logger.info("Weather Group cog loaded.")
