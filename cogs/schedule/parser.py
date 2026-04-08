"""
Schedule parsing via Claude API.

Takes raw file bytes (image or PDF) and returns a list of AnchoredEvents
with concrete dates ready for .ics generation.
"""

from __future__ import annotations

import base64
import datetime
import json
import logging
import logging.handlers
import os
import re
from typing import TypedDict

import anthropic

# ---------------------------------------------------------------------------
# Claude response logger
# Writes raw Claude JSON responses to LOGS_DIR/claude_responses.log
# LOGS_DIR defaults to "logs" for local dev; set to /apps/homebot/logs in prod.
# ---------------------------------------------------------------------------

from pathlib import Path as _Path
_logs_dir = _Path(os.environ.get("LOGS_DIR", "logs"))
_logs_dir.mkdir(parents=True, exist_ok=True)

_claude_log = logging.getLogger("claude_responses")
_claude_log.setLevel(logging.DEBUG)
_claude_log.propagate = False  # don't bubble up to the root logger

_claude_handler = logging.handlers.RotatingFileHandler(
    _logs_dir / "claude_responses.log",
    maxBytes= 10 * 1024 * 1024,  # 10 MB per file
    backupCount=3,
    encoding="utf-8",
)
_claude_handler.setFormatter(
    logging.Formatter("%(asctime)s\n%(message)s\n" + "-" * 80, datefmt="%Y-%m-%d %H:%M:%S")
)
_claude_log.addHandler(_claude_handler)

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ScheduleParseError(Exception):
    """Claude returned invalid or unparseable output."""


class EmptyScheduleError(Exception):
    """Claude returned zero events."""


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class ParsedEvent(TypedDict):
    day_name: str           # "Monday" – "Sunday"
    date_string: str | None  # "YYYY-MM-DD" or None
    title: str
    start_time: str         # "HH:MM" 24-hour
    end_time: str | None    # "HH:MM" 24-hour or None


class ParsedSchedule(TypedDict):
    has_explicit_dates: bool
    events: list[ParsedEvent]


class AnchoredEvent(TypedDict):
    date: datetime.date
    day_name: str
    title: str
    start_time: str         # "HH:MM" 24-hour
    end_time: str | None    # "HH:MM" 24-hour or None


# ---------------------------------------------------------------------------
# Claude prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are a schedule extraction engine. "
    "Your only output is valid JSON — no explanation, no markdown fences, "
    "no prose before or after the JSON object."
)

_USER_PROMPT = """\
Extract all scheduled events from this schedule. The schedule may cover any date range — \
a few days, a week, multiple weeks, or a full month.

Return a JSON object with this exact schema:
{
  "has_explicit_dates": true | false,
  "events": [
    {
      "day_name": "Monday" | "Tuesday" | "Wednesday" | "Thursday" | "Friday" | "Saturday" | "Sunday",
      "date_string": "YYYY-MM-DD or null if no explicit date can be determined",
      "title": "short event label",
      "start_time": "HH:MM in 24-hour format",
      "end_time": "HH:MM in 24-hour format or null if no end time"
    }
  ]
}

Rules:
- Set has_explicit_dates to true if any actual calendar dates appear ANYWHERE in the document —
  including page titles, column headers, row labels, or printed dates next to day names
  (e.g. "Week of March 23", "Mon 3/24", "March 24", "3/24/26").
- When has_explicit_dates is true, populate date_string for EVERY event using the date shown
  in the column header, row label, or section title that the event falls under. Do not leave
  date_string null for any event if a date can be inferred from its position on the page.
- Convert all dates to YYYY-MM-DD format (e.g. "March 24, 2026" -> "2026-03-24").
  If the year is not shown, assume the current or next upcoming year.
- Normalize ALL times to 24-hour HH:MM format.
  Examples: "630" -> "06:30", "6:30am" -> "06:30", "10pm" -> "22:00",
  "11:30pm" -> "23:30", "noon" -> "12:00", "midnight" -> "00:00"
- If a range like "630-10" appears, interpret as start 06:30 end 10:00.
- Use context to disambiguate AM/PM (e.g. a block following morning events is AM).
- If an event has no end time, set end_time to null.
- Produce one entry per event per day. If the same day name appears multiple times
  (e.g. two Mondays across two weeks), emit a separate event entry for each occurrence
  with the correct date_string.
- Keep the title short and descriptive. Prefix it with a single relevant emoji
  (e.g. "💼 Work", "🍽️ Lunch", "📋 Meeting", "🏋️ Gym", "😴 Sleep", "🚗 Commute",
  "📚 Study", "🛒 Errands", "👨‍⚕️ Doctor", "🎉 Event"). If the schedule already has
  an emoji, keep it. If none fits well, omit the emoji rather than forcing one.
- Include every event visible; make your best guess for anything unclear.
- Output only the JSON object. Nothing else."""

# ---------------------------------------------------------------------------
# Claude API call
# ---------------------------------------------------------------------------

