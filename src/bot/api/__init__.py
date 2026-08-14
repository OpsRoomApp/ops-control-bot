"""
OPS CONTROL - External API Clients

Integration layers for aviation data APIs.
Uses a shared aiohttp session for connection reuse.
All APIs are called best-effort; failures are logged but never
block bot startup or crash the process.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

logger = logging.getLogger("ops_control.api")

# ---------------------------------------------------------------------------
# Shared aiohttp session
# ---------------------------------------------------------------------------

_session: aiohttp.ClientSession | None = None
_session_lock: asyncio.Lock = asyncio.Lock()


async def _get_session() -> aiohttp.ClientSession:
    """Return the shared aiohttp session, creating it if needed."""
    global _session
    if _session is None or _session.closed:
        async with _session_lock:
            if _session is None or _session.closed:
                _session = aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=10),
                    headers={"User-Agent": "OPS-CONTROL/1.0"},
                )
    return _session


async def close_api_session() -> None:
    """Gracefully close the shared aiohttp session."""
    global _session
    if _session is not None and not _session.closed:
        await _session.close()
        _session = None
        logger.info("API session closed.")


# ---------------------------------------------------------------------------
# VATSIM Data API
# ---------------------------------------------------------------------------

VATSIM_DATA_URL = "https://data.vatsim.net/v3/vatsim-data.json"


async def fetch_vatsim_data() -> dict[str, Any]:
    """Fetch full VATSIM data feed. Returns raw JSON dict."""
    session = await _get_session()
    async with session.get(VATSIM_DATA_URL) as resp:
        resp.raise_for_status()
        return await resp.json()


async def fetch_vatsim_online_count() -> dict[str, int]:
    """Fetch current VATSIM online counts."""
    data = await fetch_vatsim_data()
    return {
        "pilots": len(data.get("pilots", [])),
        "controllers": len(data.get("controllers", [])),
        "atis": len(data.get("atis", [])),
    }


async def fetch_vatsim_flight(callsign: str) -> dict[str, Any] | None:
    """Find a specific VATSIM pilot/aircraft by callsign."""
    data = await fetch_vatsim_data()
    pilots = data.get("pilots", [])
    for pilot in pilots:
        if pilot.get("callsign", "").strip().upper() == callsign.strip().upper():
            return _pilot_summary(pilot)
    return None


async def fetch_vatsim_pilots_by_cids(cids: set[str]) -> dict[str, dict[str, Any]]:
    """Fetch VATSIM data once and index pilots by CID (string form).

    Used by the auto takeoff/landing tracker so a single feed fetch serves
    every linked user on a poll cycle.
    """
    data = await fetch_vatsim_data()
    pilots = data.get("pilots", [])
    out: dict[str, dict[str, Any]] = {}
    for pilot in pilots:
        cid = str(pilot.get("cid", "")).strip()
        if cid and cid in cids:
            out[cid] = _pilot_summary(pilot)
    return out


def _pilot_summary(pilot: dict[str, Any]) -> dict[str, Any]:
    """Normalize a raw VATSIM pilot record into the bot's flight-watch shape."""
    plan = pilot.get("flight_plan") or {}
    return {
        "callsign": pilot.get("callsign", "N/A"),
        "name": pilot.get("name", "N/A"),
        "cid": pilot.get("cid"),
        "latitude": pilot.get("latitude"),
        "longitude": pilot.get("longitude"),
        "altitude": pilot.get("altitude"),
        "groundspeed": pilot.get("groundspeed"),
        "heading": pilot.get("heading"),
        "on_ground": bool(
            pilot.get("on_ground")
            or (pilot.get("groundspeed") in (0, None) and pilot.get("altitude") in (0, None))
        ),
        "departure": plan.get("departure", "N/A"),
        "arrival": plan.get("arrival", "N/A"),
        "route": plan.get("route", "N/A"),
        "aircraft": plan.get("aircraft", "N/A"),
        "cruise_altitude": plan.get("altitude", "N/A"),
    }


