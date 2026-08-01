"""
Where2Fly provider tests.

Covers:
  * aircraft normalization -> codeletter (A320, A20N, B738, B77W, A388, ...)
  * documented request contract (Bearer header, Accept header, /api/search)
  * missing token -> provider unavailable
  * timeout / rate limit / server error -> ProviderUnavailable
  * empty / invalid results -> NoRouteFound
  * orchestration falls back when Where2Fly unavailable
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GUILD_ID", "1")
os.environ.setdefault("OWNER_USER_ID", "2")
os.environ.setdefault("ARRIVALS_CHANNEL_ID", "3")
os.environ["WHERE2FLY_ENABLED"] = "true"
os.environ["WHERE2FLY_API_TOKEN"] = "test-token"
os.environ["WHERE2FLY_TIMEOUT_SECONDS"] = "5"

from bot.services.routes.models import (  # noqa: E402
    InvalidAircraft,
    NoRouteFound,
    ProviderUnavailable,
    parse_duration,
    resolve_aircraft,
)
from bot.services.routes.where2fly import Where2FlyProvider, _airtime_range  # noqa: E402


class AircraftMappingTests(unittest.TestCase):
    def test_common_mappings(self):
        cases = {
            "A320": "JM", "A20N": "JM", "A321": "JM", "B737": "JM", "B738": "JM",
            "B77W": "JL", "B789": "JL", "A359": "JL", "B744": "JXL", "A388": "JXL",
            "AT72": "GTP", "CRJ9": "JS", "E190": "JS", "C172": "GA",
        }
        for inp, expected in cases.items():
            codeletter, basetype, code, name = resolve_aircraft(inp)
            self.assertEqual(codeletter, expected, f"{inp} -> {codeletter}")

    def test_family_fallbacks(self):
        self.assertEqual(resolve_aircraft("B777")[0], "JL")
        self.assertEqual(resolve_aircraft("A350")[0], "JL")
        self.assertEqual(resolve_aircraft("787")[0], "JL")
        self.assertEqual(resolve_aircraft("747")[0], "JXL")

    def test_unsupported_raises(self):
        with self.assertRaises(InvalidAircraft):
            resolve_aircraft("ZZZZ")


class DurationTests(unittest.TestCase):
    def test_formats(self):
        self.assertAlmostEqual(parse_duration("45m"), 0.75, places=2)
        self.assertAlmostEqual(parse_duration("1h"), 1.0, places=2)
        self.assertAlmostEqual(parse_duration("1h30"), 1.5, places=2)
        self.assertAlmostEqual(parse_duration("2h 30m"), 2.5, places=2)
        self.assertAlmostEqual(parse_duration("8 hours"), 8.0, places=2)

    def test_airtime_range_short(self):
        lo, hi = _airtime_range(0.5)
        self.assertLess(lo, 0.5)
        self.assertGreater(hi, 0.5)

    def test_airtime_range_long(self):
        lo, hi = _airtime_range(8.0)
        self.assertAlmostEqual(lo, 6.4, places=1)
        self.assertAlmostEqual(hi, 9.6, places=1)


class Where2FlyProviderTests(unittest.IsolatedAsyncioTestCase):
    """Construct the provider with explicit overrides — the frozen global
    config may have Where2Fly disabled when the full suite runs together.
    """

    async def asyncSetUp(self):
        self.provider = Where2FlyProvider(enabled=True, token="test-token", timeout=5)

    async def test_missing_token_unavailable(self):
        provider = Where2FlyProvider(enabled=True, token="", timeout=5)
        with self.assertRaises(ProviderUnavailable):
            await provider.generate("A320", "2h", "EDDF", "EGLL")

    async def test_bearer_and_accept_headers_sent(self):
        captured: dict = {}

        async def fake_post(url, headers, json, timeout):
            captured["headers"] = headers
            captured["json"] = json
            captured["url"] = url
            return []

        self.provider._post = fake_post
        try:
            await self.provider.generate("A320", "2h", "EDDF", "EGLL")
        except NoRouteFound:
            pass  # empty result is expected

        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-token")
        self.assertEqual(captured["headers"]["Accept"], "application/json")
        self.assertTrue(captured["url"].endswith("/api/search"))
        body = captured["json"]
        self.assertEqual(body["departure"], "EDDF")
        self.assertEqual(body["arrival"], "EGLL")
        self.assertEqual(body["codeletter"], "JM")
        self.assertIn("airtimeMin", body)
        self.assertIn("airtimeMax", body)

    async def test_rate_limit_raises_unavailable(self):
        async def fake_post(url, headers, json, timeout):
            raise ProviderUnavailable("Where2Fly rate limit exceeded (429)")

        self.provider._post = fake_post
        with self.assertRaises(ProviderUnavailable):
            await self.provider.generate("A320", "2h", "EDDF", "EGLL")

    async def test_timeout_raises_unavailable(self):
        async def fake_post(url, headers, json, timeout):
            raise asyncio.TimeoutError("timed out")

        self.provider._post = fake_post
        with self.assertRaises(ProviderUnavailable):
            await self.provider.generate("A320", "2h", "EDDF", "EGLL")

    async def test_empty_result_raises_no_route(self):
        async def fake_post(url, headers, json, timeout):
            return []

        self.provider._post = fake_post
        with self.assertRaises(NoRouteFound):
            await self.provider.generate("A320", "2h", "EDDF", "EGLL")

    async def test_invalid_result_raises_no_route(self):
        async def fake_post(url, headers, json, timeout):
            return {"weird": "shape"}

        self.provider._post = fake_post
        with self.assertRaises(NoRouteFound):
            await self.provider.generate("A320", "2h", "EDDF", "EGLL")

    async def test_valid_result_returns_route(self):
        async def fake_post(url, headers, json, timeout):
            return [
                {
                    "arrival": "EGLL",
                    "arrival_name": "Heathrow",
                    "airtime": 1.5,
                    "distance": 400,
                }
            ]

        self.provider._post = fake_post
        result = await self.provider.generate("A320", "1h30", "EDDF", "EGLL")
        self.assertEqual(result.destination, "EGLL")
        self.assertEqual(result.route_source, "Where2Fly")
        self.assertEqual(result.powered_by, "Powered by Where2Fly")
        self.assertEqual(result.powered_by_url, "https://where2fly.today")
        self.assertTrue(result.operator and result.callsign)


class OrchestrationFallbackTests(unittest.IsolatedAsyncioTestCase):
    """generate_route must use the local fallback when Where2Fly is disabled.

    Config is frozen at import time, so we patch _primary_provider directly
    instead of reloading modules (which does not re-read config).
    """

    async def test_fallback_used_when_disabled(self):
        from unittest import mock

        import bot.services.routes as routes

        with mock.patch.object(routes, "_primary_provider", return_value=None):
            result = await routes.generate_route("A320", "2h")
        self.assertEqual(result.route_source, "Local fallback database")
        self.assertGreater(result.distance_nm, 0)


if __name__ == "__main__":
    unittest.main()
