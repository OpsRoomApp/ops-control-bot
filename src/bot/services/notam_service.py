"""
OPS CONTROL - External NOTAM Service

Fetches active NOTAMs for ICAO airports.
Uses FAA NOTAM API where available; falls back gracefully.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from bot.api import _get_session
from bot.config import config

logger = logging.getLogger("ops_control.services.notam_service")

# FAA NOTAM API (NASR — National Airspace System Resource)
FAA_NOTAM_URL = "https://soa.smext.faa.gov/apra/notam"


async def fetch_notams(icao: str) -> list[dict[str, Any]]:
    """Fetch active NOTAMs for an ICAO airport code.

    Attempts FAA NOTAM API if configured; returns empty list if unavailable.
    """
    icao = icao.strip().upper()

    results: list[dict[str, Any]] = []

    # Try FAA NOTAM API
    if config.faa_notam_api_url:
        try:
            session = await _get_session()
            params = {"icao": icao, "format": "json"}
            async with session.get(
                config.faa_notam_api_url, params=params
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                for item in data if isinstance(data, list) else data.get("notams", []):
                    results.append({
                        "identifier": item.get("notam_id", item.get("id", "N/A")),
                        "effective": item.get("effective", "N/A"),
                        "expiry": item.get("expiry", "N/A"),
                        "description": item.get("text", item.get("description", "N/A")),
                        "type": item.get("type", "NOTAM"),
                        "source": "FAA",
                    })
            if results:
                return results
        except Exception:
            logger.exception("FAA NOTAM API failed for %s", icao)

    # Fallback: VATSIM NOTAM data (the existing notams table is for internal OPS notices,
    # not external aviation NOTAMs — we skip fallback for now since the spec
    # says to use official FAA NOTAM API where possible)
    logger.info("No NOTAM data source available for %s", icao)
    return results


async def fetch_sigmets() -> list[dict[str, Any]]:
    """Fetch active SIGMETs (aviation weather warnings)."""
    try:
        session = await _get_session()
        url = "https://aviationweather.gov/api/data/sigmet"
        params = {"format": "json"}
        async with session.get(url, params=params) as resp:
            resp.raise_for_status()
            data = await resp.json()

        results: list[dict[str, Any]] = []
        if isinstance(data, dict):
            items = data.get("features", [])
        else:
            items = data if isinstance(data, list) else []

        for item in items:
            props = item.get("properties", item) if isinstance(item, dict) else {}
            results.append({
                "id": props.get("id", props.get("hazardId", "N/A")),
                "type": props.get("hazard", props.get("type", "SIGMET")),
                "valid_from": props.get("validTimeFrom", "N/A"),
                "valid_to": props.get("validTimeTo", "N/A"),
                "description": props.get("rawAirspace", props.get("description", "N/A")),
            })

        return results

    except Exception:
        logger.exception("SIGMET fetch failed")
        return []
