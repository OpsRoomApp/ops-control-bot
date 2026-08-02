"""
OPS CONTROL - VATSIM Event Reminders (v0.25.55 / B3)

Polls the VATSIM events API on a schedule, auto-posts new upcoming events
to the configured events channel, and sends a reminder N minutes before start.
Avoids duplicate posts by tracking posted/reminded in vatsim_events table.
"""

from __future__ import annotations

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
ANNOUNCE_MINUTES_DEFAULT = 60
REMINDER_MINUTES_DEFAULT = 30


class VatsimEvents(commands.Cog):
    """Polls VATSIM events and posts reminders."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._session: aiohttp.ClientSession | None = None
        self._poll_task: tasks.Loop | None = None

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
        if self._poll_task:
            self._poll_task.cancel()
        if self._session:
            await self._session.close()

    async def _wait_until_ready(self):
        await self.bot.wait_until_ready()

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
            start_str = str(event.get("start_time") or "")
            end_str = str(event.get("end_time") or "")

            if not event_id or not start_str:
                continue

            try:
                start_time = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
            except ValueError:
                continue

            end_time = None
            try:
                if end_str:
                    end_time = datetime.fromisoformat(end_str.replace("Z", "+00:00"))
            except ValueError:
                pass

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
                    link = str(event.get("link") or "")
                    banner = str(event.get("banner") or "").strip()
                    short_desc = str(event.get("short_description") or "").strip()
                    airports = [
                        str(a.get("icao") or "").strip().upper()
                        for a in (event.get("airports") or [])
                        if isinstance(a, dict)
                    ]
                    airports = [a for a in airports if a]

                    desc = f"**Starts:** {start_time.strftime('%Y-%m-%d %H:%M')} UTC\n"
                    if end_time:
                        desc += f"**Ends:** {end_time.strftime('%Y-%m-%d %H:%M')} UTC\n"
                    if airports:
                        desc += f"**Airports:** {' » '.join(airports)}\n"
                    if short_desc:
                        desc += f"\n{short_desc}"
                    embed = discord.Embed(
                        title=title,
                        description=desc,
                        url=link or None,
                        color=discord.Color.blue(),
                    )
                    if banner:
                        embed.set_image(url=banner)
                    embed.set_footer(
                        text=f"Starting time \u27a1 \u2022 {start_time.strftime('%A, %d %b %Y %H:%M')} UTC"
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
                continue

            # Reminder: once inside the reminder window, at most once.
            if row["reminded"]:
                continue
            if timedelta(0) < time_to_start <= timedelta(minutes=reminder_minutes):
                try:
                    await channel.send(
                        f"**Reminder: {title} starts in "
                        f"{time_to_start.total_seconds() // 60:.0f} minutes at "
                        f"{start_time.strftime('%H:%M')} UTC!**"
                    )
                except discord.Forbidden:
                    pass
                else:
                    await db.execute(
                        "UPDATE vatsim_events SET reminded=1 WHERE event_id=?",
                        (event_id,),
                    )
                    await db.commit()


async def setup(bot: commands.Bot):
    await bot.add_cog(VatsimEvents(bot))
