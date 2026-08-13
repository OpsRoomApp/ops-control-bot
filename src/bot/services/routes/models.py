"""
OPS CONTROL - Route Generation Models

Shared data structures and pure helpers for the route-generation
architecture (Where2Fly provider + local fallback).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class RouteProviderError(Exception):
    """Base error for route generation."""


class ProviderUnavailable(RouteProviderError):
    """The primary provider could not be reached (timeout, rate limit, 5xx)."""


class NoRouteFound(RouteProviderError):
    """No route satisfied the requested constraints."""


class InvalidAircraft(RouteProviderError):
    """Aircraft type could not be mapped to a supported category."""


class InvalidDuration(RouteProviderError):
    """Flight duration could not be parsed."""


class InvalidICAO(RouteProviderError):
    """An ICAO code failed validation."""


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class RouteResult:
    """A generated route suggestion."""

    aircraft_code: str            # e.g. "A359"
    aircraft_name: str            # e.g. "Airbus A350-900"
    codeletter: str               # Where2Fly codeletter (GA..JXL)
    origin: str                   # ICAO
    origin_name: str
    destination: str              # ICAO
    destination_name: str
    distance_nm: float
    requested_time: str           # raw user input, e.g. "8h"
    estimated_time: str           # formatted, e.g. "7h52"
    estimated_hours: float
    operator: str                 # suggested operator (never a claim of real service)
    operator_icao: str
    callsign: str                 # suggested callsign
    flight_number: str            # e.g. "DLH482"
    route_source: str             # "Where2Fly" | "Local fallback database"
    powered_by: str | None = None  # attribution text (Where2Fly)
    powered_by_url: str | None = None
    flight_number_digits: str = ""  # numeric part for SimBrief fltnum

    def to_embed_fields(self) -> list[tuple[str, str]]:
        """Return ordered embed fields for the /randomroute result.

        The suggested operator/callsign are intentionally NOT shown: they
        are generated locally, are never a claim of a real scheduled
        service, and the pairing read as vague/confusing. They remain
        available internally for the SimBrief button prefill.
        """
        return [
            ("Aircraft", self.aircraft_name),
            ("Origin", f"{self.origin} -- {self.origin_name}"),
            ("Destination", f"{self.destination} -- {self.destination_name}"),
            ("Requested Flight Time", self.requested_time),
            ("Estimated Flight Time", self.estimated_time),
            ("Distance", f"{round(self.distance_nm)} NM"),
        ]


# ---------------------------------------------------------------------------
# Aircraft catalogue
# ---------------------------------------------------------------------------

# Where2Fly codeletters and their canonical aircraft definitions.
# Each entry: codeletter -> (display family name, list of ICAO codes, cruise kts, max range nm)
AIRCRAFT_CATALOGUE: dict[str, tuple[str, list[str], tuple[int, int], int]] = {
    "GA":  ("Light GA",            ["C172", "PA28", "C182", "P28A"],           (100, 140), 600),
    "GAT": ("Turbo GA",            ["BE36", "BE58", "C206", "C208"],           (160, 210), 1000),
    "GTP": ("Heavy Turboprop",     ["AT72", "ATR", "DH8D", "TBM9", "PC12", "B190"], (260, 330), 1800),
    "JS":  ("Regional Jet",        ["CRJ2", "CRJ9", "E145", "PC24", "E190"],   (400, 440), 2200),
    "JM":  ("Narrow Body",         ["A319", "A320", "A321", "A20N", "A21N", "B737", "B738", "B739"], (430, 470), 3400),
    "JML": ("Mid Wide Body",       ["B752", "B753", "B762", "B763"],           (460, 500), 4500),
    "JL":  ("Large Wide Body",     ["B772", "B773", "B77W", "B788", "B789", "A332", "A333", "A359", "A35K"], (480, 520), 8000),
    "JXL": ("Super Heavy",         ["B744", "B748", "A388"],                   (470, 510), 8500),
}

# Map a user-friendly ICAO/type code -> (canonical display codeletter, basetype for SimBrief)
# Normalization table: user input -> (codeletter, SimBrief basetype)
AIRCRAFT_NORMALIZATION: dict[str, tuple[str, str]] = {
    # Light GA
    "C172": ("GA", "C172"), "PA28": ("GA", "PA28"), "C182": ("GA", "C182"),
    # Turbo GA
    "BONANZA": ("GAT", "BE36"), "BARON": ("GAT", "BE58"), "CARAVAN": ("GAT", "C208"),
    # Heavy turboprop
    "AT72": ("GTP", "AT72"), "ATR": ("GTP", "AT72"), "ATR72": ("GTP", "AT72"),
    "DH8D": ("GTP", "DH8D"), "DASH8": ("GTP", "DH8D"), "Q400": ("GTP", "DH8D"),
    "TBM": ("GTP", "TBM9"), "PC12": ("GTP", "PC12"), "KINGAIR": ("GTP", "B190"),
    # Regional jet
    "CRJ": ("JS", "CRJ9"), "CRJ2": ("JS", "CRJ2"), "CRJ9": ("JS", "CRJ9"),
    "E145": ("JS", "E145"), "PC24": ("JS", "PC24"), "E190": ("JS", "E190"), "E195": ("JS", "E195"),
    # Narrow body
    "A319": ("JM", "A319"), "A320": ("JM", "A320"), "A321": ("JM", "A321"),
    "A20N": ("JM", "A20N"), "A21N": ("JM", "A21N"),
    "B737": ("JM", "B737"), "B738": ("JM", "B738"), "B739": ("JM", "B739"),
    # Mid wide body
    "B752": ("JML", "B752"), "B753": ("JML", "B753"), "B762": ("JML", "B762"), "B763": ("JML", "B763"),
    # Large wide body
    "B772": ("JL", "B772"), "B773": ("JL", "B773"), "B77W": ("JL", "B77W"),
    "B788": ("JL", "B788"), "B789": ("JL", "B789"),
    "A332": ("JL", "A332"), "A333": ("JL", "A333"), "A359": ("JL", "A359"), "A35K": ("JL", "A35K"),
    # Super heavy
    "B744": ("JXL", "B744"), "B748": ("JXL", "B748"), "A388": ("JXL", "A388"),
}

# Family fallbacks for common inputs like "A320", "B777", "A350", "787".
FAMILY_FALLBACKS: dict[str, tuple[str, str]] = {
    "A320FAMILY": ("JM", "A320"), "A320FAM": ("JM", "A320"), "A32X": ("JM", "A320"),
    "B737FAMILY": ("JM", "B738"), "B737MAX": ("JM", "B738"), "73M": ("JM", "B738"),
    "B777": ("JL", "B77W"), "777": ("JL", "B77W"),
    "B787": ("JL", "B789"), "787": ("JL", "B789"),
    "A330": ("JL", "A333"), "330": ("JL", "A333"),
    "A350": ("JL", "A359"), "350": ("JL", "A359"),
    "B747": ("JXL", "B744"), "747": ("JXL", "B744"),
    "A380": ("JXL", "A388"), "380": ("JXL", "A388"),
    "B757": ("JML", "B752"), "757": ("JML", "B752"),
    "B767": ("JML", "B763"), "767": ("JML", "B763"),
    "E-JET": ("JS", "E190"), "E170": ("JS", "E190"),
}


def resolve_aircraft(aircraft_input: str) -> tuple[str, str, str, str]:
    """Normalize a user aircraft input.

    Returns (codeletter, basetype, canonical_icao_code, display_name).
    Raises InvalidAircraft when unmappable.

    Examples:
        "A320"  -> ("JM", "A320", "A320", "Airbus A320")
        "A20N"  -> ("JM", "A20N", "A20N", "Airbus A320neo")
        "B738"  -> ("JM", "B738", "B738", "Boeing 737-800")
        "B77W"  -> ("JL", "B77W", "B77W", "Boeing 777-300ER")
        "A388"  -> ("JXL", "A388", "A388", "Airbus A380-800")
    """
    clean = aircraft_input.strip().upper().replace(" ", "").replace("-", "")
    if not clean:
        raise InvalidAircraft("No aircraft type provided")

    direct = AIRCRAFT_NORMALIZATION.get(clean)
    if direct:
        codeletter, basetype = direct
        # canonical ICAO code = basetype if in the catalogue list else first member.
        # Index [1] is the ICAO codes list (index [2] is the cruise-speed tuple -
        # using it produced an int aircraft_code that crashed URL building).
        codes = _code_details(codeletter)[1]
        canonical = basetype if basetype in codes else codes[0]
        return codeletter, basetype, canonical, _display_name(codeletter, basetype)

    family = FAMILY_FALLBACKS.get(clean)
    if family:
        codeletter, basetype = family
        return codeletter, basetype, basetype, _display_name(codeletter, basetype)

    raise InvalidAircraft(f"Unsupported aircraft type: {aircraft_input}")


def _code_details(codeletter: str) -> tuple[str, list[str], tuple[int, int], int]:
    return AIRCRAFT_CATALOGUE[codeletter]


def _display_name(codeletter: str, basetype: str) -> str:
    """Human-readable aircraft name."""
    if codeletter == "JM" and basetype.startswith("A"):
        return "Airbus A320" if basetype in ("A320",) else "Airbus A320neo" if basetype in ("A20N", "A21N") else "Airbus A320 Family"
    if codeletter == "JM" and basetype.startswith("B"):
        return "Boeing 737-800" if basetype == "B738" else "Boeing 737"
    names = {
        "C172": "Cessna 172", "PA28": "Piper PA-28 Cherokee", "C182": "Cessna 182 Skylane",
        "BE36": "Beechcraft Bonanza", "BE58": "Beechcraft Baron", "C208": "Cessna 208 Caravan",
        "AT72": "ATR 72", "DH8D": "Dash 8 Q400", "TBM9": "TBM 900", "PC12": "Pilatus PC-12",
        "B190": "King Air 1900", "CRJ9": "Bombardier CRJ900", "E145": "Embraer ERJ-145",
        "PC24": "Pilatus PC-24", "E190": "Embraer E190", "E195": "Embraer E195",
        "A319": "Airbus A319", "A320": "Airbus A320", "A321": "Airbus A321",
        "A20N": "Airbus A320neo", "A21N": "Airbus A321neo",
        "B737": "Boeing 737", "B738": "Boeing 737-800", "B739": "Boeing 737-900",
        "B752": "Boeing 757-200", "B753": "Boeing 757-300", "B762": "Boeing 767-200", "B763": "Boeing 767-300",
        "B772": "Boeing 777-200", "B773": "Boeing 777-300", "B77W": "Boeing 777-300ER",
        "B788": "Boeing 787-8", "B789": "Boeing 787-9", "A332": "Airbus A330-200",
        "A333": "Airbus A330-300", "A359": "Airbus A350-900", "A35K": "Airbus A350-1000",
        "B744": "Boeing 747-400", "B748": "Boeing 747-8", "A388": "Airbus A380-800",
    }
    return names.get(basetype, basetype)


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------


def parse_duration(raw: str) -> float:
    """Parse a duration string into decimal hours.

    Supports: '45m', '1h', '1h30', '2h 30m', '8 hours', '90' (minutes),
    '2' (hours if large).
    """
    raw = (raw or "").strip().lower()
    if not raw:
        raise InvalidDuration("No flight duration provided")

    hours = 0.0
    minutes = 0.0

    match = re.search(r"(\d+(?:\.\d+)?)\s*h", raw)
    if match:
        hours = float(match.group(1))
    match = re.search(r"(\d+(?:\.\d+)?)\s*m", raw)
    if match:
        minutes = float(match.group(1))

    # Handle "1h30" (bare minutes after hours, no 'm' suffix).
    if hours and not minutes:
        bare = re.search(r"(\d+(?:\.\d+)?)\s*h\s*(\d{1,2})(?!\d)", raw)
        if bare and bare.group(2):
            minutes = float(bare.group(2))

    if hours or minutes:
        total = hours + minutes / 60.0
    else:
        try:
            value = float(raw)
        except ValueError:
            raise InvalidDuration(f"Could not parse duration: {raw}")
        total = value / 60.0 if value <= 600 else value  # plain numbers = minutes

    if total <= 0:
        raise InvalidDuration("Duration must be positive")
    if total > 24:
        raise InvalidDuration("Duration exceeds 24 hours")
    return total


def format_flight_time(hours: float) -> str:
    """Format decimal hours as e.g. 1h35."""
    h = int(hours)
    m = int(round((hours - h) * 60))
    if m == 60:
        h += 1
        m = 0
    return f"{h}h{m:02d}"


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


ICAO_RE = re.compile(r"^[A-Z]{4}$")


def validate_icao(code: str) -> str:
    """Validate and normalize an ICAO airport code."""
    clean = (code or "").strip().upper()
    if not ICAO_RE.match(clean):
        raise InvalidICAO(f"Invalid ICAO code: {code}")
    return clean
