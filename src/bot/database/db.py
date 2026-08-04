"""
OPS CONTROL - Database Layer

SQLite persistence with a migration-friendly design.
The bot owns database initialization and migration execution.

The canonical `pending_actions` schema lives HERE and must never diverge
from the schema the admin API expects. The admin API (opsroom-website)
writes into the same SQLite database via OPS_CONTROL_DB.

Migration strategy:
  * Fresh database      -> canonical CREATE TABLE (below)
  * Legacy `payload`    -> transactional table rebuild into payload_json
  * Missing columns     -> idempotent ALTER TABLE
  * Existing data       -> preserved, never deleted
"""

from __future__ import annotations

import asyncio
import json
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

# Canonical pending_actions table. THE single source of truth for the
# admin-panel -> bot action queue. Do not rename columns here without
# updating both repositories.
PENDING_ACTIONS_CANONICAL = """
CREATE TABLE IF NOT EXISTS pending_actions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type           TEXT    NOT NULL,
    payload_json          TEXT    NOT NULL,
    status                TEXT    NOT NULL DEFAULT 'pending',
    created_at            TEXT    NOT NULL,
    scheduled_at          TEXT,
    processing_started_at TEXT,
    processed_at          TEXT,
    attempts              INTEGER NOT NULL DEFAULT 0,
    error                 TEXT,
    result_json           TEXT
);
"""

SCHEMA = f"""
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
    static_id       TEXT,
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
    title           TEXT,
    steps           TEXT,
    expected        TEXT,
    actual          TEXT,
    priority        TEXT    NOT NULL DEFAULT 'normal',
    status          TEXT    NOT NULL DEFAULT 'open',
    assigned_to     INTEGER,
    thread_id       INTEGER,
    channel_id      INTEGER,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT
);

-- Support tickets
CREATE TABLE IF NOT EXISTS tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    username        TEXT    NOT NULL,
    category        TEXT    NOT NULL,
    priority        TEXT    NOT NULL DEFAULT 'Normal',
    subject         TEXT,
    description     TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'open',
    assigned_to     INTEGER,
    thread_id       INTEGER,
    channel_id      INTEGER,
    created_at      TEXT    NOT NULL,
    updated_at      TEXT,
    closed_by       INTEGER,
    closed_at       TEXT,
    transcript_status      TEXT,
    transcript_filename    TEXT,
    transcript_channel_id  INTEGER,
    transcript_message_id  INTEGER,
    transcript_dm_sent     INTEGER NOT NULL DEFAULT 0,
    transcript_error       TEXT,
    close_reason           TEXT,
    transcript_url         TEXT
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

{PENDING_ACTIONS_CANONICAL}

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
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_actions(status);
CREATE INDEX IF NOT EXISTS idx_pending_scheduled_at ON pending_actions(scheduled_at);
CREATE INDEX IF NOT EXISTS idx_pending_created_at ON pending_actions(created_at);

-- v0.25.55 - Moderation cases (B2)
CREATE TABLE IF NOT EXISTS moderation_cases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    guild_id        INTEGER NOT NULL,
    action_type     TEXT    NOT NULL,
    reason          TEXT,
    moderator_id    INTEGER NOT NULL,
    created_at      TEXT    NOT NULL,
    expires_at      TEXT,
    active          INTEGER NOT NULL DEFAULT 1
);

-- v0.25.55 - Appeals (B2 + C4)
CREATE TABLE IF NOT EXISTS appeals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER,
    username        TEXT,
    action_type     TEXT,
    statement       TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'pending',
    reviewed_by     INTEGER,
    reviewed_at     TEXT,
    resolution      TEXT,
    created_at      TEXT    NOT NULL
);

-- v0.25.55 - VATSIM event tracking (B3)
CREATE TABLE IF NOT EXISTS vatsim_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id        TEXT    NOT NULL UNIQUE,
    title           TEXT    NOT NULL,
    start_time      TEXT    NOT NULL,
    end_time        TEXT,
    posted          INTEGER NOT NULL DEFAULT 0,
    reminded        INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT    NOT NULL
);

-- v0.25.55 - Automod configuration (B2 + C2)
CREATE TABLE IF NOT EXISTS automod_config (
    rule_key        TEXT    PRIMARY KEY,
    enabled         INTEGER NOT NULL DEFAULT 1,
    action          TEXT    NOT NULL DEFAULT 'warn',
    threshold       REAL,
    config_json     TEXT,
    updated_by      INTEGER,
    updated_at      TEXT    NOT NULL
);

-- v0.25.55 - Staff allowlist (C3)
-- Flexible shape: provider ('github'|'discord') + identifier
-- (lowercased GitHub username, or Discord user ID string). Env vars seed
-- this on first boot; the admin panel manages it live afterwards.
CREATE TABLE IF NOT EXISTS staff_allowlist (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    provider    TEXT    NOT NULL,
    identifier  TEXT    NOT NULL,
    display     TEXT,
    added_by    INTEGER,
    added_at    TEXT    NOT NULL,
    UNIQUE(provider, identifier)
);

-- v0.25.56 - VATSIM flight tracker links (auto takeoff/landing posts)
CREATE TABLE IF NOT EXISTS vatsim_links (
    discord_id  INTEGER PRIMARY KEY,
    vatsim_cid  INTEGER NOT NULL,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT
);

-- v0.25.56 - VATSIM tracker per-CID state (dedupe notifications across restarts)
CREATE TABLE IF NOT EXISTS vatsim_tracking (
    vatsim_cid  INTEGER PRIMARY KEY,
    callsign    TEXT,
    airborne    INTEGER NOT NULL DEFAULT 0,
    departure   TEXT,
    arrival     TEXT,
    aircraft    TEXT,
    last_seen   TEXT
);

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
                await _db.execute("PRAGMA busy_timeout=5000")
                logger.info("Connected to SQLite at %s", db_path)

    return _db


async def init_db() -> None:
    """Create all tables if they don't exist (idempotent)."""
    db = await get_db()
    await db.executescript(SCHEMA)
    await db.commit()
    logger.info("Database schema verified.")


