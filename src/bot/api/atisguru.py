"""
OPS CONTROL - ATIS.guru D-ATIS client

Mirrors the desktop app's ATIS.guru scraper (app/weather_client.py
fetch_realworld_atis) so the descent-briefing DM can show the same
real-world D-ATIS the app does. ATIS.guru has no public JSON API (it is a
Blazor/SignalR app), so we scrape the per-airport page and extract the
Arrival / Departure ATIS sections from the rendered text.

Best-effort: returns None on any failure; callers must never crash on it.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any

from bot.api import _get_session

logger = logging.getLogger("ops_control.api.atisguru")

ATIS_GURU_URL = "https://atis.guru/atis/{icao}"
USER_AGENT = "VATSIM-Traffic-Board/0.4 simulation-only contact: local"

MAX_SECTION = 1800
MAX_TEXT = 2400


def _strip_tags(s: str) -> str:
    s = re.sub(r"<script[\s\S]*?</script>", " ", s, flags=re.I)
    s = re.sub(r"<style[\s\S]*?</style>", " ", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", " \n ", s, flags=re.I)
    s = re.sub(r"</(?:p|div|h\d|section|li)>", " \n ", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s+", "\n", s)
    return re.sub(r"\n{2,}", "\n", s).strip()


def _section_after(text: str, title: str) -> str | None:
    # ATIS.guru pages often include a temporary "No ATIS available" placeholder
    # before the prerendered message. Do not treat the placeholder as final.
    t = re.sub(r"\s+", " ", text).strip()
    pat = (
        rf"{re.escape(title)}\s+(?:\d{{4}}-\d{{2}}-\d{{2}}\s+\d{{2}}:\d{{2}}\s+UTC\s+)?"
        r"(.*?)(?=\s+(?:Arrival ATIS|Departure ATIS|METAR|TAF|No ATIS available|An unhandled error)|$)"
    )
    matches = [m.group(1).strip() for m in re.finditer(pat, t, flags=re.I) if m.group(1).strip()]
    if not matches:
        return None
    # Prefer the longest section; it is usually the actual D-ATIS, not page chrome.
    best = max(matches, key=len)
    if len(best) < 8 or "NO ATIS AVAILABLE" in best.upper():
        return None
    return best[:MAX_SECTION]


def _extract_atis_code(text: str | None) -> str | None:
    if not text:
        return None
    match = re.search(r"\b(?:INFO|INFORMATION)\s+([A-Z])\b", text.upper())
    return match.group(1) if match else None


async def fetch_atisguru_atis(icao: str) -> dict[str, Any] | None:
    """Fetch real-world D-ATIS for an ICAO from ATIS.guru.

    Returns a dict with ``atis_type`` / ``atis_code`` / ``atis_message`` /
    ``source`` (same shape as ``fetch_vatsim_atis``) or None when the page
    has no ATIS / is unreachable.
    """
    icao = icao.strip().upper()
    url = ATIS_GURU_URL.format(icao=icao)
    try:
        session = await _get_session()
        async with session.get(url, headers={"User-Agent": USER_AGENT}) as resp:
            if resp.status != 200:
                logger.debug("ATIS.guru %s returned HTTP %s", icao, resp.status)
                return None
            raw = await resp.text()
    except Exception:
        logger.exception("ATIS.guru fetch failed for %s", icao)
        return None

    text = _strip_tags(raw)
    arrival = _section_after(text, "Arrival ATIS")
    departure = _section_after(text, "Departure ATIS")

    parts: list[str] = []
    if arrival:
        parts.append("ARR: " + arrival)
    if departure:
        parts.append("DEP: " + departure)
    combined = "\n\n".join(parts) if parts else ""
    if not combined:
        return None

    if arrival and departure:
        atis_type = "Arrival + Departure ATIS"
    elif arrival:
        atis_type = "Arrival ATIS"
    else:
        atis_type = "Departure ATIS"

    return {
        "airport": icao,
        "atis_type": atis_type,
        "atis_code": _extract_atis_code(combined),
        "atis_message": combined[:MAX_TEXT] or None,
        "source": "ATIS.guru",
        "url": url,
    }
