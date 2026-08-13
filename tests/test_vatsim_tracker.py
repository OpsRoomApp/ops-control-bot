"""
v0.25.56 -- VATSIM flight tracker tests.

Covers evaluate_tracker_state transitions:
  * takeoff on ground -> airborne
  * landing on airborne -> ground
  * landing when pilot leaves the feed after being airborne
  * no event when state is stable
  * first sighting of an airborne pilot announces takeoff (was unknown)
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

from bot.cogs.vatsim_tracker import evaluate_tracker_state  # noqa: E402


def _pilot(altitude: int = 35000, airborne: bool = True, callsign: str = "DLH400") -> dict:
    return {
        "callsign": callsign,
        "altitude": altitude,
        "on_ground": not airborne,
        "departure": "EDDF",
        "arrival": "KJFK",
        "aircraft": "A359",
        "groundspeed": 470 if airborne else 0,
    }


class TrackerStateTests(unittest.TestCase):
    def test_takeoff_from_ground(self):
        prev = {"airborne": 0, "callsign": "DLH400", "departure": "EDDF", "arrival": "KJFK", "aircraft": "A359"}
        event, state = evaluate_tracker_state(prev, _pilot(altitude=35000), "2026-08-02T12:00:00Z")
        self.assertEqual(event, "takeoff")
        self.assertEqual(state["airborne"], 1)
        self.assertEqual(state["callsign"], "DLH400")

    def test_landing_to_ground(self):
        prev = {"airborne": 1, "callsign": "DLH400", "departure": "EDDF", "arrival": "KJFK", "aircraft": "A359"}
        event, state = evaluate_tracker_state(prev, _pilot(altitude=0, airborne=False), "2026-08-02T12:00:00Z")
        self.assertEqual(event, "landing")
        self.assertEqual(state["airborne"], 0)

    def test_landing_when_pilot_leaves_feed(self):
        prev = {"airborne": 1, "callsign": "DLH400", "departure": "EDDF", "arrival": "KJFK", "aircraft": "A359"}
        event, state = evaluate_tracker_state(prev, None, "2026-08-02T12:00:00Z")
        self.assertEqual(event, "landing")
        self.assertEqual(state["airborne"], 0)

    def test_stable_airborne_no_event(self):
        prev = {"airborne": 1, "callsign": "DLH400", "departure": "EDDF", "arrival": "KJFK", "aircraft": "A359"}
        event, state = evaluate_tracker_state(prev, _pilot(altitude=36000), "2026-08-02T12:00:00Z")
        self.assertEqual(event, "none")
        self.assertEqual(state["airborne"], 1)

    def test_stable_ground_no_event(self):
        prev = {"airborne": 0, "callsign": "DLH400", "departure": "EDDF", "arrival": "KJFK", "aircraft": "A359"}
        event, state = evaluate_tracker_state(prev, _pilot(altitude=0, airborne=False), "2026-08-02T12:00:00Z")
        self.assertEqual(event, "none")
        self.assertEqual(state["airborne"], 0)

    def test_first_sighting_airborne_announces_takeoff(self):
        event, state = evaluate_tracker_state(None, _pilot(altitude=35000), "2026-08-02T12:00:00Z")
        self.assertEqual(event, "takeoff")
        self.assertEqual(state["airborne"], 1)

    def test_high_field_ground_no_false_takeoff(self):
        # Regression: EWG39KK at EDDS (~1,276 ft field). On_ground flag unset
        # during pushback, MSL altitude 1,289 ft, groundspeed ~5 kt.
        pilot = _pilot(altitude=1289, airborne=False, callsign="EWG39KK")
        pilot["on_ground"] = False  # flag momentarily unset at pushback
        pilot["groundspeed"] = 5
        event, state = evaluate_tracker_state(None, pilot, "2026-08-13T14:21:00Z")
        self.assertEqual(event, "none")
        self.assertEqual(state["airborne"], 0)

    def test_high_field_takeoff_with_ground_reference(self):
        # After the tracker saw the aircraft on the ground at EDDS, a real
        # climb above the stored field reference must still fire takeoff.
        prev = {
            "airborne": 0,
            "callsign": "EWG39KK",
            "altitude": 1289,
            "ground_ref_alt": 1289,
            "departure": "EDDS",
            "arrival": "LDZA",
            "aircraft": "A320",
        }
        event, state = evaluate_tracker_state(prev, _pilot(altitude=2000, callsign="EWG39KK"), "2026-08-13T14:25:00Z")
        self.assertEqual(event, "takeoff")
        self.assertEqual(state["airborne"], 1)

    def test_on_ground_captures_field_reference(self):
        pilot = _pilot(altitude=1289, airborne=False, callsign="EWG39KK")
        event, state = evaluate_tracker_state(None, pilot, "2026-08-13T14:00:00Z")
        self.assertEqual(event, "none")
        self.assertEqual(state["ground_ref_alt"], 1289)
        self.assertEqual(state["airborne"], 0)


if __name__ == "__main__":
    unittest.main()
