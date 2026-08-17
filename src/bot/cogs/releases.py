"""
OPS CONTROL - Releases Cog

/latest -- Current OPS ROOM release information.
/changelog -- Recent version history.
/roadmap -- Development roadmap.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.config import config
from bot.api import _get_session, fetch_opsroom_public_releases, fetch_opsroom_releases_manifest
from bot.database.db import get_db

logger = logging.getLogger("ops_control.cogs.releases")


def format_notes_for_discord(markdown: str, limit: int = 900) -> str:
    """Convert release-note markdown into a Discord-friendly embed body.

    Contract shared with the admin panel preview (ReleaseNotesEditor.jsx).
    Keep in sync by spec:
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


def _split_notes(text: str, size: int = 4000) -> list[str]:
    """Split long notes into Discord-safe chunks, breaking on line boundaries."""
    chunks: list[str] = []
    current = ""
    for line in (text or "").splitlines():
        candidate = current + chr(10) + line if current else line
        if len(candidate) > size and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks or [""]


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


def should_announce(last_announced: str, current_version: str) -> bool:
    """True when ``current_version`` is newer than the last announced one.

    An empty ``last_announced`` is a first-run sentinel: the caller should
    baseline without announcing, so this returns False. Uses the same version
    ordering as the /changelog min-version filter.
    """
    if not last_announced:
        return False
    return _version_key(current_version) > _version_key(last_announced)

# Offline fallback snapshot (kept in sync with the admin-panel roadmap at
# opsroom.live/api/public/roadmap, which /roadmap reads first).
ROADMAP_DATA = {
    "current_sprint": "v0.26 Development",
    "completed": [
        "In-sim tablet panel for MSFS 2020 and 2024",
        "Native EFB app inside the MSFS 2024 cockpit tablet",
        "Automatic updater with one-click installer",
        "In-app bug reports with diagnostics ZIP",
        "Black Box recorder with in-sim replay and landing analysis",
        "First-party performance calculator for the supported fleet",
        "Live OFP dispatch with electronic crew sign-off",
        "In-sim NOTAM closure markers for 2020 and 2024",
        "Community map, leaderboard and Discord integration",
        "CPDLC over Hoppie, GSX ground automation, RAAS and announcements",
    ],
    "in_progress": [
        "Live Map aircraft follow (click an aircraft to keep it centered)",
        "Roadmap channel and feedback forum",
        "Black Box replay robustness (freeze and hang fixes)",
        "GSX passenger-door hold-open for cabin cleaning",
        "ATIS at top of descent in the briefing DM",
        "Leaderboard sorting by flight hours",
        "UI polish pass (grid sizing, fonts, contrast, dark scrollbars)",
    ],
    "planned": [
        "Personal flight tracker and cloud logbook",
        "One-tap landing report share card",
        "Fleet hangar with per-airframe telemetry",
        "Fleet wear and maintenance tied to the economy",
        "Voice copilot checklists",
        "Replay to shareable video clip",
        "Key-moment auto-capture timeline",
    ],
}


