"""
OPS CONTROL - Local Fallback Route Engine

Improved local route generator used whenever Where2Fly is disabled,
unconfigured, unavailable, or returns no suitable result.

Data sources (bundled, read-only):
    src/db/airports.csv  -> ICAO, type, name, latitude, longitude, country
    src/db/airlines.csv  -> Airline, IATA, ICAO, Callsign, Country, Active

Improvements over the legacy randomizer:
    * Validates ICAO codes and coordinates.
    * Prefers large/medium airports by aircraft category.
    * Uses Haversine great-circle distance.
    * Estimates target distance from cruise speed, climb/descent allowance,
      block/taxi allowance and a routing factor.
    * Matches the requested flight time within a strict tolerance —
      an 8h request never yields a 30-minute route.
    * Validates approximate aircraft range.
    * Prefers Active=Y airlines and operators whose country/region is near
      the origin (no globally-random operator assignment).
    * Clearly identifies the source: "Local fallback database".
"""

from __future__ import annotations

import csv
import logging
import math
import random
from pathlib import Path

from bot.services.routes.base import RouteProvider
from bot.services.routes.models import (
    AIRCRAFT_CATALOGUE,
    InvalidICAO,
    NoRouteFound,
    ProviderUnavailable,
    RouteResult,
    format_flight_time,
    haversine_nm,
    parse_duration,
    resolve_aircraft,
    validate_icao,
)

logger = logging.getLogger("ops_control.routes.fallback")

_SRC_DIR = Path(__file__).resolve().parents[3]  # src/
AIRPORTS_CSV = _SRC_DIR / "db" / "airports.csv"
AIRLINES_CSV = _SRC_DIR / "db" / "airlines.csv"

# Routing factor: great-circle vs actual flown distance + climb/descent.
ROUTING_FACTOR = 0.75
# Block/taxi allowance in hours added to airborne time.
BLOCK_ALLOWANCE_H = 0.20
# Climb/descent penalty: effective cruise time is reduced by this amount.
CLIMB_DESCENT_ALLOWANCE_H = 0.20

# Duration tolerance policy.
BASE_TOLERANCE = 0.20
MIN_TOLERANCE_H = 0.35          # for very short flights (>= ~21 minutes)
MAX_WIDENED_TOLERANCE = 0.35

# Prefer airport types by codeletter.
AIRPORT_TYPES_BY_LETTER: dict[str, tuple[str, ...]] = {
    "GA": ("small_airport", "medium_airport", "large_airport"),
    "GAT": ("medium_airport", "large_airport"),
    "GTP": ("medium_airport", "large_airport"),
    "JS": ("medium_airport", "large_airport"),
    "JM": ("medium_airport", "large_airport"),
    "JML": ("large_airport",),
    "JL": ("large_airport",),
    "JXL": ("large_airport",),
}

# Simple region groups for operator proximity selection.
_REGIONS: dict[str, str] = {
    "DE": "eu", "FR": "eu", "GB": "eu", "IT": "eu", "ES": "eu", "NL": "eu",
    "BE": "eu", "AT": "eu", "CH": "eu", "PT": "eu", "IE": "eu", "DK": "eu",
    "SE": "eu", "NO": "eu", "FI": "eu", "PL": "eu", "CZ": "eu", "HU": "eu",
    "GR": "eu", "RO": "eu", "BG": "eu", "HR": "eu", "RS": "eu", "UA": "eu",
    "RU": "eu", "TR": "eu", "LU": "eu", "SK": "eu", "SI": "eu", "LT": "eu",
    "LV": "eu", "EE": "eu", "IS": "eu", "MT": "eu", "CY": "eu",
    "US": "na", "CA": "na", "MX": "na",
    "BR": "sa", "AR": "sa", "CL": "sa", "CO": "sa", "PE": "sa",
    "CN": "as", "JP": "as", "KR": "as", "IN": "as", "SG": "as", "MY": "as",
    "TH": "as", "ID": "as", "PH": "as", "VN": "as", "AE": "as", "SA": "as",
    "QA": "as", "KW": "as", "BH": "as", "IL": "as", "PK": "as", "BD": "as",
    "LK": "as", "NP": "as", "HK": "as", "TW": "as",
    "AU": "oc", "NZ": "oc",
    "ZA": "af", "EG": "af", "KE": "af", "NG": "af", "MA": "af", "TN": "af",
    "DZ": "af", "GH": "af", "ET": "af", "ZW": "af", "MW": "af", "MZ": "af",
    "NA": "af", "BW": "af", "ZM": "af", "UG": "af", "RW": "af",
}

