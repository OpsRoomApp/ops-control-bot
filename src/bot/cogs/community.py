"""OPS CONTROL - Community Cog

/leaderboard -- Community flight leaderboard (sortable by hours, flights, landing rate).
/link-app -- Generate a one-time pairing code for the OPS ROOM desktop app.
/flight-visibility -- Choose where your flights appear (Discord / Public / Hidden).
"""

from __future__ import annotations

import logging
import secrets
from typing import Literal

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.database import get_db
from bot.utils.helpers import utc_now_iso

logger = logging.getLogger("ops_control.cogs.community")

PERIODS = {"week", "month", "alltime"}


class CommunityCog(commands.Cog):
    """Community flight features: leaderboard, app pairing, visibility."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="leaderboard",
        description="Community flight leaderboard: hours, landings, and landing rate.",
    )
    @app_commands.describe(
        period="Week, month, or all-time (default all-time)",
        sort="Sort by hours, flights, or landing rate (default hours)",
    )
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        period: str = "alltime",
        sort: Literal["hours", "flights", "rate"] = "hours",
    ) -> None:
        """Show the community flight leaderboard."""
        period = period.strip().lower().replace("-", "").replace("_", "")
        if period not in PERIODS:
            period = "alltime"
        await interaction.response.defer(ephemeral=False)

        since = ""
        if period == "week":
            since = "AND submitted_at >= datetime('now', '-7 days')"
        elif period == "month":
            since = "AND submitted_at >= datetime('now', '-30 days')"

        # Landing rates are always negative fpm and a soft touchdown is close
        # to 0 (e.g. -220 fpm), so the rate sort mirrors the website: order by
        # negated absolute value so the least negative landing tops the list
        # and junk positive rates sink to the bottom.
        order_by = {
            "hours": "ORDER BY hours DESC, flights DESC",
            "flights": "ORDER BY flights DESC, hours DESC",
            "rate": "ORDER BY -ABS(avg_rate) DESC, hours DESC",
        }[sort]

        db = await get_db()
        cursor = await db.execute(
            f"""
            SELECT username,
                   COUNT(*)                         AS flights,
                   COALESCE(SUM(duration_min), 0) / 60.0 AS hours,
                   -- Mirrors the website: exclude only near-zero parked v/s
                   -- readings (e.g. 0.0 or -0.008 fpm) from the aggregates.
                   -- Values with |rate| >= 1 fpm stay visible even when
                   -- positive; the rate sort sinks them to the bottom.
                   AVG(CASE WHEN ABS(landing_rate) >= 1 THEN landing_rate END) AS avg_rate,
                   MAX(CASE WHEN ABS(landing_rate) >= 1 THEN landing_rate END) AS best_rate
            FROM flight_logs
            WHERE landing_rate IS NOT NULL {since}
            GROUP BY user_id
            {order_by}
            LIMIT 10
            """
        )
        rows = await cursor.fetchall()

        if not rows:
            await interaction.followup.send(
                "No flights on the leaderboard yet. Fly with OPS ROOM and link Discord to appear!"
            )
            return

        sort_labels = {
            "hours": "sorted by hours",
            "flights": "sorted by flights",
            "rate": "sorted by landing rate (softest first)",
        }
        embed = discord.Embed(
            title="🏆 OPS ROOM Flight Leaderboard",
            description=(
                {
                    "week": "Last 7 days",
                    "month": "Last 30 days",
                    "alltime": "All time",
                }.get(period, "All time")
                + f" · {sort_labels[sort]}"
            ),
            color=0x2563EB,
        )
        medal = ["🥇", "🥈", "🥉"]
        for i, row in enumerate(rows):
            name = row["username"] or "pilot"
            prefix = medal[i] if i < 3 else f"#{i + 1}"
            avg = f"{row['avg_rate']:.0f}" if row["avg_rate"] is not None else "-"
            best = f"{row['best_rate']:.0f}" if row["best_rate"] is not None else "-"
            embed.add_field(
                name=f"{prefix} {name}",
                value=(
                    f"**{row['flights']}** flights · **{row['hours']:.1f}** h\n"
                    f"Avg landing {avg} fpm · Best {best} fpm"
                ),
                inline=False,
            )
        embed.set_footer(text="OPS ROOM Community")
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="link-app",
        description="Generate a one-time pairing code for the OPS ROOM desktop app.",
    )
    async def link_app(self, interaction: discord.Interaction) -> None:
        """Issue a short-lived pairing code the app exchanges for a link."""
        db = await get_db()
        # Tolerate a missing table by creating it lazily (idempotent).
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS app_links (
                discord_id  INTEGER PRIMARY KEY,
                app_token   TEXT    NOT NULL,
                username    TEXT,
                visibility  TEXT    NOT NULL DEFAULT 'discord',
                created_at  TEXT    NOT NULL,
                updated_at  TEXT
            )
            """
        )
        await db.commit()

        code = secrets.token_urlsafe(9).replace("-", "").replace("_", "")[:10].upper()
        # Encode the code into the app_links table keyed by this Discord user.
        await db.execute(
            """
            INSERT INTO app_links (discord_id, app_token, username, visibility, created_at, updated_at)
            VALUES (?, ?, ?, 'discord', ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                app_token = excluded.app_token,
                username = excluded.username,
                updated_at = excluded.updated_at
            """,
            (
                interaction.user.id,
                code,
                interaction.user.display_name,
                utc_now_iso(),
                utc_now_iso(),
            ),
        )
        await db.commit()

        await interaction.response.send_message(
            f"Your one-time app pairing code is: **`{code}`**\n\n"
            "Enter it in OPS ROOM → Host Setup → Connect Discord.",
            ephemeral=True,
        )

    @app_commands.command(
        name="flight-visibility",
        description="Choose where your flights appear (Discord channel, public, or hidden).",
    )
    @app_commands.describe(
        visibility="discord = channel only · public = also on website · hidden = nowhere"
    )
    async def flight_visibility(
        self,
        interaction: discord.Interaction,
        visibility: str,
    ) -> None:
        """Set the user's community visibility level."""
        value = visibility.strip().lower()
        if value not in ("discord", "public", "hidden"):
            await interaction.response.send_message(
                "Choose one of: `discord`, `public`, or `hidden`.", ephemeral=True
            )
            return

        db = await get_db()
        await db.execute(
            """
            INSERT INTO app_links (discord_id, app_token, username, visibility, created_at, updated_at)
            VALUES (?, '', ?, ?, ?, ?)
            ON CONFLICT(discord_id) DO UPDATE SET
                visibility = excluded.visibility,
                updated_at = excluded.updated_at
            """,
            (
                interaction.user.id,
                interaction.user.display_name,
                value,
                utc_now_iso(),
                utc_now_iso(),
            ),
        )
        await db.commit()

        labels = {
            "discord": "Discord channel only (default)",
            "public": "Discord + public website map/leaderboard",
            "hidden": "Hidden everywhere",
        }
        await interaction.response.send_message(
            f"Flight visibility set to **{labels[value]}**.", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(CommunityCog(bot))
    logger.info("Community cog loaded.")
