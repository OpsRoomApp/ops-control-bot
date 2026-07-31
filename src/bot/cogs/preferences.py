"""
OPS CONTROL - User Preferences Cog

/airport-add ICAO TYPE -- Save an airport preference.
/airport-remove ICAO -- Remove a saved airport.
/preferences -- View notification and airport preferences.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_db
from bot.utils.helpers import utc_now_iso

logger = logging.getLogger("ops_control.cogs.preferences")

AIRPORT_TYPES = [
    app_commands.Choice(name="Departure", value="departure"),
    app_commands.Choice(name="Arrival", value="arrival"),
    app_commands.Choice(name="Alternate", value="alternate"),
]


class PreferencesCog(commands.Cog):
    """User airport and notification preferences."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="airport-add",
        description="Save an airport to your preferences.",
    )
    @app_commands.describe(
        icao="ICAO airport code",
        airport_type="Type: Departure, Arrival, or Alternate",
    )
    async def airport_add(
        self,
        interaction: discord.Interaction,
        icao: str,
        airport_type: str,
    ) -> None:
        """Add an airport preference."""
        icao = icao.strip().upper()
        if len(icao) != 4:
            await interaction.response.send_message(
                "Invalid ICAO code. Provide a 4-letter identifier.",
                ephemeral=True,
            )
            return

        db = await get_db()
        try:
            await db.execute(
                """
                INSERT OR IGNORE INTO airport_preferences (user_id, icao, airport_type, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (interaction.user.id, icao, airport_type, utc_now_iso()),
            )
            await db.commit()
        except Exception:
            await interaction.response.send_message(
                "Database error. Airport may already be saved.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Saved {icao} as {airport_type} airport.",
            ephemeral=True,
        )

    @airport_add.autocomplete("airport_type")
    async def ap_type_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return [c for c in AIRPORT_TYPES if current.lower() in c.name.lower()]

    @app_commands.command(
        name="airport-remove",
        description="Remove a saved airport from your preferences.",
    )
    @app_commands.describe(icao="ICAO airport code to remove")
    async def airport_remove(
        self, interaction: discord.Interaction, icao: str
    ) -> None:
        """Remove an airport preference."""
        icao = icao.strip().upper()
        db = await get_db()
        cursor = await db.execute(
            "DELETE FROM airport_preferences WHERE user_id = ? AND icao = ?",
            (interaction.user.id, icao),
        )
        await db.commit()

        if cursor.rowcount == 0:
            await interaction.response.send_message(
                f"No saved airports matching {icao}.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"Removed {icao} from saved airports.",
            ephemeral=True,
        )

    @app_commands.command(
        name="preferences",
        description="View your saved airports and notification settings.",
    )
    async def preferences(self, interaction: discord.Interaction) -> None:
        """Show user preferences."""
        db = await get_db()

        # Airport preferences
        cursor = await db.execute(
            "SELECT icao, airport_type FROM airport_preferences WHERE user_id = ? ORDER BY airport_type",
            (interaction.user.id,),
        )
        airports = await cursor.fetchall()

        # Notification preferences
        cursor = await db.execute(
            "SELECT * FROM notifications WHERE user_id = ?",
            (interaction.user.id,),
        )
        notif_row = await cursor.fetchone()

        embed = discord.Embed(
            title=f"Preferences -- {interaction.user.display_name}",
            color=0x6366F1,
        )

        # Airports
        if airports:
            by_type: dict[str, list[str]] = {}
            for row in airports:
                by_type.setdefault(row["airport_type"], []).append(row["icao"])
            for apt_type, icaos in by_type.items():
                embed.add_field(
                    name=apt_type.title(),
                    value=", ".join(icaos),
                    inline=False,
                )
        else:
            embed.add_field(
                name="Saved Airports",
                value="None. Use /airport-add to save airports.",
                inline=False,
            )

        # Notifications
        if notif_row:
            notifs = []
            notifs.append(f"Release announcements: {'On' if notif_row['release_notify'] else 'Off'}")
            notifs.append(f"Weather warnings: {'On' if notif_row['weather_notify'] else 'Off'}")
            notifs.append(f"VATSIM events: {'On' if notif_row['event_notify'] else 'Off'}")
            embed.add_field(
                name="Notification Settings",
                value="\n".join(notifs),
                inline=False,
            )
        else:
            embed.add_field(
                name="Notification Settings",
                value="Defaults: Release announcements on, others off. Use /preferences-set to change.",
                inline=False,
            )

        embed.set_footer(text="OPS ROOM User Preferences")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="preferences-set",
        description="Configure notification preferences.",
    )
    @app_commands.describe(
        release_notify="Receive release announcements",
        weather_notify="Receive weather alerts for saved airports",
        event_notify="Receive VATSIM event notifications",
    )
    async def preferences_set(
        self,
        interaction: discord.Interaction,
        release_notify: bool | None = None,
        weather_notify: bool | None = None,
        event_notify: bool | None = None,
    ) -> None:
        """Set notification preferences."""
        db = await get_db()
        now = utc_now_iso()

        await db.execute(
            """
            INSERT OR REPLACE INTO notifications (user_id, release_notify, weather_notify, event_notify, updated_at)
            VALUES (
                ?, 
                COALESCE(?, (SELECT release_notify FROM notifications WHERE user_id = ?), 1),
                COALESCE(?, (SELECT weather_notify FROM notifications WHERE user_id = ?), 0),
                COALESCE(?, (SELECT event_notify FROM notifications WHERE user_id = ?), 0),
                ?
            )
            """,
            (
                interaction.user.id,
                int(release_notify) if release_notify is not None else None, interaction.user.id,
                int(weather_notify) if weather_notify is not None else None, interaction.user.id,
                int(event_notify) if event_notify is not None else None, interaction.user.id,
                now,
            ),
        )
        await db.commit()

        await interaction.response.send_message(
            "Notification preferences updated.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PreferencesCog(bot))
    logger.info("Preferences cog loaded.")
