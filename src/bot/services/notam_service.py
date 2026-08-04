"""
OPS CONTROL - External NOTAM Service

Fetches active NOTAMs for ICAO airports.

v0.25.60: FAA NMS-API proxy integration. The bot now prefers the opsroom.live
NMS proxy (live, global NOTAM coverage incl. FDC/TFR) whenever a proxy token
is configured, and falls back to the legacy FAA NOTAM API exactly as before
when it is not -- so base behaviour is unchanged.
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

# NMS proxy auth: prefer the dedicated NMS token, fall back to admin token.
def _nms_token() -> str:
    return (config.nms_proxy_token or config.admin_api_token or "").strip()


def _nms_base() -> str:
    return (config.nms_proxy_base_url or "https://opsroom.live").rstrip("/")


async def _nms_proxy_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """GET a path on the opsroom.live NMS proxy with the shared bearer token.

    Returns the parsed JSON body, or None on any transport/HTTP failure so
    callers can degrade to their existing fallback paths.
    """
    token = _nms_token()
    if not token:
        return None
    url = f"{_nms_base()}/api/v1/nms{path}"
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        session = await _get_session()
        async with session.get(url, params=params or None, headers=headers) as resp:
            if resp.status != 200:
                logger.warning("NMS proxy %s returned HTTP %s", path, resp.status)
                return None
            body = await resp.json()
            return body if isinstance(body, dict) else {"data": body}
    except Exception:
        logger.exception("NMS proxy request failed for %s", path)
        return None


def _feature_to_row(feature: dict[str, Any]) -> dict[str, Any]:
    """Normalize one NMS GeoJSON feature into the bot's NOTAM row shape."""
    props = feature.get("properties") or {}
    core = props.get("coreNOTAMData") or {}
    notam = core.get("notam") or {}
    return {
        "identifier": str(notam.get("number") or notam.get("id") or "N/A"),
        "nms_id": str(notam.get("id") or ""),
        "effective": str(notam.get("effectiveStart") or "N/A"),
        "expiry": str(notam.get("effectiveEnd") or "PERM"),
        "description": str(notam.get("text") or "No NOTAM text returned."),
        "type": str(notam.get("classification") or notam.get("type") or "NOTAM"),
        "source": "FAA NMS",
        "location": str(notam.get("icaoLocation") or notam.get("location") or ""),
        "qcode": str(notam.get("selectionCode") or ""),
    }


def _geojson_rows(body: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not body or not body.get("ok"):
        return []
    data = body.get("data") or {}
    geojson = data.get("geojson") or []
    if isinstance(geojson, dict):
        geojson = geojson.get("features") or []
    return [_feature_to_row(item) for item in geojson if isinstance(item, dict)]


async def fetch_nms_notams(location: str = "", lat: float | None = None, lon: float | None = None,
                           radius: float | None = None, classification: str = "", feature: str = "") -> list[dict[str, Any]]:
    """Query the NMS proxy for NOTAMs by location or geo-radius (GeoJSON)."""
    params: dict[str, Any] = {}
    if location:
        params["location"] = location.strip().upper()
    if lat is not None and lon is not None and radius is not None:
        params.update({"latitude": lat, "longitude": lon, "radius": radius})
    if classification:
        params["classification"] = classification.strip().upper()
    if feature:
        params["feature"] = feature.strip().upper()
    body = await _nms_proxy_get("/notams", params)
    return _geojson_rows(body)


async def fetch_nms_geo(latitude: float, longitude: float, radius_nm: float) -> list[dict[str, Any]]:
    """Geo-radius NMS NOTAM query (uses the proxy's lat/lon/radius surface)."""
    return await fetch_nms_notams(lat=latitude, lon=longitude, radius=radius_nm)


async def fetch_nms_checklist(location: str, classification: str = "") -> list[dict[str, Any]]:
    """Fetch the NOTAM checklist (index entries) for an airport."""
    params: dict[str, Any] = {"location": location.strip().upper()}
    if classification:
        params["classification"] = classification.strip().upper()
    body = await _nms_proxy_get("/checklist", params)
    if not body or not body.get("ok"):
        return []
    data = body.get("data") or {}
    entries = data.get("checklist") or []
    if isinstance(entries, dict):
        entries = entries.get("items") or entries.get("checklist") or []
    return [entry for entry in entries if isinstance(entry, dict)]


async def fetch_nms_fdc(location: str) -> list[dict[str, Any]]:
    """Fetch FDC-classification NOTAMs (TFRs and permanent FDC items)."""
    return await fetch_nms_notams(location=location, classification="FDC")


async def fetch_nms_search(text: str) -> list[dict[str, Any]]:
    """Free-text NOTAM search through the proxy (exact text, 1-80 chars)."""
    text = (text or "").strip()
    if not text or len(text) > 80:
        return []
    body = await _nms_proxy_get("/search", {"text": text})
    return _geojson_rows(body)


async def fetch_notams(icao: str) -> list[dict[str, Any]]:
    """Fetch active NOTAMs for an ICAO airport code.

    Prefers the FAA NMS proxy when configured (live, global). Falls back to
    the legacy FAA NOTAM API otherwise; returns empty list if unavailable.
    """
    icao = icao.strip().upper()

    results: list[dict[str, Any]] = []

    # v0.25.60: NMS proxy first when a shared token is configured.
    if _nms_token():
        try:
            results = await fetch_nms_notams(location=icao)
            if results:
                return results
            logger.info("NMS proxy returned no NOTAMs for %s; trying legacy API", icao)
        except Exception:
            logger.exception("NMS proxy fetch failed for %s; trying legacy API", icao)

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
