"""
Command argument parser for the !add schedule command.

Parses the argument string from:
    !add [<date>] <time> <title>

into a ParsedSchedule ready for anchor_events().
"""

from __future__ import annotations

import datetime
import logging

from .parser import ParsedEvent, ParsedSchedule

log = logging.getLogger("homebot.schedule")

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class CommandParseError(ValueError):
    """Raised when !add arguments cannot be parsed."""


# ---------------------------------------------------------------------------
# Day name lookup
# ---------------------------------------------------------------------------

_DAY_ORDER = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"
]

_DAY_NAMES_LOWER: dict[str, str] = {d.lower(): d for d in _DAY_ORDER}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _try_parse_date(
    token: str,
    today: datetime.date,
) -> tuple[datetime.date, str] | None:
    """
    Try to interpret *token* as a date.

    Accepted formats:
      - MM/DD         → resolves to current year, or next year if date already passed
      - day name      → nearest upcoming occurrence of that weekday (today inclusive)

    Returns (date, day_name) on success, or None if the token is not a date.
    """
    # --- MM/DD ---
    if "/" in token:
        parts = token.split("/")
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            month, day = int(parts[0]), int(parts[1])
            for year in (today.year, today.year + 1):
                try:
                    candidate = datetime.date(year, month, day)
                except ValueError:
                    continue
                if candidate >= today:
                    return candidate, _DAY_ORDER[candidate.weekday()]
            log.debug("_try_parse_date: MM/DD token %r produced no valid future date", token)
            return None

    # --- Day name ---
    lower = token.lower()
    if lower in _DAY_NAMES_LOWER:
        day_name = _DAY_NAMES_LOWER[lower]
        day_index = _DAY_ORDER.index(day_name)
        days_ahead = (day_index - today.weekday()) % 7
        return today + datetime.timedelta(days=days_ahead), day_name

    return None


def _parse_time(tokens: list[str], pos: int) -> tuple[str, int]:
    """
    Parse a time token at *tokens[pos]*.

    Accepted formats (am/pm must be attached — no space):
      24-hour : HHMM | HH:MM  (e.g. 0900, 09:00, 930)
      12-hour : HHMMam/pm | HH:MMam/pm  (e.g. 900am, 9:00pm, 1224pm)

    Returns (HH:MM in 24-hr, new_pos) where new_pos = pos + 1.
    Raises CommandParseError on unrecognised or out-of-range input.
    """
    if pos >= len(tokens):
        raise CommandParseError("Expected a time but ran out of arguments.")

    raw = tokens[pos]
    lower = raw.lower()

    # Detect and strip attached am/pm suffix
    am_pm: str | None = None
    if lower.endswith("am") or lower.endswith("pm"):
        am_pm = lower[-2:]
        raw = raw[:-2]

    # Normalise separator
    digits = raw.replace(":", "")

    # Pad 3-digit times (e.g. 930 → 0930)
    if len(digits) == 3 and digits.isdigit():
        digits = "0" + digits

    if len(digits) != 4 or not digits.isdigit():
        raise CommandParseError(
            f"Could not parse time {tokens[pos]!r}. "
            f"Use formats like 0900, 09:00, 9:00am, 930pm."
        )

    hours = int(digits[:2])
    minutes = int(digits[2:])

    # Apply 12-hr → 24-hr conversion
    if am_pm == "am":
        if hours == 12:
            hours = 0
    elif am_pm == "pm":
        if hours != 12:
            hours += 12

    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        raise CommandParseError(
            f"Time {tokens[pos]!r} is out of range (hours 0-23, minutes 0-59)."
        )

    return f"{hours:02d}:{minutes:02d}", pos + 1


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_add_args(
    args: str,
    reference_dt: datetime.datetime | None = None,
) -> ParsedSchedule:
    """
    Parse the argument string from an ``!add`` command.

    Grammar::

        [<date>] <time> <title>

    date  : MM/DD  |  day-name (monday … sunday)  |  <absent → today>
    time  : HHMM | HH:MM  (24-hr)  |  HHMMam/pm | HH:MMam/pm  (12-hr, suffix attached)
    title : everything remaining after the time token (required)

    Returns a :class:`ParsedSchedule` with ``has_explicit_dates=True`` and a
    single event whose ``date_string`` is already set to ``YYYY-MM-DD``, so
    :func:`anchor_events` can use the explicit-date path directly.

    Raises :class:`CommandParseError` for any unrecognisable input.
    """
    if reference_dt is None:
        reference_dt = datetime.datetime.now()

    today = reference_dt.date()
    tokens = args.split()

    if not tokens:
        raise CommandParseError(
            "No arguments provided. Usage: `!add [date] <time> <title>`"
        )

    pos = 0

    # --- Optional date token ---
    date_result = _try_parse_date(tokens[0], today)
    if date_result is not None:
        date, day_name = date_result
        log.debug("parse_add_args: date token %r → %s (%s)", tokens[0], date, day_name)
        pos = 1
    else:
        date = today
        day_name = _DAY_ORDER[today.weekday()]
        log.debug("parse_add_args: no date token — defaulting to today %s (%s)", date, day_name)

    # --- Required time token ---
    time_str, pos = _parse_time(tokens, pos)
    log.debug("parse_add_args: time token → %s", time_str)

    # --- Required title (everything remaining) ---
    title = " ".join(tokens[pos:]).strip()
    if not title:
        raise CommandParseError(
            "A title is required after the time. "
            "Usage: `!add [date] <time> <title>`"
        )

    log.debug("parse_add_args: title=%r date=%s time=%s", title, date, time_str)

    event: ParsedEvent = {
        "day_name": day_name,
        "date_string": date.isoformat(),
        "title": title,
        "start_time": time_str,
        "end_time": None,
    }

    return ParsedSchedule(
        has_explicit_dates=True,
        events=[event],
    )
