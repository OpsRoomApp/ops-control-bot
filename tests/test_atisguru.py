"""
fetch_atisguru_atis regression tests.

ATIS.guru has no public JSON API; the client scrapes the per-airport page
and extracts the Arrival / Departure ATIS sections from the rendered text.
These tests lock the extraction so the descent-briefing ATIS fallback cannot
silently regress into "not available".
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))

os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GUILD_ID", "1")
os.environ.setdefault("OWNER_USER_ID", "2")

from bot.api.atisguru import fetch_atisguru_atis  # noqa: E402


def _page_html(body: str) -> str:
    return (
        "<html><head><title>EDDF - ATIS</title></head><body>"
        "<section><h2>EDDF</h2>"
        + body
        + "</section></body></html>"
    )


class FakeResponse:
    def __init__(self, text: str, status: int = 200):
        self._text = text
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def text(self):
        return self._text


class FakeSession:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url, **kwargs):
        # aiohttp's session.get() returns a request context manager (not a
        # coroutine), which the client enters with `async with`.
        return self._response


class AtisGuruFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_extracts_arrival_and_departure(self):
        body = (
            "Arrival ATIS 2026-08-16 10:00 UTC "
            "EDDF ARRIVAL INFORMATION ALPHA EXPECT ILS 25L "
            "Departure ATIS 2026-08-16 10:00 UTC "
            "EDDF DEPARTURE INFORMATION BRAVO EXPECT RWY 25L"
        )
        with mock.patch(
            "bot.api.atisguru._get_session",
            return_value=FakeSession(FakeResponse(_page_html(body))),
        ):
            data = await fetch_atisguru_atis("eddf")
        self.assertIsNotNone(data)
        self.assertEqual(data["airport"], "EDDF")
        self.assertEqual(data["atis_type"], "Arrival + Departure ATIS")
        self.assertIn("ARR:", data["atis_message"])
        self.assertIn("DEP:", data["atis_message"])
        self.assertIn("INFORMATION ALPHA", data["atis_message"])

    async def test_no_atis_returns_none(self):
        with mock.patch(
            "bot.api.atisguru._get_session",
            return_value=FakeSession(FakeResponse(_page_html("No ATIS available"))),
        ):
            data = await fetch_atisguru_atis("EDDF")
        self.assertIsNone(data)

    async def test_http_error_returns_none(self):
        with mock.patch(
            "bot.api.atisguru._get_session",
            return_value=FakeSession(FakeResponse("oops", status=503)),
        ):
            data = await fetch_atisguru_atis("EDDF")
        self.assertIsNone(data)

    async def test_atis_code_extracted(self):
        body = "Arrival ATIS 2026-08-16 10:00 UTC EDDF INFO C RWY 25L"
        with mock.patch(
            "bot.api.atisguru._get_session",
            return_value=FakeSession(FakeResponse(_page_html(body))),
        ):
            data = await fetch_atisguru_atis("EDDF")
        self.assertIsNotNone(data)
        self.assertEqual(data["atis_code"], "C")


if __name__ == "__main__":
    unittest.main()
