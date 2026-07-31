"""
OPS CONTROL - Flight Logging Cog

/logbook -- View recent flight logs.
/log-flight -- [Owner] Manually log a flight.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_db
from bot.utils.helpers import utc_now_iso

logger = logging.getLogger("ops_control.cogs.logbook")


class LogbookCog(commands.Cog):
    """Flight logbook and history."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="logbook",
        description="View your recent flight logs.",
    )
    async def logbook(self, interaction: discord.Interaction) -> None:
        """Show the user's recent flights."""
        await interaction.response.defer(ephemeral=True)

        db = await get_db()
        cursor = await db.execute(
            """
            SELECT * FROM flight_logs
            WHERE user_id = ?
            ORDER BY submitted_at DESC LIMIT 10
            """,
            (interaction.user.id,),
        )
        rows = await cursor.fetchall()

        if not rows:
            await interaction.followup.send(
                "No flight logs yet. Flight logging will be available when OPS ROOM telemetry integration is enabled.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(title="Flight Logbook", color=0x2563EB)

        for i, row in enumerate(rows, 1):
            route = f"{row['departure'] or '???'} - {row['arrival'] or '???'}"
            duration = f"{row['duration_min']:.0f} min" if row["duration_min"] else "N/A"
            landing = f"{row['landing_rate']:.0f} fpm" if row["landing_rate"] else "N/A"
            score = f"{row['score']:.0f}" if row["score"] else "N/A"

            embed.add_field(
                name=f"#{i} {row['callsign'] or 'Flight'} -- {route}",
                value=(
                    f"Aircraft: {row['aircraft'] or 'N/A'}\n"
                    f"Duration: {duration} | Landing: {landing} | Score: {score}\n"
                    f"Date: {row['submitted_at'][:10]}"
                ),
                inline=False,
            )

        embed.set_footer(text="OPS ROOM Flight Data")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="log-flight",
        description="[Owner] Manually log a flight.",
    )
    @app_commands.describe(
        callsign="Flight callsign",
        aircraft="Aircraft type",
        departure="Departure ICAO",
        arrival="Arrival ICAO",
        duration="Flight duration in minutes",
        landing_rate="Landing rate in fpm",
        score="Flight score",
    )
    async def log_flight(
        self,
        interaction: discord.Interaction,
        callsign: str,
        aircraft: str,
        departure: str,
        arrival: str,
        duration: float = 0.0,
        landing_rate: float = 0.0,
        score: float = 0.0,
    ) -> None:
        """Manually log a flight (owner only)."""
        from bot.utils.permissions import require_owner
        if not await require_owner(interaction):
            return

        db = await get_db()
        await db.execute(
            """
            INSERT INTO flight_logs (user_id, username, callsign, aircraft, departure, arrival,
                                     duration_min, landing_rate, score, submitted_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (interaction.user.id, interaction.user.display_name, callsign.upper(), aircraft,
             departure.upper(), arrival.upper(), duration, landing_rate, score, utc_now_iso()),
        )
        await db.commit()

        await interaction.response.send_message(
            f"Flight logged: {callsign.upper()} {departure.upper()} - {arrival.upper()}",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LogbookCog(bot))
    logger.info("Logbook cog loaded.")
