"""
v0.25.55 (B3) -- VATSIM events poller tests.

Covers:
  * duplicate-post prevention: posting the same event twice (simulated bot
    restart with a fresh cog instance against the same DB) must not double-post
  * reminder is sent at most once
  * events far in the future are announced but not reminded early
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
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

import discord  # noqa: E402

from bot.cogs.vatsim_events import VatsimEvents  # noqa: E402
from bot.database import get_db, init_db  # noqa: E402

CHANNEL_ID = 999


class FakeResp:
    status = 200

    def __init__(self, events: list[dict]):
        self._events = events

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return {"items": self._events}


class FakeSession:
    def __init__(self, events: list[dict]):
        self._events = events
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return FakeResp(self._events)


class FakeBot:
    def __init__(self, channel):
        self.channel = channel

    def get_channel(self, channel_id):
        return self.channel if channel_id == CHANNEL_ID else None


def _event_dict(event_id: str, starts_in_minutes: int) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": event_id,
        "name": f"Test Event {event_id}",
        "start_time": (now + timedelta(minutes=starts_in_minutes)).isoformat(),
        "end_time": (now + timedelta(hours=2)).isoformat(),
    }


class VatsimEventsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        db = await get_db()
        await db.execute("DELETE FROM vatsim_events")
        await db.commit()

    async def _poll_with_restart(self, events: list[dict], times: int):
        """Simulate `times` poll runs, each with a fresh cog (i.e. a restart)."""
        sends = []
        for _ in range(times):
            channel = mock.Mock(spec=discord.TextChannel)
            channel.send = mock.AsyncMock(return_value=mock.Mock(id=1))
            bot = FakeBot(channel)
            cog = VatsimEvents(bot)
            cog._session = FakeSession(events)
            with mock.patch("bot.cogs.vatsim_events.config",
                            SimpleNamespace(vatsim_events_channel_id=CHANNEL_ID)):
                await cog._poll_vatsim_events()
            sends.append(channel.send.call_args_list)
        return sends

    async def test_no_duplicate_post_across_restart(self):
        """Same event seen by three cog instances (restarts) posts only once."""
        events = [_event_dict("123", starts_in_minutes=45)]
        sends = await self._poll_with_restart(events, times=3)

        # Exactly one announcement embed across all polls, no duplicates.
        total = sum(len(calls) for calls in sends)
        self.assertEqual(total, 1)
        first_kwargs = sends[0][0].kwargs if sends[0] else {}
        self.assertIn("embed", first_kwargs)

        db = await get_db()
        cur = await db.execute("SELECT posted, reminded FROM vatsim_events WHERE event_id='123'")
        row = await cur.fetchone()
        self.assertEqual(row["posted"], 1)
        self.assertEqual(row["reminded"], 0)  # not yet within reminder window

    async def test_reminder_sent_once_then_suppressed(self):
        """Event starting in 10 minutes: announcement, then a single reminder."""
        events = [_event_dict("456", starts_in_minutes=10)]
        sends = await self._poll_with_restart(events, times=3)

        # Poll 1: announcement. Poll 2: reminder. Poll 3: suppressed.
        self.assertEqual(len(sends[0]), 1)
        self.assertEqual(len(sends[1]), 1)
        self.assertEqual(len(sends[2]), 0)

        # Announcement is an embed; reminder is a plain text message.
        self.assertIn("embed", sends[0][0].kwargs)
        self.assertNotIn("embed", sends[1][0].kwargs)
        self.assertIn("Reminder", str(sends[1][0].args))

        db = await get_db()
        cur = await db.execute("SELECT posted, reminded FROM vatsim_events WHERE event_id='456'")
        row = await cur.fetchone()
        self.assertEqual(row["posted"], 1)
        self.assertEqual(row["reminded"], 1)

    async def test_second_poll_no_new_activity_when_already_reminded(self):
        events = [_event_dict("789", starts_in_minutes=10)]
        sends = await self._poll_with_restart(events, times=4)
        # Only polls 1 (announcement) and 2 (reminder) produced output.
        self.assertEqual([len(c) for c in sends], [1, 1, 0, 0])


if __name__ == "__main__":
    unittest.main()
