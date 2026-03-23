"""
Persistent calendar storage backed by a JSON file.

Stores AnchoredEvents with an `alerted` flag so the bot can resume
correctly after a restart without re-alerting already-sent events.
"""

from __future__ import annotations

import datetime
import json
import os
from pathlib import Path
from typing import Any

from cogs.schedule.ics_builder import _uid
from cogs.schedule.parser import AnchoredEvent

DATA_PATH = Path("data/calendar.json")

# ---------------------------------------------------------------------------
# User resolution
# ---------------------------------------------------------------------------

def resolve_user(discord_user_id: int, fallback_name: str) -> str:
    """Map a Discord user ID to a display name using env vars."""
    max_id = os.environ.get("USR_MAX_DISCORD_ID", "")
    wil_id = os.environ.get("USR_WIL_DISCORD_ID", "")
    if max_id and str(discord_user_id) == max_id:
        return "Maxilia"
    if wil_id and str(discord_user_id) == wil_id:
        return "Wilmond"
    return fallback_name


# ---------------------------------------------------------------------------
# Read / write helpers
# ---------------------------------------------------------------------------

def _read() -> dict[str, Any]:
    if not DATA_PATH.exists():
        return {"events": []}
    with DATA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write(data: dict[str, Any]) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DATA_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_events() -> list[dict]:
    """Return all stored events."""
    return _read().get("events", [])


def save_events(anchored_events: list[AnchoredEvent], user: str) -> None:
    """
    Merge new events into storage by UID.

    Existing events (matched by UID) are not overwritten — their `alerted`
    state is preserved. New events are appended with `alerted: False`.
    """
    data = _read()
    existing: dict[str, dict] = {ev["uid"]: ev for ev in data["events"]}

    for ev in anchored_events:
        uid = _uid(ev)
        if uid not in existing:
            existing[uid] = {
                "uid": uid,
                "date": ev["date"].isoformat(),
                "day_name": ev.get("day_name", ""),
                "title": ev.get("title", "Event"),
                "start_time": ev.get("start_time", "00:00"),
                "end_time": ev.get("end_time"),
                "user": user,
                "alerted": False,
            }

    data["events"] = list(existing.values())
    _write(data)


def mark_alerted(uid: str) -> None:
    """Set alerted=True for the event with the given UID."""
    data = _read()
    for ev in data["events"]:
        if ev["uid"] == uid:
            ev["alerted"] = True
            break
    _write(data)


def prune_old_events() -> None:
    """Remove events whose date is strictly before today."""
    today = datetime.date.today().isoformat()
    data = _read()
    data["events"] = [ev for ev in data["events"] if ev["date"] >= today]
    _write(data)