# ---------------------------------------------------------------------------
# pending_actions migration (canonical schema)
# ---------------------------------------------------------------------------

# Column names of the canonical pending_actions table.
_CANONICAL_COLUMNS = {
    "id", "action_type", "payload_json", "status", "created_at",
    "scheduled_at", "processing_started_at", "processed_at",
    "attempts", "error", "result_json",
}


async def _table_columns(db: aiosqlite.Connection) -> set[str]:
    """Return the set of column names for pending_actions (empty if missing)."""
    cursor = await db.execute("PRAGMA table_info(pending_actions)")
    rows = await cursor.fetchall()
    return {str(row["name"]) for row in rows}


async def migrate_pending_actions(db: aiosqlite.Connection) -> None:
    """Bring the pending_actions table to the canonical schema.

    Handles:
      * fresh DB (no table)              -> create canonical
      * legacy table with `payload`      -> transactional rebuild into payload_json
      * table with both payload/payload_json -> rebuild, prefer payload_json
      * missing columns                  -> idempotent ALTER TABLE
      * existing pending/completed rows  -> preserved
      * repeated execution               -> no-op / safe

    The bot owns schema migration; the admin API only reads/writes the
    canonical columns.
    """
    columns = await _table_columns(db)
    if not columns:
        # Table does not exist yet — canonical CREATE already ran in SCHEMA.
        await db.execute(PENDING_ACTIONS_CANONICAL)
        await db.commit()
        logger.info("pending_actions table created (canonical).")
        return

    needs_rebuild = "payload" in columns
    missing = _CANONICAL_COLUMNS - columns
    if needs_rebuild or missing:
        await _rebuild_pending_actions(db, columns)
    else:
        logger.info("pending_actions schema already canonical.")

    # Indexes (idempotent)
    for idx_sql in (
        "CREATE INDEX IF NOT EXISTS idx_pending_status ON pending_actions(status)",
        "CREATE INDEX IF NOT EXISTS idx_pending_scheduled_at ON pending_actions(scheduled_at)",
        "CREATE INDEX IF NOT EXISTS idx_pending_created_at ON pending_actions(created_at)",
    ):
        try:
            await db.execute(idx_sql)
        except Exception:
            logger.exception("Failed to create pending_actions index")
    await db.commit()


