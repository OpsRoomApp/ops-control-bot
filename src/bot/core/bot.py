"""
OPS CONTROL - Discord Bot Client

Extends discord.py's commands.Bot with lifecycle management,
Cog auto-loading, and graceful shutdown handling.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

import discord
from discord.ext import commands

from bot.config import config

logger = logging.getLogger("ops_control.core")


class OpsControlBot(commands.Bot):
    """
    The OPS CONTROL Discord bot instance.

    Provides:
    - Clean startup / shutdown lifecycle
    - Cog auto-discovery and loading
    - Database initialisation hook
    - Consistent error handling
    """

    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True  # Required for on_member_join welcome system

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            owner_id=config.owner_user_id,
            help_command=None,  # We provide our own via /help
        )

        self._startup_complete = asyncio.Event()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def setup_hook(self) -> None:
        """Called once before the bot connects to the gateway."""
        logger.info("--- OPS CONTROL starting ---")

        # 1. Initialise database
        await self._init_database()

        # 2. Load Cogs
        await self._load_cogs()

        # 3. Register persistent views for ticket system
        self._register_persistent_views()

        # 4. Sync slash commands to the target guild for instant registration
        guild = discord.Object(id=config.guild_id)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        logger.info("Slash commands synced to guild %s", config.guild_id)

        logger.info("--- Setup complete ---")

    async def on_ready(self) -> None:
        """Fires after the bot has logged in and is ready."""
        logger.info("Connected to Discord as %s (ID: %s)", self.user, self.user.id)
        logger.info("Guild ID: %s | Owner ID: %s", config.guild_id, config.owner_user_id)

        # Log startup to Discord log channel
        try:
            from bot.services.discord_log import log_startup
            await log_startup(self)
        except Exception:
            logger.exception("Failed to send startup log")

        self._startup_complete.set()

    async def close(self) -> None:
        """Graceful shutdown — close DB and API connections, clean up."""
        logger.info("Shutting down OPS CONTROL...")

        # Log shutdown to Discord
        try:
            from bot.services.discord_log import log_shutdown
            await log_shutdown(self)
        except Exception:
            pass

        try:
            from bot.database import close_db
            await close_db()
        except Exception:
            logger.exception("Error closing database during shutdown")
        try:
            from bot.api import close_api_session
            await close_api_session()
        except Exception:
            logger.exception("Error closing API session during shutdown")
        await super().close()
        logger.info("OPS CONTROL offline.")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _init_database(self) -> None:
        """Initialise the SQLite database and create tables.

        Column additions via ALTER TABLE are executed in a separate
        migration step that tolerates 'duplicate column' errors on
        fresh deployments.
        """
        try:
            from bot.database import init_db, run_migrations
            await init_db()
            await run_migrations()
            logger.info("Database initialised.")
        except Exception:
            logger.exception("Failed to initialise database — continuing")

    async def _load_cogs(self) -> None:
        """Auto-discover and load all Cog modules from bot/cogs/."""
        from bot.core.loader import load_cogs
        await load_cogs(self)

    def _register_persistent_views(self) -> None:
        """Register persistent views so they survive bot restarts."""
        try:
            from bot.cogs.ticket_system import (
                SupportPanelView,
                TicketActionView,
                EscalateToSupportView,
            )
            self.add_view(SupportPanelView())
            self.add_view(TicketActionView())
            self.add_view(EscalateToSupportView())
            logger.info("Persistent views registered.")
        except Exception:
            logger.exception("Failed to register persistent views")

    # ------------------------------------------------------------------
    # Global error handler
    # ------------------------------------------------------------------

    async def on_command_error(
        self,
        ctx: commands.Context[Any],
        error: commands.CommandError,
    ) -> None:
        """Global error handler for prefix commands (if any are ever added)."""
        logger.error("Command error from %s: %s", ctx.author, error)

    async def on_error(self, event_method: str, /, *args: Any, **kwargs: Any) -> None:
        """Catch unhandled event-loop errors (e.g. on_member_join)."""
        logger.exception(
            "Unhandled error in event '%s' -- args=%s kwargs=%s",
            event_method,
            args,
            kwargs,
        )
