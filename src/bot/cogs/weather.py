"""
OPS CONTROL - Weather Cog

/metar ICAO -- METAR weather data from aviationweather.gov.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.api import fetch_metar

logger = logging.getLogger("ops_control.cogs.weather")


class WeatherCog(commands.Cog):
    """Aviation weather commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="metar",
        description="METAR weather data for an ICAO airport.",
    )
    @app_commands.describe(icao="ICAO airport code (e.g. EDDL, KJFK, EGLL)")
    async def metar(self, interaction: discord.Interaction, icao: str) -> None:
        """Fetch METAR for the given ICAO code."""
        icao = icao.strip().upper()

        if len(icao) != 4:
            await interaction.response.send_message(
                "Invalid ICAO code. Provide a 4-letter identifier (e.g. EDDL).",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        try:
            data = await fetch_metar(icao)
        except Exception:
            await interaction.followup.send(
                f"METAR unavailable for {icao}. The weather service may be down.",
                ephemeral=True,
            )
            return

        if data is None:
            await interaction.followup.send(
                f"No METAR report available for {icao}.",
                ephemeral=True,
            )
            return

        temp = data["temperature"]
        temp_str = f"{temp}C" if temp != "N/A" else "N/A"
        dew_str = f"{data['dewpoint']}C" if data["dewpoint"] != "N/A" else "N/A"

        embed = discord.Embed(
            title=f"METAR -- {data['icao']}",
            color=0x2563EB,
            description=f"```{data['raw_text']}```" if data["raw_text"] != "N/A" else None,
        )

        embed.add_field(name="Wind", value=_fmt_wind(data), inline=True)
        embed.add_field(name="Visibility", value=_fmt_vis(data), inline=True)
        embed.add_field(name="Clouds", value=data["clouds"], inline=True)
        embed.add_field(name="Temperature", value=temp_str, inline=True)
        embed.add_field(name="Dewpoint", value=dew_str, inline=True)
        embed.add_field(name="Altimeter", value=_fmt_alt(data), inline=True)
        embed.add_field(name="Flight Category", value=data["flight_category"].upper(), inline=True)
        embed.add_field(name="Observed", value=data["obs_time"], inline=True)

        embed.set_footer(text="Source: aviationweather.gov")

        await interaction.followup.send(embed=embed)


def _fmt_wind(data: dict) -> str:
    wdir = data.get("wind_dir", "N/A")
    wspd = data.get("wind_speed", "N/A")
    if wdir == "N/A" or wspd == "N/A":
        return "N/A"
    return f"{wdir} / {wspd} kt"


def _fmt_vis(data: dict) -> str:
    vis = data.get("visibility", "N/A")
    if vis == "N/A":
        return "N/A"
    try:
        v = float(vis)
        return f"{v} SM" if v < 10 else "10+ SM"
    except (ValueError, TypeError):
        return str(vis)


def _fmt_alt(data: dict) -> str:
    alt = data.get("pressure", "N/A")
    if alt == "N/A":
        return "N/A"
    return f"{alt} inHg"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WeatherCog(bot))
    logger.info("Weather cog loaded.")
