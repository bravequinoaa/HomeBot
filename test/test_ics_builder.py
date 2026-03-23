"""
Unit tests for cogs/schedule/ics_builder.py

Tests cover:
  - build_ics: filename convention, output is valid .ics bytes
  - _build_vevent: timezone, overnight events, missing end_time
  - Null start_time is skipped (not crashed on)
"""

import datetime
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from icalendar import Calendar

from cogs.schedule.ics_builder import _build_vevent, _parse_hhmm, _uid, build_ics
from cogs.schedule.parser import AnchoredEvent

import pytz

EASTERN = pytz.timezone("America/New_York")


def _event(
    date: datetime.date,
    title: str,
    start: str,
    end: str | None,
    day_name: str = "Monday",
) -> AnchoredEvent:
    return AnchoredEvent(date=date, day_name=day_name, title=title, start_time=start, end_time=end)


# ---------------------------------------------------------------------------
# _parse_hhmm
# ---------------------------------------------------------------------------


def test_parse_hhmm_basic():
    assert _parse_hhmm("09:00") == (9, 0)
    assert _parse_hhmm("00:00") == (0, 0)
    assert _parse_hhmm("23:30") == (23, 30)
    assert _parse_hhmm("06:30") == (6, 30)


# ---------------------------------------------------------------------------
# _uid
# ---------------------------------------------------------------------------


def test_uid_is_stable():
    ev = _event(datetime.date(2026, 3, 23), "Work", "09:00", "17:00")
    assert _uid(ev) == _uid(ev)


def test_uid_differs_for_different_events():
    ev1 = _event(datetime.date(2026, 3, 23), "Work", "09:00", "17:00")
    ev2 = _event(datetime.date(2026, 3, 23), "Lunch", "12:00", "13:00")
    assert _uid(ev1) != _uid(ev2)


def test_uid_ends_with_homebot():
    ev = _event(datetime.date(2026, 3, 23), "Work", "09:00", "17:00")
    assert _uid(ev).endswith("@homebot")


# ---------------------------------------------------------------------------
# _build_vevent
# ---------------------------------------------------------------------------


def test_build_vevent_summary():
    ev = _event(datetime.date(2026, 3, 23), "Gym", "07:00", "08:00")
    vevent = _build_vevent(ev)
    assert str(vevent.get("summary")) == "Gym"


def test_build_vevent_start_is_eastern():
    ev = _event(datetime.date(2026, 3, 23), "Work", "09:00", "17:00")
    vevent = _build_vevent(ev)
    dtstart = vevent.get("dtstart").dt
    assert dtstart.tzinfo is not None
    # Should be UTC-4 (EDT) on 2026-03-23
    offset = dtstart.utcoffset()
    assert offset == datetime.timedelta(hours=-4)


def test_build_vevent_no_end_time_defaults_one_hour():
    ev = _event(datetime.date(2026, 3, 23), "Standup", "10:00", None)
    vevent = _build_vevent(ev)
    dtstart = vevent.get("dtstart").dt
    dtend = vevent.get("dtend").dt
    assert dtend - dtstart == datetime.timedelta(hours=1)


def test_build_vevent_overnight_event():
    """An event ending before it starts should span midnight."""
    ev = _event(datetime.date(2026, 3, 23), "Night Shift", "22:00", "06:00")
    vevent = _build_vevent(ev)
    dtstart = vevent.get("dtstart").dt
    dtend = vevent.get("dtend").dt
    assert dtend > dtstart
    assert dtend.date() == datetime.date(2026, 3, 24)


# ---------------------------------------------------------------------------
# build_ics
# ---------------------------------------------------------------------------

_EVENTS = [
    _event(datetime.date(2026, 3, 23), "Work", "09:00", "17:00", "Monday"),
    _event(datetime.date(2026, 3, 24), "Gym", "07:00", "08:00", "Tuesday"),
    _event(datetime.date(2026, 3, 27), "Lunch", "12:00", "13:00", "Friday"),
]


def test_build_ics_filename_convention():
    filename, _ = build_ics(_EVENTS)
    assert filename == "032326-032726.ics"


def test_build_ics_returns_valid_ics_bytes():
    _, ics_bytes = build_ics(_EVENTS)
    assert isinstance(ics_bytes, bytes)
    # Should be parseable by icalendar
    cal = Calendar.from_ical(ics_bytes)
    vevents = [c for c in cal.walk() if c.name == "VEVENT"]
    assert len(vevents) == len(_EVENTS)


def test_build_ics_event_titles():
    _, ics_bytes = build_ics(_EVENTS)
    cal = Calendar.from_ical(ics_bytes)
    titles = {str(c.get("summary")) for c in cal.walk() if c.name == "VEVENT"}
    assert titles == {"Work", "Gym", "Lunch"}


def test_build_ics_skips_null_start_time():
    """Events with no start_time should be silently skipped, not crash."""
    events = [
        _event(datetime.date(2026, 3, 23), "Work", "09:00", "17:00"),
        AnchoredEvent(
            date=datetime.date(2026, 3, 24),
            day_name="Tuesday",
            title="Bad Event",
            start_time=None,  # type: ignore[arg-type]
            end_time=None,
        ),
    ]
    filename, ics_bytes = build_ics(events)
    cal = Calendar.from_ical(ics_bytes)
    vevents = [c for c in cal.walk() if c.name == "VEVENT"]
    assert len(vevents) == 1  # only the good event
    assert filename == "032326-032326.ics"


def test_build_ics_two_day_filename():
    events = [
        _event(datetime.date(2026, 3, 23), "Work", "09:00", "17:00", "Monday"),
        _event(datetime.date(2026, 3, 25), "Gym", "07:00", "08:00", "Wednesday"),
    ]
    filename, _ = build_ics(events)
    assert filename == "032326-032526.ics"
