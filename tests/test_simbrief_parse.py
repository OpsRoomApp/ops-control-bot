"""
v0.25.56 -- SimBrief XML fetcher parser tests.

The fetcher returns OFP sections as TOP-LEVEL siblings of a ``params``
metadata block. The old parser read flat keys off ``params`` and produced
N/A for everything. These tests pin the corrected nested parsing.

Covers:
  * full realistic payload (general/origin/destination/aircraft/times/fuel)
  * no flight plan (fetch.fetched = 0)
  * error status raising
  * missing fields degrade to N/A / ???
"""

from __future__ import annotations

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

from bot.api import parse_simbrief_payload  # noqa: E402


def _full_payload() -> dict:
    return {
        "fetch": {"fetched": 1, "status": "success"},
        "params": {"time_generated": 1700000000, "units": "KGS"},
        "general": {
            "icao_airline": "DLH",
            "flight_number": "400",
            "callsign": "DLH400",
            "initial_altitude": "FL350",
            "air_distance": 3900,
            "gc_distance": 3850,
            "block_time": "500",
            "route": "SOBRA1F SOBRA Y180 DIK",
            "faa_aircraft": "A359",
        },
        "origin": {"icao_code": "EDDF", "iata_code": "FRA", "name": "Frankfurt Main"},
        "destination": {"icao_code": "KJFK", "iata_code": "JFK", "name": "John F Kennedy Intl"},
        "aircraft": {"icaocode": "A359", "name": "Airbus A350-900", "reg": "D-AIXL"},
        "times": {"est_time_enroute": "455", "sched_block": "500"},
        "fuel": {"plan_ramp": "62100", "plan_takeoff": "61000"},
        "files": {
            "directory": "https://www.simbrief.com/ofp/flightplans",
            "pdf": {"link": "DLH400_PDF_1234.pdf"},
        },
    }


class SimbriefParseTests(unittest.TestCase):
    def test_full_payload_fields(self):
        plan = parse_simbrief_payload(_full_payload())
        self.assertIsNotNone(plan)
        self.assertEqual(plan["callsign"], "DLH400")
        self.assertEqual(plan["aircraft"], "A359")
        self.assertEqual(plan["aircraft_name"], "Airbus A350-900")
        self.assertEqual(plan["registration"], "D-AIXL")
        self.assertEqual(plan["origin"], "EDDF")
        self.assertEqual(plan["origin_name"], "Frankfurt Main")
        self.assertEqual(plan["destination"], "KJFK")
        self.assertEqual(plan["destination_name"], "John F Kennedy Intl")
        self.assertEqual(plan["cruise_altitude"], "FL350")
        self.assertEqual(plan["distance"], "3900")
        self.assertEqual(plan["air_time"], "455")
        self.assertEqual(plan["block_time"], "500")
        self.assertEqual(plan["plan_fuel"], "62100")
        self.assertIn("DLH400_PDF_1234.pdf", plan["ofp_link"])

    def test_no_flight_plan_returns_none(self):
        payload = {"fetch": {"fetched": 0, "status": "No flight plan found"}}
        self.assertIsNone(parse_simbrief_payload(payload))

    def test_error_status_raises(self):
        payload = {"fetch": {"fetched": 1, "status": "error", "message": "Invalid user"}}
        with self.assertRaises(RuntimeError):
            parse_simbrief_payload(payload)

    def test_error_status_with_fetched_zero_returns_none(self):
        # fetched=0 means "no plan" even when status is not success.
        payload = {"fetch": {"fetched": 0, "status": "error", "message": "Invalid user"}}
        self.assertIsNone(parse_simbrief_payload(payload))

    def test_missing_fields_degrade(self):
        plan = parse_simbrief_payload({"fetch": {"status": "success"}})
        self.assertIsNotNone(plan)
        self.assertEqual(plan["origin"], "???")
        self.assertEqual(plan["destination"], "???")
        self.assertEqual(plan["aircraft"], "N/A")
        self.assertEqual(plan["route"], "N/A")
        self.assertEqual(plan["callsign"], "N/A")

    def test_callsign_built_from_airline_fltnum(self):
        payload = {"fetch": {"status": "success"}, "general": {"icao_airline": "BAW", "flight_number": "123"}}
        plan = parse_simbrief_payload(payload)
        self.assertEqual(plan["callsign"], "BAW123")

    def test_aircraft_code_from_general_type(self):
        payload = {
            "fetch": {"status": "success"},
            "general": {"icao_aircraft": "B738"},
            "origin": {"icao_code": "EIDW"},
            "destination": {"icao_code": "EGCC"},
        }
        plan = parse_simbrief_payload(payload)
        self.assertEqual(plan["aircraft"], "B738")
        self.assertEqual(plan["origin"], "EIDW")
        self.assertEqual(plan["destination"], "EGCC")

    def test_non_dict_returns_none(self):
        self.assertIsNone(parse_simbrief_payload([]))
        self.assertIsNone(parse_simbrief_payload("nope"))


if __name__ == "__main__":
    unittest.main()