_MODEL = "claude-sonnet-4-6"

# Supported image MIME types
_IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _strip_fences(raw: str) -> str:
    """Remove accidental ```json ... ``` fences Claude sometimes adds."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.MULTILINE)
    raw = re.sub(r"\s*```$", "", raw, flags=re.MULTILINE)
    return raw.strip()


async def parse_schedule(
    file_bytes: bytes,
    file_type: str,
    media_type: str = "image/jpeg",
) -> list[AnchoredEvent]:
    """
    Call the Claude API with the provided file bytes and return AnchoredEvents.

    Parameters
    ----------
    file_bytes:
        Raw bytes of the uploaded file.
    file_type:
        Either "image" or "pdf".
    media_type:
        MIME type for images (e.g. "image/png"). Ignored for PDFs.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY is not set in the environment.")

    encoded = base64.standard_b64encode(file_bytes).decode("utf-8")

    if file_type == "pdf":
        client = anthropic.AsyncAnthropic(
            api_key=api_key,
            default_headers={"anthropic-beta": "pdfs-2024-09-25"},
        )
        content_block: dict = {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": encoded,
            },
        }
    else:
        client = anthropic.AsyncAnthropic(api_key=api_key)
        content_block = {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": encoded,
            },
        }

    response = await client.messages.create(
        model=_MODEL,
        max_tokens=4096 * 2,
        system=_SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    content_block,
                    {"type": "text", "text": _USER_PROMPT},
                ],
            }
        ],
    )

    raw_text = response.content[0].text if response.content else ""
    _claude_log.debug("file_type=%s\n%s", file_type, raw_text)

    try:
        data: ParsedSchedule = json.loads(_strip_fences(raw_text))
    except (json.JSONDecodeError, ValueError) as exc:
        raise ScheduleParseError(
            f"Claude returned non-JSON output: {raw_text[:200]}"
        ) from exc

    events = data.get("events", [])
    if not events:
        raise EmptyScheduleError("No events were found in the schedule.")

    return anchor_events(data)


# ---------------------------------------------------------------------------
# Date anchoring
# ---------------------------------------------------------------------------

_DAY_ORDER = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
]


def anchor_events(
    parsed: ParsedSchedule,
    reference_dt: datetime.datetime | None = None,
) -> list[AnchoredEvent]:
    """
    Convert ParsedSchedule events to AnchoredEvents with concrete dates.

    If the schedule has explicit dates, parse them directly.
    Otherwise anchor to the upcoming Monday from reference_dt (default: today).
    """
    if reference_dt is None:
        reference_dt = datetime.datetime.now()

    today = reference_dt.date()
    anchored: list[AnchoredEvent] = []

    if parsed.get("has_explicit_dates"):
        for ev in parsed["events"]:
            ds = ev.get("date_string")
            if ds:
                try:
                    date = datetime.date.fromisoformat(ds)
                except ValueError:
                    date = _fallback_date(ev.get("day_name", "Monday"), today)
            else:
                date = _fallback_date(ev.get("day_name", "Monday"), today)

            anchored.append(_make_anchored(ev, date))
    else:
        # Anchor to the upcoming Monday (or today if today is Monday).
        # Track how many times each day name has appeared so that the 2nd
        # occurrence of "Monday" is pushed to the following week, the 3rd
        # to the week after that, etc. — supporting multi-week schedules.
        days_ahead = (7 - today.weekday()) % 7  # 0 when today is Monday
        anchor_monday = today + datetime.timedelta(days=days_ahead)
        seen: dict[str, int] = {}  # day_name -> occurrence count (0-based)

        for ev in parsed["events"]:
            day_name = ev.get("day_name", "Monday")
            try:
                day_index = _DAY_ORDER.index(day_name)
            except ValueError:
                day_index = 0

            occurrence = seen.get(day_name, 0)
            seen[day_name] = occurrence + 1

            week_offset = datetime.timedelta(weeks=occurrence)
            date = anchor_monday + datetime.timedelta(days=day_index) + week_offset
            anchored.append(_make_anchored(ev, date))

    return anchored


def _fallback_date(day_name: str, today: datetime.date) -> datetime.date:
    """Anchor a single day name to the upcoming week."""
    days_ahead = (7 - today.weekday()) % 7
    anchor_monday = today + datetime.timedelta(days=days_ahead)
    try:
        day_index = _DAY_ORDER.index(day_name)
    except ValueError:
        day_index = 0
    return anchor_monday + datetime.timedelta(days=day_index)


def _make_anchored(ev: ParsedEvent, date: datetime.date) -> AnchoredEvent:
    # Use `or` so that an explicit null from Claude falls back to the default
    # just as a missing key would.
    return AnchoredEvent(
        date=date,
        day_name=ev.get("day_name") or "",
        title=ev.get("title") or "Event",
        start_time=ev.get("start_time") or "00:00",
        end_time=ev.get("end_time") or None,
    )
