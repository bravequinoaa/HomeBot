"""
Calendar event storage — JSON persistence via the shared util/storage framework.

Inherits read/write mechanics from JsonFileStorage and adds calendar-domain
methods the AlertsCog depends on.

Events are stored as a list (not a keyed dict) so the base class get/save/
remove/list_all helpers are not used directly here — the domain methods
operate directly on the list via _read/_write.

Data lives in data/calendar.json:
{
  "events": [
    { "uid": "...", "date": "...", "alerted": false, ... },
    ...
  ]
}
"""

from __future__ import annotations

import datetime
import logging
import os
import sys

from cogs.schedule.ics_builder import _uid
from cogs.schedule.parser import AnchoredEvent
from util.storage.json_storage import JsonFileStorage

log = logging.getLogger("homebot.storage")


class CalendarStorage(JsonFileStorage):
    """JSON-backed storage for calendar events and alert state."""

    _FACTORY_TOKEN = JsonFileStorage._FACTORY_TOKEN

    # ------------------------------------------------------------------
    # User resolution
    # ------------------------------------------------------------------

    def resolve_user(self, discord_user_id: int, fallback_name: str) -> str:
        """Map a Discord user ID to a display name using env vars."""
        max_id = os.environ.get("USR_MAX_DISCORD_ID", "")
        wil_id = os.environ.get("USR_WIL_DISCORD_ID", "")
        if max_id and str(discord_user_id) == max_id:
            name = "Maxilia"
        elif wil_id and str(discord_user_id) == wil_id:
            name = "Wilmond"
        else:
            name = fallback_name
        log.debug(
            "[%s] CalendarStorage.resolve_user discord_id=%d → %r",
            hex(id(self)), discord_user_id, name,
        )
        return name

    # ------------------------------------------------------------------
    # Event CRUD
    # ------------------------------------------------------------------

    def load_events(self) -> list[dict]:
        """Return all stored events."""
        data = self._read()
        events = data.get(self._collection_key, [])
        log.debug(
            "[%s] CalendarStorage.load_events count=%d obj_bytes=%d",
            hex(id(self)), len(events), sys.getsizeof(events),
        )
        return events

    def save_events(self, anchored_events: list[AnchoredEvent], user: str) -> None:
        """
        Merge new events into storage by UID.

        Existing events (matched by UID) are not overwritten — their
        `alerted` state is preserved.  New events are appended with
        alerted=False.
        """
        data = self._read()
        existing: dict[str, dict] = {
            ev["uid"]: ev for ev in data.get(self._collection_key, [])
        }
        added = 0
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
                added += 1
        data[self._collection_key] = list(existing.values())
        log.debug(
            "[%s] CalendarStorage.save_events user=%r new=%d total=%d obj_bytes=%d",
            hex(id(self)), user, added, len(existing), sys.getsizeof(data),
        )
        self._write(data)

    def mark_alerted(self, uid: str) -> None:
        """Set alerted=True for the event with the given UID."""
        data = self._read()
        found = False
        for ev in data.get(self._collection_key, []):
            if ev["uid"] == uid:
                ev["alerted"] = True
                found = True
                break
        log.debug(
            "[%s] CalendarStorage.mark_alerted uid=%r found=%s",
            hex(id(self)), uid, found,
        )
        self._write(data)

    def restore_events(self, events: list[dict]) -> None:
        """
        Write pre-formed event dicts directly to storage (used by !refreshdb).

        Each dict must have at minimum: uid, date, title, start_time.
        alerted is always set to False so restored events can re-alert if upcoming.
        Existing events with the same UID are not overwritten (idempotent).
        """
        data = self._read()
        existing: dict[str, dict] = {
            ev["uid"]: ev for ev in data.get(self._collection_key, [])
        }
        added = 0
        for ev in events:
            uid = ev.get("uid", "")
            if uid and uid not in existing:
                existing[uid] = {**ev, "alerted": False}
                added += 1
        data[self._collection_key] = list(existing.values())
        log.debug(
            "[%s] CalendarStorage.restore_events added=%d total=%d",
            hex(id(self)), added, len(existing),
        )
        self._write(data)

    def prune_old_events(self) -> None:
        """Remove events whose date is strictly before today."""
        today = datetime.date.today().isoformat()
        data = self._read()
        before = len(data.get(self._collection_key, []))
        data[self._collection_key] = [
            ev for ev in data.get(self._collection_key, [])
            if ev["date"] >= today
        ]
        after = len(data[self._collection_key])
        log.debug(
            "[%s] CalendarStorage.prune_old_events today=%s pruned=%d remaining=%d",
            hex(id(self)), today, before - after, after,
        )
        self._write(data)
