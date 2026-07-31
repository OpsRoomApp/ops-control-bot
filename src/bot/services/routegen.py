"""
OPS CONTROL - Random Route Generator Service

Generates realistic random aviation routes using:
- src/db/airports.csv (ICAO, type, name, lat, lon, country)
- src/db/airlines.csv (Airline, IATA, ICAO, Callsign, Country, Active)

Logic:
- Aircraft category determines cruise speed and distance.
- distance = speed * flight_time * 0.75 (accounts for climb/descent/routing).
- Origin/destination airports matched against the airport database.
- Active airlines (Active=Y) preferred for flight number generation.
"""

from __future__ import annotations

import csv
import logging
import math
import random
import re
from pathlib import Path

logger = logging.getLogger("ops_control.services.routegen")

# ---------------------------------------------------------------------------
# Paths (Docker-safe: resolved relative to src/, never __file__-dependent CWD)
# ---------------------------------------------------------------------------

_SRC_DIR = Path(__file__).resolve().parents[2]  # src/
AIRPORTS_CSV = _SRC_DIR / "db" / "airports.csv"
AIRLINES_CSV = _SRC_DIR / "db" / "airlines.csv"

# ---------------------------------------------------------------------------
# Aircraft definitions
# ---------------------------------------------------------------------------

# category -> (aircraft_codes, display name, speed range kts)
AIRCRAFT_CATEGORIES: dict[str, tuple[list[str], str, tuple[int, int]]] = {
    "short": (
        ["AT72", "ATR", "DH8D", "E190", "CRJ9", "E195"],
        "Regional Jet / Turboprop",
        (400, 450),
    ),
    "medium": (
        ["A319", "A320", "A321", "B737", "B738", "B739"],
        "Narrow-body Airliner",
        (430, 470),
    ),
    "long": (
        ["B772", "B773", "B77W", "B788", "B789", "A332", "A333", "A359"],
        "Wide-body Long-haul",
        (480, 520),
    ),
}

AIRCRAFT_TYPE_NAMES = {
    "AT72": "ATR 72",
    "ATR": "ATR 42",
    "DH8D": "Dash 8 Q400",
    "E190": "Embraer E190",
    "E195": "Embraer E195",
    "CRJ9": "Bombardier CRJ900",
    "A319": "Airbus A319",
    "A320": "Airbus A320",
    "A321": "Airbus A321",
    "B737": "Boeing 737",
    "B738": "Boeing 737-800",
    "B739": "Boeing 737-900",
    "B772": "Boeing 777-200",
    "B773": "Boeing 777-300",
    "B77W": "Boeing 777-300ER",
    "B788": "Boeing 787-8",
    "B789": "Boeing 787-9",
    "A332": "Airbus A330-200",
    "A333": "Airbus A330-300",
    "A359": "Airbus A350-900",
}

KNOWN_ICAO_TYPES = set(AIRCRAFT_TYPE_NAMES.keys())

# ---------------------------------------------------------------------------
# Data loading (lazy, cached)
# ---------------------------------------------------------------------------

_airports: list[dict] | None = None
_airlines: list[dict] | None = None