async def fetch_vatsim_atis(icao: str) -> dict[str, Any] | None:
    """Fetch ATIS for an airport from VATSIM.

    The v3 data feed ATIS records do not carry an ``airport`` field - the
    ICAO is the prefix of the callsign (e.g. ``KJFK_D_ATIS``) and the text
    lives as a list under ``text_atis`` (v2 used the ``atis_message``
    string). We match on the explicit ``airport`` key when present and on
    the callsign prefix otherwise, then normalize the text list into a
    single string.
    """
    target = icao.strip().upper()
    data = await fetch_vatsim_data()
    for atis in data.get("atis", []):
        if not isinstance(atis, dict):
            continue
        airport = str(atis.get("airport") or "").strip().upper()
        callsign = str(atis.get("callsign") or "")
        if not airport and callsign:
            airport = callsign.split("_")[0].strip().upper()
        if airport != target:
            continue

        text = atis.get("text_atis") or atis.get("atis_message") or ""
        if isinstance(text, list):
            text = "\n".join(str(part) for part in text if str(part).strip())

        parts = callsign.split("_")
        atis_type = "ATIS"
        if len(parts) >= 3:
            segment = parts[-2].strip().upper()
            if segment in ("D", "DEP"):
                atis_type = "Departure ATIS"
            elif segment in ("A", "ARR"):
                atis_type = "Arrival ATIS"

        return {
            "airport": airport,
            "atis_type": atis_type,
            "atis_code": atis.get("atis_code"),
            "atis_message": str(text).strip() or None,
            "cid": atis.get("cid"),
            "name": atis.get("name"),
        }
    return None


# ---------------------------------------------------------------------------
# OpenSky Network API
# ---------------------------------------------------------------------------

OPENSKY_API_URL = "https://opensky-network.org/api/states/all"


async def fetch_opensky_states(icao24: str | None = None) -> list[dict[str, Any]]:
    """Fetch aircraft state vectors from OpenSky Network."""
    try:
        url = OPENSKY_API_URL
        if icao24:
            url = f"{OPENSKY_API_URL}?icao24={icao24}"

        session = await _get_session()
        async with session.get(url) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()

        states = data.get("states")
        if states is None:
            return []

        result: list[dict[str, Any]] = []
        for state in states[:5]:
            result.append({
                "icao24": state[0],
                "callsign": state[1],
                "origin_country": state[2],
                "baro_altitude": state[7],
                "velocity": state[9],
                "on_ground": bool(state[8]),
            })

        return result

    except Exception:
        logger.exception("OpenSky API request failed")
        raise


# ---------------------------------------------------------------------------
# SimBrief API
# ---------------------------------------------------------------------------

SIMBRIEF_API_URL = "https://www.simbrief.com/api/xml.fetcher.php"


