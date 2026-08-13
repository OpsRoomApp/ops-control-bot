"""
OPS CONTROL - Route Generation Orchestration

Chooses the primary provider (Where2Fly when configured) and falls back
to the improved local CSV engine when the API is disabled, unconfigured,
unavailable, rate-limited, or returns no suitable result.
"""

from __future__ import annotations

import logging

from bot.config import config
from bot.services.routes.base import RouteProvider
from bot.services.routes.models import (
    InvalidAircraft,
    InvalidDuration,
    InvalidICAO,
    NoRouteFound,
    ProviderUnavailable,
    RouteResult,
)

logger = logging.getLogger("ops_control.routes")


def _primary_provider() -> RouteProvider | None:
    """Return the Where2Fly provider when fully configured, else None."""
    if not (config.where2fly_enabled and config.where2fly_api_token):
        return None
    from bot.services.routes.where2fly import Where2FlyProvider

    provider = Where2FlyProvider()
    if provider.available:
        return provider
    return None


async def generate_route(
    aircraft_input: str,
    duration_input: str,
    origin: str | None = None,
    destination: str | None = None,
    filters: dict | None = None,
) -> RouteResult:
    """Generate a route using the best available provider.

    Primary:  Where2Fly API (when WHERE2FLY_ENABLED + WHERE2FLY_API_TOKEN).
    Fallback: improved local database engine (always available).

    Raises InvalidAircraft / InvalidDuration / InvalidICAO / NoRouteFound.
    ProviderUnavailable is never raised to callers - it triggers fallback.
    """
    from bot.services.routes.fallback import FallbackProvider

    primary = _primary_provider()
    fallback = FallbackProvider()

    if primary is not None:
        try:
            result = await primary.generate(aircraft_input, duration_input, origin, destination, filters=filters)
            logger.info(
                "Route generated via %s: %s -> %s (%s)",
                primary.name, result.origin, result.destination, result.estimated_time,
            )
            return result
        except (InvalidAircraft, InvalidDuration, InvalidICAO):
            raise
        except NoRouteFound as exc:
            logger.warning("Where2Fly: no suitable route (%s) - falling back", exc)
        except ProviderUnavailable as exc:
            logger.warning("Where2Fly unavailable (%s) - falling back", exc)
        except Exception as exc:
            logger.exception("Where2Fly provider error - falling back")
            logger.warning("Where2Fly error: %s", exc)
    else:
        logger.info("Where2Fly disabled or no token - using local fallback database")

    try:
        result = await fallback.generate(aircraft_input, duration_input, origin, destination, filters=filters)
        logger.info(
            "Route generated via %s: %s -> %s (%s)",
            fallback.name, result.origin, result.destination, result.estimated_time,
        )
        return result
    except (InvalidAircraft, InvalidDuration, InvalidICAO, NoRouteFound):
        raise
    except Exception as exc:
        logger.exception("Fallback route generation failed")
        raise NoRouteFound(f"Route generation failed: {exc}") from exc


__all__ = [
    "generate_route",
    "RouteResult",
    "RouteProvider",
    "InvalidAircraft",
    "InvalidDuration",
    "InvalidICAO",
    "NoRouteFound",
    "ProviderUnavailable",
]
