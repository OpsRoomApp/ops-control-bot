"""
OPS CONTROL - Admin Cog

/admin-health -- [Owner] Detailed bot health.
/admin-logs -- [Owner] Recent audit logs.
/admin-db-stats -- [Owner] Database statistics.
"""

from __future__ import annotations

import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from bot.config import config
from bot.database import get_db
from bot.utils.permissions import require_owner

logger = logging.getLogger("ops_control.cogs.admin")


class AdminCog(commands.Cog):
    """Administrative commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="admin-health",
        description="[Owner] Detailed bot health report.",
    )
    async def admin_health(self, interaction: discord.Interaction) -> None:
        """Detailed health report."""
        if not await require_owner(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        latency_ms = round(self.bot.latency * 1000, 1)
        guild_count = len(self.bot.guilds)
        user_count = sum(g.member_count for g in self.bot.guilds)
        cogs = list(self.bot.cogs.keys())

        db = await get_db()
        cursor = await db.execute("SELECT COUNT(*) FROM logs")
        log_count = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM notams WHERE is_active = 1")
        notam_count = (await cursor.fetchone())[0]
        cursor = await db.execute("SELECT COUNT(*) FROM users")
        user_db_count = (await cursor.fetchone())[0]

        embed = discord.Embed(title="OPS CONTROL -- Health Report", color=0x2563EB)
        embed.add_field(name="Latency", value=f"{latency_ms}ms", inline=True)
        embed.add_field(name="Guilds", value=str(guild_count), inline=True)
        embed.add_field(name="Users Seen", value=str(user_count), inline=True)
        embed.add_field(name="Cogs Loaded", value=str(len(cogs)), inline=True)
        embed.add_field(name="DB Users", value=str(user_db_count), inline=True)
        embed.add_field(name="Active NOTAMs", value=str(notam_count), inline=True)
        embed.add_field(name="Log Entries", value=str(log_count), inline=True)
        embed.add_field(
            name="Modules",
            value="\n".join(f"  {c}" for c in sorted(cogs)),
            inline=False,
        )
        embed.set_footer(text="OPS ROOM Operations")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="admin-logs",
        description="[Owner] Recent audit log entries.",
    )
    @app_commands.describe(limit="Number of entries (default: 10, max: 50)")
    async def admin_logs(self, interaction: discord.Interaction, limit: int = 10) -> None:
        """View recent audit log entries."""
        if not await require_owner(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        limit = max(1, min(limit, 50))
        db = await get_db()
        cursor = await db.execute(
            "SELECT * FROM logs ORDER BY created_at DESC LIMIT ?", (limit,),
        )
        rows = await cursor.fetchall()

        if not rows:
            await interaction.followup.send("No log entries found.", ephemeral=True)
            return

        embed = discord.Embed(title="Audit Log", color=0x6B7280)

        for row in rows:
            detail = row["detail"][:200] if row["detail"] else "--"
            embed.add_field(
                name=f"[{row['event_type']}] {row['created_at'][:19]}",
                value=(
                    f"User: {row['username']} (ID {row['user_id']})\n"
                    f"Detail: {detail}"
                ),
                inline=False,
            )

        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(
        name="admin-db-stats",
        description="[Owner] Database statistics.",
    )
    async def admin_db_stats(self, interaction: discord.Interaction) -> None:
        """Show database table sizes."""
        if not await require_owner(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        db = await get_db()

        tables = ["users", "guild_settings", "notams", "announcements", "logs",
                  "bugs", "tickets", "flight_logs", "events", "simbrief_accounts",
                  "discord_announcements", "airport_preferences"]
        stats: list[str] = []

        for table in tables:
            try:
                cursor = await db.execute(f"SELECT COUNT(*) FROM {table}")
                count = (await cursor.fetchone())[0]
                stats.append(f"  {table}: {count} rows")
            except Exception:
                stats.append(f"  {table}: --")

        db_path = config.database_path
        try:
            size_kb = os.path.getsize(db_path) // 1024
            stats.append(f"\nDatabase file: {db_path} ({size_kb} KB)")
        except OSError:
            pass

        embed = discord.Embed(
            title="Database Statistics",
            description="\n".join(stats),
            color=0x0F766E,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AdminCog(bot))
    logger.info("Admin cog loaded.")