def parse_simbrief_payload(data: dict[str, Any]) -> dict[str, Any] | None:
    """Parse the SimBrief XML fetcher JSON response into the bot's plan shape.

    The fetcher returns the OFP sections as *top-level* siblings of a small
    ``params`` metadata block:

        fetch       -> {status, fetched, message, ...}
        params      -> metadata (time_generated, request_id, sequence_id, units)
        general     -> airline, flight_number, callsign, initial_altitude,
                       air_distance, gc_distance, block_time, route, ...
        origin      -> icao_code, iata_code, name
        destination -> icao_code, iata_code, name
        aircraft    -> icaocode, name, reg
        times       -> sched_out, sched_off, est_time_enroute, ...
        fuel        -> plan_ramp, plan_takeoff, ...
        files/links -> pdf / ofp links

    Returns None when the API reports "no flight plan" for the account.
    """
    if not isinstance(data, dict):
        return None

    fetch = data.get("fetch") if isinstance(data.get("fetch"), dict) else {}
    fetch_status = str(fetch.get("status") or "").strip().lower()
    fetched = fetch.get("fetched")
    if fetch_status and fetch_status not in {"success", "ok"}:
        message = str(fetch.get("message") or fetch.get("error") or "").lower()
        if fetched == 0 or "no flight plan" in message or "does not exist" in message:
            return None
        raise RuntimeError(
            str(fetch.get("message") or fetch.get("error") or "SimBrief could not return the latest OFP")
        )

    general = data.get("general") if isinstance(data.get("general"), dict) else {}
    origin = data.get("origin") if isinstance(data.get("origin"), dict) else {}
    dest = data.get("destination") if isinstance(data.get("destination"), dict) else {}
    aircraft = data.get("aircraft") if isinstance(data.get("aircraft"), dict) else {}
    times = data.get("times") if isinstance(data.get("times"), dict) else {}
    fuel = data.get("fuel") if isinstance(data.get("fuel"), dict) else {}
    links = data.get("links") if isinstance(data.get("links"), dict) else {}
    files = data.get("files") if isinstance(data.get("files"), dict) else {}
    atc = data.get("atc") if isinstance(data.get("atc"), dict) else {}

    def _text(value: Any, default: str = "") -> str:
        if value is None:
            return default
        return str(value).strip()

    def _first(*values: Any, default: str = "") -> str:
        for value in values:
            text = _text(value)
            if text:
                return text
        return default

    airline = _first(general.get("icao_airline"), general.get("airline")).upper()
    flight_number = _first(general.get("flight_number"), general.get("fltnum")).upper()
    callsign = _first(
        atc.get("callsign"),
        general.get("atc_callsign"),
        general.get("callsign"),
    ).upper()
    if not callsign:
        callsign = f"{airline}{flight_number}".strip()

    origin_code = _first(origin.get("icao_code"), origin.get("icao"), default="???")
    dest_code = _first(dest.get("icao_code"), dest.get("icao"), default="???")
    aircraft_code = _first(
        aircraft.get("icaocode"),
        aircraft.get("icao_code"),
        general.get("icao_aircraft"),
        general.get("type"),
        default="N/A",
    ).upper()
    aircraft_name = _text(aircraft.get("name"))
    registration = _first(aircraft.get("reg"), aircraft.get("registration")).upper()

    route = _first(general.get("route"), general.get("route_ifps"), data.get("route"), default="N/A")
    cruise = _first(
        general.get("initial_altitude"),
        general.get("cruise_altitude"),
        default="N/A",
    )
    distance = _first(
        general.get("air_distance"),
        general.get("gc_distance"),
        general.get("distance"),
        default="N/A",
    )
    air_time = _first(
        times.get("est_time_enroute"),
        times.get("ete"),
        general.get("ete"),
        default="N/A",
    )
    block_time = _first(
        general.get("block_time"),
        times.get("sched_block"),
        times.get("block_time"),
        default="N/A",
    )
    plan_fuel = _first(
        fuel.get("plan_ramp"),
        fuel.get("plan_takeoff"),
        fuel.get("takeoff"),
        default="N/A",
    )

    ofp_link = ""
    pdf = files.get("pdf") if isinstance(files.get("pdf"), dict) else {}
    directory = _text(files.get("directory"))
    pdf_link = _text(_first(pdf.get("link"), pdf.get("url")))
    if pdf_link:
        ofp_link = pdf_link if "http" in pdf_link.lower() else f"{directory.rstrip('/')}/{pdf_link}"
    if not ofp_link:
        ofp_link = _first(links.get("ofp"), links.get("pdf"), data.get("ofp_link"))

    return {
        "callsign": callsign or "N/A",
        "aircraft": aircraft_code,
        "aircraft_name": aircraft_name or "N/A",
        "registration": registration or "N/A",
        "aircraft_faa": _text(general.get("faa_aircraft")) or "N/A",
        "origin": origin_code,
        "origin_name": _text(origin.get("name")) or "N/A",
        "destination": dest_code,
        "destination_name": _text(dest.get("name")) or "N/A",
        "route": route,
        "cruise_altitude": cruise,
        "distance": distance,
        "air_time": air_time,
        "plan_fuel": plan_fuel,
        "ete": air_time,
        "fuel": plan_fuel,
        "block_time": block_time,
        "loadsheet_time": _first(times.get("loadsheet_time"), general.get("loadsheet_time"), default="N/A"),
        "ofp_link": ofp_link,
    }