_AIRPORT_COUNTRY_ISO: dict[str, str] = {
    "DE": "Germany", "FR": "France", "GB": "United Kingdom", "IT": "Italy",
    "ES": "Spain", "NL": "Netherlands", "BE": "Belgium", "AT": "Austria",
    "CH": "Switzerland", "PT": "Portugal", "IE": "Ireland", "DK": "Denmark",
    "SE": "Sweden", "NO": "Norway", "FI": "Finland", "PL": "Poland",
    "CZ": "Czechia", "HU": "Hungary", "GR": "Greece", "RO": "Romania",
    "BG": "Bulgaria", "HR": "Croatia", "RS": "Serbia", "UA": "Ukraine",
    "TR": "Turkey", "US": "United States", "CA": "Canada", "MX": "Mexico",
    "BR": "Brazil", "AR": "Argentina", "CL": "Chile", "CO": "Colombia",
    "PE": "Peru", "CN": "China", "JP": "Japan", "KR": "South Korea",
    "IN": "India", "SG": "Singapore", "MY": "Malaysia", "TH": "Thailand",
    "ID": "Indonesia", "PH": "Philippines", "VN": "Vietnam", "AE": "United Arab Emirates",
    "SA": "Saudi Arabia", "QA": "Qatar", "KW": "Kuwait", "BH": "Bahrain",
    "IL": "Israel", "AU": "Australia", "NZ": "New Zealand", "ZA": "South Africa",
    "EG": "Egypt", "KE": "Kenya", "NG": "Nigeria", "MA": "Morocco",
    "TN": "Tunisia", "DZ": "Algeria", "GH": "Ghana", "ET": "Ethiopia",
    "IS": "Iceland", "MT": "Malta", "CY": "Cyprus", "LU": "Luxembourg",
    "SK": "Slovakia", "SI": "Slovenia", "LT": "Lithuania", "LV": "Latvia",
    "EE": "Estonia",
}

# Airline country-name -> region, derived once from the ISO table.
_COUNTRY_REGION_BY_NAME: dict[str, str] = {
    name: _REGIONS[iso]
    for iso, name in _AIRPORT_COUNTRY_ISO.items()
    if iso in _REGIONS
}


