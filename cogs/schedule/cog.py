"""
Schedule Cog — Discord event listener for the schedule-to-ICS feature.

Listens for messages with image or PDF attachments and replies with a
.ics calendar file named {STARTDATE}-{ENDDATE}.ics.
"""

from __future__ import annotations

import io
import logging
import os

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

CH_CALENDAR_UPLOAD_ID = str(os.environ.get('CH_CALENDAR_UPLOAD_ID'))


class ScheduleCog(commands.Cog):
    """Converts schedule images/PDFs to .ics calendar files."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        
        # Verify that the message is coming from the upload channel
        if message.channel != CH_CALENDAR_UPLOAD_ID:
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

            await message.reply(
                file=discord.File(io.BytesIO(ics_bytes), filename=filename)
            )
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
