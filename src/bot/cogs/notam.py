"""
OPS CONTROL - NOTAM Cog

/notam add|list|remove -- OPS NOTAM management.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.database import get_db
from bot.utils.helpers import utc_now_iso, fmt_ts
from bot.utils.permissions import require_owner_or_admin
from bot.services.audit import log_event

logger = logging.getLogger("ops_control.cogs.notam")

PRIORITY_CHOICES = [
    app_commands.Choice(name="Info", value="info"),
    app_commands.Choice(name="Warning", value="warning"),
    app_commands.Choice(name="Critical", value="critical"),
]

PRIORITY_COLOR = {
    "info": 0x3498DB,
    "warning": 0xF59E0B,
    "critical": 0xDC2626,
}

PRIORITY_LABEL = {
    "info": "INFO",
    "warning": "WARNING",
    "critical": "CRITICAL",
}


class NotamCog(commands.Cog):
    """NOTAM management commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    notam_group = app_commands.Group(
        name="notam",
        description="Manage OPS NOTAMs (Notices to Airmen).",
    )

    @notam_group.command(name="add", description="Create a new NOTAM.")
    @app_commands.describe(
        title="NOTAM title",
        message="NOTAM message body",
        priority="Priority level",
    )
    async def notam_add(
        self, interaction: discord.Interaction, title: str, message: str, priority: str = "info",
    ) -> None:
        """Add a new NOTAM (owner/admin only)."""
        if not await require_owner_or_admin(interaction):
            return

        db = await get_db()
        now = utc_now_iso()
        cursor = await db.execute(
            """
            INSERT INTO notams (title, message, priority, created_by, created_by_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (title, message, priority, interaction.user.id, interaction.user.display_name, now),
        )
        await db.commit()
        notam_id = cursor.lastrowid

        embed = discord.Embed(
            title=f"NOTAM Created: {title}",
            description=message,
            color=PRIORITY_COLOR.get(priority, 0x3498DB),
        )
        embed.add_field(name="Priority", value=PRIORITY_LABEL.get(priority, priority.upper()), inline=True)
        embed.add_field(name="ID", value=str(notam_id), inline=True)
        embed.set_footer(text=f"Created by {interaction.user.display_name}")

        await interaction.response.send_message(embed=embed)

        await log_event(
            "notam",
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            guild_id=interaction.guild_id,  # type: ignore[arg-type]
            channel_id=interaction.channel_id,
            detail=f"NOTAM #{notam_id} created: {title}",
        )
        logger.info("NOTAM #%s created by %s", notam_id, interaction.user.name)

    @notam_group.command(name="list", description="List active NOTAMs.")
    async def notam_list(self, interaction: discord.Interaction) -> None:
        """List all active NOTAMs."""
        db = await get_db()
        cursor = await db.execute(
            """
            SELECT id, title, message, priority, created_by_name, created_at
            FROM notams WHERE is_active = 1
            ORDER BY
                CASE priority WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 WHEN 'info' THEN 2 END,
                created_at DESC
            LIMIT 25
            """
        )
        rows = await cursor.fetchall()

        if not rows:
            await interaction.response.send_message("No active NOTAMs.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Active NOTAMs",
            color=0x3498DB,
            description=f"{len(rows)} active NOTAM(s)",
        )

        for row in rows:
            label = PRIORITY_LABEL.get(row["priority"], row["priority"].upper())
            embed.add_field(
                name=f"#{row['id']}: {row['title']} [{label}]",
                value=(
                    f"{row['message'][:200]}{'...' if len(row['message']) > 200 else ''}\n"
                    f"By {row['created_by_name']} -- {fmt_ts(row['created_at'], '%d %b %Y %H:%M')}Z"
                ),
                inline=False,
            )

        await interaction.response.send_message(embed=embed)

    @notam_group.command(name="remove", description="Deactivate a NOTAM by ID.")
    @app_commands.describe(notam_id="The ID of the NOTAM to remove")
    async def notam_remove(self, interaction: discord.Interaction, notam_id: int) -> None:
        """Deactivate a NOTAM (owner/admin only)."""
        if not await require_owner_or_admin(interaction):
            return

        db = await get_db()
        cursor = await db.execute(
            "UPDATE notams SET is_active = 0, updated_at = ? WHERE id = ? AND is_active = 1",
            (utc_now_iso(), notam_id),
        )
        await db.commit()

        if cursor.rowcount == 0:
            await interaction.response.send_message(
                f"No active NOTAM found with ID #{notam_id}.", ephemeral=True,
            )
            return

        await interaction.response.send_message(
            f"NOTAM #{notam_id} deactivated.", ephemeral=True,
        )

        await log_event(
            "notam",
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            guild_id=interaction.guild_id,  # type: ignore[arg-type]
            channel_id=interaction.channel_id,
            detail=f"NOTAM #{notam_id} deactivated",
        )
        logger.info("NOTAM #%s deactivated by %s", notam_id, interaction.user.name)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(NotamCog(bot))
    logger.info("NOTAM cog loaded.")
