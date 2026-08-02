"""
DB migration dry run -- v0.25.55 work order (Section 8).

Constructs a database schema as it existed BEFORE the v0.25.55 work order
(no tickets.close_reason, no tickets.transcript_url, no announcements
scheduling columns, and no moderation_cases / appeals / vatsim_events /
automod_config / staff_allowlist tables), then runs init_db() +
run_migrations() and verifies the final schema. Runs the same routine a
second time against the already-migrated schema to prove true idempotency
(a clean no-op, no duplicate columns, existing data preserved).
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))

os.environ["DATABASE_PATH"] = os.path.join(tempfile.gettempdir(), "ops_control_workorder_test.db")
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GUILD_ID", "1")
os.environ.setdefault("OWNER_USER_ID", "2")
os.environ.setdefault("ARRIVALS_CHANNEL_ID", "3")
os.environ.setdefault("PENDING_ACTION_MAX_ATTEMPTS", "3")
os.environ.setdefault("PENDING_ACTION_POLL_SECONDS", "2")

import aiosqlite  # noqa: E402

from bot.database.db import init_db, run_migrations  # noqa: E402

# Tables created by the v0.25.55 work order.
NEW_TABLES = {"moderation_cases", "appeals", "vatsim_events", "automod_config", "staff_allowlist"}

# Schema as it existed before the v0.25.55 work order.
PRE_WORK_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT NOT NULL, display_name TEXT,
    first_joined TEXT NOT NULL, last_seen TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1, simulator TEXT, network TEXT,
    beta_status INTEGER NOT NULL DEFAULT 0, opsroom_version TEXT
);
CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, content TEXT NOT NULL,
    image_url TEXT, created_by INTEGER NOT NULL, created_by_name TEXT NOT NULL,
    created_at TEXT NOT NULL, channel_id INTEGER, message_id INTEGER
);
CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, username TEXT NOT NULL,
    category TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'Normal', subject TEXT,
    description TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', assigned_to INTEGER,
    thread_id INTEGER, channel_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT,
    closed_by INTEGER, closed_at TEXT, transcript_status TEXT, transcript_filename TEXT,
    transcript_channel_id INTEGER, transcript_message_id INTEGER,
    transcript_dm_sent INTEGER NOT NULL DEFAULT 0, transcript_error TEXT
);
CREATE TABLE IF NOT EXISTS bugs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, reporter_id INTEGER NOT NULL, reporter_name TEXT NOT NULL,
    version TEXT NOT NULL, simulator TEXT, aircraft TEXT, module TEXT NOT NULL,
    description TEXT NOT NULL, title TEXT, steps TEXT, expected TEXT, actual TEXT,
    priority TEXT NOT NULL DEFAULT 'normal', status TEXT NOT NULL DEFAULT 'open',
    assigned_to INTEGER, thread_id INTEGER, channel_id INTEGER,
    created_at TEXT NOT NULL, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, event_type TEXT NOT NULL,
    payload TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS simbrief_accounts (
    discord_id INTEGER PRIMARY KEY, simbrief_user TEXT NOT NULL, pilot_id TEXT,
    created_at TEXT NOT NULL, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS pending_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL, scheduled_at TEXT, processing_started_at TEXT,
    processed_at TEXT, attempts INTEGER NOT NULL DEFAULT 0, error TEXT, result_json TEXT
);
INSERT INTO tickets (id, user_id, username, category, description, status, created_at, updated_at)
VALUES (1, 101, 'user101', 'support', 'pre-existing row', 'open', '2026-01-01', '2026-01-01');
"""


class MigrationDryRunTests(unittest.IsolatedAsyncioTestCase):
    async def _new_db(self) -> tuple[aiosqlite.Connection, str]:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db = await aiosqlite.connect(path)
        db.row_factory = aiosqlite.Row
        await db.executescript(PRE_WORK_DDL)
        await db.commit()
        return db, path

    async def _close(self, db: aiosqlite.Connection, path: str) -> None:
        await db.close()
        try:
            os.unlink(path)
        except OSError:
            pass

    async def _apply(self, db: aiosqlite.Connection) -> None:
        """Run the full migration routine against an explicit connection.

        init_db/run_migrations resolve `get_db` from the bot.database.db module
        globals, so the patch target must be the module, not the package alias.
        """
        with mock.patch("bot.database.db.get_db", new=mock.AsyncMock(return_value=db)):
            await init_db()
            await run_migrations()

    async def _table_names(self, db: aiosqlite.Connection) -> set[str]:
        cur = await db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        return {r["name"] for r in await cur.fetchall()}

    async def _columns(self, db: aiosqlite.Connection, table: str) -> list[str]:
        cur = await db.execute(f"PRAGMA table_info({table})")
        return [r["name"] for r in await cur.fetchall()]

    # -- Sanity: the pre-work schema really is pre-work --------------------

    async def test_pre_work_schema_lacks_new_tables_and_columns(self):
        db, path = await self._new_db()
        try:
            tables = await self._table_names(db)
            self.assertTrue(NEW_TABLES.isdisjoint(tables))
            tcols = await self._columns(db, "tickets")
            self.assertNotIn("close_reason", tcols)
            self.assertNotIn("transcript_url", tcols)
            acols = await self._columns(db, "announcements")
            self.assertNotIn("scheduled_at", acols)
            self.assertNotIn("template_name", acols)
        finally:
            await self._close(db, path)

    # -- First run: pre-work -> final schema -------------------------------

    async def test_migration_upgrades_pre_work_schema(self):
        db, path = await self._new_db()
        try:
            await self._apply(db)

            tables = await self._table_names(db)
            for table in NEW_TABLES:
                self.assertIn(table, tables)

            tcols = await self._columns(db, "tickets")
            self.assertIn("close_reason", tcols)
            self.assertIn("transcript_url", tcols)

            acols = await self._columns(db, "announcements")
            self.assertIn("scheduled_at", acols)
            self.assertIn("status", acols)
            self.assertIn("template_name", acols)

            # events / simbrief_accounts earlier migrations applied too
            ecols = await self._columns(db, "events")
            self.assertIn("callsign", ecols)
            scols = await self._columns(db, "simbrief_accounts")
            self.assertIn("static_id", scols)

            # pre-existing data preserved, new columns default to NULL
            cur = await db.execute("SELECT id, status, close_reason FROM tickets WHERE id=1")
            row = await cur.fetchone()
            self.assertEqual(row["status"], "open")
            self.assertIsNone(row["close_reason"])
        finally:
            await self._close(db, path)

    # -- Second run: true idempotency (clean no-op) ------------------------

    async def test_second_run_is_clean_noop(self):
        db, path = await self._new_db()
        try:
            await self._apply(db)
            await self._apply(db)  # already-migrated schema

            tables = await self._table_names(db)
            for table in NEW_TABLES:
                self.assertIn(table, tables)

            # No duplicate columns introduced by the second run.
            tcols = await self._columns(db, "tickets")
            self.assertEqual(tcols.count("close_reason"), 1)
            self.assertEqual(tcols.count("transcript_url"), 1)
            acols = await self._columns(db, "announcements")
            self.assertEqual(acols.count("scheduled_at"), 1)
            self.assertEqual(acols.count("template_name"), 1)

            # Data unchanged.
            cur = await db.execute("SELECT COUNT(*) AS c FROM tickets")
            self.assertEqual((await cur.fetchone())["c"], 1)
            cur = await db.execute("SELECT COUNT(*) AS c FROM pending_actions")
            self.assertEqual((await cur.fetchone())["c"], 0)
        finally:
            await self._close(db, path)


if __name__ == "__main__":
    unittest.main()
