"""
OPS CONTROL - GitHub Release Service

Checks latest releases, prepares webhook/event architecture.
Designed to eventually auto-announce new releases to Discord.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from bot.api import fetch_github_latest_release
from bot.config import config

logger = logging.getLogger("ops_control.services.github_release")


@dataclass
class ReleaseInfo:
    """Parsed release information."""
    version: str
    tag: str
    published_at: str
    url: str
    body: str
    prerelease: bool
    draft: bool


async def check_latest_release(repo: str | None = None) -> ReleaseInfo | None:
    """
    Check the latest GitHub release for the configured repo.

    Returns parsed ReleaseInfo or None if the API is unavailable.
    """
    repo = repo or config.github_repo

    try:
        data = await fetch_github_latest_release(repo)
    except Exception:
        logger.exception("Failed to fetch GitHub release for %s", repo)
        return None

    if data is None:
        return None

    return ReleaseInfo(
        version=(data.get("tag_name") or "").lstrip("v"),
        tag=data.get("tag_name", "unknown"),
        published_at=data.get("published_at", ""),
        url=data.get("html_url", ""),
        body=(data.get("body") or "")[:2000],
        prerelease=bool(data.get("prerelease", False)),
        draft=bool(data.get("draft", False)),
    )


async def format_release_announcement(repo: str | None = None) -> str | None:
    """
    Format a release as a Discord announcement string.

    Returns None if no release data is available.
    """
    info = await check_latest_release(repo)
    if info is None:
        return None

    lines = [
        f"**OPS ROOM {info.version}** has been released.",
        "",
    ]
    if info.body:
        # Truncate body to reasonable Discord message length
        body = info.body[:1500]
        if len(info.body) > 1500:
            body += "..."
        lines.append(body)
        lines.append("")

    lines.append(f"[Download and Changelog]({info.url})")
    return "\n".join(lines)
