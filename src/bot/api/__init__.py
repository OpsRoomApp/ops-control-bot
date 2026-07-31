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
            plan = pilot.get("flight_plan") or {}
            return {
                "callsign": pilot.get("callsign", "N/A"),
                "name": pilot.get("name", "N/A"),
                "latitude": pilot.get("latitude"),
                "longitude": pilot.get("longitude"),
                "altitude": pilot.get("altitude"),
                "groundspeed": pilot.get("groundspeed"),
                "heading": pilot.get("heading"),
                "departure": plan.get("departure", "N/A"),
                "arrival": plan.get("arrival", "N/A"),
                "route": plan.get("route", "N/A"),
                "aircraft": plan.get("aircraft", "N/A"),
                "cruise_altitude": plan.get("altitude", "N/A"),
            }
    return None


async def fetch_vatsim_atis(icao: str) -> dict[str, Any] | None:
    """Fetch ATIS for an airport from VATSIM."""
    data = await fetch_vatsim_data()
    atis_list = data.get("atis", [])
    for atis in atis_list:
        if atis.get("airport", "").strip().upper() == icao.strip().upper():
            return {
                "airport": atis.get("airport"),
                "atis_code": atis.get("atis_code"),
                "atis_message": atis.get("atis_message"),
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


async def fetch_simbrief_flightplan(username: str | None = None) -> dict[str, Any] | None:
    """Fetch a SimBrief flight plan. Returns None if not found."""
    from bot.config import config

    params: dict[str, str] = {"json": "1"}
    if username:
        params["username"] = username

    headers: dict[str, str] = {}
    if config.simbrief_api_key:
        headers["X-API-Key"] = config.simbrief_api_key

    try:
        session = await _get_session()
        async with session.get(SIMBRIEF_API_URL, params=params, headers=headers) as resp:
            resp.raise_for_status()
            data: dict[str, Any] = await resp.json()

        plan = data.get("params") or data

        if not plan or plan.get("fetch_status") == "No flight plan found":
            return None

        origin = plan.get("origin", {})
        dest = plan.get("destination", {})
        return {
            "callsign": plan.get("atc_callsign", "N/A"),
            "aircraft": plan.get("icao_aircraft", "N/A"),
            "aircraft_faa": plan.get("faa_aircraft", "N/A"),
            "origin": origin.get("icao_code", "???") if isinstance(origin, dict) else "???",
            "origin_name": origin.get("name", "") if isinstance(origin, dict) else "",
            "destination": dest.get("icao_code", "???") if isinstance(dest, dict) else "???",
            "destination_name": dest.get("name", "") if isinstance(dest, dict) else "",
            "route": plan.get("route", "N/A"),
            "cruise_altitude": plan.get("initial_altitude", "N/A"),
            "distance": plan.get("distance", "N/A"),
            "air_time": plan.get("air_time", "N/A"),
            "plan_fuel": plan.get("plan_fuel", "N/A"),
            "ete": plan.get("air_time", "N/A"),
            "fuel": plan.get("plan_fuel", "N/A"),
            "block_time": plan.get("block_time", "N/A"),
            "loadsheet_time": plan.get("loadsheet_time", "N/A"),
            "ofp_link": plan.get("link", ""),
        }

    except Exception:
        logger.exception("SimBrief API request failed")
        raise


# ---------------------------------------------------------------------------
# Aviation Weather API (aviationweather.gov — free, no key required)
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
