"""
OPS CONTROL - Route Provider Interface

Abstract base class for route-generation providers.

Providers return a RouteResult or raise one of the exceptions defined in
models.py (ProviderUnavailable, NoRouteFound, InvalidAircraft, ...).
The orchestration layer (routes/__init__.py) decides which provider is
primary and when to fall back.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from bot.services.routes.models import RouteResult


class RouteProvider(ABC):
    """Interface implemented by all route providers."""

    name: str = "base"

    @abstractmethod
    async def generate(
        self,
        aircraft_input: str,
        duration_input: str,
        origin: str | None = None,
        destination: str | None = None,
        filters: dict | None = None,
    ) -> RouteResult:
        """Generate a route matching the requested aircraft and duration.

        Args:
            aircraft_input: user aircraft text (e.g. "A320", "B738", "8h").
            duration_input: user duration text (e.g. "1h30", "8 hours").
            origin: optional 4-letter ICAO departure code.
            destination: optional 4-letter ICAO arrival code.

        Returns:
            RouteResult on success.

        Raises:
            ProviderUnavailable: provider could not be reached.
            NoRouteFound: no route satisfied constraints.
            InvalidAircraft / InvalidDuration / InvalidICAO: bad input.
        """
        raise NotImplementedError
