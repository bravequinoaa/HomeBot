"""
Schedule Cog — Discord event listener for the schedule-to-ICS feature.

Listens for messages with image or PDF attachments and replies with a
.ics calendar file named {STARTDATE}-{ENDDATE}.ics.
"""

from __future__ import annotations

import datetime
import io
import logging
import os
from pathlib import Path

import anthropic
import discord
from discord.ext import commands

from .command_parser import CommandParseError, parse_add_args
from .ics_builder import build_ics
from .parser import (
    EmptyScheduleError,
    ScheduleParseError,
    _IMAGE_MIME_TYPES,
    anchor_events,
    parse_schedule,
)

log = logging.getLogger("homebot.schedule")

MAX_FILE_BYTES = 20 * 1024 * 1024  # 20 MB

# Mapping of file extension → file_type ("image" or "pdf")
_SUPPORTED: dict[str, str] = {
    ".jpg": "image",
    ".jpeg": "image",
    ".png": "image",
    ".gif": "image",
    ".webp": "image",
    ".pdf": "pdf",
}

class ScheduleCog(commands.Cog):
    """Converts schedule images/PDFs to .ics calendar files."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        raw = os.environ.get("CH_CALENDAR_UPLOAD_ID")
        if not raw:
            raise RuntimeError("CH_CALENDAR_UPLOAD_ID is not set in the environment.")
        try:
            self.upload_channel_id = int(raw)
        except ValueError:
            raise RuntimeError(f"CH_CALENDAR_UPLOAD_ID must be an integer, got: {raw!r}")

    async def cog_check(self, ctx: commands.Context) -> bool:
        """Restrict all prefix commands in this cog to the upload channel."""
        return ctx.channel.id == self.upload_channel_id

    @commands.command(name="add")
    async def add_event(self, ctx: commands.Context, *, args: str = "") -> None:
        """Add a single calendar event: !add [date] <time> <title>

        date  : MM/DD | day-name (e.g. wednesday) | omit for today
        time  : 0900 | 09:00 | 9:00am | 930pm  (am/pm must be attached)
        title : event label (everything after the time)
        """
        log.info("!add by %s#%s — args=%r", ctx.author.name, ctx.author.discriminator, args)

        if not args.strip():
            await ctx.reply(
                "**Usage:** `!add [date] <time> <title>`\n"
                "**date** — `MM/DD`, day name (e.g. `friday`), or omit for today\n"
                "**time** — `0900`, `09:00`, `9:00am`, `930pm` *(am/pm attached)*\n"
                "**title** — event label"
            )
            return

        try:
            parsed = parse_add_args(args, reference_dt=datetime.datetime.now())
        except CommandParseError as exc:
            log.debug("!add parse error from %s: %s", ctx.author, exc)
            await ctx.reply(f"Could not parse your command: {exc}")
            return

        try:
            anchored_events = anchor_events(parsed)
            filename, ics_bytes = build_ics(anchored_events)

            ics_dir = Path(os.environ.get("DATA_DIR", "data")) / "ics_exports"
            ics_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            export_path = ics_dir / f"{ts}_{filename}"
            export_path.write_bytes(ics_bytes)
            log.info(
                "!add succeeded — event=%r date=%s time=%s saved=%s",
                anchored_events[0]["title"],
                anchored_events[0]["date"],
                anchored_events[0]["start_time"],
                export_path,
            )

            await ctx.reply(file=discord.File(io.BytesIO(ics_bytes), filename=filename))
            self.bot.dispatch("schedule_parsed", anchored_events, ctx.author.id, ctx.author.name)

        except Exception:
            log.exception("!add unexpected error for args=%r", args)
            await ctx.reply("An unexpected error occurred. Please try again.")

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        # Only process messages in the designated schedule upload channel
        if message.channel.id != self.upload_channel_id:
            return

        attachment = _find_supported_attachment(message)
        if attachment is None:
            log.info('new message but no file attached -- ignoring')
            return

        # Size guard
        if attachment.size > MAX_FILE_BYTES:
            await message.reply("File must be under 20 MB.")
            return

        await message.add_reaction("⏳")

        try:
            file_bytes = await attachment.read()
            ext = os.path.splitext(attachment.filename)[1].lower()
            file_type = _SUPPORTED.get(ext, "image")
            media_type = _IMAGE_MIME_TYPES.get(ext, "image/jpeg")

            anchored_events = await parse_schedule(file_bytes, file_type, media_type)
            filename, ics_bytes = build_ics(anchored_events)

            # Persist ICS to disk so !refreshdb can restore calendar on a fresh deploy
            ics_dir = Path(os.environ.get("DATA_DIR", "data")) / "ics_exports"
            ics_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            export_path = ics_dir / f"{ts}_{filename}"
            export_path.write_bytes(ics_bytes)
            log.info("Saved ICS export to %s", export_path)

            await message.reply(
                file=discord.File(io.BytesIO(ics_bytes), filename=filename)
            )
            self.bot.dispatch("schedule_parsed", anchored_events, message.author.id, message.author.name)
            await message.remove_reaction("⏳", self.bot.user)
            await message.add_reaction("✅")

        except EmptyScheduleError:
            await _error(message, self.bot.user, "No events were found in that schedule.")
        except ScheduleParseError:
            log.exception("Failed to parse schedule from %s", attachment.filename)
            await _error(message, self.bot.user, "Could not extract a schedule from that file.")
        except anthropic.APITimeoutError:
            log.exception("Anthropic API timeout")
            await _error(message, self.bot.user, "The AI took too long to respond. Please try again.")
        except anthropic.RateLimitError:
            log.exception("Anthropic rate limit")
            await _error(message, self.bot.user, "Rate limited — please wait a moment and try again.")
        except Exception:
            log.exception("Unexpected error processing %s", attachment.filename)
            await _error(message, self.bot.user, "An unexpected error occurred. Please try again.")


def _find_supported_attachment(message: discord.Message) -> discord.Attachment | None:
    """Return the first supported attachment in a message, or None."""
    for attachment in message.attachments:
        ext = os.path.splitext(attachment.filename)[1].lower()
        content_type = (attachment.content_type or "").lower()

        # Accept by extension or by Discord-reported content type
        if ext in _SUPPORTED:
            return attachment
        if content_type.startswith("image/") or content_type == "application/pdf":
            return attachment

    return None


async def _error(
    message: discord.Message,
    bot_user: discord.ClientUser,
    text: str,
) -> None:
    """Remove the processing reaction, add ❌, and reply with an error message."""
    try:
        await message.remove_reaction("⏳", bot_user)
    except discord.HTTPException:
        pass
    await message.add_reaction("❌")
    await message.reply(text)
