"""
Where2Fly provider tests.

Covers:
  * aircraft normalization -> codeletter (A320, A20N, B738, B77W, A388, ...)
  * documented request contract (Bearer header, Accept header, /api/search,
    single-anchor departure OR arrival + arrivalWhitelist when both given)
  * missing token -> provider unavailable
  * timeout / rate limit / server error -> ProviderUnavailable
  * empty / invalid results -> NoRouteFound
  * nested response envelope {message, data:{departure, arrivals}} parsing
  * orchestration falls back when Where2Fly unavailable
"""

from __future__ import annotations

import asyncio
import os
import re
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
from bot.services.simbrief_url import build_simbrief_options_url  # noqa: E402
from bot.services.routes.where2fly import (  # noqa: E402
    Where2FlyProvider,
    _airtime_range,
    parse_filters,
)


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
        # The API anchors on EXACTLY ONE airport. Both endpoints given ->
        # anchor on departure + arrivalWhitelist (NOT a separate arrival key).
        self.assertEqual(body["departure"], "EDDF")
        self.assertNotIn("arrival", body)
        self.assertEqual(body["arrivalWhitelist"], ["EGLL"])
        self.assertEqual(body["codeletter"], "JM")
        self.assertIn("airtimeMin", body)
        self.assertIn("airtimeMax", body)

    async def test_origin_only_sends_departure(self):
        captured: dict = {}

        async def fake_post(url, headers, json, timeout):
            captured["json"] = json
            return []

        self.provider._post = fake_post
        try:
            await self.provider.generate("A320", "2h", "EDDF", None)
        except NoRouteFound:
            pass

        self.assertEqual(captured["json"]["departure"], "EDDF")
        self.assertNotIn("arrival", captured["json"])
        self.assertNotIn("arrivalWhitelist", captured["json"])

    async def test_destination_only_sends_arrival(self):
        captured: dict = {}

        async def fake_post(url, headers, json, timeout):
            captured["json"] = json
            return []

        self.provider._post = fake_post
        try:
            await self.provider.generate("A320", "2h", None, "EGLL")
        except NoRouteFound:
            pass

        self.assertEqual(captured["json"]["arrival"], "EGLL")
        self.assertNotIn("departure", captured["json"])

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
        """Nested envelope: {message, data:{arrivals:[...]}} anchored on
        departure -> data.arrivals holds the suggested destinations."""

        async def fake_post(url, headers, json, timeout):
            return {
                "message": "Success",
                "data": {
                    "departure": {
                        "name": "Frankfurt am Main",
                        "icao": "EDDF",
                    },
                    "arrivals": [
                        {
                            "name": "Heathrow",
                            "icao": "EGLL",
                            "airtime": 1.5,
                            "distanceNm": 400,
                        }
                    ],
                },
            }

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


class OptionalFilterTests(unittest.TestCase):
    """Parsing of the optional /randomroute filter string (v0.25.58)."""

    def test_empty_and_whitespace(self):
        self.assertEqual(parse_filters(""), {})
        self.assertEqual(parse_filters("   "), {})

    def test_unknown_tokens_ignored(self):
        self.assertEqual(parse_filters("gibberish xyz=1"), {})

    def test_score_exclude_and_require(self):
        self.assertEqual(parse_filters("-windy"), {"scores": {"METAR_WINDY": -1}})
        self.assertEqual(parse_filters("+atc"), {"scores": {"VATSIM_ATC": 1}})
        self.assertEqual(
            parse_filters("-windy +atc"),
            {"scores": {"METAR_WINDY": -1, "VATSIM_ATC": 1}},
        )

    def test_metcondition(self):
        self.assertEqual(parse_filters("ifr"), {"metcondition": "IFR"})
        self.assertEqual(parse_filters("vfr"), {"metcondition": "VFR"})

    def test_runway_filters(self):
        parsed = parse_filters("lights rwy>6000 rwy<12000")
        self.assertEqual(parsed["destinationRunwayLights"], 1)
        self.assertEqual(parsed["rwyLengthMin"], 6000)
        self.assertEqual(parsed["rwyLengthMax"], 12000)

    def test_airport_size_normalized(self):
        parsed = parse_filters("size=medium,large")
        self.assertEqual(parsed["destinationAirportSize"], ["medium_airport", "large_airport"])
        # Legacy naming is normalized too.
        parsed2 = parse_filters("size=airport_small,airport_medium")
        self.assertEqual(parsed2["destinationAirportSize"], ["small_airport", "medium_airport"])

    def test_destinations_regions(self):
        parsed = parse_filters("region=EU country=de,nl state=US-CA limit=15")
        self.assertEqual(parsed["destinations"]["continents"], ["EU"])
        self.assertEqual(parsed["destinations"]["countries"], ["DE", "NL"])
        self.assertEqual(parsed["destinations"]["states"], ["US-CA"])
        self.assertEqual(parsed["limit"], 15)


