"""
OPS CONTROL - Database Layer

SQLite persistence with a migration-friendly design.
All table schemas are defined here and executed idempotently via
CREATE TABLE IF NOT EXISTS, so this file functions as both
schema definition and migration.

To migrate to PostgreSQL later:
    1. Replace aiosqlite with asyncpg / SQLAlchemy async.
    2. The schema DDL is standard SQL — minimal changes needed.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import aiosqlite

from bot.config import config

logger = logging.getLogger("ops_control.database")

_db: aiosqlite.Connection | None = None
_db_lock: asyncio.Lock = asyncio.Lock()

# ---------------------------------------------------------------------------
# Schema (DDL)
# ---------------------------------------------------------------------------

SCHEMA = """
-- Users table — stores Discord user information
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY,   -- Discord user ID
    username        TEXT    NOT NULL,
    display_name    TEXT,
    first_joined    TEXT    NOT NULL,       -- ISO 8601 timestamp
    last_seen       TEXT    NOT NULL,
    is_active       INTEGER NOT NULL DEFAULT 1,
    simulator       TEXT,                   -- e.g. MSFS2020, MSFS2024, X-Plane 12
    network         TEXT,                   -- e.g. VATSIM, IVAO, None
    beta_status     INTEGER NOT NULL DEFAULT 0,
    opsroom_version TEXT                    -- OPS ROOM desktop version
);

-- Guild-level settings (key-value store)
CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id        INTEGER NOT NULL,
    key             TEXT    NOT NULL,
    value           TEXT    NOT NULL,
    updated_by      INTEGER,
    updated_at      TEXT    NOT NULL,
    PRIMARY KEY (guild_id, key)
);

-- NOTAMs — Notice to Airmen / operations notices
CREATE TABLE IF NOT EXISTS notams (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    message         TEXT    NOT NULL,
    priority        TEXT    NOT NULL DEFAULT 'info',
    created_by      INTEGER NOT NULL,
    created_by_name TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT,
    is_active       INTEGER NOT NULL DEFAULT 1
);

-- Announcements — formatted broadcast messages
CREATE TABLE IF NOT EXISTS announcements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    image_url       TEXT,
    created_by      INTEGER NOT NULL,
    created_by_name TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    channel_id      INTEGER,
    message_id      INTEGER
);

-- Log — audit trail
CREATE TABLE IF NOT EXISTS logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type      TEXT    NOT NULL,
    user_id         INTEGER,
    username        TEXT,
    guild_id        INTEGER,
    channel_id      INTEGER,
    detail          TEXT,
    created_at      TEXT    NOT NULL
);

-- SimBrief account links
CREATE TABLE IF NOT EXISTS simbrief_accounts (
    discord_id      INTEGER PRIMARY KEY,
    simbrief_user    TEXT NOT NULL,
    pilot_id        TEXT,
    created_at      TEXT NOT NULL,
    updated_at      TEXT
);

-- Bug reports
CREATE TABLE IF NOT EXISTS bugs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id     INTEGER NOT NULL,
    reporter_name   TEXT    NOT NULL,
    version         TEXT    NOT NULL,
    simulator       TEXT,
    aircraft        TEXT,
    module          TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    steps           TEXT,
    expected        TEXT,
    actual          TEXT,
    priority        TEXT    NOT NULL DEFAULT 'normal',
    status          TEXT    NOT NULL DEFAULT 'open',
    thread_id       INTEGER,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT
);

-- Support tickets
CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    username        TEXT    NOT NULL,
    category        TEXT    NOT NULL,
    description     TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'open',
    thread_id       INTEGER,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT
);

-- Flight logs (prepared for future telemetry integration)
CREATE TABLE IF NOT EXISTS flight_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    username        TEXT    NOT NULL,
    callsign        TEXT,
    aircraft        TEXT,
    departure       TEXT,
    arrival         TEXT,
    route           TEXT,
    duration_min    REAL,
    landing_rate    REAL,
    score           REAL,
    submitted_at    TEXT    NOT NULL
);

-- Desktop app events (prepared for OPS ROOM integration)
CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    event_type      TEXT    NOT NULL,
    callsign        TEXT,
    aircraft        TEXT,
    route           TEXT,
    version         TEXT,
    payload         TEXT,
    created_at      TEXT    NOT NULL
);

-- Discord announcements (scheduled/broadcast)
CREATE TABLE IF NOT EXISTS discord_announcements (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    channel_id      INTEGER NOT NULL,
    scheduled_at    TEXT,
    announced_at    TEXT,
    status          TEXT    NOT NULL DEFAULT 'pending'
);

