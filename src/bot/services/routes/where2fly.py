"""
OPS CONTROL - Where2Fly Route Provider

Primary route-generation provider following the official Where2Fly API:

    Production: https://where2fly.today/
    QA:         https://qa.where2fly.today/

Auth:     Authorization: Bearer <token>
Headers:  Accept: application/json
Endpoint: POST /api/search

Documented request contract (v0.25.58, verified against the Where2Fly
server source at github.com/blt950/where2fly):

  * The endpoint anchors on EXACTLY ONE airport per request. Sending both
    ``departure`` AND ``arrival`` is rejected with HTTP 400
    ("You cannot search for both departure and arrival at the same time").
    - user gave only an origin   -> send ``departure``
    - user gave only a destination -> send ``arrival``
    - user gave both             -> send ``departure`` + ``arrivalWhitelist``
  * Response envelope is nested: {"message":"Success","data":{
      "departure": <anchor object | suggested list>,
      "arrivals":  <suggested list | anchor object>}}
    When departure was sent, ``data.arrivals`` holds the suggested airports.
    When arrival was sent, ``data.departure`` holds the suggested airports.
  * Each suggested airport carries: name, icao, iata, continent, country,
    region, metar, taf, longestRwyFt, scores[], airtime (hours),
    distanceNm (NM), isAirforcebase, hasAirlineService, forecastSource.
  * ``destinationAirportSize`` values are: small_airport, medium_airport,
    large_airport (NOT "airport_small" etc.).
  * ``airtimeMin`` / ``airtimeMax`` are validated between 0 and 24 hours.
  * ``codeletter`` is required and must be one of: GA, GAT, GTP, JS, JM,
    JML, JL, JXL.
  * ``limit`` must be between 1 and 30.

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
import re
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

# Where2Fly airport-size preference per codeletter (verified values:
# small_airport / medium_airport / large_airport).
_AIRPORT_SIZE_BY_LETTER = {
    "GA": ["small_airport", "medium_airport", "large_airport"],
    "GAT": ["medium_airport", "large_airport"],
    "GTP": ["medium_airport", "large_airport"],
    "JS": ["medium_airport", "large_airport"],
    "JM": ["medium_airport", "large_airport"],
    "JML": ["large_airport"],
    "JL": ["large_airport"],
    "JXL": ["large_airport"],
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
        filters: dict[str, Any] | None = None,
    ) -> RouteResult:
        if not self.available:
            raise ProviderUnavailable("Where2Fly is not configured (no token)")

        codeletter, basetype, canonical_code, display_name = resolve_aircraft(aircraft_input)
        duration_hours = parse_duration(duration_input)

        origin_code = validate_icao(origin) if origin else None
        dest_code = validate_icao(destination) if destination else None

        airtime_min, airtime_max = _airtime_range(duration_hours)

        # -- Gather candidate routes from the API ------------------------
        # The API anchors on one airport per request. Build the anchor list:
        #   (anchor_icao, anchor_is_departure, arrival_whitelist)
        anchors: list[tuple[str, bool, list[str] | None]] = []
        if origin_code and dest_code:
            # Both given: anchor on departure, whitelist the requested arrival.
            anchors.append((origin_code, True, [dest_code]))
        elif origin_code:
            anchors.append((origin_code, True, None))
        elif dest_code:
            anchors.append((dest_code, False, None))
        else:
            # Neither given: probe hub departures from the local catalogue.
            from bot.services.routes.fallback import AirportDatabase

            adb = AirportDatabase()
            _, _, _, _ = adb.catalogue(codeletter)
            for hub in adb.hub_candidates(limit=4):
                anchors.append((hub, True, None))

        last_error: Exception | None = None
        widened = _BASE_TOLERANCE

        while widened <= _MAX_WIDENED_TOLERANCE:
            lo = max(0.0, duration_hours * (1 - widened))
            hi = min(24.0, duration_hours * (1 + widened))
            best: RouteResult | None = None
            for anchor, is_departure, whitelist in anchors:
                try:
                    candidates = await self._search(
                        anchor,
                        is_departure=is_departure,
                        arrival_whitelist=whitelist,
                        codeletter=codeletter,
                        airtime_min=lo,
                        airtime_max=hi,
                        filters=filters,
                    )
                except ProviderUnavailable:
                    raise
                except Exception as exc:  # malformed result, network hiccup
                    last_error = exc
                    continue

                if is_departure:
                    picked = self._pick_best(
                        candidates, anchor, "", lo, hi, duration_hours
                    )
                else:
                    picked = self._pick_best(
                        candidates, "", anchor, lo, hi, duration_hours, anchor_is_departure=False
                    )
                if picked:
                    if is_departure:
                        picked.origin = anchor
                        picked.origin_name = ""
                    else:
                        picked.destination = anchor
                        picked.destination_name = ""
                    picked.aircraft_code = canonical_code
                    picked.aircraft_name = display_name
                    picked.codeletter = codeletter
                    picked.requested_time = duration_input
                    best = picked
                    break
            if best:
                return self._finalize(best, duration_hours)
            widened += 0.05

        if last_error:
            raise last_error
        raise NoRouteFound("No suitable route found matching the requested aircraft and duration")

    # -- internal -------------------------------------------------------

    async def _search(
        self,
        anchor_icao: str,
        *,
        is_departure: bool,
        codeletter: str,
        airtime_min: float,
        airtime_max: float,
        limit: int = 10,
        arrival_whitelist: list[str] | None = None,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Call the documented POST /api/search endpoint.

        Only ONE of ``departure`` / ``arrival`` is sent (the API rejects a
        request that contains both). When both endpoints were requested by
        the user, ``arrivalWhitelist`` constrains the result instead.
        """
        url = self.base_url + "api/search"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        body: dict[str, Any] = {
            "codeletter": codeletter,
            "destinations": _NO_FILTER,
            "airtimeMin": str(int(round(max(0.0, min(airtime_min, 24.0))))),
            "airtimeMax": str(int(round(max(0.0, min(airtime_max, 24.0))))),
            "destinationAirportSize": _AIRPORT_SIZE_BY_LETTER.get(
                codeletter, ["medium_airport", "large_airport"]
            ),
            "limit": limit,
        }
        if filters:
            _apply_filters(body, filters)
        if is_departure:
            body["departure"] = anchor_icao
            if arrival_whitelist:
                body["arrivalWhitelist"] = arrival_whitelist
        else:
            body["arrival"] = anchor_icao

        try:
            data = await self._post(url, headers=headers, json=body, timeout=self.timeout)
        except ProviderUnavailable:
            raise
        except Exception as exc:
            raise ProviderUnavailable(f"Where2Fly request failed: {exc}") from exc

        return self._parse_results(data, suggested_key="arrivals" if is_departure else "departure")

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

    def _parse_results(self, data: Any, *, suggested_key: str) -> list[dict[str, Any]]:
        """Parse the verified {message, data:{departure, arrivals}} envelope.

        ``suggested_key`` is "arrivals" when the request anchored on a
        departure, or "departure" when it anchored on an arrival. Each
        suggested item is an AirportResource + SuggestedAirportResource
        (name, icao, airtime hours, distanceNm, ...).
        """
        if not isinstance(data, dict):
            return []
        payload = data.get("data")
        if not isinstance(payload, dict):
            return []
        items = payload.get(suggested_key)
        if not isinstance(items, list):
            return []

        out: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            icao = _extract_icao(item)
            if not icao:
                continue
            out.append({
                "icao": icao,
                "name": str(item.get("name") or ""),
                "airtime": _extract_airtime(item),
                "distance": _extract_distance(item),
                "raw": item,
            })
        return out

    def _pick_best(
        self,
        candidates: list[dict[str, Any]],
        origin: str,
        destination: str,
        lo: float,
        hi: float,
        duration_hours: float,
        *,
        anchor_is_departure: bool = True,
    ) -> RouteResult | None:
        """Select the candidate whose airtime best matches the request."""
        if anchor_is_departure:
            # origin is the anchor, destination comes from the candidate
            pass
        best: dict[str, Any] | None = None
        best_delta: float | None = None
        for c in candidates:
            airtime = c.get("airtime")
            if airtime is None:
                continue
            if lo <= airtime <= hi:
                delta = abs(airtime - duration_hours)
                if best_delta is None or delta < best_delta:
                    best = c
                    best_delta = delta

        if best is None and candidates:
            # Accept the closest airtime only when it is a sane result.
            best = min(
                candidates,
                key=lambda c: abs((c.get("airtime") or 0) - duration_hours),
            )
            airtime = best.get("airtime") or 0
            if airtime <= 0 or airtime < lo * 0.5 or airtime > hi * 1.5:
                return None

        if best is None:
            return None

        if anchor_is_departure:
            route_origin = origin
            route_dest = best["icao"]
            dest_name = best.get("name", "")
        else:
            route_origin = best["icao"]
            route_dest = destination
            dest_name = ""

        return RouteResult(
            aircraft_code="",
            aircraft_name="",
            codeletter="",
            origin=route_origin,
            origin_name="",
            destination=route_dest,
            destination_name=dest_name,
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
    for key in ("icao", "arrival", "icao_code", "airport", "dest", "destination", "ident", "code"):
        value = item.get(key)
        if isinstance(value, dict):
            value = value.get("icao") or value.get("code") or value.get("icao_code")
        if isinstance(value, str) and len(value.strip()) == 4:
            return value.strip().upper()
    return None


def _extract_airtime(item: dict[str, Any]) -> float | None:
    """Extract airtime in HOURS from a Where2Fly result item.

    The verified API response carries ``airtime`` in hours (the server
    computes it as distanceNm / cruiseKts + climb/descend allowance).
    """
    for key in ("airtime", "airTime", "flightTime", "flight_time", "duration", "ete", "air_time", "hours"):
        value = item.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            value = value.get("hours") or value.get("value") or value.get("total") or value.get("minutes")
        try:
            num = float(value)
        except (TypeError, ValueError):
            continue
        if key in ("minutes", "air_time"):
            return num / 60.0
        if 0.0 <= num <= 24.0:
            return num
        if 24.0 < num <= 1440.0:
            return num / 60.0  # defensive: minutes encoded as minutes
        return None
    return None


def _extract_distance(item: dict[str, Any]) -> float | None:
    """Extract distance in NM from a Where2Fly result item."""
    for key in ("distanceNm", "distance_nm", "distanceNM", "distance"):
        value = item.get(key)
        if value is None:
            continue
        if isinstance(value, dict):
            value = value.get("nm") or value.get("nauticalMiles") or value.get("value")
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


# ---------------------------------------------------------------------------
# Optional filters (parsed from the /randomroute modal "Filters" field)
# ---------------------------------------------------------------------------

# Compact token -> Where2Fly score name.
_FILTER_SCORE_TOKENS = {
    "windy": "METAR_WINDY",
    "gusts": "METAR_GUSTS",
    "crosswind": "METAR_CROSSWIND",
    "sight": "METAR_SIGHT",
    "rvr": "METAR_RVR",
    "ceiling": "METAR_CEILING",
    "foggy": "METAR_FOGGY",
    "rain": "METAR_HEAVY_RAIN",
    "snow": "METAR_HEAVY_SNOW",
    "storm": "METAR_THUNDERSTORM",
    "atc": "VATSIM_ATC",
    "event": "VATSIM_EVENT",
    "popular": "VATSIM_POPULAR",
}

# Size aliases -> canonical API values (verified from the server source).
_AIRPORT_SIZE_ALIASES = {
    "airport_small": "small_airport",
    "airport_medium": "medium_airport",
    "airport_large": "large_airport",
    "small": "small_airport",
    "medium": "medium_airport",
    "large": "large_airport",
}


def parse_filters(text: str) -> dict[str, Any]:
    """Parse the optional /randomroute filter string into API parameters.

    Compact whitespace/comma separated tokens, all optional:

      -windy            exclude windy airports        -> scores METAR_WINDY=-1
      +atc              require live VATSIM ATC       -> scores VATSIM_ATC=1
      ifr | vfr         IFR/VFR conditions            -> metcondition
      lights            require runway lights         -> destinationRunwayLights=1
      nolights          exclude lit runways           -> destinationRunwayLights=-1
      rwy>6000          minimum runway length (ft)    -> rwyLengthMin
      rwy<12000         maximum runway length (ft)    -> rwyLengthMax
      size=medium,large destination airport sizes     -> destinationAirportSize
      region=EU         continent filter              -> destinations.continents
      country=DE,NL     country codes                 -> destinations.countries
      state=US-CA       US states                     -> destinations.states
      limit=15          result limit (1-30)           -> limit

    Unknown or malformed tokens are ignored (never fatal).
    """
    filters: dict[str, Any] = {}
    if not text:
        return filters
    # Split on whitespace only; keep key=value comma lists (size=medium,large,
    # country=de,nl) intact. Comma-joined score tokens like -windy,-gusts are
    # split apart afterwards (comma followed by +/-).
    for part in re.split(r"\s+", text.strip()):
        if not part:
            continue
        for tok in re.split(r",(?=[+-])", part):
            tok = tok.strip()
            if not tok:
                continue
            low = tok.lower()
            try:
                if low.startswith("-") and len(low) > 1:
                    name = _FILTER_SCORE_TOKENS.get(low[1:], low[1:].upper())
                    filters.setdefault("scores", {})[name] = -1
                elif low.startswith("+") and len(low) > 1:
                    name = _FILTER_SCORE_TOKENS.get(low[1:], low[1:].upper())
                    filters.setdefault("scores", {})[name] = 1
                elif low in ("ifr", "vfr"):
                    filters["metcondition"] = low.upper()
                elif low == "lights":
                    filters["destinationRunwayLights"] = 1
                elif low == "nolights":
                    filters["destinationRunwayLights"] = -1
                elif low.startswith("rwy>") and low[4:].isdigit():
                    filters["rwyLengthMin"] = int(low[4:])
                elif low.startswith("rwy<") and low[4:].isdigit():
                    filters["rwyLengthMax"] = int(low[4:])
                elif low.startswith("size="):
                    sizes = [
                        _AIRPORT_SIZE_ALIASES.get(s.strip(), s.strip())
                        for s in low[5:].split(",")
                        if s.strip()
                    ]
                    if sizes:
                        filters["destinationAirportSize"] = sizes
                elif low.startswith("region="):
                    regions = [s.strip().upper() for s in low[7:].split(",") if s.strip()]
                    if regions:
                        filters.setdefault("destinations", {})["continents"] = regions
                elif low.startswith("country="):
                    countries = [s.strip().upper() for s in low[8:].split(",") if s.strip()]
                    if countries:
                        filters.setdefault("destinations", {})["countries"] = countries
                elif low.startswith("state="):
                    states = [s.strip().upper() for s in low[6:].split(",") if s.strip()]
                    if states:
                        filters.setdefault("destinations", {})["states"] = states
                elif low.startswith("limit=") and low[6:].isdigit():
                    filters["limit"] = int(low[6:])
            except (ValueError, IndexError):
                continue
    return filters


def _apply_filters(body: dict[str, Any], filters: dict[str, Any]) -> None:
    """Merge parsed filters into the request body (never overrides anchor)."""
    if filters.get("scores"):
        body["scores"] = filters["scores"]
    if filters.get("metcondition"):
        body["metcondition"] = filters["metcondition"]
    for key in ("destinationRunwayLights", "destinationAirbases"):
        if key in filters:
            body[key] = int(filters[key])
    if filters.get("destinationAirportSize"):
        body["destinationAirportSize"] = filters["destinationAirportSize"]
    for key in ("rwyLengthMin", "rwyLengthMax"):
        if key in filters:
            body[key] = int(filters[key])
    if filters.get("destinations"):
        body["destinations"] = filters["destinations"]
    if filters.get("limit"):
        body["limit"] = int(filters["limit"])
