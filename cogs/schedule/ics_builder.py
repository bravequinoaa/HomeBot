"""
Build a .ics calendar file from a list of AnchoredEvents.

Returns the filename and raw bytes ready to send as a Discord attachment.
Filename format: {STARTDATE}-{ENDDATE}.ics  e.g. 2026-03-23-2026-03-29.ics
"""

from __future__ import annotations

import datetime
import hashlib

import pytz
from icalendar import Calendar, Event

from .parser import AnchoredEvent

EASTERN = pytz.timezone("America/New_York")


def build_ics(events: list[AnchoredEvent]) -> tuple[str, bytes]:
    """
    Convert a list of AnchoredEvents into a .ics file.

    Returns
    -------
    (filename, ics_bytes)
        filename follows the {STARTDATE}-{ENDDATE}.ics convention.
    """
    cal = Calendar()
    cal.add("prodid", "-//HomeBot//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", "Weekly Schedule")
    cal.add("x-wr-timezone", "America/New_York")

    valid_events = [ev for ev in events if ev.get("start_time")]
    for ev in valid_events:
        cal.add_component(_build_vevent(ev))

    ics_bytes: bytes = cal.to_ical()

    # Determine date range for filename using only events that were included
    dates = [ev["date"] for ev in valid_events]
    start_date = min(dates)
    end_date = max(dates)
    filename = f"{start_date.strftime('%m%d%y')}-{end_date.strftime('%m%d%y')}.ics"

    return filename, ics_bytes


def _build_vevent(ev: AnchoredEvent) -> Event:
    vevent = Event()
    vevent.add("summary", ev["title"])
    vevent.add("uid", _uid(ev))

    start_h, start_m = _parse_hhmm(ev["start_time"])
    start_naive = datetime.datetime(
        ev["date"].year, ev["date"].month, ev["date"].day, start_h, start_m
    )
    # Always use .localize() — never .replace(tzinfo=...) — for correct DST handling
    start_aware = EASTERN.localize(start_naive)
    vevent.add("dtstart", start_aware)

    if ev["end_time"]:
        end_h, end_m = _parse_hhmm(ev["end_time"])
        end_naive = datetime.datetime(
            ev["date"].year, ev["date"].month, ev["date"].day, end_h, end_m
        )
        # Handle overnight events (end time is earlier than start time)
        if end_naive <= start_naive:
            end_naive += datetime.timedelta(days=1)
        end_aware = EASTERN.localize(end_naive)
    else:
        # Default to 1-hour duration when no end time is given
        end_aware = start_aware + datetime.timedelta(hours=1)

    vevent.add("dtend", end_aware)
    vevent.add("dtstamp", datetime.datetime.now(tz=pytz.utc))

    return vevent


def _parse_hhmm(time_str: str) -> tuple[int, int]:
    """Parse "HH:MM" into (hour, minute) integers."""
    parts = time_str.split(":")
    return int(parts[0]), int(parts[1])


def _uid(ev: AnchoredEvent) -> str:
    """Generate a stable unique ID for a calendar event."""
    raw = f"{ev['date'].isoformat()}-{ev['start_time']}-{ev['title']}"
    digest = hashlib.md5(raw.encode()).hexdigest()
    return f"{digest}@homebot"
