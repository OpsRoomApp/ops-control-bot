"""
OPS CONTROL - VATSIM Event Reminders (v0.25.58)

Polls the VATSIM events API on a schedule, auto-posts new upcoming events
to the configured events channel, and sends a reminder N minutes before start.

Announcement (default 90 min before start):
  Rich embed with the event title, full description, banner image, link,
  airport route line and a Discord local-time timestamp footer so every
  viewer sees the start time in their own timezone.

Reminder (default 30 min before start):
  Also a rich embed (never a plain-text message), carrying the same banner
  image, link and local-time timestamp.

Avoids duplicate posts by tracking posted/reminded in vatsim_events table.

Reminder timing (fixed in v0.25.60):
  With the legacy 15-minute poll, the reminder only fired on the first poll
  *inside* the window - which could be up to 15 minutes late (observed at
  19 minutes before start instead of 30). Reminders are now delivered by an
  exact-time asyncio task scheduled when the event is announced, so they fire
  at precisely `start_time - reminder_minutes`. The poll check remains as a
  restart safety net (fires late only if the task was lost).

Footer timestamps:
  Discord embed footers do NOT render `<t:...>` timestamps - the same markup
  in the description/fields does. The footer therefore stays plain text; the
  description carries the rendered local-time timestamps.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone, timedelta

import aiohttp
import discord
from discord.ext import commands, tasks

from bot.config import config
from bot.database import get_db

logger = logging.getLogger("ops_control.vatsim_events")

VATSIM_EVENTS_URL = "https://my.vatsim.net/api/v2/events/latest"
# Legacy payloads wrapped the list under "items"; v2 uses "data".
ANNOUNCE_MINUTES_DEFAULT = 90
REMINDER_MINUTES_DEFAULT = 30


def _discord_timestamp(dt: datetime) -> str:
    """Render a UTC datetime as a Discord local-time timestamp (viewer's TZ)."""
    return f"<t:{int(dt.timestamp())}:f>"


class VatsimEvents(commands.Cog):
    """Polls VATSIM events and posts reminders."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._poll_task: tasks.Loop | None = None
        # Exact-time reminder deliveries keyed by event id (cancelled on unload).
        self._reminder_tasks: dict[str, asyncio.Task] = {}

    async def cog_load(self):
        self._session = aiohttp.ClientSession()
        if not config.vatsim_events_channel_id:
            logger.info("VATSIM_EVENTS_CHANNEL_ID not set - event reminders disabled")
            return
        # Poll every 15 minutes. Only start after the guild/channel cache is
        # populated so the first poll can resolve the events channel.
        self._poll_task = tasks.loop(minutes=15)(self._poll_vatsim_events)
        self._poll_task.before_loop(self._wait_until_ready)
        self._poll_task.start()

    async def cog_unload(self):
        for task in self._reminder_tasks.values():
            task.cancel()
        self._reminder_tasks.clear()
        if self._poll_task:
            self._poll_task.cancel()
        if self._session:
            await self._session.close()

    async def _wait_until_ready(self):
        await self.bot.wait_until_ready()

    @staticmethod
    def _parse_event_times(event: dict) -> tuple[datetime | None, datetime | None]:
        """Return (start_time, end_time) aware datetimes, or (None, None)."""
        start_str = str(event.get("start_time") or "")
        end_str = str(event.get("end_time") or "")
        start_time = None
        end_time = None
        try:
            if start_str:
                start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        except ValueError:
            start_time = None
        try:
            if end_str:
                end_time = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
        except ValueError:
            end_time = None
        return start_time, end_time

    @staticmethod
    def _event_airports(event: dict) -> list[str]:
        airports = [
            str(a.get("icao") or "").strip().upper()
            for a in (event.get("airports") or [])
            if isinstance(a, dict)
        ]
        return [a for a in airports if a]

    def _build_embed(
        self,
        event: dict,
        title: str,
        start_time: datetime,
        end_time: datetime | None,
        *,
        footer: str,
        include_banner: bool = True,
    ) -> discord.Embed:
        """Build the event embed (title, description, banner, link, footer)."""
        link = str(event.get("link") or "")
        banner = str(event.get("banner") or "").strip()
        short_desc = str(event.get("short_description") or "").strip()
        full_desc = str(event.get("description") or "").strip()
        airports = self._event_airports(event)

        desc = f"**Starts:** {_discord_timestamp(start_time)}\n"
        if end_time:
            desc += f"**Ends:** {_discord_timestamp(end_time)}\n"
        if airports:
            desc += f"**Airports:** {' » '.join(airports)}\n"
        # Prefer the full description, fall back to the short description.
        body = full_desc or short_desc
        if body:
            desc += f"\n{body}"

        embed = discord.Embed(
            title=title,
            description=desc,
            url=link or None,
            color=discord.Color.blue(),
        )
        if include_banner and banner:
            embed.set_image(url=banner)
        embed.set_footer(text=footer)
        return embed

    # -- reminder scheduling --------------------------------------------

    def _schedule_reminder(
        self,
        event: dict,
        title: str,
        start_time: datetime,
        end_time: datetime | None,
        channel: discord.TextChannel,
        reminder_minutes: int,
    ) -> None:
        """Schedule an exact-time reminder at ``start_time - reminder_minutes``.

        Only runs while the cog's poll loop is live (``cog_load`` ran). The
        direct-call path used by tests (and restart recovery) relies on the
        poll safety net instead, which keeps the event DB deterministic.
        """
        if self._poll_task is None:
            return
        event_id = str(event.get("id") or "")
        if not event_id or event_id in self._reminder_tasks:
            return
        delay = (
            start_time - timedelta(minutes=reminder_minutes) - datetime.now(timezone.utc)
        ).total_seconds()
        if delay <= 0:
            return  # ideal point already passed - the poll safety net covers it
        task = asyncio.create_task(
            self._deliver_reminder_later(
                event, title, start_time, end_time, channel, delay
            )
        )
        self._reminder_tasks[event_id] = task

    async def _deliver_reminder_later(
        self,
        event: dict,
        title: str,
        start_time: datetime,
        end_time: datetime | None,
        channel: discord.TextChannel,
        delay: float,
    ) -> None:
        """Sleep until the reminder point, then send (DB-gated, at most once)."""
        event_id = str(event.get("id") or "")
        try:
            await asyncio.sleep(delay)
            if datetime.now(timezone.utc) >= start_time:
                return  # event already started - no reminder
            db = await get_db()
            cursor = await db.execute(
                "SELECT reminded FROM vatsim_events WHERE event_id=?", (event_id,)
            )
            row = await cursor.fetchone()
            if row is None or row["reminded"]:
                return
            await self._send_reminder(event, title, start_time, end_time, channel)
            await db.execute(
                "UPDATE vatsim_events SET reminded=1 WHERE event_id=?", (event_id,)
            )
            await db.commit()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Scheduled VATSIM event reminder failed (event=%s)", event_id
            )
        finally:
            self._reminder_tasks.pop(event_id, None)

    async def _send_reminder(
        self,
        event: dict,
        title: str,
        start_time: datetime,
        end_time: datetime | None,
        channel: discord.TextChannel,
    ) -> None:
        """Send the reminder embed once (used by the scheduled task and poll)."""
        try:
            minutes_left = max(
                1,
                int((start_time - datetime.now(timezone.utc)).total_seconds() // 60),
            )
            embed = self._build_embed(
                event,
                title,
                start_time,
                end_time,
                footer=f"Reminder \u2022 Starting in {minutes_left} min",
            )
            await channel.send(embed=embed)
        except discord.HTTPException:
            pass

    # -- poll loop -------------------------------------------------------

    async def _poll_vatsim_events(self):
        try:
            async with self._session.get(VATSIM_EVENTS_URL, timeout=15.0) as resp:
                if resp.status != 200:
                    logger.warning(
                        "VATSIM events API returned HTTP %s (url=%s)",
                        resp.status,
                        VATSIM_EVENTS_URL,
                    )
                    return
                data = await resp.json()
        except Exception:
            logger.exception("VATSIM events API poll failed")
            return

        if isinstance(data, dict):
            if "data" in data:
                events = data["data"]
            else:
                events = data.get("items") or []
        else:
            events = data
        if not isinstance(events, list) or not events:
            return
        logger.info("VATSIM events poll: %d event(s) fetched", len(events))

        channel = self.bot.get_channel(config.vatsim_events_channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            return

        db = await get_db()
        now = datetime.now(timezone.utc)

        announce_minutes = getattr(
            config, "vatsim_events_announce_minutes", ANNOUNCE_MINUTES_DEFAULT
        )
        reminder_minutes = getattr(
            config, "vatsim_events_reminder_minutes", REMINDER_MINUTES_DEFAULT
        )

        for event in events:
            if not isinstance(event, dict):
                continue
            event_id = str(event.get("id") or "")
            title = str(event.get("name") or event.get("title") or "VATSIM Event")

            if not event_id:
                continue

            start_time, end_time = self._parse_event_times(event)
            if start_time is None:
                continue

            # Skip events that already ended or already started.
            if end_time and end_time < now:
                continue
            if start_time < now:
                continue

            time_to_start = start_time - now

            cursor = await db.execute(
                "SELECT posted, reminded FROM vatsim_events WHERE event_id=?",
                (event_id,),
            )
            row = await cursor.fetchone()

            if row is None:
                # First time seeing the event: track it, but do NOT announce
                # yet - announcements only fire inside the announce window.
                await db.execute(
                    "INSERT INTO vatsim_events(event_id,title,start_time,end_time,posted,reminded,created_at) "
                    "VALUES(?,?,?,?,0,0,?)",
                    (event_id, title, start_time.isoformat(), (end_time.isoformat() if end_time else None), now.isoformat()),
                )
                await db.commit()
                row = {"posted": 0, "reminded": 0}

            # Announcement: only once the event is inside the announce window.
            if not row["posted"]:
                if timedelta(0) < time_to_start <= timedelta(minutes=announce_minutes):
                    embed = self._build_embed(
                        event,
                        title,
                        start_time,
                        end_time,
                        footer="Starting soon \u2022 VATSIM Event",
                    )
                    try:
                        await channel.send(embed=embed)
                    except discord.Forbidden:
                        pass
                    else:
                        await db.execute(
                            "UPDATE vatsim_events SET posted=1 WHERE event_id=?",
                            (event_id,),
                        )
                        await db.commit()
                    # Schedule the precise reminder now the event is live.
                    self._schedule_reminder(
                        event, title, start_time, end_time, channel, reminder_minutes
                    )
                continue

            # Reminder: delivered at the exact time by the scheduled task in
            # normal operation. The poll is a restart safety net that only
            # fires once the ideal reminder point has passed and no task is
            # pending (i.e. the task was lost in a restart).
            if row["reminded"]:
                continue
            if timedelta(0) < time_to_start <= timedelta(minutes=reminder_minutes):
                ideal = start_time - timedelta(minutes=reminder_minutes)
                if now >= ideal and event_id not in self._reminder_tasks:
                    await self._send_reminder(
                        event, title, start_time, end_time, channel
                    )
                    await db.execute(
                        "UPDATE vatsim_events SET reminded=1 WHERE event_id=?",
                        (event_id,),
                    )
                    await db.commit()


async def setup(bot: commands.Bot):
    await bot.add_cog(VatsimEvents(bot))
