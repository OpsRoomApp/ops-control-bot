"""
OPS CONTROL - Releases Cog

/latest -- Current OPS ROOM release information.
/changelog -- Recent version history.
/roadmap -- Development roadmap.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.config import config
from bot.api import fetch_github_latest_release, fetch_opsroom_releases_manifest

logger = logging.getLogger("ops_control.cogs.releases")

ROADMAP_DATA = {
    "current_sprint": "v0.25 Public Beta",
    "completed": [
        "Discord Bot Operations Interface",
        "Welcome Image Generation",
        "NOTAM Management System",
        "Flight Operations (VATSIM, OpenSky, SimBrief)",
        "Weather / METAR Commands",
        "ATIS Support",
        "Bug Reporting System",
        "Support Ticket System",
        "User Profile System",
    ],
    "in_progress": [
        "OPS ROOM Desktop Telemetry Integration",
        "Flight Logging and Analytics",
        "Admin Web Panel v2",
    ],
    "planned": [
        "MSFS 2024 Full Compatibility",
        "Multi-User Operations Sync",
        "Live Flight Tracking Map",
        "Community Events System",
    ],
}


class ReleasesCog(commands.Cog):
    """OPS ROOM release information commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # v0.25.55 (B5) — Changelog auto-announce background task
    _last_announced_tag: str | None = None

    async def cog_load(self):
        if config.discord_announcement_channel:
            self._release_poller.start()

    async def cog_unload(self):
        if hasattr(self, "_release_poller") and self._release_poller.is_running():
            self._release_poller.cancel()

    @tasks.loop(minutes=30)
    async def _release_poller(self):
        """Poll GitHub for new releases and auto-announce to the configured channel."""
        try:
            release = await fetch_github_latest_release(config.github_repo)
        except Exception:
            return
        if not release:
            return
        tag = str(release.get("tag_name") or "")
        if not tag or tag == self._last_announced_tag:
            return
        self._last_announced_tag = tag
        version = tag.lstrip("v")
        body = (release.get("body") or "")[:1500]
        channel = self.bot.get_channel(config.discord_announcement_channel)
        if not channel or not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(
            title=f"OPS ROOM {version} Released",
            description=body if body else "No release notes available.",
            color=0x2563EB,
            url=f"https://github.com/{config.github_repo}/releases/tag/{tag}",
        )
        embed.add_field(name="Version", value=version, inline=True)
        embed.add_field(name="Download", value="[opsroom.live/downloads](https://opsroom.live/downloads)", inline=True)
        try:
            await channel.send(embed=embed)
        except discord.Forbidden:
            pass



    @app_commands.command(
        name="latest",
        description="Display the latest OPS ROOM release information.",
    )
    async def latest(self, interaction: discord.Interaction) -> None:
        """Fetch and display the latest OPS ROOM release."""
        await interaction.response.defer()

        release = None
        manifest = None

        try:
            release = await fetch_github_latest_release(config.github_repo)
        except Exception as e:
            logger.debug("GitHub release fetch failed: %s", e)

        if release is None:
            try:
                manifest = await fetch_opsroom_releases_manifest()
            except Exception as e:
                logger.debug("Manifest fetch failed: %s", e)

        if release is None and manifest is None:
            await interaction.followup.send(
                "Release information unavailable. Visit opsroom.live for the latest version.",
                ephemeral=True,
            )
            return

        if release:
            tag = release.get("tag_name", "Unknown").lstrip("v")
            body = (release.get("body") or "")[:1500]
            published = (release.get("published_at") or "")[:10]

            embed = discord.Embed(
                title=f"OPS ROOM {tag}",
                description=body if body else "No release notes available.",
                color=0x2563EB,
                url="https://opsroom.live/downloads",
            )
            embed.add_field(name="Version", value=tag, inline=True)
            embed.add_field(name="Release Date", value=published, inline=True)
            embed.add_field(
                name="Download",
                value="[opsroom.live/downloads](https://opsroom.live/downloads)",
                inline=True,
            )
        elif manifest:
            version = manifest.get("latest_version") or manifest.get("version", "Unknown")
            embed = discord.Embed(
                title=f"OPS ROOM {version}",
                description=manifest.get("notes", manifest.get("message", "")),
                color=0x2563EB,
                url="https://opsroom.live/downloads",
            )
            embed.add_field(name="Version", value=version, inline=True)
            embed.add_field(name="Codename", value=manifest.get("codename", "N/A"), inline=True)
            embed.add_field(name="Channel", value=manifest.get("channel", "stable"), inline=True)
            embed.add_field(
                name="Download",
                value="[opsroom.live/downloads](https://opsroom.live/downloads)",
                inline=True,
            )

        embed.set_footer(text="OPS ROOM Release System")
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="changelog",
        description="Recent OPS ROOM version history.",
    )
    async def changelog(self, interaction: discord.Interaction) -> None:
        """Display recent version history from GitHub releases."""
        await interaction.response.defer()

        import aiohttp
        from bot.api import _get_session

        releases_list = []
        try:
            session = await _get_session()
            url = f"https://api.github.com/repos/{config.github_repo}/releases?per_page=5"
            async with session.get(url) as resp:
                resp.raise_for_status()
                releases_list = await resp.json()
        except Exception:
            pass

        if not releases_list:
            # Fallback to manifest
            try:
                manifest = await fetch_opsroom_releases_manifest()
                if manifest:
                    version = manifest.get("latest_version") or manifest.get("version", "Unknown")
                    releases_list = [{
                        "tag_name": f"v{version}",
                        "published_at": manifest.get("published_at", ""),
                        "body": manifest.get("notes", manifest.get("message", "")),
                    }]
            except Exception:
                pass

        if not releases_list:
            await interaction.followup.send(
                "Changelog unavailable. Visit opsroom.live/changelog for details.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="OPS ROOM Version History",
            color=0x2563EB,
        )

        for rel in releases_list[:5]:
            tag = rel.get("tag_name", "Unknown")
            date = (rel.get("published_at") or "")[:10]
            body = (rel.get("body") or "No notes.")
            summary = body[:300] + ("..." if len(body) > 300 else "")
            embed.add_field(
                name=f"{tag} -- {date}",
                value=summary,
                inline=False,
            )

        embed.set_footer(text="OPS ROOM Release System")
        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="roadmap",
        description="OPS ROOM development roadmap.",
    )
    async def roadmap(self, interaction: discord.Interaction) -> None:
        """Display the OPS ROOM roadmap."""
        embed = discord.Embed(
            title="OPS ROOM Development Roadmap",
            color=0x059669,
            description=f"Current Sprint: {ROADMAP_DATA['current_sprint']}",
        )

        embed.add_field(
            name="Completed",
            value="\n".join(f"- {item}" for item in ROADMAP_DATA["completed"]),
            inline=False,
        )
        embed.add_field(
            name="In Progress",
            value="\n".join(f"- {item}" for item in ROADMAP_DATA["in_progress"]),
            inline=False,
        )
        embed.add_field(
            name="Planned",
            value="\n".join(f"- {item}" for item in ROADMAP_DATA["planned"]),
            inline=False,
        )

        embed.set_footer(text="OPS ROOM Development")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReleasesCog(bot))
    logger.info("Releases cog loaded.")
