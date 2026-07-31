"""
OPS CONTROL - NOAA Aviation Weather Service

Fetches METAR and TAF data from aviationweather.gov.
No API key required.
"""

from __future__ import annotations

import logging
from typing import Any

from bot.api import _get_session

logger = logging.getLogger("ops_control.services.noaa_weather")

NOAA_METAR_URL = "https://aviationweather.gov/api/data/metar"
NOAA_TAF_URL = "https://aviationweather.gov/api/data/taf"


async def fetch_noaa_metar(icao: str) -> dict[str, Any] | None:
    """Fetch METAR for an ICAO airport from NOAA."""
    try:
        session = await _get_session()
        params = {"ids": icao.strip().upper(), "format": "json"}
        async with session.get(NOAA_METAR_URL, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if not data:
            return None

        raw = data[0]
        return {
            "icao": raw.get("icaoId", icao.upper()),
            "raw_text": raw.get("rawOb", raw.get("raw", "N/A")),
            "obs_time": raw.get("obsTime", "N/A"),
            "wind_dir": raw.get("wdir", "N/A"),
            "wind_speed": raw.get("wspd", "N/A"),
            "wind_gust": raw.get("wgst", "N/A"),
            "visibility": raw.get("visib", "N/A"),
            "temperature": raw.get("temp", "N/A"),
            "dewpoint": raw.get("dewp", "N/A"),
            "pressure": raw.get("altim", "N/A"),
            "flight_category": raw.get("fltcat", "N/A"),
            "clouds": _parse_noaa_clouds(raw),
            "elevation": raw.get("elev", "N/A"),
            "station_name": raw.get("name", ""),
        }

    except Exception:
        logger.exception("NOAA METAR fetch failed for %s", icao)
        raise


async def fetch_noaa_taf(icao: str) -> dict[str, Any] | None:
    """Fetch TAF for an ICAO airport from NOAA."""
    try:
        session = await _get_session()
        params = {"ids": icao.strip().upper(), "format": "json"}
        async with session.get(NOAA_TAF_URL, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if not data:
            return None

        raw = data[0]
        return {
            "icao": raw.get("icaoId", icao.upper()),
            "raw_text": raw.get("rawTAF", raw.get("raw", "N/A")),
            "issue_time": raw.get("issueTime", "N/A"),
            "valid_from": raw.get("validFrom", "N/A"),
            "valid_to": raw.get("validTo", "N/A"),
            "forecast": _parse_taf_forecast(raw),
        }

    except Exception:
        logger.exception("NOAA TAF fetch failed for %s", icao)
        raise


def _parse_noaa_clouds(raw: dict[str, Any]) -> list[dict[str, str]]:
    """Extract cloud layers from NOAA METAR data."""
    clouds = []
    for i in range(1, 5):
        cover = raw.get(f"skyc{i}", "")
        base = raw.get(f"skyl{i}", "")
        if cover:
            clouds.append({"cover": cover, "base_ft": base})
    return clouds


def _parse_taf_forecast(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract TAF forecast periods."""
    forecasts = []
    fcst = raw.get("fcsts", [])
    for period in fcst:
        forecasts.append({
            "time_from": period.get("timeFrom", "N/A"),
            "time_to": period.get("timeTo", "N/A"),
            "change": period.get("change", ""),
            "wind_dir": period.get("wdir", "N/A"),
            "wind_speed": period.get("wspd", "N/A"),
            "wind_gust": period.get("wgst", "N/A"),
            "visibility": period.get("visib", "N/A"),
            "clouds": _parse_noaa_clouds(period),
        })
    return forecasts
