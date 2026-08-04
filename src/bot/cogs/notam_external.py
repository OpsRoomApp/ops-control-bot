"""
OPS CONTROL - External NOTAM Cog

/notam-external ICAO -- Fetch active external NOTAMs for an airport (unchanged).
/notams icao ICAO    -- Live FAA NMS NOTAMs for an airport.
/notams geo LAT LON  -- Live FAA NMS NOTAMs within a radius of a point.
/notams fdc ICAO     -- Live FDC NOTAMs (TFRs) for an airport.
/notams checklist ICAO -- Active NOTAM checklist entries for an airport.
/notams search TEXT  -- Free-text search across live NOTAMs (1-80 chars).

v0.25.60: the new /notams group queries the opsroom.live FAA NMS-API proxy
(global coverage incl. FDC/TFR). The existing /notam-external command is
unchanged and now prefers the NMS proxy with the legacy FAA API fallback.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.services.notam_service import (
    fetch_notams,
    fetch_nms_checklist,
    fetch_nms_fdc,
    fetch_nms_geo,
    fetch_nms_notams,
    fetch_nms_search,
)

logger = logging.getLogger("ops_control.cogs.notam_external")


def _notam_embed(icao: str, rows: list[dict]) -> discord.Embed | None:
    if not rows:
        return None
    embed = discord.Embed(title=f"NOTAMs -- {icao}", color=0xEA580C)
    for n in rows[:10]:
        value = f"{n.get('description', 'N/A')[:250]}"
        if len(n.get("description", "")) > 250:
            value += "..."
        embed.add_field(
            name=f"{n['identifier']} ({n['type']})",
            value=(
                f"{value}\n"
                f"Effective: {n['effective']}  |  Expires: {n['expiry']}\n"
                f"Source: {n['source']}"
            ),
            inline=False,
        )
    embed.set_footer(text="Source: FAA NOTAM System (NMS proxy)")
    return embed


def _checklist_embed(location: str, entries: list[dict]) -> discord.Embed | None:
    if not entries:
        return None
    embed = discord.Embed(title=f"NOTAM Checklist -- {location}", color=0xEA580C)
    for entry in entries[:15]:
        embed.add_field(
            name=entry.get("number", "N/A"),
            value=(
                f"Location: {entry.get('location') or entry.get('icaoLocation') or 'N/A'}\n"
                f"Classification: {entry.get('classification') or 'N/A'}\n"
                f"Updated: {entry.get('lastUpdated') or 'N/A'}"
            ),
            inline=False,
        )
    embed.set_footer(text="Source: FAA NOTAM System (NMS checklist)")
    return embed


class NotamExternalCog(commands.Cog):
    """External aviation NOTAM retrieval."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="notam-external",
        description="Fetch active external NOTAMs for an ICAO airport.",
    )
    @app_commands.describe(icao="ICAO airport code (e.g. KLAX, EGLL)")
    async def notam_external(
        self, interaction: discord.Interaction, icao: str
    ) -> None:
        """Fetch external NOTAMs for the given ICAO code."""
        icao = icao.strip().upper()
        if len(icao) != 4:
            await interaction.response.send_message(
                "Invalid ICAO code. Provide a 4-letter identifier.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        try:
            notams = await fetch_notams(icao)
        except Exception:
            await interaction.followup.send(
                f"NOTAM data unavailable for {icao}.",
                ephemeral=True,
            )
            return

        if not notams:
            await interaction.followup.send(
                f"No active external NOTAMs found for {icao}.",
                ephemeral=True,
            )
            return

        embed = _notam_embed(icao, notams)
        await interaction.followup.send(embed=embed or discord.Embed(title=f"NOTAMs -- {icao}"))

    # ------------------------------------------------------------------
    # /notams group -- live FAA NMS proxy queries (v0.25.60)
    # ------------------------------------------------------------------

    notams_group = app_commands.Group(
        name="notams",
        description="Live FAA NMS NOTAM queries (global coverage).",
    )

    @notams_group.command(
        name="icao",
        description="Live FAA NMS NOTAMs for an ICAO airport.",
    )
    @app_commands.describe(icao="ICAO airport code (e.g. KJFK, EGLL)")
    async def notams_icao(self, interaction: discord.Interaction, icao: str) -> None:
        icao = icao.strip().upper()
        if len(icao) != 4:
            await interaction.response.send_message(
                "Invalid ICAO code. Provide a 4-letter identifier.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        try:
            rows = await fetch_nms_notams(location=icao)
        except Exception:
            await interaction.followup.send(
                f"Live NOTAM data unavailable for {icao}.", ephemeral=True
            )
            return
        if not rows:
            await interaction.followup.send(
                f"No live NOTAMs found for {icao}.", ephemeral=True
            )
            return
        embed = _notam_embed(icao, rows)
        await interaction.followup.send(
            embed=embed or discord.Embed(title=f"NOTAMs -- {icao}")
        )

    @notams_group.command(
        name="geo",
        description="Live NOTAMs within a radius of a lat/lon point.",
    )
    @app_commands.describe(
        latitude="Decimal latitude (e.g. 40.6398)",
        longitude="Decimal longitude (e.g. -73.7789)",
        radius_nm="Search radius in nautical miles (default 25)",
    )
    async def notams_geo(
        self,
        interaction: discord.Interaction,
        latitude: float,
        longitude: float,
        radius_nm: float = 25.0,
    ) -> None:
        if not (-90 <= latitude <= 90) or not (-180 <= longitude <= 180):
            await interaction.response.send_message(
                "Invalid coordinates. Latitude -90..90, longitude -180..180.",
                ephemeral=True,
            )
            return
        radius = max(1.0, min(radius_nm, 100.0))
        await interaction.response.defer()
        try:
            rows = await fetch_nms_geo(latitude, longitude, radius)
        except Exception:
            await interaction.followup.send(
                "Live NOTAM data unavailable for that position.", ephemeral=True
            )
            return
        if not rows:
            await interaction.followup.send(
                f"No live NOTAMs within {radius:.0f} NM of that position.",
                ephemeral=True,
            )
            return
        embed = _notam_embed(f"GEO {latitude:.4f},{longitude:.4f} ({radius:.0f} NM)", rows)
        await interaction.followup.send(
            embed=embed or discord.Embed(title="NOTAMs -- GEO")
        )

    @notams_group.command(
        name="fdc",
        description="Live FDC NOTAMs (TFRs) for an ICAO airport.",
    )
    @app_commands.describe(icao="ICAO airport code (e.g. KDFW)")
    async def notams_fdc(self, interaction: discord.Interaction, icao: str) -> None:
        icao = icao.strip().upper()
        if len(icao) != 4:
            await interaction.response.send_message(
                "Invalid ICAO code. Provide a 4-letter identifier.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        try:
            rows = await fetch_nms_fdc(icao)
        except Exception:
            await interaction.followup.send(
                f"FDC NOTAM data unavailable for {icao}.", ephemeral=True
            )
            return
        if not rows:
            await interaction.followup.send(
                f"No live FDC NOTAMs found for {icao}.", ephemeral=True
            )
            return
        embed = _notam_embed(f"FDC NOTAMs -- {icao}", rows)
        await interaction.followup.send(
            embed=embed or discord.Embed(title=f"FDC NOTAMs -- {icao}")
        )

    @notams_group.command(
        name="checklist",
        description="Active NOTAM checklist entries for an airport.",
    )
    @app_commands.describe(icao="ICAO airport code (e.g. KATL)")
    async def notams_checklist(
        self, interaction: discord.Interaction, icao: str
    ) -> None:
        icao = icao.strip().upper()
        if len(icao) != 4:
            await interaction.response.send_message(
                "Invalid ICAO code. Provide a 4-letter identifier.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        try:
            entries = await fetch_nms_checklist(icao)
        except Exception:
            await interaction.followup.send(
                f"NOTAM checklist unavailable for {icao}.", ephemeral=True
            )
            return
        if not entries:
            await interaction.followup.send(
                f"No NOTAM checklist entries found for {icao}.", ephemeral=True
            )
            return
        embed = _checklist_embed(icao, entries)
        await interaction.followup.send(
            embed=embed or discord.Embed(title=f"NOTAM Checklist -- {icao}")
        )

    @notams_group.command(
        name="search",
        description="Free-text search across live NOTAMs (1-80 chars).",
    )
    @app_commands.describe(text="Exact text to search for (e.g. CRANE)")
    async def notams_search(
        self, interaction: discord.Interaction, text: str
    ) -> None:
        text = text.strip()
        if not text or len(text) > 80:
            await interaction.response.send_message(
                "Search text must be 1-80 characters.",
                ephemeral=True,
            )
            return
        await interaction.response.defer()
        try:
            rows = await fetch_nms_search(text)
        except Exception:
            await interaction.followup.send(
                "Live NOTAM search unavailable.", ephemeral=True
            )
            return
        if not rows:
            await interaction.followup.send(
                f"No live NOTAMs matched '{text}'.", ephemeral=True
            )
            return
        embed = _notam_embed(f"Search: {text}", rows)
        await interaction.followup.send(
            embed=embed or discord.Embed(title=f"NOTAM Search -- {text}")
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NotamExternalCog(bot))
    logger.info("External NOTAM cog loaded.")
