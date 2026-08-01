"""
Deterministic tests for the local fallback route engine.

Covers the required scenarios:
  * A320 2h
  * A320 8h
  * B777 8h
  * ATR72 1h
  * fixed origin
  * fixed destination
  * fixed origin and destination
  * invalid ICAO
  * unsupported aircraft
  * impossible duration
"""

from __future__ import annotations

import asyncio
import os
import random
import sys
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GUILD_ID", "1")
os.environ.setdefault("OWNER_USER_ID", "2")
os.environ.setdefault("ARRIVALS_CHANNEL_ID", "3")
os.environ["WHERE2FLY_ENABLED"] = "false"
os.environ["WHERE2FLY_API_TOKEN"] = ""

from bot.services.routes.fallback import FallbackProvider  # noqa: E402
from bot.services.routes.models import (  # noqa: E402
    InvalidAircraft,
    InvalidICAO,
    NoRouteFound,
)


async def _gen(aircraft, duration, origin=None, dest=None, seed: int = 42):
    random.seed(seed)
    provider = FallbackProvider()
    return await provider.generate(aircraft, duration, origin, dest)


class FallbackRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_a320_2h(self):
        result = await _gen("A320", "2h")
        self.assertEqual(result.codeletter, "JM")
        self.assertEqual(result.route_source, "Local fallback database")
        # estimated time must be near the 2h request (within tolerance)
        self.assertGreaterEqual(result.estimated_hours, 1.0)
        self.assertLessEqual(result.estimated_hours, 3.0)
        self.assertGreater(result.distance_nm, 50)

    async def test_a320_8h_not_30min(self):
        result = await _gen("A320", "8h")
        self.assertGreaterEqual(result.estimated_hours, 5.0)
        self.assertGreater(result.distance_nm, 1000)

    async def test_b777_8h(self):
        result = await _gen("B777", "8h")
        self.assertEqual(result.codeletter, "JL")
        self.assertGreaterEqual(result.estimated_hours, 5.0)
        self.assertGreater(result.distance_nm, 1000)

    async def test_atr72_1h(self):
        result = await _gen("ATR72", "1h")
        self.assertEqual(result.codeletter, "GTP")
        self.assertLess(result.distance_nm, 1000)

    async def test_fixed_origin(self):
        result = await _gen("A320", "2h", origin="EDDF")
        self.assertEqual(result.origin, "EDDF")

    async def test_fixed_destination(self):
        result = await _gen("A320", "2h", dest="KJFK")
        self.assertEqual(result.destination, "KJFK")

    async def test_fixed_both(self):
        result = await _gen("A320", "2h", origin="EDDF", dest="EGLL")
        self.assertEqual(result.origin, "EDDF")
        self.assertEqual(result.destination, "EGLL")
        self.assertGreater(result.distance_nm, 100)

    async def test_invalid_icao(self):
        with self.assertRaises(InvalidICAO):
            await _gen("A320", "2h", origin="XX")

    async def test_unknown_airport(self):
        with self.assertRaises(InvalidICAO):
            await _gen("A320", "2h", origin="ZZZZ")

    async def test_unsupported_aircraft(self):
        with self.assertRaises(InvalidAircraft):
            await _gen("ZZZZ", "2h")

    async def test_impossible_duration_long_haul(self):
        # 24h in a B777 exceeds its ~8000 NM range at cruise speed
        # (24h * ~500 kts * 0.75 routing factor >> 8000 NM).
        with self.assertRaises(NoRouteFound):
            await _gen("B777", "24h")

    async def test_deterministic_with_seed(self):
        a = await _gen("A320", "2h", seed=7)
        b = await _gen("A320", "2h", seed=7)
        self.assertEqual(a.origin, b.origin)
        self.assertEqual(a.destination, b.destination)


if __name__ == "__main__":
    unittest.main()
