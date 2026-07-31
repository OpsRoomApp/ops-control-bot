"""
OPS CONTROL - SimBrief Integration Cog

/link-simbrief -- Link Discord account to SimBrief.
/ofp -- Fetch latest Operational Flight Plan via SimBrief's public
       XML fetcher API (no API key required).
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from bot.api import fetch_simbrief_flightplan
from bot.config import config
from bot.database import get_db
from bot.utils.helpers import utc_now_iso
from bot.services.audit import log_event
from bot.services.discord_log import log_simbrief_link, log_ofp_request

logger = logging.getLogger("ops_control.cogs.simbrief")


class SimBriefCog(commands.Cog):
    """SimBrief account linking and OFP retrieval."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="link-simbrief",
        description="Link your Discord account to your SimBrief account.",
    )
    @app_commands.describe(
        username="Your SimBrief username or pilot ID",
        static_id="Optional SimBrief static ID for persistent OFP links",
    )
    async def link_simbrief(
        self,
        interaction: discord.Interaction,
        username: str,
        static_id: str | None = None,
    ) -> None:
        """Link a Discord user to a SimBrief username."""
        db = await get_db()
        await db.execute(
            """
            INSERT OR REPLACE INTO simbrief_accounts
                (discord_id, simbrief_user, pilot_id, static_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, COALESCE((SELECT created_at FROM simbrief_accounts WHERE discord_id = ?), ?), ?)
            """,
            (
                interaction.user.id,
                username.strip(),
                username.strip(),
                static_id.strip() if static_id else None,
                interaction.user.id,
                utc_now_iso(),
                utc_now_iso(),
            ),
        )
        await db.commit()

        await interaction.response.send_message(
            f"SimBrief account linked: {username.strip()}. Use /ofp to fetch your flight plan.",
            ephemeral=True,
        )

        await log_event(
            "command",
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            guild_id=interaction.guild_id,  # type: ignore[arg-type]
            channel_id=interaction.channel_id,
            detail=f"SimBrief linked: {username.strip()}",
        )

        if isinstance(interaction.user, discord.Member):
            await log_simbrief_link(self.bot, interaction.user, username.strip())

    @app_commands.command(
        name="ofp",
        description="Fetch your latest Operational Flight Plan from SimBrief.",
    )
    @app_commands.describe(username="SimBrief username (overrides linked account)")
    async def ofp(self, interaction: discord.Interaction, username: str | None = None) -> None:
        """Retrieve the latest OFP from SimBrief (public API, no key needed)."""
        sb_user = username
        static_id = None

        if not sb_user:
            db = await get_db()
            cursor = await db.execute(
                "SELECT simbrief_user, static_id FROM simbrief_accounts WHERE discord_id = ?",
                (interaction.user.id,),
            )
            row = await cursor.fetchone()
            if row:
                sb_user = row["simbrief_user"]
                static_id = row["static_id"]

        if not sb_user:
            await interaction.response.send_message(
                "No SimBrief account linked. Use /link-simbrief <username> first, "
                "or provide a username: /ofp username:YOUR_USERNAME",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        try:
            plan = await fetch_simbrief_flightplan(sb_user, static_id)
        except Exception as exc:
            logger.warning("SimBrief API unavailable: %s", exc)
            await interaction.followup.send(
                "SimBrief data is currently unavailable. The API may be down or the username is invalid.",
                ephemeral=True,
            )
            return

        if plan is None:
            await interaction.followup.send(
                f"No active flight plan found for {sb_user}.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"SimBrief OFP -- {plan['callsign']}",
            color=0xEA580C,
            timestamp=discord.utils.utcnow(),
        )
        embed.add_field(name="Aircraft", value=plan["aircraft"], inline=True)
        embed.add_field(name="Route", value=f"{plan['origin']} - {plan['destination']}", inline=True)
        embed.add_field(name="Distance", value=f"{plan['distance']} NM", inline=True)
        embed.add_field(name="Cruise Level", value=plan["cruise_altitude"], inline=True)
        embed.add_field(name="Block Time", value=plan.get("block_time", "N/A"), inline=True)
        embed.add_field(name="Plan Fuel", value=f"{plan['plan_fuel']} kg", inline=True)

        if plan.get("route") and plan["route"] != "N/A":
            embed.add_field(name="Route String", value=f"```{plan['route'][:1020]}```", inline=False)

        ofp_link = plan.get("ofp_link", "")
        if ofp_link:
            embed.add_field(name="OFP Link", value=f"[View full OFP]({ofp_link})", inline=False)

        embed.set_footer(text=f"Source: SimBrief API | User: {sb_user}")
        await interaction.followup.send(embed=embed)

        await log_event(
            "command",
            user_id=interaction.user.id,
            username=interaction.user.display_name,
            guild_id=interaction.guild_id,  # type: ignore[arg-type]
            channel_id=interaction.channel_id,
            detail=f"OFP requested for {sb_user}",
        )

        if isinstance(interaction.user, discord.Member):
            await log_ofp_request(self.bot, interaction.user, sb_user)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SimBriefCog(bot))
    logger.info("SimBrief cog loaded.")
