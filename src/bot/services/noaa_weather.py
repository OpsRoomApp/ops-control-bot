"""
OPS CONTROL - NOAA Aviation Weather Service

Fetches METAR and TAF data from aviationweather.gov.
No API key required.

Primary source is the aviationweather.gov JSON API. When it is unreachable,
blocked, or returns no data (datacenter IPs, Azure bot protection, transient
outages), the service falls back to the legacy tgftp.nws.noaa.gov text mirror
on a separate host, so the bot still answers with real reports.
"""

from __future__ import annotations

import logging
from typing import Any

from bot.api import _get_session

logger = logging.getLogger("ops_control.services.noaa_weather")

NOAA_METAR_URL = "https://aviationweather.gov/api/data/metar"
NOAA_TAF_URL = "https://aviationweather.gov/api/data/taf"

# Legacy NOAA text mirror (tgftp.nws.noaa.gov). Different host with no bot
# protection - survives aviationweather.gov outages / blocks.
TGFTP_METAR_URL = "https://tgftp.nws.noaa.gov/data/observations/metar/stations/{icao}.TXT"
TGFTP_TAF_URL = "https://tgftp.nws.noaa.gov/data/forecasts/taf/stations/{icao}.TXT"


async def _fetch_tgftp_report(url: str) -> str | None:
    """Fetch a raw report from the legacy tgftp.nws.noaa.gov text mirror.

    The station files carry a generation-timestamp line followed by the
    (possibly line-wrapped) report. Returns the joined report, or None.
    """
    try:
        session = await _get_session()
        async with session.get(url) as resp:
            if resp.status != 200:
                return None
            text = (await resp.text()).strip()
    except Exception:
        logger.debug("tgftp report fetch failed for %s", url)
        return None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return None
    report = " ".join(lines[1:] if len(lines) > 1 else lines)
    report = " ".join(report.split())
    if report.startswith("TAF TAF "):
        report = "TAF " + report[len("TAF TAF "):]
    return report or None


async def fetch_noaa_metar(icao: str) -> dict[str, Any] | None:
    """Fetch METAR for an ICAO airport from NOAA.

    Primary: aviationweather.gov JSON API. Fallback: tgftp.nws.noaa.gov text
    mirror (raw report only). Returns None only when both sources fail.
    """
    icao = icao.strip().upper()
    try:
        session = await _get_session()
        params = {"ids": icao, "format": "json"}
        async with session.get(NOAA_METAR_URL, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if data:
            raw = data[0]
            return {
                "icao": raw.get("icaoId", icao),
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
                "source": "NOAA API",
            }
    except Exception:
        logger.exception("NOAA METAR API failed for %s; trying tgftp mirror", icao)

    report = await _fetch_tgftp_report(TGFTP_METAR_URL.format(icao=icao))
    if not report:
        return None
    return {
        "icao": icao,
        "raw_text": report,
        "obs_time": "N/A",
        "wind_dir": "N/A",
        "wind_speed": "N/A",
        "wind_gust": "N/A",
        "visibility": "N/A",
        "temperature": "N/A",
        "dewpoint": "N/A",
        "pressure": "N/A",
        "flight_category": "N/A",
        "clouds": [],
        "elevation": "N/A",
        "station_name": "",
        "source": "tgftp",
    }


async def fetch_noaa_taf(icao: str) -> dict[str, Any] | None:
    """Fetch TAF for an ICAO airport from NOAA (same dual-source strategy)."""
    icao = icao.strip().upper()
    try:
        session = await _get_session()
        params = {"ids": icao, "format": "json"}
        async with session.get(NOAA_TAF_URL, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        if data:
            raw = data[0]
            return {
                "icao": raw.get("icaoId", icao),
                "raw_text": raw.get("rawTAF", raw.get("raw", "N/A")),
                "issue_time": raw.get("issueTime", "N/A"),
                "valid_from": raw.get("validFrom", "N/A"),
                "valid_to": raw.get("validTo", "N/A"),
                "forecast": _parse_taf_forecast(raw),
                "source": "NOAA API",
            }
    except Exception:
        logger.exception("NOAA TAF API failed for %s; trying tgftp mirror", icao)

    report = await _fetch_tgftp_report(TGFTP_TAF_URL.format(icao=icao))
    if not report:
        return None
    return {
        "icao": icao,
        "raw_text": report,
        "issue_time": "N/A",
        "valid_from": "N/A",
        "valid_to": "N/A",
        "forecast": [],
        "source": "tgftp",
    }


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