async def fetch_simbrief_flightplan(username: str | None = None, static_id: str | None = None) -> dict[str, Any] | None:
    """Fetch a SimBrief flight plan via the public XML fetcher API.

    No API key is required - the public XML fetcher endpoint works with the
    SimBrief pilot ID (digits) or username plus an optional static_id.

    Args:
        username: SimBrief pilot ID (digits) or username.
        static_id: Optional static ID for persistent OFP links.

    Returns:
        Parsed flight plan dict, or None if no plan found / account not
        configured. Raises on network or API failures.
    """
    params: dict[str, str] = {"json": "1"}
    if username:
        key = "userid" if username.strip().isdigit() else "username"
        params[key] = username.strip()
    if static_id:
        params["static_id"] = static_id

    try:
        session = await _get_session()
        async with session.get(SIMBRIEF_API_URL, params=params) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()
    except Exception:
        logger.exception("SimBrief API request failed")
        raise

    return parse_simbrief_payload(data) if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Aviation Weather API (aviationweather.gov - free, no key required)
# ---------------------------------------------------------------------------

METAR_API_URL = "https://aviationweather.gov/api/data/metar"


async def fetch_metar(icao: str) -> dict[str, Any] | None:
    """Fetch METAR for an ICAO airport code."""
    try:
        session = await _get_session()
        params = {"ids": icao.strip().upper(), "format": "json"}
        async with session.get(METAR_API_URL, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if not data:
            return None

        raw = data[0]
        return {
            "icao": raw.get("icaoId", icao.upper()),
            "raw_text": raw.get("rawOb", raw.get("raw", "N/A")),
            "wind": raw.get("wdir", "N/A") if raw.get("wspd") is not None else "N/A",
            "wind_dir": raw.get("wdir", "N/A"),
            "wind_speed": raw.get("wspd", "N/A"),
            "visibility": raw.get("visib", "N/A"),
            "temperature": raw.get("temp", "N/A"),
            "dewpoint": raw.get("dewp", "N/A"),
            "pressure": raw.get("altim", "N/A"),
            "clouds": _parse_clouds(raw),
            "flight_category": raw.get("fltcat", "N/A"),
            "obs_time": raw.get("obsTime", "N/A"),
        }

    except Exception:
        logger.exception("METAR API request failed for %s", icao)
        raise


def _parse_clouds(raw: dict[str, Any]) -> str:
    """Extract cloud layers from METAR data."""
    clouds = []
    for i in range(1, 5):
        cover = raw.get(f"skyc{i}", "")
        base = raw.get(f"skyl{i}", "")
        if cover:
            clouds.append(f"{cover}@{base}ft" if base else cover)
    return ", ".join(clouds) if clouds else "N/A"


# ---------------------------------------------------------------------------
# GitHub Releases API
# ---------------------------------------------------------------------------


async def fetch_github_latest_release(repo: str) -> dict[str, Any] | None:
    """Fetch the latest GitHub release for the given repo."""
    try:
        session = await _get_session()
        url = f"https://api.github.com/repos/{repo}/releases/latest"
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.json()
    except Exception:
        logger.exception("GitHub Releases API request failed for %s", repo)
        raise


async def fetch_opsroom_releases_manifest() -> dict[str, Any] | None:
    """Fetch the OPS ROOM releases manifest (update.json)."""
    from bot.config import config

    try:
        session = await _get_session()
        async with session.get(config.opsroom_releases_api) as resp:
            resp.raise_for_status()
            return await resp.json()
    except Exception:
        logger.exception("OPS ROOM releases API request failed")
        raise


async def fetch_opsroom_public_releases() -> dict[str, Any] | None:
    """Fetch the public release history from opsroom.live (website-primary).

    Returns the raw JSON body: ``{"releases": [{version, codename, channel,
    state, published_at, notes, filename, installer_filename}, ...]}``.
    """
    from bot.config import config

    try:
        session = await _get_session()
        async with session.get(config.opsroom_public_releases_api) as resp:
            resp.raise_for_status()
            return await resp.json()
    except Exception:
        logger.exception("OPS ROOM public releases API request failed")
        raise
