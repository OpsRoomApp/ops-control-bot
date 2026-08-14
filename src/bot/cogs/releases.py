"""
OPS CONTROL - Releases Cog

/latest -- Current OPS ROOM release information.
/changelog -- Recent version history.
/roadmap -- Development roadmap.
"""

from __future__ import annotations

import logging
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.config import config
from bot.api import (
    _get_session,
    fetch_github_latest_release,
    fetch_opsroom_public_releases,
    fetch_opsroom_releases_manifest,
)

logger = logging.getLogger("ops_control.cogs.releases")


def format_notes_for_discord(markdown: str, limit: int = 900) -> str:
    """Convert release-note markdown into a Discord-friendly embed body.

    Contract shared with admin-api (discord_webhooks.py) and the admin panel
    preview (ReleaseNotesEditor.jsx). Keep in sync by spec:
      - "# / ## / ###" headings become bold lines
      - "- " bullets stay bullets (Discord renders them natively)
      - blank lines collapse; everything else is kept as plain text
      - the result is truncated to ``limit`` chars with an ellipsis
    """
    lines: list[str] = []
    for raw in (markdown or "").splitlines():
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("### "):
            lines.append(f"**{stripped[4:].strip()}**")
        elif stripped.startswith("## "):
            lines.append(f"**{stripped[3:].strip()}**")
        elif stripped.startswith("# "):
            lines.append(f"**{stripped[2:].strip()}**")
        elif stripped.startswith("> "):
            lines.append(stripped[2:].strip())
        else:
            lines.append(stripped)
    text = "\n".join(lines)
    if len(text) > limit:
        text = text[:limit].rstrip() + "\u2026"
    return text


def _version_key(version: str) -> tuple[int, ...]:
    """Parse '0.25.0' / 'v0.25.0' / '0.9' into a comparable numeric tuple.

    Always 3 components so '0.9' == (0, 9, 0) compares correctly against
    '0.9.1' == (0, 9, 1).
    """
    parts: list[int] = []
    for chunk in (version or "").lstrip("v").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    parts += [0] * (3 - len(parts))
    return tuple(parts[:3])

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

    # v0.25.55 (B5) - Changelog auto-announce background task
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



    async def _fetch_releases(self) -> list[dict[str, Any]]:
        """Release history, website-primary with GitHub fallback.

        Order of preference:
          1. opsroom.live/api/public/releases (admin-panel catalog, markdown
             notes, published + archived entries)
          2. GitHub Releases API (same notes when the GitHub body is kept in
             sync via the admin panel's "Copy for GitHub")
          3. opsroom.live/api/update.json manifest (current release only)
        """
        try:
            data = await fetch_opsroom_public_releases()
            entries = (data or {}).get("releases") or []
            out = [
                {
                    "version": str(e.get("version") or ""),
                    "date": str(e.get("published_at") or "")[:10],
                    "notes": str(e.get("notes") or ""),
                }
                for e in entries
                if e.get("version")
            ]
            if out:
                return out
        except Exception as e:
            logger.debug("Public releases API failed: %s", e)

        try:
            session = await _get_session()
            url = f"https://api.github.com/repos/{config.github_repo}/releases?per_page=10"
            async with session.get(url) as resp:
                resp.raise_for_status()
                gh = await resp.json()
            out = [
                {
                    "version": str(r.get("tag_name") or "").lstrip("v"),
                    "date": str(r.get("published_at") or "")[:10],
                    "notes": str(r.get("body") or ""),
                }
                for r in gh
                if r.get("tag_name")
            ]
            if out:
                return out
        except Exception as e:
            logger.debug("GitHub releases fallback failed: %s", e)

        try:
            manifest = await fetch_opsroom_releases_manifest()
            version = str(manifest.get("latest_version") or manifest.get("version") or "")
            if version:
                return [
                    {
                        "version": version,
                        "date": str(manifest.get("published_at") or "")[:10],
                        "notes": str(manifest.get("notes") or manifest.get("message") or ""),
                    }
                ]
        except Exception as e:
            logger.debug("Manifest fallback failed: %s", e)

        return []

    @app_commands.command(
        name="latest",
        description="Display the latest OPS ROOM release information.",
    )
    async def latest(self, interaction: discord.Interaction) -> None:
        """Fetch and display the latest OPS ROOM release."""
        await interaction.response.defer()

        releases = await self._fetch_releases()
        if not releases:
            await interaction.followup.send(
                "Release information unavailable. Visit opsroom.live for the latest version.",
                ephemeral=True,
            )
            return

        rel = releases[0]
        version = rel.get("version", "Unknown")
        body = format_notes_for_discord(rel.get("notes") or "", limit=1500)

        embed = discord.Embed(
            title=f"OPS ROOM {version}",
            description=body if body else "No release notes available.",
            color=0x2563EB,
            url="https://opsroom.live/downloads",
        )
        embed.add_field(name="Version", value=version, inline=True)
        embed.add_field(name="Release Date", value=rel.get("date") or "N/A", inline=True)
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
        """Display recent version history (website primary, GitHub fallback)."""
        await interaction.response.defer()

        releases = await self._fetch_releases()
        min_key = _version_key(config.changelog_min_version)
        releases = [
            r for r in releases
            if r.get("version") and _version_key(r["version"]) >= min_key
        ]

        if not releases:
            await interaction.followup.send(
                "Changelog unavailable. Visit opsroom.live/changelog for details.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="OPS ROOM Version History",
            color=0x2563EB,
        )

        for rel in releases[:5]:
            tag = rel.get("version", "Unknown")
            date = rel.get("date") or ""
            summary = format_notes_for_discord(rel.get("notes") or "No notes.", limit=900)
            name = f"v{tag} -- {date}" if date else f"v{tag}"
            embed.add_field(
                name=name,
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
