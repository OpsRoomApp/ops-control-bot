"""
OPS CONTROL - SimBrief Options URL Builder

Builds prefilled SimBrief "Options" URLs used by /randomroute.

Correct endpoint (Options page):
    https://dispatch.simbrief.com/options/custom

The legacy OFP creation endpoint is intentionally not used (it returns 404).

Required parameters:
    airline, fltnum, orig, dest, basetype

Optional parameters (added when available):
    callsign, route, static_id, date, reg, altn, targetfl

Static ID priority:
    1. User-linked SimBrief static_id (per Discord user)
    2. SIMBRIEF_STATIC_ID environment default
    3. Omit static_id

Never pass userid to this endpoint (it is not documented for Options URLs).
"""

from __future__ import annotations

import urllib.parse


def build_simbrief_options_url(
    *,
    airline: str,
    fltnum: str,
    orig: str,
    dest: str,
    basetype: str,
    callsign: str | None = None,
    route: str | None = None,
    static_id: str | None = None,
    date: str | None = None,
    reg: str | None = None,
    altn: str | None = None,
    targetfl: str | None = None,
) -> str:
    """Build a URL-encoded SimBrief Options URL.

    Example minimum:
        https://dispatch.simbrief.com/options/custom?airline=DLH&fltnum=32D&orig=EDDF&dest=EGLL&basetype=A359
    """
    def _norm(v: str | int | float | None) -> str:
        return str(v or "").strip().upper()

    params: dict[str, str] = {
        "airline": _norm(airline),
        "fltnum": _norm(fltnum),
        "orig": _norm(orig),
        "dest": _norm(dest),
        "basetype": _norm(basetype),
    }

    optional: dict[str, str | None] = {
        "callsign": callsign,
        "route": route,
        "static_id": static_id,
        "date": date,
        "reg": reg,
        "altn": altn,
        "targetfl": targetfl,
    }
    for key, value in optional.items():
        if value:
            params[key] = str(value).strip()

    query = urllib.parse.urlencode(params)
    return f"https://dispatch.simbrief.com/options/custom?{query}"


def resolve_static_id(user_static_id: str | None, config_static_id: str | None) -> str | None:
    """Resolve the static_id to use, honoring priority order."""
    if user_static_id:
        return user_static_id
    if config_static_id:
        return config_static_id
    return None
