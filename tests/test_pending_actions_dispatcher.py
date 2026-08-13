"""
Dispatcher tests for the pending_actions queue.

Covers:
  * immediate announcement processing
  * scheduled announcement stays pending until due
  * transient failure -> retry (attempts bounded)
  * terminal failure -> failed
  * malformed payload -> failed, never crashes loop
  * unknown action type -> failed
  * claim prevents duplicate processing
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))

# IMPORTANT: set DATABASE_PATH BEFORE importing bot modules - the frozen
# config is read once at import time. All tests share one temp DB file;
# each test resets the queue table in asyncSetUp.
_TMPDIR = tempfile.mkdtemp(prefix="ops_test_dispatcher_")
os.environ["DISCORD_TOKEN"] = "test-token"
os.environ["GUILD_ID"] = "1"
os.environ["OWNER_USER_ID"] = "2"
os.environ["ARRIVALS_CHANNEL_ID"] = "3"
os.environ["PENDING_ACTION_MAX_ATTEMPTS"] = "3"
os.environ["PENDING_ACTION_POLL_SECONDS"] = "2"
os.environ["DATABASE_PATH"] = os.path.join(_TMPDIR, "test.db")

import aiosqlite  # noqa: E402
from unittest import mock  # noqa: E402

import discord  # noqa: E402

from bot.database import close_db, get_db, init_db  # noqa: E402
from bot.services import pending_actions as pa  # noqa: E402


def _make_channel(channel_id: int, name: str = "announcements"):
    """Return a mock TextChannel that passes isinstance(TextChannel) - the
    dispatcher validates channel types before sending."""
    ch = mock.Mock(spec=discord.TextChannel)
    ch.id = channel_id
    ch.name = name
    ch.sent = []

    async def fake_send(embed=None, content=None, **kwargs):
        ch.sent.append({"embed": embed, "content": content})
        msg = mock.Mock()
        msg.id = 123456
        return msg

    ch.send = fake_send
    return ch


class FakeGuild:
    def __init__(self, guild_id: int):
        self.id = guild_id
        self.members: dict[int, dict] = {}

    def get_member(self, user_id: int):
        return self.members.get(user_id)

    async def fetch_member(self, user_id: int):
        m = self.members.get(user_id)
        if not m:
            raise ValueError("not found")
        return m

    def get_role(self, role_id: int):
        return _FakeRole(role_id) if role_id else None


class _FakeRole:
    def __init__(self, role_id: int):
        self.id = role_id
        self.name = f"role-{role_id}"


class _FakeMember:
    def __init__(self, user_id: int):
        self.id = user_id
        self.name = f"user{user_id}"
        self.roles: list = []

    async def add_roles(self, role, reason=None):
        if role not in self.roles:
            self.roles.append(role)

    async def remove_roles(self, role, reason=None):
        if role in self.roles:
            self.roles.remove(role)


class FakeBot:
    def __init__(self, channels: dict[int, object] | None = None):
        self.channels = channels or {999: _make_channel(999)}
        self.guild = FakeGuild(1)
        self.closed = False

    def get_channel(self, channel_id: int):
        return self.channels.get(channel_id)

    def get_guild(self, guild_id: int):
        return self.guild if guild_id == 1 else None

    def is_closed(self):
        return self.closed


class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Fresh queue per test. Create the FULL schema FIRST (the temp DB
        # starts empty; the announcement handler also touches
        # discord_announcements), then reset rows so IDs restart.
        db = await get_db()
        await init_db()
        await db.execute("DELETE FROM pending_actions")
        await db.execute("DELETE FROM sqlite_sequence WHERE name = 'pending_actions'")
        await db.commit()
        self.bot = FakeBot()

    async def asyncTearDown(self):
        await close_db()

    async def _insert(self, action_type: str, payload: dict | str, *, scheduled_at=None):
        db = await get_db()
        raw = json.dumps(payload) if isinstance(payload, dict) else payload
        cursor = await db.execute(
            "INSERT INTO pending_actions (action_type, payload_json, status, created_at, scheduled_at) "
            "VALUES (?, ?, 'pending', ?, ?)",
            (action_type, raw, "2026-01-01T00:00:00", scheduled_at),
        )
        await db.commit()
        return cursor.lastrowid

    async def _status(self, action_id: int) -> str:
        db = await get_db()
        cur = await db.execute("SELECT status FROM pending_actions WHERE id = ?", (action_id,))
        row = await cur.fetchone()
        return row["status"] if row else None

    async def test_immediate_announcement(self):
        await self._insert("announcement", {
            "announcement_id": 1,
            "title": "Hello",
            "content": "World",
            "channel_id": 999,
        })
        count = await pa.process_pending_actions(self.bot)
        self.assertEqual(count, 1)
        self.assertEqual(await self._status(1), "completed")
        self.assertTrue(self.bot.channels[999].sent)

    async def test_unknown_action_type_fails_without_crash(self):
        await self._insert("totally_unknown", {"x": 1})
        count = await pa.process_pending_actions(self.bot)
        self.assertEqual(count, 1)
        self.assertEqual(await self._status(1), "failed")

    async def test_malformed_payload_fails_without_crash(self):
        await self._insert("announcement", "{not valid json")
        count = await pa.process_pending_actions(self.bot)
        self.assertEqual(count, 1)
        self.assertEqual(await self._status(1), "failed")

    async def test_missing_channel_retries_then_fails(self):
        await self._insert("announcement", {
            "announcement_id": 1,
            "title": "T",
            "content": "C",
            "channel_id": 424242,  # not in fake bot channels
        })
        for _ in range(3):
            await pa.process_pending_actions(self.bot)
        self.assertEqual(await self._status(1), "failed")
        db = await get_db()
        cur = await db.execute("SELECT attempts, error FROM pending_actions WHERE id = 1")
        row = await cur.fetchone()
        self.assertGreaterEqual(row["attempts"], 1)
        self.assertIn("channel not found", row["error"] or "")

    async def test_scheduled_future_stays_pending(self):
        await self._insert("scheduled_announcement", {
            "announcement_id": 1,
            "title": "T",
            "content": "C",
            "channel_id": 999,
        }, scheduled_at="2099-01-01T00:00:00")
        count = await pa.process_pending_actions(self.bot)
        self.assertEqual(count, 0)
        self.assertEqual(await self._status(1), "pending")

    async def test_scheduled_due_processes(self):
        await self._insert("scheduled_announcement", {
            "announcement_id": 1,
            "title": "T",
            "content": "C",
            "channel_id": 999,
        }, scheduled_at="2000-01-01T00:00:00")
        count = await pa.process_pending_actions(self.bot)
        self.assertEqual(count, 1)
        self.assertEqual(await self._status(1), "completed")

    async def test_claim_prevents_duplicate_processing(self):
        await self._insert("announcement", {
            "announcement_id": 1,
            "title": "T",
            "content": "C",
            "channel_id": 999,
        })
        # First pass processes; second pass must find nothing pending.
        await pa.process_pending_actions(self.bot)
        db = await get_db()
        cur = await db.execute("SELECT status FROM pending_actions WHERE id = 1")
        self.assertEqual((await cur.fetchone())["status"], "completed")
        count2 = await pa.process_pending_actions(self.bot)
        self.assertEqual(count2, 0)

    async def test_one_failure_does_not_stop_loop(self):
        await self._insert("announcement", "{bad json")
        await self._insert("announcement", {
            "announcement_id": 2,
            "title": "OK",
            "content": "Fine",
            "channel_id": 999,
        })
        count = await pa.process_pending_actions(self.bot)
        self.assertEqual(count, 2)
        self.assertEqual(await self._status(2), "completed")

    async def test_legacy_announce_dispatch_alias(self):
        await self._insert("announce_dispatch", {
            "announcement_id": 1,
            "title": "Legacy",
            "content": "Works",
            "channel_id": 999,
        })
        count = await pa.process_pending_actions(self.bot)
        self.assertEqual(count, 1)
        self.assertEqual(await self._status(1), "completed")


if __name__ == "__main__":
    unittest.main()