class OptionalFilterBodyTests(unittest.IsolatedAsyncioTestCase):
    """Parsed filters must reach the Where2Fly request body (v0.25.58)."""

    async def asyncSetUp(self):
        self.provider = Where2FlyProvider(enabled=True, token="test-token", timeout=5)

    async def test_filters_are_sent_in_body(self):
        captured: dict = {}

        async def fake_post(url, headers, json, timeout):
            captured["json"] = json
            return []

        self.provider._post = fake_post
        filters = parse_filters("-windy +atc ifr rwy>6000 size=medium,large region=EU")
        try:
            await self.provider.generate("A320", "2h", "EDDF", "EGLL", filters=filters)
        except NoRouteFound:
            pass  # empty result is expected

        body = captured["json"]
        self.assertEqual(body["scores"], {"METAR_WINDY": -1, "VATSIM_ATC": 1})
        self.assertEqual(body["metcondition"], "IFR")
        self.assertEqual(body["rwyLengthMin"], 6000)
        self.assertEqual(body["destinationAirportSize"], ["medium_airport", "large_airport"])
        self.assertEqual(body["destinations"]["continents"], ["EU"])
        # Anchor contract is preserved alongside the filters.
        self.assertEqual(body["departure"], "EDDF")
        self.assertNotIn("arrival", body)
        self.assertEqual(body["arrivalWhitelist"], ["EGLL"])

    async def test_no_filters_keeps_default_body(self):
        captured: dict = {}

        async def fake_post(url, headers, json, timeout):
            captured["json"] = json
            return []

        self.provider._post = fake_post
        try:
            await self.provider.generate("A320", "2h", "EDDF", "EGLL")
        except NoRouteFound:
            pass

        body = captured["json"]
        self.assertNotIn("scores", body)
        self.assertNotIn("metcondition", body)
        self.assertIn("departure", body)


class AircraftCodeRegressionTests(unittest.TestCase):
    """Regression: aircraft_code must be a string ICAO code, never an int.

    resolve_aircraft previously read the catalogue at index [2] (the
    cruise-speed tuple, e.g. (430, 470)) instead of [1] (the ICAO codes
    list), so A320 -> 430 (an int). The int crashed basetype.strip() in
    build_simbrief_options_url after the interaction was deferred, killing
    the /randomroute reply silently.
    """

    def test_aircraft_code_is_string_icao(self):
        for inp in ("A320", "B738", "A359", "C172", "AT72", "B77W", "A20N"):
            codeletter, basetype, canonical, name = resolve_aircraft(inp)
            self.assertIsInstance(canonical, str, f"{inp} canonical -> {canonical!r}")
            self.assertEqual(canonical.upper(), canonical)
            self.assertTrue(1 <= len(canonical) <= 4)

    def test_known_canonical_codes(self):
        self.assertEqual(resolve_aircraft("A320")[2], "A320")
        self.assertEqual(resolve_aircraft("B738")[2], "B738")
        self.assertEqual(resolve_aircraft("A359")[2], "A359")

    def test_simbrief_url_accepts_non_str_basetype(self):
        # Defensive: even if a provider ever returns an int aircraft_code the
        # URL builder must not crash.
        url = build_simbrief_options_url(
            airline="DLH", fltnum="400", orig="EDDF", dest="EGLL", basetype=430,
        )
        self.assertIn("basetype=430", url)
        self.assertTrue(url.startswith("https://dispatch.simbrief.com/options/custom?"))


if __name__ == "__main__":
    unittest.main()



class FooterAttributionTests(unittest.TestCase):
    """The embed footer attribution must be a clickable Markdown link when the
    provider carries a powered_by_url (v0.25.58+).

    Discord embed footers render `[text](url)` as a hyperlink. The render
    block in randomroute.py builds the footer from route.powered_by /
    route.powered_by_url; this test locks that format so it cannot regress
    back to plain text.
    """

    def _footer(self, powered_by, powered_by_url, route_source="Where2Fly"):
        if powered_by:
            if powered_by_url:
                return f"[{powered_by}]({powered_by_url}) -- {route_source}"
            return f"{powered_by} -- {route_source}"
        return "Operational suggestion only \u2014 not a confirmed scheduled service."

    def test_where2fly_footer_is_hyperlink(self):
        footer = self._footer("Powered by Where2Fly", "https://where2fly.today")
        self.assertEqual(footer, "[Powered by Where2Fly](https://where2fly.today) -- Where2Fly")
        # Discord footer renders `[text](url)` as a clickable link.
        self.assertIsNotNone(re.match(r"^\[[^]]+\]\(https?://[^)]+\)", footer))

    def test_footer_without_url_stays_plain_text(self):
        footer = self._footer("Powered by Where2Fly", None)
        self.assertEqual(footer, "Powered by Where2Fly -- Where2Fly")
        self.assertNotIn("[", footer)

    def test_footer_without_powered_by_is_generic(self):
        footer = self._footer(None, None)
        self.assertIn("Operational suggestion only", footer)
        self.assertNotIn("Where2Fly", footer)
