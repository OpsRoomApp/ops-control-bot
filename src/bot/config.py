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
    try:
        return int(raw)
    except ValueError:
        return int(default) if default is not _REQUIRED else 0


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    """Immutable application configuration loaded from the environment."""

    # -- Discord credentials --
    discord_token: str = field(
        default_factory=lambda: _env_str("DISCORD_TOKEN")
    )
    client_id: int = field(
        default_factory=lambda: _env_int("CLIENT_ID", 0)
    )

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
    log_channel_id: int = field(
        default_factory=lambda: _env_int("LOG_CHANNEL_ID", 0)
    )
    bug_reports_channel_id: int = field(
        default_factory=lambda: _env_int("BUG_REPORTS_CHANNEL_ID", 0)
    )

    # -- Ticket System --
    support_category_id: int = field(
        default_factory=lambda: _env_int("SUPPORT_CATEGORY_ID", 0)
    )
    support_dispatch_role_id: int = field(
        default_factory=lambda: _env_int("SUPPORT_DISPATCH_ROLE_ID", 0)
    )
    moderator_role_id: int = field(
        default_factory=lambda: _env_int("MODERATOR_ROLE_ID", 0)
    )
    ops_control_role_id: int = field(
        default_factory=lambda: _env_int("OPS_CONTROL_ROLE_ID", 0)
    )
    developer_role_id: int = field(
        default_factory=lambda: _env_int("DEVELOPER_ROLE_ID", 0)
    )

    # -- Transcript archive --
    ticket_transcript_channel_id: int = field(
        default_factory=lambda: _env_int("TICKET_TRANSCRIPT_CHANNEL_ID", 0)
    )

    # -- Moderation / automod (B2) --
    mod_log_channel_id: int = field(
        default_factory=lambda: _env_int("MOD_LOG_CHANNEL_ID", 0)
    )
    muted_role_id: int = field(
        default_factory=lambda: _env_int("MUTED_ROLE_ID", 0)
    )
    verified_tester_role_id: int = field(
        default_factory=lambda: _env_int("VERIFIED_TESTER_ROLE_ID", 0)
    )
    public_beta_role_id: int = field(
        default_factory=lambda: _env_int("PUBLIC_BETA_ROLE_ID", 0)
    )
    beta_coordinator_role_id: int = field(
        default_factory=lambda: _env_int("BETA_COORDINATOR_ROLE_ID", 0)
    )

    # -- VATSIM events (B3) --
    vatsim_events_channel_id: int = field(
        default_factory=lambda: _env_int("VATSIM_EVENTS_CHANNEL_ID", 0)
    )
    vatsim_events_announce_minutes: int = field(
        default_factory=lambda: _env_int("VATSIM_EVENTS_ANNOUNCE_MINUTES", 90)
    )
    vatsim_events_reminder_minutes: int = field(
        default_factory=lambda: _env_int("VATSIM_EVENTS_REMINDER_MINUTES", 30)
    )

    # -- VATSIM flight tracker (auto takeoff/landing posts) --
    vatsim_tracker_channel_id: int = field(
        default_factory=lambda: _env_int(
            "VATSIM_TRACKER_CHANNEL_ID", 1533447716359639131
        )
    )
    vatsim_tracker_poll_seconds: int = field(
        default_factory=lambda: _env_int("VATSIM_TRACKER_POLL_SECONDS", 60)
    )

    # -- Admin API integration (B1 hosted transcripts, C4 appeals) --
    admin_api_base_url: str = field(
        default_factory=lambda: _env_str(
            "ADMIN_API_BASE_URL", "https://admin.opsroom.live"
        )
    )
    admin_api_token: str = field(
        default_factory=lambda: _env_str("ADMIN_API_TOKEN", "")
    )
    appeal_form_url: str = field(
        default_factory=lambda: _env_str(
            "APPEAL_FORM_URL", "https://opsroom.live/appeal"
        )
    )
    transcript_retention_days: int = field(
        default_factory=lambda: _env_int("TRANSCRIPT_RETENTION_DAYS", 14)
    )

    # -- FAA NMS-API NOTAM proxy (v0.25.60) --
    # Credentials for the FAA NMS-API itself live only on the VPS. The bot
    # talks to the opsroom.live proxy with a shared bearer token, falling
    # back to the admin API token when NMS_PROXY_TOKEN is not configured.
    nms_proxy_base_url: str = field(
        default_factory=lambda: _env_str(
            "NMS_PROXY_BASE_URL", "https://opsroom.live"
        )
    )
    nms_proxy_token: str = field(
        default_factory=lambda: _env_str("NMS_PROXY_TOKEN", "")
    )

    # -- NOTAM database (v0.25.63) --
    # The server-side NOTAM store is preferred (zero FAA quota per request);
    # the bot falls back to the NMS proxy when the DB is not deployed yet.
    notam_db_base_url: str = field(
        default_factory=lambda: _env_str("NOTAM_DB_BASE_URL", "")
    )
    notam_db_enabled: bool = field(
        default_factory=lambda: _env_bool("NOTAM_DB_ENABLED", True)
    )

    # -- Pending action dispatcher --
    pending_action_poll_seconds: int = field(
        default_factory=lambda: _env_int("PENDING_ACTION_POLL_SECONDS", 15)
    )
    pending_action_max_attempts: int = field(
        default_factory=lambda: _env_int("PENDING_ACTION_MAX_ATTEMPTS", 3)
    )

    # -- SimBrief route-generation links (optional default account) --
    simbrief_user_id: str = field(
        default_factory=lambda: _env_str("SIMBRIEF_USER_ID", "")
    )
    simbrief_static_id: str = field(
        default_factory=lambda: _env_str("SIMBRIEF_STATIC_ID", "")
    )

    # -- Where2Fly route provider (pre-existing service, fields were missing) --
    where2fly_enabled: bool = field(
        default_factory=lambda: _env_bool("WHERE2FLY_ENABLED", False)
    )
    where2fly_api_base_url: str = field(
        default_factory=lambda: _env_str(
            "WHERE2FLY_API_BASE_URL", "https://where2fly.today"
        )
    )
    where2fly_api_token: str = field(
        default_factory=lambda: _env_str("WHERE2FLY_API_TOKEN", "")
    )
    where2fly_timeout_seconds: int = field(
        default_factory=lambda: _env_int("WHERE2FLY_TIMEOUT_SECONDS", 15)
    )


# Single shared configuration instance consumed across the codebase.
# Required env vars (DISCORD_TOKEN etc.) raise at startup if missing.
config = Config()
