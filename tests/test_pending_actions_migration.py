"""
Migration tests for the canonical pending_actions schema.

Covers:
  * fresh DB (no table)
  * legacy DB with `payload TEXT NOT NULL` only
  * DB with both payload and payload_json
  * existing pending/completed rows preserved
  * repeated migration execution (idempotent)
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure src/ is importable and required env vars exist before bot.config loads.
_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GUILD_ID", "1")
os.environ.setdefault("OWNER_USER_ID", "2")
os.environ.setdefault("ARRIVALS_CHANNEL_ID", "3")

import aiosqlite  # noqa: E402

from bot.database.db import migrate_pending_actions  # noqa: E402

CANONICAL = [
    "id", "action_type", "payload_json", "status", "created_at",
    "scheduled_at", "processing_started_at", "processed_at",
    "attempts", "error", "result_json",
]

LEGACY_DDL = """
CREATE TABLE pending_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    processed_at TEXT,
    error_detail TEXT
);
"""


async def _columns(db) -> set[str]:
    cur = await db.execute("PRAGMA table_info(pending_actions)")
    rows = await cur.fetchall()
    return {str(r["name"]) for r in rows}


class PendingActionsMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def _new_db(self, ddl: str | None) -> tuple[aiosqlite.Connection, str]:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db = await aiosqlite.connect(path)
        db.row_factory = aiosqlite.Row
        if ddl:
            await db.executescript(ddl)
            await db.commit()
        return db, path

    async def _close(self, db, path):
        await db.close()
        try:
            os.unlink(path)
        except OSError:
            pass

    async def test_fresh_db_creates_canonical(self):
        db, path = await self._new_db(None)
        try:
            await migrate_pending_actions(db)
            cols = await _columns(db)
            self.assertEqual(cols, set(CANONICAL))
        finally:
            await self._close(db, path)

    async def test_missing_table_creates_canonical(self):
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        db = await aiosqlite.connect(path)
        db.row_factory = aiosqlite.Row
        # empty DB without pending_actions
        await migrate_pending_actions(db)
        cols = await _columns(db)
        self.assertEqual(cols, set(CANONICAL))
        await self._close(db, path)

    async def test_legacy_payload_only_migrates(self):
        db, path = await self._new_db(LEGACY_DDL)
        try:
            await db.execute(
                "INSERT INTO pending_actions (action_type, payload, status, created_at) "
                "VALUES ('announcement', ?, 'pending', ?)",
                ('{"title": "T", "content": "C"}', "2026-01-01T00:00:00"),
            )
            await db.execute(
                "INSERT INTO pending_actions (action_type, payload, status, created_at, processed_at, error_detail) "
                "VALUES ('beta_role_change', ?, 'completed', ?, ?, ?)",
                ('{"x": 1}', "2026-01-01T00:00:00", "2026-01-01T00:01:00", "old error"),
            )
            await db.commit()

            await migrate_pending_actions(db)

            cols = await _columns(db)
            self.assertEqual(cols, set(CANONICAL))

            cur = await db.execute("SELECT * FROM pending_actions ORDER BY id")
            rows = await cur.fetchall()
            self.assertEqual(len(rows), 2)
            # payload content moved into payload_json
            self.assertIn('"title": "T"', rows[0]["payload_json"])
            self.assertIn("announcement", rows[0]["action_type"])
            self.assertEqual(rows[0]["attempts"], 0)
            self.assertEqual(rows[0]["error"], None)
            # completed row preserved with error_detail moved into error
            self.assertEqual(rows[1]["status"], "completed")
            self.assertEqual(rows[1]["error"], "old error")
            self.assertIsNotNone(rows[1]["processed_at"])
        finally:
            await self._close(db, path)

    async def test_both_payload_and_payload_json_prefers_json(self):
        ddl = LEGACY_DDL.replace(
            "payload TEXT NOT NULL",
            "payload TEXT, payload_json TEXT",
        )
        db, path = await self._new_db(ddl)
        try:
            await db.execute(
                "INSERT INTO pending_actions (action_type, payload, payload_json, status, created_at) "
                "VALUES ('announcement', ?, ?, 'pending', ?)",
                ('{"legacy": true}', '{"canonical": true}', "2026-01-01T00:00:00"),
            )
            await db.commit()

            await migrate_pending_actions(db)

            cols = await _columns(db)
            self.assertEqual(cols, set(CANONICAL))
            cur = await db.execute("SELECT payload_json FROM pending_actions WHERE id = 1")
            row = await cur.fetchone()
            self.assertIn('"canonical": true', row["payload_json"])
        finally:
            await self._close(db, path)

    async def test_existing_pending_actions_preserved(self):
        db, path = await self._new_db(LEGACY_DDL)
        try:
            for i in range(3):
                await db.execute(
                    "INSERT INTO pending_actions (action_type, payload, status, created_at) "
                    "VALUES ('announcement', ?, 'pending', ?)",
                    (f'{{"n": {i}}}', "2026-01-01T00:00:00"),
                )
            await db.commit()

            await migrate_pending_actions(db)
            cur = await db.execute("SELECT COUNT(*) AS c FROM pending_actions WHERE status='pending'")
            self.assertEqual((await cur.fetchone())["c"], 3)
        finally:
            await self._close(db, path)

    async def test_repeated_migration_idempotent(self):
        db, path = await self._new_db(LEGACY_DDL)
        try:
            await db.execute(
                "INSERT INTO pending_actions (action_type, payload, status, created_at) "
                "VALUES ('announcement', '{}', 'pending', '2026-01-01T00:00:00')",
            )
            await db.commit()
            for _ in range(3):
                await migrate_pending_actions(db)
            cols = await _columns(db)
            self.assertEqual(cols, set(CANONICAL))
            cur = await db.execute("SELECT COUNT(*) AS c FROM pending_actions")
            self.assertEqual((await cur.fetchone())["c"], 1)
        finally:
            await self._close(db, path)

    async def test_indexes_created(self):
        db, path = await self._new_db(LEGACY_DDL)
        try:
            await migrate_pending_actions(db)
            cur = await db.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='pending_actions'"
            )
            idx = {r["name"] for r in await cur.fetchall()}
            self.assertIn("idx_pending_status", idx)
            self.assertIn("idx_pending_scheduled_at", idx)
            self.assertIn("idx_pending_created_at", idx)
        finally:
            await self._close(db, path)


if __name__ == "__main__":
    unittest.main()