async def _rebuild_pending_actions(db: aiosqlite.Connection, columns: set[str]) -> None:
    """Transactionally rebuild pending_actions into the canonical schema.

    SQLite cannot drop a NOT NULL constraint, so a full rebuild is required
    when a legacy `payload TEXT NOT NULL` column exists.
    """
    has_payload = "payload" in columns
    has_payload_json = "payload_json" in columns
    has_error = "error" in columns
    has_error_detail = "error_detail" in columns
    has_attempts = "attempts" in columns

    # Build a per-row SELECT expression that prefers the canonical column
    # and falls back to the legacy column when present.
    payload_expr = (
        "CASE WHEN payload_json IS NOT NULL THEN payload_json ELSE payload END"
        if has_payload and has_payload_json
        else "payload_json" if has_payload_json else "payload"
    )
    error_expr = (
        "CASE WHEN error IS NOT NULL THEN error ELSE error_detail END"
        if has_error and has_error_detail
        else "error" if has_error else "error_detail" if has_error_detail else "NULL"
    )
    attempts_expr = "attempts" if has_attempts else "0"
    result_expr = "result_json" if "result_json" in columns else "NULL"
    scheduled_expr = "scheduled_at" if "scheduled_at" in columns else "NULL"
    processing_expr = "processing_started_at" if "processing_started_at" in columns else "NULL"
    processed_expr = "processed_at" if "processed_at" in columns else "NULL"

    try:
        await db.execute("BEGIN")
        await db.execute("DROP TABLE IF EXISTS pending_actions_migrated")
        await db.execute(PENDING_ACTIONS_CANONICAL.replace(
            "pending_actions", "pending_actions_migrated"
        ))
        await db.execute(
            f"""
            INSERT INTO pending_actions_migrated (
                id, action_type, payload_json, status, created_at,
                scheduled_at, processing_started_at, processed_at,
                attempts, error, result_json
            )
            SELECT
                id, action_type, {payload_expr}, status, created_at,
                {scheduled_expr}, {processing_expr}, {processed_expr},
                {attempts_expr}, {error_expr}, {result_expr}
            FROM pending_actions
            """
        )
        await db.execute("DROP TABLE pending_actions")
        await db.execute("ALTER TABLE pending_actions_migrated RENAME TO pending_actions")
        await db.commit()
        logger.info("pending_actions table rebuilt to canonical schema (%d rows migrated).")
    except Exception:
        await db.rollback()
        raise


async def run_migrations() -> None:
    """Run safe, idempotent, non-destructive migrations for existing deployments."""
    db = await get_db()

    # 1. pending_actions canonical schema (handles legacy payload rebuild)
    try:
        await migrate_pending_actions(db)
    except Exception:
        logger.exception("pending_actions migration failed — continuing with other migrations")

    # 2. Column additions (tolerate duplicate-column errors on fresh DBs)
    migrations = [
        "ALTER TABLE users ADD COLUMN simulator TEXT",
        "ALTER TABLE users ADD COLUMN network TEXT",
        "ALTER TABLE users ADD COLUMN beta_status INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE users ADD COLUMN opsroom_version TEXT",
        "ALTER TABLE events ADD COLUMN callsign TEXT",
        "ALTER TABLE events ADD COLUMN aircraft TEXT",
        "ALTER TABLE events ADD COLUMN route TEXT",
        "ALTER TABLE events ADD COLUMN version TEXT",
        "ALTER TABLE simbrief_accounts ADD COLUMN static_id TEXT",
        "ALTER TABLE tickets ADD COLUMN channel_id INTEGER",
        "ALTER TABLE tickets ADD COLUMN subject TEXT",
        "ALTER TABLE tickets ADD COLUMN assigned_to INTEGER",
        "ALTER TABLE tickets ADD COLUMN priority TEXT NOT NULL DEFAULT 'Normal'",
        "ALTER TABLE tickets ADD COLUMN closed_by INTEGER",
        "ALTER TABLE tickets ADD COLUMN closed_at TEXT",
        "ALTER TABLE tickets ADD COLUMN transcript_status TEXT",
        "ALTER TABLE tickets ADD COLUMN transcript_filename TEXT",
        "ALTER TABLE tickets ADD COLUMN transcript_channel_id INTEGER",
        "ALTER TABLE tickets ADD COLUMN transcript_message_id INTEGER",
        "ALTER TABLE tickets ADD COLUMN transcript_dm_sent INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE tickets ADD COLUMN transcript_error TEXT",
        "ALTER TABLE bugs ADD COLUMN channel_id INTEGER",
        "ALTER TABLE bugs ADD COLUMN title TEXT",
        "ALTER TABLE bugs ADD COLUMN assigned_to INTEGER",
        # v0.25.55 - ticket close reason (B1)
        "ALTER TABLE tickets ADD COLUMN close_reason TEXT",
        # v0.25.55 - hosted transcript URL (B1 + C1)
        "ALTER TABLE tickets ADD COLUMN transcript_url TEXT",
        # v0.25.55 - announcement scheduling + templates (C2)
        "ALTER TABLE announcements ADD COLUMN scheduled_at TEXT",
        "ALTER TABLE announcements ADD COLUMN status TEXT NOT NULL DEFAULT sent",
        "ALTER TABLE announcements ADD COLUMN template_name TEXT",
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
