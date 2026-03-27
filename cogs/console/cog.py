"""
Console Cog — operational messages and admin commands.

Sends lifecycle notices to CH_CONSOLE_ID and provides admin commands
that are restricted to that same channel.

Commands (prefix !):
  !log      — upload the last 50 lines of homebot.log as a text file
  !status   — embed with bot health, uptime, latency, and tracked-data counts

Environment variables:
  CH_CONSOLE_ID   Discord channel ID for console/admin output
"""

from __future__ import annotations

import datetime
import io
import logging
import os
import sys
from pathlib import Path

import discord
from discord.ext import commands

log = logging.getLogger("homebot.console")


class ConsoleCog(commands.Cog):
    """Sends bot lifecycle notices and provides admin/diagnostic commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._ready_at: datetime.datetime | None = None

        raw = os.environ.get("CH_CONSOLE_ID")
        if not raw:
            raise RuntimeError("CH_CONSOLE_ID is not set in the environment.")
        try:
            self.console_channel_id = int(raw)
        except ValueError:
            raise RuntimeError(f"CH_CONSOLE_ID must be an integer, got: {raw!r}")

        logs_dir = Path(os.environ.get("LOGS_DIR", "logs"))
        self.log_file = logs_dir / "homebot.log"

    # ------------------------------------------------------------------
    # Channel guard — all commands restricted to the console channel
    # ------------------------------------------------------------------

    async def cog_check(self, ctx: commands.Context) -> bool:
        if ctx.channel.id != self.console_channel_id:
            return False
        return True

    # ------------------------------------------------------------------
    # Lifecycle events
    # ------------------------------------------------------------------

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        self._ready_at = datetime.datetime.now(datetime.timezone.utc)
        log.info("ConsoleCog: bot ready — sending online notice")

        channel = self.bot.get_channel(self.console_channel_id)
        if not channel:
            log.warning("Console channel (CH_CONSOLE_ID=%d) not found", self.console_channel_id)
            return

        embed = discord.Embed(
            title="🟢  Bot Online",
            description=f"**{self.bot.user}** is now connected.",
            color=discord.Color.green(),
            timestamp=self._ready_at,
        )
        embed.add_field(name="Latency", value=f"{self.bot.latency * 1000:.1f} ms")
        embed.add_field(name="Guilds", value=str(len(self.bot.guilds)))
        embed.set_footer(text=f"discord.py {discord.__version__}  •  Python {sys.version.split()[0]}")
        await channel.send(embed=embed)

    # ------------------------------------------------------------------
    # Commands
    # ------------------------------------------------------------------

    @commands.command(name="log")
    async def show_log(self, ctx: commands.Context) -> None:
        """Upload the last 50 lines of homebot.log."""
        log.info("Command 'log' by %s", ctx.author)

        if not self.log_file.exists():
            await ctx.send(f"Log file not found: `{self.log_file}`")
            return

        try:
            lines = self.log_file.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            await ctx.send(f"Could not read log file: {exc}")
            return

        tail = "\n".join(lines[-50:]) if lines else "(empty)"
        file_bytes = tail.encode("utf-8")

        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        await ctx.send(
            content=f"Last **{min(len(lines), 50)}** lines of `homebot.log`:",
            file=discord.File(io.BytesIO(file_bytes), filename=f"homebot_{timestamp}.log"),
        )

    @commands.command(name="status")
    async def show_status(self, ctx: commands.Context) -> None:
        """Show bot health, uptime, and tracked-data summary."""
        log.info("Command 'status' by %s", ctx.author)

        now = datetime.datetime.now(datetime.timezone.utc)
        uptime_str = _fmt_uptime(now - self._ready_at) if self._ready_at else "Unknown"

        # Gather counts from storage if available
        bills_count = 0
        calendar_count = 0
        try:
            bills_storage = self.bot.storage_manager.get("bills")
            bills_count = len(bills_storage.list_bills())
        except Exception:
            pass
        try:
            cal_storage = self.bot.storage_manager.get("calendar")
            calendar_count = len(cal_storage.list_events())
        except Exception:
            pass

        # Log file size
        log_size_str = "N/A"
        if self.log_file.exists():
            size_kb = self.log_file.stat().st_size / 1024
            log_size_str = f"{size_kb:.1f} KB"

        # Bills poll interval (read from the bills cog if loaded)
        poll_info = "N/A"
        bills_cog = self.bot.cogs.get("BillsCog")
        if bills_cog and hasattr(bills_cog, "_poll"):
            task = bills_cog._poll
            interval = task.hours if hasattr(task, "hours") else "?"
            next_iter = task.next_iteration
            if next_iter:
                delta = next_iter.replace(tzinfo=datetime.timezone.utc) - now
                mins = int(delta.total_seconds() // 60)
                poll_info = f"every {bills_cog._poll.hours}h — next in {mins}m"
            else:
                poll_info = f"every {interval}h"

        embed = discord.Embed(
            title="📊  HomeBot Status",
            color=discord.Color.blue(),
            timestamp=now,
        )
        embed.add_field(name="Bot", value=str(self.bot.user), inline=False)
        embed.add_field(name="Uptime", value=uptime_str)
        embed.add_field(name="Latency", value=f"{self.bot.latency * 1000:.1f} ms")
        embed.add_field(name="Guilds", value=str(len(self.bot.guilds)))
        embed.add_field(name="Tracked Bills", value=str(bills_count))
        embed.add_field(name="Calendar Events", value=str(calendar_count))
        embed.add_field(name="Bills Poll", value=poll_info, inline=False)
        embed.add_field(name="Log File", value=log_size_str)
        await ctx.send(embed=embed)

    @commands.command(name="stat-schedule")
    async def stat_schedule(self, ctx: commands.Context) -> None:
        """Show calendar event count and the next 5 upcoming events."""
        log.info("Command 'stat-schedule' by %s", ctx.author)

        try:
            cal_storage = self.bot.storage_manager.get("calendar")
            all_events: list[dict] = cal_storage.load_events()
        except Exception as exc:
            await ctx.send(f"Could not read calendar storage: {exc}")
            return

        today = datetime.date.today().isoformat()
        upcoming = sorted(
            (ev for ev in all_events if ev.get("date", "") >= today),
            key=lambda ev: (ev.get("date", ""), ev.get("start_time", "")),
        )

        header = (
            "╔════════════════════════╗\n"
            "   📋 Schedule Stats\n"
            "╚════════════════════════╝"
        )

        lines = [
            header,
            f"**Total count:** {len(all_events)}",
            "",
            "**Upcoming** 🗓️",
            "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄",
        ]

        if not upcoming:
            lines.append("*No upcoming events.*")
        else:
            for ev in upcoming[:5]:
                date_str = _fmt_event_date(ev.get("date", ""), ev.get("day_name", ""))
                time_str = ev.get("start_time", "—")
                title    = ev.get("title", "—")
                user     = ev.get("user", "—")
                lines.append(f"\n🌸 **{date_str}** ✦ *{time_str}*")
                lines.append(f"> 📌 {title}")
                lines.append(f"> 👤 for {user}")

        await ctx.send("\n".join(lines))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_event_date(iso_date: str, day_name: str) -> str:
    """Return a human-friendly date string, e.g. 'Thursday, March 26'."""
    try:
        d = datetime.date.fromisoformat(iso_date)
        day = day_name or d.strftime("%A")
        return f"{day}, {d.strftime('%B %-d')}"
    except ValueError:
        return iso_date


def _fmt_uptime(delta: datetime.timedelta) -> str:
    total = int(delta.total_seconds())
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)
