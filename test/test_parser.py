"""
Unit tests for cogs/schedule/parser.py

Tests cover:
  - _make_anchored: null/missing fields from Claude
  - anchor_events: day-name anchoring and explicit-date anchoring
  - _strip_fences: markdown fence removal
"""

import datetime
import sys
import os

# Allow running from the project root or the test/ directory
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cogs.schedule.parser import (
    _DAY_ORDER,
    _make_anchored,
    _strip_fences,
    anchor_events,
)


# ---------------------------------------------------------------------------
# _strip_fences
# ---------------------------------------------------------------------------


def test_strip_fences_no_fences():
    raw = '{"has_explicit_dates": false, "events": []}'
    assert _strip_fences(raw) == raw


def test_strip_fences_json_block():
    raw = "```json\n{}\n```"
    assert _strip_fences(raw) == "{}"


def test_strip_fences_plain_block():
    raw = "```\n{}\n```"
    assert _strip_fences(raw) == "{}"


def test_strip_fences_extra_whitespace():
    raw = "  ```json\n  {}\n  ```  "
    assert _strip_fences(raw).strip() == "{}"


# ---------------------------------------------------------------------------
# _make_anchored — null / missing fields
# ---------------------------------------------------------------------------

_DATE = datetime.date(2026, 3, 23)


def test_make_anchored_all_present():
    ev = {
        "day_name": "Monday",
        "date_string": None,
        "title": "Work",
        "start_time": "09:00",
        "end_time": "17:00",
    }
    result = _make_anchored(ev, _DATE)
    assert result["start_time"] == "09:00"
    assert result["end_time"] == "17:00"
    assert result["title"] == "Work"


def test_make_anchored_null_start_time():
    """Claude sent null for start_time — should default to 00:00."""
    ev = {
        "day_name": "Tuesday",
        "date_string": None,
        "title": "Meeting",
        "start_time": None,
        "end_time": "10:00",
    }
    result = _make_anchored(ev, _DATE)
    assert result["start_time"] == "00:00"


def test_make_anchored_missing_start_time():
    """start_time key is absent entirely."""
    ev = {"day_name": "Wednesday", "title": "Lunch", "end_time": None}
    result = _make_anchored(ev, _DATE)
    assert result["start_time"] == "00:00"


def test_make_anchored_null_end_time():
    ev = {
        "day_name": "Thursday",
        "title": "Gym",
        "start_time": "07:00",
        "end_time": None,
    }
    result = _make_anchored(ev, _DATE)
    assert result["end_time"] is None


def test_make_anchored_null_title():
    ev = {"day_name": "Friday", "title": None, "start_time": "08:00", "end_time": None}
    result = _make_anchored(ev, _DATE)
    assert result["title"] == "Event"


def test_make_anchored_date_preserved():
    ev = {"day_name": "Monday", "title": "Work", "start_time": "09:00", "end_time": None}
    result = _make_anchored(ev, _DATE)
    assert result["date"] == _DATE


# ---------------------------------------------------------------------------
# anchor_events — day-name mode
# ---------------------------------------------------------------------------

# Reference: 2026-03-23 is a Monday
_MONDAY_REF = datetime.datetime(2026, 3, 23)
# Reference: 2026-03-25 is a Wednesday
_WEDNESDAY_REF = datetime.datetime(2026, 3, 25)


def _make_schedule(day_names: list[str], has_explicit_dates: bool = False) -> dict:
    return {
        "has_explicit_dates": has_explicit_dates,
        "events": [
            {
                "day_name": d,
                "date_string": None,
                "title": d,
                "start_time": "09:00",
                "end_time": "10:00",
            }
            for d in day_names
        ],
    }


def test_anchor_day_names_from_monday():
    """Uploaded on a Monday — anchor to that same Monday."""
    schedule = _make_schedule(["Monday", "Wednesday", "Friday"])
    result = anchor_events(schedule, reference_dt=_MONDAY_REF)

    dates = {ev["day_name"]: ev["date"] for ev in result}
    assert dates["Monday"] == datetime.date(2026, 3, 23)
    assert dates["Wednesday"] == datetime.date(2026, 3, 25)
    assert dates["Friday"] == datetime.date(2026, 3, 27)


def test_anchor_day_names_from_wednesday():
    """Uploaded on a Wednesday — anchor to the upcoming Monday (2026-03-30)."""
    schedule = _make_schedule(["Monday", "Tuesday", "Sunday"])
    result = anchor_events(schedule, reference_dt=_WEDNESDAY_REF)

    dates = {ev["day_name"]: ev["date"] for ev in result}
    assert dates["Monday"] == datetime.date(2026, 3, 30)
    assert dates["Tuesday"] == datetime.date(2026, 3, 31)
    assert dates["Sunday"] == datetime.date(2026, 4, 5)


def test_anchor_full_week():
    """All seven days anchor to the correct offsets."""
    schedule = _make_schedule(_DAY_ORDER)
    result = anchor_events(schedule, reference_dt=_MONDAY_REF)
    for i, ev in enumerate(result):
        expected = datetime.date(2026, 3, 23) + datetime.timedelta(days=i)
        assert ev["date"] == expected, f"{ev['day_name']} should be {expected}"


def test_anchor_unknown_day_defaults_to_monday():
    schedule = {
        "has_explicit_dates": False,
        "events": [
            {"day_name": "Funday", "title": "Rest", "start_time": "10:00", "end_time": None}
        ],
    }
    result = anchor_events(schedule, reference_dt=_MONDAY_REF)
    assert result[0]["date"] == datetime.date(2026, 3, 23)


# ---------------------------------------------------------------------------
# anchor_events — explicit dates mode
# ---------------------------------------------------------------------------


def test_anchor_explicit_dates():
    schedule = {
        "has_explicit_dates": True,
        "events": [
            {
                "day_name": "Monday",
                "date_string": "2026-04-07",
                "title": "Work",
                "start_time": "09:00",
                "end_time": "17:00",
            },
            {
                "day_name": "Wednesday",
                "date_string": "2026-04-09",
                "title": "Gym",
                "start_time": "07:00",
                "end_time": "08:00",
            },
        ],
    }
    result = anchor_events(schedule, reference_dt=_MONDAY_REF)
    assert result[0]["date"] == datetime.date(2026, 4, 7)
    assert result[1]["date"] == datetime.date(2026, 4, 9)


def test_anchor_explicit_dates_bad_format_falls_back():
    """A malformed date_string should fall back to day-name anchoring."""
    schedule = {
        "has_explicit_dates": True,
        "events": [
            {
                "day_name": "Tuesday",
                "date_string": "not-a-date",
                "title": "Meeting",
                "start_time": "10:00",
                "end_time": None,
            }
        ],
    }
    result = anchor_events(schedule, reference_dt=_MONDAY_REF)
    # Fallback: Tuesday from anchor Monday 2026-03-23
    assert result[0]["date"] == datetime.date(2026, 3, 24)


def test_anchor_explicit_dates_null_date_string_falls_back():
    """Explicit-dates mode but date_string is null — fall back to day name."""
    schedule = {
        "has_explicit_dates": True,
        "events": [
            {
                "day_name": "Friday",
                "date_string": None,
                "title": "Lunch",
                "start_time": "12:00",
                "end_time": "13:00",
            }
        ],
    }
    result = anchor_events(schedule, reference_dt=_MONDAY_REF)
    assert result[0]["date"] == datetime.date(2026, 3, 27)
