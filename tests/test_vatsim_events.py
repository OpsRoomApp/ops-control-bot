"""
v0.25.55 (B3) -- VATSIM events poller tests.

Covers:
  * duplicate-post prevention: posting the same event twice (simulated bot
    restart with a fresh cog instance against the same DB) must not double-post
  * announcements fire only once the event is inside the announce window
    (default 60 min before start)
  * reminder is sent at most once, inside the reminder window (default 30 min)
  * events already started are skipped entirely
  * the announcement embed includes the event banner image
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

    def __init__(self, events: list[dict], key: str = "data", status: int = 200):
        self.status = status
        self._events = events
        self._key = key

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def json(self):
        return {self._key: self._events}


class FakeSession:
    def __init__(self, events: list[dict], key: str = "data", status: int = 200):
        self._events = events
        self._key = key
        self._status = status
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return FakeResp(self._events, self._key, self._status)


class FakeBot:
    def __init__(self, channel):
        self.channel = channel

    def get_channel(self, channel_id):
        return self.channel if channel_id == CHANNEL_ID else None


def _event_dict_range(event_id: str, starts_in_minutes: int,
                       ends_in_minutes: int = 120) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": event_id,
        "name": f"Test Event {event_id}",
        "start_time": (now + timedelta(minutes=starts_in_minutes)).isoformat(),
        "end_time": (now + timedelta(minutes=ends_in_minutes)).isoformat(),
    }


def _event_dict(event_id: str, starts_in_minutes: int) -> dict:
    return _event_dict_range(event_id, starts_in_minutes, 120)


class VatsimEventsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await init_db()
        db = await get_db()
        await db.execute("DELETE FROM vatsim_events")
        await db.commit()

    async def _poll_with_restart(self, events: list[dict], times: int,
                                    key: str = "data", status: int = 200):
        """Simulate `times` poll runs, each with a fresh cog (i.e. a restart)."""
        sends = []
        for _ in range(times):
            channel = mock.Mock(spec=discord.TextChannel)
            channel.send = mock.AsyncMock(return_value=mock.Mock(id=1))
            bot = FakeBot(channel)
            cog = VatsimEvents(bot)
            cog._session = FakeSession(events, key, status)
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


    async def test_legacy_items_wrapper_still_works(self):
        """Backwards compatibility: legacy {"items": [...]} payload still posts."""
        events = [_event_dict("321", starts_in_minutes=60)]
        sends = await self._poll_with_restart(events, times=1, key="items")
        self.assertEqual(len(sends[0]), 1)
        self.assertIn("embed", sends[0][0].kwargs)

    async def test_http_error_is_logged_not_fatal(self):
        """HTTP 404 from the API must not crash the loop and must not post."""
        events = [_event_dict("404", starts_in_minutes=30)]
        sends = await self._poll_with_restart(events, times=1, status=404)
        self.assertEqual(len(sends[0]), 0)

    async def test_real_payload_fields_drive_embed(self):
        """v2 payload includes link/short_description - embed uses them."""
        now = datetime.now(timezone.utc)
        ev = _event_dict("999", starts_in_minutes=60)
        ev["link"] = "https://my.vatsim.net/events/sample"
        ev["short_description"] = "Join us tonight!"
        sends = await self._poll_with_restart([ev], times=1)
        embed = sends[0][0].kwargs.get("embed")
        self.assertIsNotNone(embed)
        self.assertEqual(embed.url, "https://my.vatsim.net/events/sample")
        self.assertIn("Join us tonight!", embed.description)


    async def test_ongoing_event_is_skipped_entirely(self):
        """Started-but-not-ended event is skipped: no announcement, no reminder."""
        # Event started 45 minutes ago, ends in 75 minutes.
        ev = _event_dict_range("111", starts_in_minutes=-45, ends_in_minutes=75)
        sends = await self._poll_with_restart([ev], times=2)
        self.assertEqual(len(sends[0]), 0)
        self.assertEqual(len(sends[1]), 0)

        db = await get_db()
        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM vatsim_events WHERE event_id='111'"
        )
        row = await cur.fetchone()
        self.assertEqual(row["n"], 0)


    async def test_far_future_event_tracked_but_not_announced(self):
        """90 minutes out (outside the 60-min window): tracked, no post yet."""
        events = [_event_dict("555", starts_in_minutes=90)]
        sends = await self._poll_with_restart(events, times=1)
        self.assertEqual(len(sends[0]), 0)

        db = await get_db()
        cur = await db.execute(
            "SELECT posted, reminded FROM vatsim_events WHERE event_id='555'"
        )
        row = await cur.fetchone()
        self.assertEqual(row["posted"], 0)
        self.assertEqual(row["reminded"], 0)

    async def test_announce_window_boundary(self):
        """A 90-min event announces once the announce window is widened."""
        events = [_event_dict("666", starts_in_minutes=90)]
        # Default 60-min window: nothing posted.
        sends = await self._poll_with_restart(events, times=1)
        self.assertEqual(len(sends[0]), 0)

        # Widen the window past 90 min: the same event now announces.
        with mock.patch("bot.cogs.vatsim_events.ANNOUNCE_MINUTES_DEFAULT", 120):
            sends = await self._poll_with_restart(events, times=1)
        self.assertEqual(len(sends[0]), 1)
        self.assertIn("embed", sends[0][0].kwargs)

    async def test_banner_image_is_embedded(self):
        """Announcement embed uses the event banner image."""
        ev = _event_dict("777", starts_in_minutes=30)
        ev["banner"] = "https://vatsim.example/banners/test.png"
        sends = await self._poll_with_restart([ev], times=1)
        self.assertEqual(len(sends[0]), 1)
        embed = sends[0][0].kwargs.get("embed")
        self.assertIsNotNone(embed)
        self.assertEqual(embed.image.url, "https://vatsim.example/banners/test.png")

    async def test_airports_route_line_renders(self):
        """Announcement description includes the event's airport route line."""
        ev = _event_dict("778", starts_in_minutes=30)
        ev["airports"] = [{"icao": "unnt"}, {"icao": "urww"}]
        sends = await self._poll_with_restart([ev], times=1)
        embed = sends[0][0].kwargs.get("embed")
        self.assertIsNotNone(embed)
        self.assertIn("UNNT » URWW", embed.description)


if __name__ == "__main__":
    unittest.main()
