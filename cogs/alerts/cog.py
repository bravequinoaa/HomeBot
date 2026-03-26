"""
Calendar Alerts Cog — sends @everyone alerts 5 minutes before scheduled events.

Events are persisted in data/calendar.json between restarts. The cog
listens for a "schedule_parsed" dispatch from the Schedule Cog and
stores the events; a background task fires alerts 5 minutes before each
event's start time.

Commands:
  !stopalerts    — disable alert sending
  !enablealerts  — re-enable alert sending (default: on)
"""

from __future__ import annotations

import datetime
import logging
import os

import pytz
import discord
from discord.ext import commands, tasks

from .storage import CalendarStorage

log = logging.getLogger("homebot.alerts")

EASTERN = pytz.timezone("America/New_York")


class AlertsCog(commands.Cog):
    """Sends 5-minute advance @everyone alerts for upcoming calendar events."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        raw = os.environ.get("CH_CALENDAR_ALERTS")
        if not raw:
            raise RuntimeError("CH_CALENDAR_ALERTS is not set in the environment.")
        try:
            self.alerts_channel_id = int(raw)
        except ValueError:
            raise RuntimeError(f"CH_CALENDAR_ALERTS must be an integer, got: {raw!r}")

        # Storage instance provided by the shared StorageManager
        self.storage: CalendarStorage = bot.storage_manager.get("calendar")
        log.info("AlertsCog using storage instance 0x%x", id(self.storage))

        self._alerts_enabled = True
        self._check_alerts.start()

    def cog_unload(self) -> None:
        self._check_alerts.cancel()

    # -----------------------------------------------------------------------
    # Event listener — receives parsed events from the Schedule Cog
    # -----------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_schedule_parsed(
        self,
        anchored_events: list,
        uploader_id: int,
        uploader_name: str,
    ) -> None:
        user = self.storage.resolve_user(uploader_id, uploader_name)
        self.storage.save_events(anchored_events, user)
        self.storage.prune_old_events()
        log.info(
            "Stored %d event(s) for user %s (discord_id=%d)",
            len(anchored_events), user, uploader_id,
        )

    # -----------------------------------------------------------------------
    # Background task — check every 60 s for events ~5 minutes away
    # -----------------------------------------------------------------------

    @tasks.loop(seconds=60)
    async def _check_alerts(self) -> None:
        if not self._alerts_enabled:
            return

        now = datetime.datetime.now(EASTERN)

        for event in self.storage.load_events():
            if event.get("alerted"):
                continue

            start_time = event.get("start_time")
            if not start_time:
                continue

            try:
                h, m = int(start_time.split(":")[0]), int(start_time.split(":")[1])
                y, mo, d = [int(x) for x in event["date"].split("-")]
                event_dt = EASTERN.localize(datetime.datetime(y, mo, d, h, m))
            except (ValueError, KeyError):
                log.warning("Skipping malformed event: %s", event)
                continue

            delta = (event_dt - now).total_seconds()
            if 240 <= delta <= 360:  # 4–6 min window catches the 5-min mark
                channel = self.bot.get_channel(self.alerts_channel_id)
                if channel:
                    time_str = event_dt.strftime("%-I:%M %p")
                    await channel.send(
                        f"@everyone\n5 MINUTES UNTIL: {event['title']} at {time_str}"
                    )
                    log.info(
                        "Alert sent for event '%s' at %s (user=%s)",
                        event["title"], time_str, event.get("user", "unknown"),
                    )
                self.storage.mark_alerted(event["uid"])

    @_check_alerts.before_loop
    async def _before_check(self) -> None:
        await self.bot.wait_until_ready()

    # -----------------------------------------------------------------------
    # Commands
    # -----------------------------------------------------------------------

    @commands.command(name="stopalerts")
    async def stop_alerts(self, ctx: commands.Context) -> None:
        """Disable @everyone alert sending."""
        self._alerts_enabled = False
        log.info("Command 'stopalerts' by %s — alerts disabled", ctx.author)
        await ctx.send("Alerts disabled.")

    @commands.command(name="enablealerts")
    async def enable_alerts(self, ctx: commands.Context) -> None:
        """Enable @everyone alert sending."""
        self._alerts_enabled = True
        log.info("Command 'enablealerts' by %s — alerts enabled", ctx.author)
        await ctx.send("Alerts enabled.")
