"""
OPS CONTROL - Cog Loader

Auto-discovers and loads all Discord.py Cog modules from bot/cogs/.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bot.core.bot import OpsControlBot

logger = logging.getLogger("ops_control.loader")

_REQUIRED_COGS: list[str] = [
    "bot.cogs.status",
    "bot.cogs.welcome",
    "bot.cogs.announce",
    "bot.cogs.notam",
    "bot.cogs.flight_ops",
    "bot.cogs.simbrief",
    "bot.cogs.weather",
    "bot.cogs.weather_group",
    "bot.cogs.atis",
    "bot.cogs.sigmet",
    "bot.cogs.notam_external",
    "bot.cogs.releases",
    "bot.cogs.vatsim",
    "bot.cogs.ops_dashboard",
    "bot.cogs.bugs",
    "bot.cogs.support",
    "bot.cogs.profile",
    "bot.cogs.preferences",
    "bot.cogs.logbook",
    "bot.cogs.admin",
]


async def load_cogs(bot: OpsControlBot) -> None:
    """Load all Cog modules, gracefully skipping failures."""
    loaded: list[str] = []
    failed: list[tuple[str, str]] = []

    for module_name in _REQUIRED_COGS:
        try:
            await bot.load_extension(module_name)
            loaded.append(module_name)
            logger.info("  Loaded: %s", module_name)
        except Exception as exc:
            failed.append((module_name, str(exc)))
            logger.error("  Failed to load %s: %s", module_name, exc)

    logger.info(
        "Cog loading complete -- %d loaded, %d failed",
        len(loaded),
        len(failed),
    )
    for name, reason in failed:
        logger.warning("  Skipped %s: %s", name, reason)