class AirportDatabase:
    """Lazy-loaded, cached airport dataset with query helpers."""

    _airports: list[dict] | None = None

    def catalogue(self, codeletter: str) -> tuple[str, list[str], tuple[int, int], int]:
        """Return (name, codes, speed range, max range nm) for a codeletter."""
        return AIRCRAFT_CATALOGUE[codeletter]

    def _load(self) -> list[dict]:
        if AirportDatabase._airports is not None:
            return AirportDatabase._airports
        airports: list[dict] = []
        try:
            with open(AIRPORTS_CSV, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    try:
                        lat = float(row.get("latitude_deg", 0))
                        lon = float(row.get("longitude_deg", 0))
                        icao = row.get("ident", "").strip().upper()
                        if not icao or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
                            continue
                        airports.append({
                            "icao": icao,
                            "type": row.get("type", "").strip(),
                            "name": row.get("name", "").strip(),
                            "lat": lat,
                            "lon": lon,
                            "country": row.get("iso_country", "").strip().upper(),
                        })
                    except (ValueError, TypeError):
                        continue
        except Exception:
            logger.exception("Failed to load airports database")
        AirportDatabase._airports = airports
        logger.info("Loaded %d airports from %s", len(airports), AIRPORTS_CSV)
        return airports

    def find(self, icao: str) -> dict | None:
        """Find an airport by ICAO."""
        icao = icao.strip().upper()
        for a in self._load():
            if a["icao"] == icao:
                return a
        return None

    def candidates(
        self,
        origin: dict,
        target_distance_nm: float,
        tolerance: float,
        codeletter: str,
        exclude_icao: str = "",
    ) -> list[dict]:
        """Return airports matching a target distance within tolerance,
        preferring suitable types for the aircraft category."""
        allowed_types = AIRPORT_TYPES_BY_LETTER.get(codeletter, ("medium_airport", "large_airport"))
        scored: list[tuple[float, dict]] = []
        for a in self._load():
            if a["icao"] == exclude_icao:
                continue
            if a["type"] not in allowed_types:
                continue
            d = haversine_nm(origin["lat"], origin["lon"], a["lat"], a["lon"])
            if d < 30:  # ignore same-airport / helipad hops
                continue
            if abs(d - target_distance_nm) <= target_distance_nm * tolerance:
                scored.append((abs(d - target_distance_nm), a))
        scored.sort(key=lambda x: x[0])
        return [a for _, a in scored[:10]]

    def nearby_candidates(
        self,
        icao: str,
        duration_hours: float,
        speed_range: tuple[int, int],
        exclude: list[str] | None = None,
        limit: int = 5,
        codeletter: str = "JM",
    ) -> list[str]:
        """Candidate destinations near an origin (used by Where2Fly pair
        orchestration when an endpoint is missing)."""
        exclude = exclude or []
        origin = self.find(icao)
        if not origin:
            return []
        avg_speed = (speed_range[0] + speed_range[1]) / 2
        target = avg_speed * duration_hours * ROUTING_FACTOR
        candidates = self.candidates(
            origin, target, 0.4, codeletter, exclude_icao=icao
        )
        return [c["icao"] for c in candidates[:limit]]

    def hub_candidates(self, limit: int = 4) -> list[str]:
        """A few large international hubs for open-ended generation."""
        hubs = ["EDDF", "EGLL", "KJFK", "LFPG", "EHAM", "WSSS", "OMDB", "YSSY"]
        loaded = {a["icao"]: a for a in self._load()}
        return [h for h in hubs if h in loaded][:limit]


class OperatorSelector:
    """Selects a plausible (active) operator for a route origin."""

    _airlines: list[dict] | None = None

    def _load(self) -> list[dict]:
        if OperatorSelector._airlines is not None:
            return OperatorSelector._airlines
        airlines: list[dict] = []
        try:
            with open(AIRLINES_CSV, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
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
        OperatorSelector._airlines = airlines
        return airlines

    def _region_of_airport(self, airport: dict | None) -> str | None:
        if not airport:
            return None
        iso = airport.get("country", "")
        region = _REGIONS.get(iso)
        if region:
            return region
        # fall back to a coarse continent guess from latitude
        lat = airport.get("lat", 0)
        if lat > 35:
            return "na" if iso == "US" or iso == "CA" else "eu"
        return None

    def select_operator(self, origin_icao: str, rng: random.Random | None = None) -> dict:
        """Pick an active operator, preferring one whose country/region is
        near the origin. Returns {name, icao, callsign, flight_number}."""
        rng = rng or random
        airlines = [a for a in self._load() if a["active"] and a["icao"] and a["icao"] != "N/A"]
        if not airlines:
            return {
                "name": "OPS ROOM Virtual",
                "icao": "OPR",
                "callsign": "OPSROOM",
                "flight_number": f"{rng.randint(100, 999)}",
            }

        from bot.services.routes.fallback import AirportDatabase
        airport = AirportDatabase().find(origin_icao) if origin_icao else None
        region = self._region_of_airport(airport)
        country_name = _AIRPORT_COUNTRY_ISO.get((airport or {}).get("country", ""), "")

        near: list[dict] = []
        for a in airlines:
            a_country = (a.get("country") or "").strip().lower()
            if not a_country:
                continue
            # country-level match first, then region-level
            if a_country == country_name.lower():
                near.append(a)
            elif region and _COUNTRY_REGION_BY_NAME.get(a["country"]) == region and a not in near:
                near.append(a)
        pool = near if near else airlines
        airline = rng.choice(pool)

        flight_number = f"{rng.randint(100, 999)}"
        return {
            "name": airline["name"],
            "icao": airline["icao"],
            "callsign": airline["callsign"] or airline["icao"],
            "flight_number": flight_number,
        }


class FallbackProvider(RouteProvider):
    """Local fallback route provider (always available, no network)."""

    name = "Local fallback database"

    async def generate(
        self,
        aircraft_input: str,
        duration_input: str,
        origin: str | None = None,
        destination: str | None = None,
        filters: dict | None = None,
    ) -> RouteResult:
        # filters (Where2Fly API params) are not applicable to the local engine.
        codeletter, basetype, canonical_code, display_name = resolve_aircraft(aircraft_input)
        duration_hours = parse_duration(duration_input)

        origin_code = validate_icao(origin) if origin else None
        dest_code = validate_icao(destination) if destination else None

        adb = AirportDatabase()
        _, _, speed_range, max_range = AIRCRAFT_CATALOGUE[codeletter]

        origin_airport = adb.find(origin_code) if origin_code else None
        dest_airport = adb.find(dest_code) if dest_code else None

        if origin_code and not origin_airport:
            raise InvalidICAO(f"Unknown origin airport: {origin_code}")
        if dest_code and not dest_airport:
            raise InvalidICAO(f"Unknown destination airport: {dest_code}")

        rng = random

        # If both endpoints fixed, verify feasibility and return.
        if origin_airport and dest_airport:
            distance = haversine_nm(origin_airport["lat"], origin_airport["lon"],
                                    dest_airport["lat"], dest_airport["lon"])
            if distance > max_range:
                raise NoRouteFound(
                    f"{display_name} range (~{max_range} NM) insufficient for "
                    f"{origin_code}->{dest_code} ({round(distance)} NM)"
                )
            return self._build_result(
                codeletter, canonical_code, display_name, duration_input,
                duration_hours, origin_airport, dest_airport, distance,
                speed_range, rng,
            )

        # Choose origin
        if not origin_airport:
            origin_airport = self._pick_origin(codeletter, adb, rng)

        # Compute target distance
        avg_speed = (speed_range[0] + speed_range[1]) / 2
        target_distance = self._target_distance(avg_speed, duration_hours)

        # Choose destination
        if not dest_airport:
            dest_airport = self._pick_destination(
                origin_airport, target_distance, duration_hours, codeletter,
                max_range, adb, rng,
            )

        if dest_airport is None:
            raise NoRouteFound(
                "No suitable destination found for the requested duration "
                "(try a shorter flight or a different aircraft)"
            )

        distance = haversine_nm(origin_airport["lat"], origin_airport["lon"],
                                dest_airport["lat"], dest_airport["lon"])
        return self._build_result(
            codeletter, canonical_code, display_name, duration_input,
            duration_hours, origin_airport, dest_airport, distance,
            speed_range, rng,
        )

    # -- internals ------------------------------------------------------

    def _target_distance(self, avg_speed: float, duration_hours: float) -> float:
        """Estimate target distance: speed * air time * routing factor,
        with climb/descent and block/taxi allowances."""
        cruise_time = max(0.1, duration_hours - CLIMB_DESCENT_ALLOWANCE_H - BLOCK_ALLOWANCE_H)
        return avg_speed * cruise_time * ROUTING_FACTOR

    def _pick_origin(self, codeletter: str, adb: AirportDatabase, rng: random.Random) -> dict:
        allowed = AIRPORT_TYPES_BY_LETTER.get(codeletter, ("large_airport",))
        pool = [a for a in adb._load() if a["type"] in allowed and a["name"]]
        if not pool:
            raise NoRouteFound("No suitable departure airports in database")
        return rng.choice(pool)

    def _pick_destination(
        self,
        origin: dict,
        target_distance: float,
        duration_hours: float,
        codeletter: str,
        max_range: float,
        adb: AirportDatabase,
        rng: random.Random,
    ) -> dict | None:
        # Never exceed aircraft range.
        if target_distance > max_range:
            raise NoRouteFound(
                f"Requested duration exceeds the aircraft range "
                f"(~{round(max_range)} NM)"
            )

        tolerance = self._tolerance(duration_hours)
        candidates = adb.candidates(origin, target_distance, tolerance, codeletter,
                                    exclude_icao=origin["icao"])
        if not candidates:
            return None
        return rng.choice(candidates[:5])

    def _tolerance(self, duration_hours: float) -> float:
        if duration_hours <= 1.0:
            return max(BASE_TOLERANCE, MIN_TOLERANCE_H / max(duration_hours, 0.1))
        return min(BASE_TOLERANCE, MAX_WIDENED_TOLERANCE)

    def _build_result(
        self,
        codeletter: str,
        canonical_code: str,
        display_name: str,
        requested_time: str,
        duration_hours: float,
        origin: dict,
        destination: dict,
        distance: float,
        speed_range: tuple[int, int],
        rng: random.Random,
    ) -> RouteResult:
        avg_speed = (speed_range[0] + speed_range[1]) / 2
        # Estimated flight time derived from actual distance.
        cruise_hours = max(0.2, distance / avg_speed / ROUTING_FACTOR + CLIMB_DESCENT_ALLOWANCE_H)
        operator = OperatorSelector().select_operator(origin["icao"], rng)

        return RouteResult(
            aircraft_code=canonical_code,
            aircraft_name=display_name,
            codeletter=codeletter,
            origin=origin["icao"],
            origin_name=origin["name"],
            destination=destination["icao"],
            destination_name=destination["name"],
            distance_nm=round(distance),
            requested_time=requested_time,
            estimated_time=format_flight_time(cruise_hours),
            estimated_hours=round(cruise_hours, 2),
            operator=operator["name"],
            operator_icao=operator["icao"],
            callsign=f"{operator['callsign']} {operator['flight_number']}".strip(),
            flight_number=f"{operator['icao']}{operator['flight_number']}",
            flight_number_digits=operator["flight_number"],
            route_source="Local fallback database",
        )
