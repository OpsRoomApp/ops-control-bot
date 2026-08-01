"""
OPS CONTROL - Where2Fly Route Provider

Primary route-generation provider following the official Where2Fly API:

    Production: https://where2fly.today/
    QA:         https://qa.where2fly.today/

Auth:     Authorization: Bearer <token>
Headers:  Accept: application/json
Endpoint: POST /api/search

Documented request contract (required):
    departure      string  Departure airport ICAO
    arrival        string  Arrival airport ICAO
    destinations   array   Continent/country/state filters
    codeletter     string  Aircraft type category (GA, GAT, GTP, JS, JM, JML, JL, JXL)

Documented optional filters (subset used here):
    airtimeMin, airtimeMax, destinationAirportSize, limit, and others.

Attribution:
    Where2Fly requires "Powered by Where2Fly" with a hyperlink to
    https://where2fly.today near any data provided to users. Airline /
    route data cannot be further distributed — we therefore never present
    an operator as a confirmed real-world service; operators are always
    labelled "Suggested Operator / Suggested Callsign".

The provider is optional: when WHERE2FLY_API_TOKEN is empty the bot starts
normally and the local fallback engine is used instead.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

from bot.config import config
from bot.services.routes.base import RouteProvider
from bot.services.routes.models import (
    NoRouteFound,
    ProviderUnavailable,
    RouteResult,
    format_flight_time,
    parse_duration,
    resolve_aircraft,
    validate_icao,
)

logger = logging.getLogger("ops_control.routes.where2fly")

# Default destinations filter — no geographic restriction.
_NO_FILTER: dict[str, Any] = {
    "continents": None,
    "countries": None,
    "states": None,
}

# Where2Fly airport-size preference per codeletter (medium/large for jets).
_AIRPORT_SIZE_BY_LETTER = {
    "GA": ["airport_small", "airport_medium", "airport_large"],
    "GAT": ["airport_medium", "airport_large"],
    "GTP": ["airport_medium", "airport_large"],
    "JS": ["airport_medium", "airport_large"],
    "JM": ["airport_medium", "airport_large"],
    "JML": ["airport_large"],
    "JL": ["airport_large"],
    "JXL": ["airport_large"],
}

# Duration tolerance policy (fraction of requested time).
_BASE_TOLERANCE = 0.20          # plus/minus 20%
_SHORT_FLIGHT_MIN_MINUTES = 25  # practical minimum tolerance for very short flights
_MAX_WIDENED_TOLERANCE = 0.35   # never widen beyond 35%


class Where2FlyProvider(RouteProvider):
    """Route provider backed by the Where2Fly /api/search endpoint."""

    name = "Where2Fly"

    def __init__(
        self,
        *,
        token: str | None = None,
        enabled: bool | None = None,
        base_url: str | None = None,
        timeout: int | None = None,
    ) -> None:
        """Provider configured from env by default; overridable for tests."""
        self.base_url = (base_url or config.where2fly_api_base_url).rstrip("/") + "/"
        self.token = config.where2fly_api_token if token is None else token
        self.timeout = config.where2fly_timeout_seconds if timeout is None else timeout
        self._enabled = config.where2fly_enabled if enabled is None else enabled
        # Injectable HTTP transport (used by tests to mock the API).
        self._post = Where2FlyProvider._http_post

    # -- capability ------------------------------------------------------

    @property
    def available(self) -> bool:
        """True only when enabled AND a token is configured."""
        return bool(self._enabled and self.token)

    # -- public API ------------------------------------------------------

    async def generate(
        self,
        aircraft_input: str,
        duration_input: str,
        origin: str | None = None,
        destination: str | None = None,
    ) -> RouteResult:
        if not self.available:
            raise ProviderUnavailable("Where2Fly is not configured (no token)")

        codeletter, basetype, canonical_code, display_name = resolve_aircraft(aircraft_input)
        duration_hours = parse_duration(duration_input)

        origin_code = validate_icao(origin) if origin else None
        dest_code = validate_icao(destination) if destination else None

        airtime_min, airtime_max = _airtime_range(duration_hours)

        # -- Orchestrate candidate pairs --------------------------------
        pairs = await self._candidate_pairs(
            origin_code, dest_code, duration_hours, codeletter
        )

        last_error: Exception | None = None
        widened = _BASE_TOLERANCE

        while widened <= _MAX_WIDENED_TOLERANCE:
            lo = max(0.0, duration_hours * (1 - widened))
            hi = duration_hours * (1 + widened)
            for dep, arr in pairs:
                try:
                    candidates = await self._search(dep, arr, codeletter, lo, hi)
                except ProviderUnavailable:
                    raise
                except Exception as exc:  # malformed result, network hiccup
                    last_error = exc
                    continue

                result = self._pick_best(candidates, dep, arr, lo, hi)
                if result:
                    result.aircraft_code = canonical_code
                    result.aircraft_name = display_name
                    result.codeletter = codeletter
                    result.requested_time = duration_input
                    return self._finalize(result, duration_hours)
            widened += 0.05

        if last_error:
            raise last_error
        raise NoRouteFound("No suitable route found matching the requested aircraft and duration")

    # -- internal -------------------------------------------------------

    async def _candidate_pairs(
        self,
        origin: str | None,
        destination: str | None,
        duration_hours: float,
        codeletter: str,
    ) -> list[tuple[str, str]]:
        """Build departure/arrival candidate pairs.

        The documented API requires both departure and arrival, so missing
        endpoints are filled from the local airport database before the pair
        is validated with Where2Fly.
        """
        from bot.services.routes.fallback import AirportDatabase

        adb = AirportDatabase()
        _, _, speeds, _ = adb.catalogue(codeletter)

        if origin and destination:
            return [(origin, destination)]

        if origin:
            dest_candidates = adb.nearby_candidates(
                origin, duration_hours, speeds, exclude=[origin], limit=5, codeletter=codeletter
            )
            return [(origin, d) for d in dest_candidates]

        if destination:
            origin_candidates = adb.nearby_candidates(
                destination, duration_hours, speeds, exclude=[destination], limit=5, codeletter=codeletter
            )
            return [(o, destination) for o in origin_candidates]

        # Neither endpoint given: pick sensible hub pairs, then let the
        # duration/aircraft constraints reject unsuitable combinations.
        hubs = adb.hub_candidates(limit=4)
        pairs: list[tuple[str, str]] = []
        for dep in hubs:
            for arr in adb.nearby_candidates(dep, duration_hours, speeds, exclude=[dep], limit=3):
                pairs.append((dep, arr))
        return pairs

    async def _search(
        self,
        departure: str,
        arrival: str,
        codeletter: str,
        airtime_min: float,
        airtime_max: float,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Call the documented POST /api/search endpoint."""
        url = self.base_url + "api/search"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        body: dict[str, Any] = {
            "departure": departure,
            "arrival": arrival,
            "destinations": _NO_FILTER,
            "codeletter": codeletter,
            "airtimeMin": str(int(round(airtime_min))),
            "airtimeMax": str(int(round(airtime_max))),
            "destinationAirportSize": _AIRPORT_SIZE_BY_LETTER.get(codeletter, ["airport_medium", "airport_large"]),
            "limit": limit,
        }

        try:
            data = await self._post(url, headers=headers, json=body, timeout=self.timeout)
        except ProviderUnavailable:
            raise
        except Exception as exc:
            raise ProviderUnavailable(f"Where2Fly request failed: {exc}") from exc

        return self._parse_results(data)

    @staticmethod
    async def _http_post(
        url: str,
        headers: dict[str, str],
        json: dict[str, Any],
        timeout: int,
    ) -> Any:
        """Default HTTP transport using aiohttp."""
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout)
        ) as session:
            async with session.post(url, json=json, headers=headers) as resp:
                if resp.status == 429:
                    raise ProviderUnavailable("Where2Fly rate limit exceeded (429)")
                if resp.status >= 500:
                    raise ProviderUnavailable(f"Where2Fly server error ({resp.status})")
                if resp.status != 200:
                    raise ProviderUnavailable(f"Where2Fly returned status {resp.status}")
                return await resp.json()

    def _parse_results(self, data: Any) -> list[dict[str, Any]]:
        """Tolerantly parse the Where2Fly /api/search response.

        The documented response is "structured JSON containing matched
        airport objects, calculated flight details, airtime, weather
        briefings and availability statistics". We accept a list of
        candidates or a dict containing one under a common key.
        """
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            for key in ("results", "data", "destinations", "candidates", "items", "airports"):
                if isinstance(data.get(key), list):
                    items = data[key]
                    break
            else:
                items = []
        else:
            items = []

        out: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            arrival = _extract_icao(item)
            airtime = _extract_airtime(item)
            if not arrival:
                continue
            out.append({
                "arrival": arrival,
                "airtime": airtime,
                "arrival_name": str(item.get("arrival_name") or item.get("name") or ""),
                "distance": item.get("distance"),
                "raw": item,
            })
        return out

    def _pick_best(
        self,
        candidates: list[dict[str, Any]],
        dep: str,
        arr: str,
        lo: float,
        hi: float,
    ) -> RouteResult | None:
        """Select the candidate whose airtime best matches the request."""
        best: dict[str, Any] | None = None
        best_delta: float | None = None
        for c in candidates:
            airtime = c.get("airtime")
            if airtime is None:
                continue
            if lo <= airtime <= hi:
                delta = abs(airtime - ((lo + hi) / 2))
                if best_delta is None or delta < best_delta:
                    best = c
                    best_delta = delta

        if best is None and candidates:
            # Accept the closest airtime only when it is a sane result.
            # Reject anything severely mismatched: below half the lower
            # bound or above 1.5x the upper bound. This prevents an 8h
            # request from ever returning a 30-minute route.
            best = min(
                candidates,
                key=lambda c: abs((c.get("airtime") or 0) - ((lo + hi) / 2)),
            )
            airtime = best.get("airtime") or 0
            if airtime <= 0 or airtime < lo * 0.5 or airtime > hi * 1.5:
                return None

        if best is None:
            return None

        return RouteResult(
            aircraft_code="",
            aircraft_name="",
            codeletter="",
            origin=dep,
            origin_name="",
            destination=best["arrival"],
            destination_name=best.get("arrival_name", ""),
            distance_nm=float(best.get("distance") or 0) or 0,
            requested_time="",
            estimated_time=format_flight_time(best.get("airtime") or 0),
            estimated_hours=float(best.get("airtime") or 0),
            operator="",
            operator_icao="",
            callsign="",
            flight_number="",
            route_source="Where2Fly",
        )

    def _finalize(self, result: RouteResult, duration_hours: float) -> RouteResult:
        """Attach attribution and a locally-suggested operator/callsign."""
        result.powered_by = "Powered by Where2Fly"
        result.powered_by_url = "https://where2fly.today"

        # Distance fallback: Where2Fly may return distance; if missing we
        # estimate from airtime using a typical speed for the codeletter.
        if result.distance_nm <= 0:
            from bot.services.routes.fallback import AirportDatabase
            _, _, speeds, _ = AirportDatabase().catalogue(result.codeletter)
            avg_speed = (speeds[0] + speeds[1]) / 2
            result.distance_nm = avg_speed * result.estimated_hours * 0.75

        from bot.services.routes.fallback import OperatorSelector
        operator = OperatorSelector().select_operator(result.origin)
        result.operator = operator["name"]
        result.operator_icao = operator["icao"]
        result.flight_number = f"{operator['icao']}{operator['flight_number']}"
        result.flight_number_digits = operator["flight_number"]
        result.callsign = f"{operator['callsign']} {operator['flight_number']}".strip()
        return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _airtime_range(duration_hours: float) -> tuple[float, float]:
    """Return (airtimeMin, airtimeMax) honoring practical tolerances."""
    tol = _BASE_TOLERANCE
    lo = max(0.0, duration_hours * (1 - tol))
    hi = duration_hours * (1 + tol)
    # Very short flights: practical minimum tolerance
    min_minutes = duration_hours * 60
    if min_minutes < 60:
        min_tol = max(_SHORT_FLIGHT_MIN_MINUTES, min_minutes * 0.5) / 60.0
        lo = max(0.0, min_minutes / 60.0 - min_tol)
        hi = min_minutes / 60.0 + min_tol
    return lo, hi


def _extract_icao(item: dict[str, Any]) -> str | None:
    """Extract an ICAO code from a Where2Fly result item (tolerant)."""
    for key in ("arrival", "icao", "icao_code", "airport", "dest", "destination", "ident", "code"):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("icao") or value.get("code") or value.get("icao_code")
        if isinstance(value, str) and len(value.strip()) == 4:
            return value.strip().upper()
    return None


def _extract_airtime(item: dict[str, Any]) -> float | None:
    """Extract airtime in hours from a Where2Fly result item (tolerant)."""
    for key in ("airtime", "airTime", "flightTime", "flight_time", "duration", "ete", "air_time", "minutes"):
        value = item.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            value = value.get("hours") or value.get("value") or value.get("total") or value.get("minutes")
        try:
            num = float(value)
        except (TypeError, ValueError):
            continue
        if key in ("minutes",) or (isinstance(value, (int, float)) and 5 < num < 600 and key not in ("airtime",)):
            return num / 60.0
        return num
    return None