-- Discord channels registry for admin panel
CREATE TABLE IF NOT EXISTS discord_channels (
    channel_id      INTEGER PRIMARY KEY,
    channel_name    TEXT    NOT NULL,
    channel_type    TEXT    NOT NULL,
    guild_id        INTEGER NOT NULL,
    registered_at   TEXT    NOT NULL
);

-- User airport preferences (saved departure/arrival/alt)
CREATE TABLE IF NOT EXISTS airport_preferences (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    icao            TEXT    NOT NULL,
    airport_type    TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    UNIQUE(user_id, icao, airport_type)
);

-- Notification preferences
CREATE TABLE IF NOT EXISTS notifications (
    user_id         INTEGER PRIMARY KEY,
    release_notify  INTEGER NOT NULL DEFAULT 1,
    weather_notify  INTEGER NOT NULL DEFAULT 0,
    event_notify    INTEGER NOT NULL DEFAULT 0,
    updated_at      TEXT    NOT NULL
);

-- Weather cache for airport queries
CREATE TABLE IF NOT EXISTS weather_cache (
    icao            TEXT    NOT NULL,
    data_type       TEXT    NOT NULL,
    raw_data        TEXT    NOT NULL,
    cached_at       TEXT    NOT NULL,
    expires_at      TEXT    NOT NULL,
    PRIMARY KEY (icao, data_type)
);

-- API request logs for analytics
CREATE TABLE IF NOT EXISTS api_logs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    endpoint        TEXT    NOT NULL,
    user_id         INTEGER,
    success         INTEGER NOT NULL DEFAULT 1,
    response_ms     INTEGER,
    error_detail    TEXT,
    created_at      TEXT    NOT NULL
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_notams_active ON notams(is_active);
CREATE INDEX IF NOT EXISTS idx_notams_priority ON notams(priority);
CREATE INDEX IF NOT EXISTS idx_logs_type ON logs(event_type);
CREATE INDEX IF NOT EXISTS idx_logs_user ON logs(user_id);
CREATE INDEX IF NOT EXISTS idx_logs_created ON logs(created_at);
CREATE INDEX IF NOT EXISTS idx_announcements_created ON announcements(created_at);
CREATE INDEX IF NOT EXISTS idx_bugs_status ON bugs(status);
CREATE INDEX IF NOT EXISTS idx_bugs_reporter ON bugs(reporter_id);
CREATE INDEX IF NOT EXISTS idx_tickets_user ON tickets(user_id);
CREATE INDEX IF NOT EXISTS idx_tickets_status ON tickets(status);
CREATE INDEX IF NOT EXISTS idx_flight_logs_user ON flight_logs(user_id);
CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_simbrief_discord ON simbrief_accounts(discord_id);
"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def get_db() -> aiosqlite.Connection:
    """Return the shared database connection, creating it if needed.

    Uses an asyncio.Lock to prevent race conditions during initialisation.
    """
    global _db
    if _db is None:
        async with _db_lock:
            if _db is None:  # double-checked locking
                db_path = Path(config.database_path)
                db_path.parent.mkdir(parents=True, exist_ok=True)

                _db = await aiosqlite.connect(str(db_path))
                _db.row_factory = aiosqlite.Row
                await _db.execute("PRAGMA journal_mode=WAL")
                await _db.execute("PRAGMA foreign_keys=ON")
                logger.info("Connected to SQLite at %s", db_path)

    return _db


async def init_db() -> None:
    """Create all tables if they don't exist (idempotent)."""
    db = await get_db()
    await db.executescript(SCHEMA)
    await db.commit()
    logger.info("Database schema verified.")


async def run_migrations() -> None:
    """Run safe ALTER TABLE migrations for existing deployments.

    Each ALTER is wrapped in a try/except so that fresh deployments
    (which already have the columns from CREATE TABLE) don't fail.
    """
    db = await get_db()
    migrations = [
        "ALTER TABLE users ADD COLUMN simulator TEXT",
        "ALTER TABLE users ADD COLUMN network TEXT",
        "ALTER TABLE users ADD COLUMN beta_status INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN opsroom_version TEXT",
        "ALTER TABLE events ADD COLUMN callsign TEXT",
        "ALTER TABLE events ADD COLUMN aircraft TEXT",
        "ALTER TABLE events ADD COLUMN route TEXT",
        "ALTER TABLE events ADD COLUMN version TEXT",
    ]
    for stmt in migrations:
        try:
            await db.execute(stmt)
            await db.commit()
            logger.info("Migration applied: %s", stmt[:60])
        except Exception:
            # Column already exists — safe to skip
            pass
    logger.info("Migrations complete.")


async def close_db() -> None:
    """Close the database connection gracefully."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None
        logger.info("Database connection closed.")