class ReleasesCog(commands.Cog):
    """OPS ROOM release information commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self):
        if config.discord_release_channel_id or config.discord_downloads_channel_id:
            self._release_poller.change_interval(seconds=config.release_poll_seconds)
            self._release_poller.start()

    async def cog_unload(self):
        if hasattr(self, "_release_poller") and self._release_poller.is_running():
            self._release_poller.cancel()

    # ------------------------------------------------------------------
    # Release announcements (bot identity, no webhooks)
    #
    # Polls the public releases endpoint (the same website-primary source
    # /latest and /changelog read). When a new version appears it posts to
    # #release-notes and #downloads as the bot itself, so the posts carry
    # the bot's name and avatar. The last announced version is persisted in
    # guild_settings so a restart never re-announces the same release.
    # ------------------------------------------------------------------

    LAST_ANNOUNCED_KEY = "last_announced_release_version"

    async def _get_last_announced(self) -> str:
        db = await get_db()
        try:
            cur = await db.execute(
                "SELECT value FROM guild_settings WHERE guild_id = ? AND key = ?",
                (config.guild_id, self.LAST_ANNOUNCED_KEY),
            )
            row = await cur.fetchone()
            return str(row["value"]) if row else ""
        except Exception:
            logger.exception("Failed to read last announced release version")
            return ""

    async def _set_last_announced(self, version: str) -> None:
        db = await get_db()
        try:
            await db.execute(
                """
                INSERT INTO guild_settings (guild_id, key, value, updated_by, updated_at)
                VALUES (?, ?, ?, NULL, ?)
                ON CONFLICT(guild_id, key)
                DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """,
                (
                    config.guild_id,
                    self.LAST_ANNOUNCED_KEY,
                    version,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()
        except Exception:
            logger.exception("Failed to persist last announced release version")

    @tasks.loop(seconds=30)
    async def _release_poller(self):
        """Announce newly published releases to #release-notes and #downloads."""
        try:
            data = await fetch_opsroom_public_releases()
            entries = (data or {}).get("releases") or []
        except Exception as e:
            logger.debug("Public releases poll failed: %s", e)
            return
        if not entries:
            return

        newest = entries[0]
        version = str(newest.get("version") or "")
        if not version:
            return

        last = await self._get_last_announced()
        if not last:
            # First run: baseline without announcing so the bot only posts
            # releases published after it starts.
            await self._set_last_announced(version)
            return
        if not should_announce(last, version):
            return

        await self._announce_release(newest)
        await self._set_last_announced(version)

    async def _announce_release(self, release: dict[str, Any]) -> None:
        """Post the release note and download links as the bot itself."""
        version = str(release.get("version") or "?")
        notes = format_notes_for_discord(str(release.get("notes") or ""), limit=100000)
        downloads = "https://opsroom.live/downloads"

        chunks = _split_notes(notes, 3800)
        if config.discord_release_channel_id:
            channel = self.bot.get_channel(config.discord_release_channel_id)
            if isinstance(channel, discord.TextChannel):
                try:
                    for index, chunk in enumerate(chunks):
                        if index == 0:
                            embed = discord.Embed(
                                title=f"OPS ROOM v{version} Released",
                                description=chunk or "A new OPS ROOM release is available.",
                                color=0x2563EB,
                                url=downloads,
                            )
                            embed.add_field(name="Version", value=version, inline=True)
                            embed.add_field(name="Download", value=f"[opsroom.live/downloads]({downloads})", inline=True)
                        else:
                            embed = discord.Embed(description=chunk, color=0x2563EB)
                        # One embed per message: Discord caps the combined character
                        # count across all embeds in a single message, so one
                        # send(embeds=[...]) with several large embeds trips the
                        # 6000-char limit. Send them as separate messages instead.
                        await channel.send(embed=embed)
                        if index < len(chunks) - 1:
                            await asyncio.sleep(0.75)
                except discord.Forbidden:
                    logger.warning(
                        "No permission to post in release channel %s", config.discord_release_channel_id
                    )
                except Exception:
                    logger.exception("Failed to post release announcement")

        if config.discord_downloads_channel_id:
            channel = self.bot.get_channel(config.discord_downloads_channel_id)
            if not isinstance(channel, discord.TextChannel):
                return
            dl_embed = discord.Embed(
                title=f"OPS ROOM v{version} - Downloads",
                color=0x2563EB,
                url=downloads,
            )
            installer = str(release.get("installer_filename") or "")
            if installer:
                dl_embed.add_field(name="Installer", value=f"[{installer}]({downloads}/{installer})", inline=False)
            zip_name = str(release.get("filename") or "")
            if zip_name:
                dl_embed.add_field(name="ZIP archive", value=f"[Download ZIP]({downloads}/{zip_name})", inline=False)
            dl_embed.add_field(
                name="Downloads page",
                value=f"[opsroom.live/downloads]({downloads})",
                inline=False,
            )
            try:
                await channel.send(embed=dl_embed)
            except discord.Forbidden:
                logger.warning(
                    "No permission to post in downloads channel %s", config.discord_downloads_channel_id
                )
            except Exception:
                logger.exception("Failed to post download links")

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
        """Display the OPS ROOM roadmap (live from opsroom.live, fallback snapshot)."""
        await interaction.response.defer()

        data = await self._load_roadmap()
        sprint = data.get("current_sprint") or ROADMAP_DATA["current_sprint"]
        completed = data.get("completed") or ROADMAP_DATA["completed"]
        in_progress = data.get("in_progress") or ROADMAP_DATA["in_progress"]
        planned = data.get("planned") or ROADMAP_DATA["planned"]

        def _field(title_list: list[str]) -> str:
            text = "\n".join(f"- {item}" for item in title_list) or "- None yet"
            return text[:1024]

        embed = discord.Embed(
            title="OPS ROOM Development Roadmap",
            color=0x059669,
            description=f"Current Sprint: {sprint}",
        )
        embed.add_field(name="Completed", value=_field(completed), inline=False)
        embed.add_field(name="In Progress", value=_field(in_progress), inline=False)
        embed.add_field(name="Planned", value=_field(planned), inline=False)
        embed.set_footer(text="OPS ROOM Development · opsroom.live/api/public/roadmap")
        await interaction.followup.send(embed=embed)

    async def _load_roadmap(self) -> dict[str, Any]:
        """Fetch the live roadmap from opsroom.live; fall back to the snapshot."""
        try:
            from bot.api import fetch_opsroom_public_roadmap

            body = await fetch_opsroom_public_roadmap()
            if body and body.get("ok"):
                grouped: dict[str, list[str]] = {"planned": [], "in_progress": [], "completed": []}
                for item in body.get("items") or []:
                    status = str(item.get("status") or "planned").lower()
                    title = str(item.get("title") or "").strip()
                    if status in grouped and title:
                        grouped[status].append(title)
                return {
                    "current_sprint": str(body.get("current_sprint") or ""),
                    "completed": grouped["completed"],
                    "in_progress": grouped["in_progress"],
                    "planned": grouped["planned"],
                }
        except Exception:
            logger.exception("Failed to load live roadmap; using bundled snapshot")
        return {}  # caller falls back to ROADMAP_DATA


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ReleasesCog(bot))
    logger.info("Releases cog loaded.")