def _load_airports() -> list[dict]:
    """Load airports from CSV (cached)."""
    global _airports
    if _airports is not None:
        return _airports

    airports: list[dict] = []
    try:
        with open(AIRPORTS_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    airports.append({
                        "icao": row.get("ident", "").strip().upper(),
                        "type": row.get("type", "").strip(),
                        "name": row.get("name", "").strip(),
                        "lat": float(row.get("latitude_deg", 0)),
                        "lon": float(row.get("longitude_deg", 0)),
                        "country": row.get("iso_country", "").strip(),
                    })
                except (ValueError, TypeError):
                    continue
    except Exception:
        logger.exception("Failed to load airports database")

    _airports = airports
    logger.info("Loaded %d airports from %s", len(airports), AIRPORTS_CSV)
    return airports


def _load_airlines() -> list[dict]:
    """Load airlines from CSV (cached)."""
    global _airlines
    if _airlines is not None:
        return _airlines

    airlines: list[dict] = []
    try:
        with open(AIRLINES_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                airlines.append({
                    "name": row.get("Name", "").strip(),
                    "iata": row.get("IATA", "").strip(),
                    "icao": row.get("ICAO", "").strip(),
                    "callsign": row.get("Callsign", "").strip(),
                    "country": row.get("Country", "").strip(),
                    "active": row.get("Active", "").strip().upper() == "Y",
                })
    except Exception:
        logger.exception("Failed to load airlines database")

    _airlines = airlines
    logger.info("Loaded %d airlines from %s", len(airlines), AIRLINES_CSV)
    return airlines


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def haversine_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    r_earth_nm = 3440.065
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    )
    return 2 * r_earth_nm * math.asin(math.sqrt(a))


def format_flight_time(hours: float) -> str:
    """Format decimal hours as e.g. 1h35."""
    h = int(hours)
    m = int(round((hours - h) * 60))
    if m == 60:
        h += 1
        m = 0
    return f"{h}h{m:02d}"


def parse_duration(raw: str) -> float:
    """Parse duration strings like '45m', '1h30', '2 hours', '8h', '90' into decimal hours."""
    raw = raw.strip().lower()
    hours = 0.0
    minutes = 0.0

    # Try "XhYm" pattern
    match = re.search(r"(\d+(?:\.\d+)?)\s*h", raw)
    if match:
        hours = float(match.group(1))
    match = re.search(r"(\d+(?:\.\d+)?)\s*m", raw)
    if match:
        minutes = float(match.group(1))

    if hours or minutes:
        return hours + minutes / 60.0

    # Plain number -> assume minutes if small, hours if large
    try:
        value = float(raw)
    except ValueError:
        return 2.0  # default 2h

    if value <= 600:
        return value / 60.0
    return value


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------


def resolve_aircraft(aircraft_input: str) -> tuple[str, str, str] | None:
    """Map user input to (icao_type, display_name, category).

    Examples: 'A320' -> ('A320', 'Airbus A320', 'medium'), 'B738' -> ...
    """
    clean = aircraft_input.strip().upper().replace(" ", "")

    # Direct known ICAO type match
    for code in KNOWN_ICAO_TYPES:
        if clean == code:
            category = "short" if code in AIRCRAFT_CATEGORIES["short"][0] else (
                "medium" if code in AIRCRAFT_CATEGORIES["medium"][0] else "long"
            )
            return code, AIRCRAFT_TYPE_NAMES[code], category

    # Family-level matching
    if clean in ("A32X", "A320FAMILY", "A320FAM") or (clean.startswith("A320") and len(clean) <= 6):
        return "A320", "Airbus A320", "medium"
    if clean in ("B737", "73"):
        return "B738", "Boeing 737-800", "medium"
    if clean in ("B777", "777"):
        return "B77W", "Boeing 777-300ER", "long"
    if clean in ("B787", "787"):
        return "B789", "Boeing 787-9", "long"
    if clean in ("A330", "330"):
        return "A333", "Airbus A330-300", "long"
    if clean in ("A350", "350"):
        return "A359", "Airbus A350-900", "long"

    return None


def generate_route(
    aircraft_input: str,
    duration_input: str,
    origin_input: str | None = None,
    dest_input: str | None = None,
) -> dict | None:
    """Generate a realistic random route.

    Returns a dict with aircraft, flight, route, distance, time, airline,
    callsign, plus lat/lon for both ends, or None if generation fails.
    """
    aircraft = resolve_aircraft(aircraft_input)
    if not aircraft:
        return None

    icao_type, display_name, category = aircraft
    airports = _load_airports()
    airlines = _load_airlines()

    if not airports:
        return None

    # Choose origin / destination
    def _find_airport(code: str) -> dict | None:
        code = code.strip().upper()
        for a in airports:
            if a["icao"] == code:
                return a
        return None

    origin = _find_airport(origin_input) if origin_input else None
    if not origin:
        origin = random.choice(airports)

    dest = None
    if dest_input:
        dest = _find_airport(dest_input)
    if not dest:
        # Pick a destination a reasonable distance away
        speed_lo, speed_hi = AIRCRAFT_CATEGORIES[category][2]
        flight_hours = parse_duration(duration_input)
        speed = random.randint(speed_lo, speed_hi)
        target_distance = speed * flight_hours * 0.75

        best = None
        best_delta = float("inf")
        for a in airports:
            if a["icao"] == origin["icao"]:
                continue
            d = haversine_nm(origin["lat"], origin["lon"], a["lat"], a["lon"])
            delta = abs(d - target_distance)
            if delta < best_delta:
                best_delta = delta
                best = a
        dest = best or random.choice(airports)

    # Flight time and distance
    actual_distance = haversine_nm(origin["lat"], origin["lon"], dest["lat"], dest["lon"])
    speed_lo, speed_hi = AIRCRAFT_CATEGORIES[category][2]
    speed = random.randint(speed_lo, speed_hi)

    # Distance used for display: the great-circle distance (realistic),
    # time derived from the 0.75 routing factor formula.
    flight_hours = max(0.5, (actual_distance / speed) / 0.75)
    flight_time = format_flight_time(flight_hours)

    # Airline selection (prefer active)
    if airlines:
        active = [a for a in airlines if a["active"] and a["icao"]]
        pool = active if active else airlines
        airline = random.choice(pool)
    else:
        airline = {"name": "OPS ROOM Virtual", "icao": "OPR", "callsign": "OPSROOM", "iata": ""}

    flight_num = random.randint(100, 999)
    callsign_digits = str(flight_num)
    flight_code = f"{airline['icao']}{flight_num}"
    callsign = f"{airline['callsign']} {callsign_digits}".strip()

    return {
        "aircraft_code": icao_type,
        "aircraft": display_name,
        "flight": flight_code,
        "origin": origin["icao"],
        "origin_name": origin["name"],
        "destination": dest["icao"],
        "destination_name": dest["name"],
        "route": f"{origin['icao']} -> {dest['icao']}",
        "distance_nm": round(actual_distance),
        "flight_time": flight_time,
        "airline": airline["name"],
        "callsign": callsign,
        "speed_kts": speed,
    }


def build_simbrief_url(
    aircraft_code: str,
    origin: str,
    dest: str,
    username: str | None = None,
    static_id: str | None = None,
) -> str:
    """Build a SimBrief 'new flight plan' link.

    If a SimBrief username is available it is included so the OFP can
    be generated for the user's account (public API, no key required).
    """
    import urllib.parse

    params = {
        "aircraft": aircraft_code,
        "origin": origin,
        "dest": dest,
    }
    if username:
        params["userid"] = username
    if static_id:
        params["static_id"] = static_id

    query = urllib.parse.urlencode(params)
    return f"https://www.simbrief.com/ofp/flightplans/new?{query}"
