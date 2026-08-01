"""
SimBrief Options URL tests.

Covers:
  * correct endpoint (dispatch.simbrief.com/options/custom)
  * deprecated /ofp/flightplans/new is absent
  * URL encoding
  * static ID priority (user-linked > config > omit)
  * documented examples: A359/DLH/EDDF/EGLL, B738/RYR/EIDW/EGCC
"""

from __future__ import annotations

import os
import sys
import unittest
import urllib.parse
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(_SRC))
os.environ.setdefault("DISCORD_TOKEN", "test-token")
os.environ.setdefault("GUILD_ID", "1")
os.environ.setdefault("OWNER_USER_ID", "2")
os.environ.setdefault("ARRIVALS_CHANNEL_ID", "3")

from bot.services.simbrief_url import (  # noqa: E402
    build_simbrief_options_url,
    resolve_static_id,
)


def _parse(url: str) -> dict[str, str]:
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))


class SimBriefUrlTests(unittest.TestCase):
    def test_endpoint_is_dispatch_options_custom(self):
        url = build_simbrief_options_url(
            airline="DLH", fltnum="32D", orig="EDDF", dest="EGLL", basetype="A359",
        )
        self.assertTrue(url.startswith("https://dispatch.simbrief.com/options/custom"))
        self.assertNotIn("/ofp/flightplans/new", url)
        self.assertNotIn("simbrief.com/ofp", url)

    def test_example_a359_dlh(self):
        url = build_simbrief_options_url(
            airline="DLH", fltnum="32D", orig="EDDF", dest="EGLL", basetype="A359",
        )
        params = _parse(url)
        self.assertEqual(params["airline"], "DLH")
        self.assertEqual(params["fltnum"], "32D")
        self.assertEqual(params["orig"], "EDDF")
        self.assertEqual(params["dest"], "EGLL")
        self.assertEqual(params["basetype"], "A359")

    def test_example_b738_ryr(self):
        url = build_simbrief_options_url(
            airline="RYR", fltnum="8421", orig="EIDW", dest="EGCC", basetype="B738",
        )
        params = _parse(url)
        self.assertEqual(params["airline"], "RYR")
        self.assertEqual(params["basetype"], "B738")
        self.assertEqual(params["orig"], "EIDW")
        self.assertEqual(params["dest"], "EGCC")

    def test_no_static_id_omits_param(self):
        url = build_simbrief_options_url(
            airline="DLH", fltnum="001", orig="EDDF", dest="EGLL", basetype="A320",
        )
        self.assertNotIn("static_id", _parse(url))

    def test_linked_static_id_included(self):
        url = build_simbrief_options_url(
            airline="DLH", fltnum="001", orig="EDDF", dest="EGLL", basetype="A320",
            static_id="ABC123",
        )
        self.assertEqual(_parse(url)["static_id"], "ABC123")

    def test_static_id_priority(self):
        self.assertEqual(resolve_static_id("user-id", "config-id"), "user-id")
        self.assertEqual(resolve_static_id(None, "config-id"), "config-id")
        self.assertIsNone(resolve_static_id(None, None))

    def test_url_encoding(self):
        url = build_simbrief_options_url(
            airline="DLH", fltnum="32D", orig="EDDF", dest="EGLL", basetype="A359",
            callsign="Lufthansa 32D",
            route="KUMIK T108 HMM KONAN L603 MOGTI DCT",
            reg="D-AIXA",
        )
        params = _parse(url)
        self.assertEqual(params["callsign"], "Lufthansa 32D")
        self.assertEqual(params["route"], "KUMIK T108 HMM KONAN L603 MOGTI DCT")
        self.assertEqual(params["reg"], "D-AIXA")

    def test_optional_params_included(self):
        url = build_simbrief_options_url(
            airline="DLH", fltnum="001", orig="EDDF", dest="EGLL", basetype="A320",
            date="2026-08-01", altn="EHAM", targetfl="FL380",
        )
        params = _parse(url)
        self.assertEqual(params["date"], "2026-08-01")
        self.assertEqual(params["altn"], "EHAM")
        self.assertEqual(params["targetfl"], "FL380")

    def test_no_userid_param(self):
        url = build_simbrief_options_url(
            airline="DLH", fltnum="001", orig="EDDF", dest="EGLL", basetype="A320",
        )
        self.assertNotIn("userid", _parse(url))


if __name__ == "__main__":
    unittest.main()
