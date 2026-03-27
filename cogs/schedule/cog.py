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

from .ics_builder import build_ics
from .parser import (
    EmptyScheduleError,
    ScheduleParseError,
    _IMAGE_MIME_TYPES,
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
    
    @commands.command(name="add")
    async def add_schedule(self, ctx: commands.Context, content: str) -> None:
        # call on_message 
        await self.on_message(ctx.message, add_flag=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message, add_flag: bool = False) -> None:
        if message.author.bot:
            return

        # Only process messages in the designated schedule upload channel
        if message.channel.id != self.upload_channel_id:
            return

        attachment = _find_supported_attachment(message)
        if not add_flag and attachment is None:
            log.info('new message but no file attached -- ignoring')
            return

        # Size guard
        if not add_flag or attachment.size > MAX_FILE_BYTES:
            await message.reply("File must be under 20 MB.")
            return

        await message.add_reaction("⏳")

        if attachment:
            await self._get_attachment_response(message, attachment)
        
        if add_flag:
            await self._get_add_response(message)
        
        log.info('shouldnt be here - no attachment and no add_flag')
        await message.reply('uhhhh')

    async def _get_attachment_response(self, message, attachment):
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

    async def _get_add_response(self, message):
        ''' 
            Handle requests containing text 

            format should follow by default:
            !add <date> <time> <event>, <date2> <time2> <event2>, etc.

            if not in format then send to claude agent to parse.        
        '''
        try:
            content = message.content
            if not self._check_message_follows_format(content):
                log.info(f'message does not follow ')
            


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
