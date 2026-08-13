"""
OPS CONTROL - Main entry point.

Initialises logging, database, and starts the Discord bot.
Run with: python -m bot.main
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys

from bot.config import config
from bot.core.bot import OpsControlBot
from bot.logger import setup_logging


async def main() -> None:
    """Bootstrap and run OPS CONTROL."""

    # 1. Logging
    logger = setup_logging()
    logger.info("╔══════════════════════════════════╗")
    logger.info("║   OPS CONTROL v1.0.0 starting   ║")
    logger.info("╚══════════════════════════════════╝")
    logger.info("Guild ID: %s", config.guild_id)
    logger.info("Owner ID: %s", config.owner_user_id)
    logger.info("Arrivals Channel: %s", config.arrivals_channel_id)

    # 2. Validate critical configuration
    if not config.discord_token:
        logger.critical("DISCORD_TOKEN is not set. Check your .env file.")
        sys.exit(1)

    # 3. Create and start bot
    bot = OpsControlBot()

    # Handle graceful shutdown on signals
    loop = asyncio.get_running_loop()

    def shutdown_handler() -> None:
        logger.info("Received shutdown signal.")
        asyncio.ensure_future(bot.close())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_handler)
        except NotImplementedError:
            # Windows doesn't support add_signal_handler
            pass

    try:
        await bot.start(config.discord_token)
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
    except Exception:
        logger.critical("Fatal error during bot execution.", exc_info=True)
        sys.exit(1)
    finally:
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
