"""
Unit tests for cogs/schedule/command_parser.py

Tests cover:
  - _try_parse_date: MM/DD, day names, unrecognised tokens
  - _parse_time: all valid formats (24-hr, 12-hr attached), edge cases, errors
  - parse_add_args: full command integration, error paths
"""

import datetime
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cogs.schedule.command_parser import (
    CommandParseError,
    _try_parse_date,
    _parse_time,
    parse_add_args,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

# 2026-04-07 is a Tuesday
_TODAY = datetime.date(2026, 4, 7)
_TODAY_DT = datetime.datetime(2026, 4, 7, 10, 0, 0)


# ---------------------------------------------------------------------------
# _try_parse_date
# ---------------------------------------------------------------------------


def test_date_mm_dd_future():
    """MM/DD that hasn't passed yet resolves to the current year."""
    result = _try_parse_date("4/10", _TODAY)
    assert result is not None
    date, day_name = result
    assert date == datetime.date(2026, 4, 10)
    assert day_name == "Friday"


def test_date_mm_dd_today():
    """MM/DD matching today resolves to today."""
    result = _try_parse_date("4/7", _TODAY)
    assert result is not None
    date, _ = result
    assert date == _TODAY


def test_date_mm_dd_already_passed_rolls_to_next_year():
    """MM/DD that already passed this year rolls to next year."""
    result = _try_parse_date("1/1", _TODAY)  # Jan 1 is before April 7
    assert result is not None
    date, _ = result
    assert date == datetime.date(2027, 1, 1)


def test_date_mm_dd_invalid_day():
    """MM/DD with an impossible day (e.g. 2/30) returns None."""
    result = _try_parse_date("2/30", _TODAY)
    assert result is None


def test_date_day_name_same_as_today():
    """Day name matching today → today (0 days ahead)."""
    # _TODAY is Tuesday
    result = _try_parse_date("tuesday", _TODAY)
    assert result is not None
    date, day_name = result
    assert date == _TODAY
    assert day_name == "Tuesday"


def test_date_day_name_case_insensitive():
    result = _try_parse_date("WEDNESDAY", _TODAY)
    assert result is not None
    date, day_name = result
    assert day_name == "Wednesday"
    assert date == datetime.date(2026, 4, 8)  # next day after Tuesday


def test_date_day_name_upcoming():
    """Day name for a future weekday resolves to next occurrence."""
    # _TODAY is Tuesday; next Monday is 2026-04-13
    result = _try_parse_date("monday", _TODAY)
    assert result is not None
    date, day_name = result
    assert day_name == "Monday"
    assert date == datetime.date(2026, 4, 13)


def test_date_day_name_sunday():
    result = _try_parse_date("sunday", _TODAY)
    assert result is not None
    date, _ = result
    assert date == datetime.date(2026, 4, 12)


def test_date_unrecognised_token():
    """A plain number or word is not a date."""
    assert _try_parse_date("0900", _TODAY) is None
    assert _try_parse_date("meeting", _TODAY) is None
    assert _try_parse_date("14:30", _TODAY) is None


# ---------------------------------------------------------------------------
# _parse_time
# ---------------------------------------------------------------------------


def _tokens(*args: str) -> list[str]:
    return list(args)


def test_time_24hr_hhmm():
    time_str, new_pos = _parse_time(_tokens("0900", "title"), 0)
    assert time_str == "09:00"
    assert new_pos == 1


def test_time_24hr_hhmm_with_colon():
    time_str, new_pos = _parse_time(_tokens("09:00", "title"), 0)
    assert time_str == "09:00"
    assert new_pos == 1


def test_time_24hr_three_digits_padded():
    """3-digit time like 930 is padded to 0930 → 09:30."""
    time_str, _ = _parse_time(_tokens("930"), 0)
    assert time_str == "09:30"


def test_time_12hr_am_attached():
    time_str, new_pos = _parse_time(_tokens("900am", "title"), 0)
    assert time_str == "09:00"
    assert new_pos == 1


def test_time_12hr_am_with_colon():
    time_str, _ = _parse_time(_tokens("9:00am"), 0)
    assert time_str == "09:00"


def test_time_12hr_pm_attached():
    time_str, _ = _parse_time(_tokens("9:00pm"), 0)
    assert time_str == "21:00"


def test_time_12hr_1224pm():
    time_str, _ = _parse_time(_tokens("1224pm"), 0)
    assert time_str == "12:24"


def test_time_12hr_colon_pm():
    time_str, _ = _parse_time(_tokens("12:24pm"), 0)
    assert time_str == "12:24"


def test_time_noon_pm():
    """1200pm → 12:00 (noon, not 24:00)."""
    time_str, _ = _parse_time(_tokens("1200pm"), 0)
    assert time_str == "12:00"


def test_time_midnight_am():
    """1200am → 00:00 (midnight)."""
    time_str, _ = _parse_time(_tokens("1200am"), 0)
    assert time_str == "00:00"


def test_time_edge_2359():
    time_str, _ = _parse_time(_tokens("2359"), 0)
    assert time_str == "23:59"


def test_time_edge_0000():
    time_str, _ = _parse_time(_tokens("0000"), 0)
    assert time_str == "00:00"


def test_time_detached_am_not_consumed():
    """'900 am' — the 'am' is a separate token and is NOT consumed as a modifier.
    The time '900' is parsed as 09:00 in 24-hr mode; 'am' stays as title."""
    time_str, new_pos = _parse_time(_tokens("900", "am", "standup"), 0)
    assert time_str == "09:00"
    assert new_pos == 1  # only the time token consumed


def test_time_invalid_text():
    with pytest.raises(CommandParseError):
        _parse_time(_tokens("abc"), 0)


def test_time_out_of_range_hours():
    with pytest.raises(CommandParseError):
        _parse_time(_tokens("2500"), 0)


def test_time_out_of_range_minutes():
    with pytest.raises(CommandParseError):
        _parse_time(_tokens("0960"), 0)


def test_time_empty_tokens():
    with pytest.raises(CommandParseError):
        _parse_time([], 0)


# ---------------------------------------------------------------------------
# parse_add_args — integration
# ---------------------------------------------------------------------------


def test_parse_time_only_and_title():
    """No date → defaults to today."""
    result = parse_add_args("0900 Team Meeting", reference_dt=_TODAY_DT)
    assert result["has_explicit_dates"] is True
    events = result["events"]
    assert len(events) == 1
    ev = events[0]
    assert ev["date_string"] == "2026-04-07"
    assert ev["start_time"] == "09:00"
    assert ev["title"] == "Team Meeting"
    assert ev["end_time"] is None


def test_parse_day_name_date():
    """Day name → resolves to upcoming occurrence."""
    result = parse_add_args("wednesday 14:30 Gym", reference_dt=_TODAY_DT)
    ev = result["events"][0]
    assert ev["date_string"] == "2026-04-08"  # next Wednesday from Tuesday
    assert ev["start_time"] == "14:30"
    assert ev["title"] == "Gym"


def test_parse_mm_dd_date():
    result = parse_add_args("4/15 0800 Doctor", reference_dt=_TODAY_DT)
    ev = result["events"][0]
    assert ev["date_string"] == "2026-04-15"
    assert ev["start_time"] == "08:00"
    assert ev["title"] == "Doctor"


def test_parse_12hr_pm_attached():
    result = parse_add_args("9:00pm Long Title With Spaces", reference_dt=_TODAY_DT)
    ev = result["events"][0]
    assert ev["start_time"] == "21:00"
    assert ev["title"] == "Long Title With Spaces"


def test_parse_12hr_am_no_date():
    result = parse_add_args("1000am Stand-up", reference_dt=_TODAY_DT)
    ev = result["events"][0]
    assert ev["start_time"] == "10:00"
    assert ev["title"] == "Stand-up"


def test_parse_day_name_sets_correct_day_name_field():
    result = parse_add_args("friday 0900 Weekly Sync", reference_dt=_TODAY_DT)
    ev = result["events"][0]
    assert ev["day_name"] == "Friday"
    assert ev["date_string"] == "2026-04-10"


def test_parse_no_date_day_name_is_today():
    """When no date token, day_name should match today."""
    result = parse_add_args("1200 Lunch", reference_dt=_TODAY_DT)
    ev = result["events"][0]
    assert ev["day_name"] == "Tuesday"  # _TODAY is Tuesday


def test_parse_title_with_multiple_spaces_normalized():
    """Title tokens are joined with single spaces."""
    result = parse_add_args("0900 some   title", reference_dt=_TODAY_DT)
    # split() already handles multiple spaces
    ev = result["events"][0]
    assert ev["title"] == "some title"


def test_parse_error_empty_string():
    with pytest.raises(CommandParseError):
        parse_add_args("", reference_dt=_TODAY_DT)


def test_parse_error_whitespace_only():
    with pytest.raises(CommandParseError):
        parse_add_args("   ", reference_dt=_TODAY_DT)


def test_parse_error_time_only_no_title():
    with pytest.raises(CommandParseError):
        parse_add_args("0900", reference_dt=_TODAY_DT)


def test_parse_error_date_and_time_no_title():
    with pytest.raises(CommandParseError):
        parse_add_args("monday 0900", reference_dt=_TODAY_DT)


def test_parse_error_bad_time():
    with pytest.raises(CommandParseError):
        parse_add_args("notadate notatime something", reference_dt=_TODAY_DT)
