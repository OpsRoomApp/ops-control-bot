"""
OPS CONTROL - Configuration Loader

Loads configuration from environment variables with sensible defaults.
All IDs and secrets come exclusively from environment variables.
"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


_REQUIRED = object()


def _env_str(key: str, default: object = _REQUIRED) -> str:
    value = os.getenv(key)
    if value is None:
        if default is _REQUIRED:
            raise EnvironmentError(f"Missing required environment variable: {key}")
        return default  # type: ignore[return-value]
    return value


def _env_int(key: str, default: object = _REQUIRED) -> int:
    raw = _env_str(key, str(default) if default is not _REQUIRED else None)
    if raw is None or raw == "None":
        raise EnvironmentError(f"Missing required environment variable: {key}")
    return int(raw)


@dataclass(frozen=True)
class Config:
    """Immutable application configuration loaded from the environment."""

    # -- Discord credentials --
    discord_token: str = field(default_factory=lambda: _env_str("DISCORD_TOKEN"))

    # -- Discord IDs --
    guild_id: int = field(default_factory=lambda: _env_int("GUILD_ID"))
    owner_user_id: int = field(default_factory=lambda: _env_int("OWNER_USER_ID"))
    arrivals_channel_id: int = field(
        default_factory=lambda: _env_int("ARRIVALS_CHANNEL_ID")
    )

    # -- Time --
    timezone: str = field(default_factory=lambda: _env_str("TIMEZONE", "UTC"))

    # -- Paths --
    database_path: str = field(
        default_factory=lambda: _env_str("DATABASE_PATH", "data/ops-control.db")
    )
    log_path: str = field(
        default_factory=lambda: _env_str("LOG_PATH", "logs/ops-control.log")
    )
    log_level: str = field(
        default_factory=lambda: _env_str("LOG_LEVEL", "INFO")
    )

    # -- External API keys --
    simbrief_api_key: str | None = field(
        default_factory=lambda: _env_str("SIMBRIEF_API_KEY", None)
    )

    # -- API URLs --
    noaa_api_url: str = field(
        default_factory=lambda: _env_str(
            "NOAA_API_URL", "https://aviationweather.gov/api/data"
        )
    )
    faa_notam_api_url: str = field(
        default_factory=lambda: _env_str("FAA_NOTAM_API_URL", "")
    )

    # -- OPS ROOM release tracking --
    github_repo: str = field(
        default_factory=lambda: _env_str(
            "GITHUB_REPO", "OpsRoomApp/ops-room-releases"
        )
    )
    opsroom_releases_api: str = field(
        default_factory=lambda: _env_str(
            "OPSROOM_RELEASES_API", "https://opsroom.live/api/update.json"
        )
    )

    # -- Channel IDs --
    bug_forum_channel_id: int = field(
        default_factory=lambda: _env_int("BUG_FORUM_CHANNEL_ID", 0)
    )
    support_forum_channel_id: int = field(
        default_factory=lambda: _env_int("SUPPORT_FORUM_CHANNEL_ID", 0)
    )
    discord_announcement_channel: int = field(
        default_factory=lambda: _env_int("DISCORD_ANNOUNCEMENT_CHANNEL", 0)
    )

    # -- Future PostgreSQL --
    database_url: str | None = field(
        default_factory=lambda: _env_str("DATABASE_URL", None)
    )


config = Config()
