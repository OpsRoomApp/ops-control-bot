"""
fetch_vatsim_atis regression tests.

The VATSIM v3 data feed ATIS records have NO ``airport`` field and store the
text as a list under ``text_atis`` (v2 used the ``atis_message`` string).
The ICAO is the prefix of the callsign (e.g. ``KJFK_D_ATIS``). These tests
lock the parser so /atis cannot regress into "not available".
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

from bot.api import fetch_vatsim_atis  # noqa: E402

V3_ATIS = [
    {
        "cid": 1,
        "name": "A Controller",
        "callsign": "KJFK_D_ATIS",
        "frequency": "128.700",
        "atis_code": "D",
        "text_atis": ["KJFK DEP ATIS DELTA", "RWY 31L", "CONFIRM ATIS D"],
    },
    {
        "cid": 2,
        "name": "B Controller",
        "callsign": "KJFK_A_ATIS",
        "frequency": "126.200",
        "atis_code": "E",
        "text_atis": ["KJFK ARR ATIS ECHO", "EXP ILS 31L"],
    },
    {
        "cid": 3,
        "name": "C Controller",
        "callsign": "EGLL_ATIS",
        "frequency": "120.000",
        "atis_code": "B",
        "text_atis": ["HEATHROW ATIS BRAVO"],
    },
]


class AtisFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_matches_by_callsign_prefix(self):
        with mock.patch("bot.api.fetch_vatsim_data", return_value={"atis": V3_ATIS}):
            data = await fetch_vatsim_atis("kjfk")
        self.assertIsNotNone(data)
        self.assertEqual(data["airport"], "KJFK")
        self.assertEqual(data["atis_code"], "D")
        self.assertEqual(data["atis_type"], "Departure ATIS")
        self.assertIn("KJFK DEP ATIS DELTA", data["atis_message"])
        self.assertIn("CONFIRM ATIS D", data["atis_message"])

    async def test_arrival_atis_type(self):
        with mock.patch("bot.api.fetch_vatsim_data", return_value={"atis": [V3_ATIS[1]]}):
            data = await fetch_vatsim_atis("KJFK")
        self.assertIsNotNone(data)
        self.assertEqual(data["atis_type"], "Arrival ATIS")
        self.assertEqual(data["atis_message"], "KJFK ARR ATIS ECHO\nEXP ILS 31L")

    async def test_plain_callsign_has_generic_type(self):
        with mock.patch("bot.api.fetch_vatsim_data", return_value={"atis": [V3_ATIS[2]]}):
            data = await fetch_vatsim_atis("EGLL")
        self.assertIsNotNone(data)
        self.assertEqual(data["atis_type"], "ATIS")
        self.assertEqual(data["atis_message"], "HEATHROW ATIS BRAVO")

    async def test_unknown_airport_returns_none(self):
        with mock.patch("bot.api.fetch_vatsim_data", return_value={"atis": V3_ATIS}):
            data = await fetch_vatsim_atis("XXXX")
        self.assertIsNone(data)

    async def test_v2_airport_key_and_message_compat(self):
        v2 = [{"airport": "KLAX", "atis_code": "A", "atis_message": "LAX ATIS ALPHA"}]
        with mock.patch("bot.api.fetch_vatsim_data", return_value={"atis": v2}):
            data = await fetch_vatsim_atis("KLAX")
        self.assertIsNotNone(data)
        self.assertEqual(data["airport"], "KLAX")
        self.assertEqual(data["atis_message"], "LAX ATIS ALPHA")

    async def test_empty_text_is_none(self):
        with mock.patch(
            "bot.api.fetch_vatsim_data",
            return_value={"atis": [{"callsign": "KJFK_ATIS", "text_atis": []}]},
        ):
            data = await fetch_vatsim_atis("KJFK")
        self.assertIsNotNone(data)
        self.assertIsNone(data["atis_message"])


if __name__ == "__main__":
    unittest.main()
