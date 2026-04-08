"""
Scheduler configuration reader for the MBT poll task.

Reads configs/scheduler_config.ini (or a custom path) to determine which
clock times the poll task should fire on a given day.  The config file is
re-read on every call to get_scheduled_times() so changes take effect without
restarting the bot.

Config format:

    [scheduler_configs]
    modes=weekday,weekend
    timezone=America/New_York

    [mode_weekday]
    days=0,1,2,3,4       # Python weekday() — 0=Mon, 6=Sun
    times=0800,1000,1700  # 24-hour HHMM, no colon

    [mode_weekend]
    days=5,6
    times=1700
"""

from __future__ import annotations

import configparser
import datetime
import logging
import zoneinfo
from pathlib import Path

log = logging.getLogger("homebot.scheduler")

_DEFAULT_CONFIG = Path("configs/scheduler_config.ini")
_DEFAULT_TZ = "America/New_York"


class SchedulerConfig:
    """Hot-reloadable INI-based schedule for the MBT poll task."""

    def __init__(self, config_path: str | Path = _DEFAULT_CONFIG) -> None:
        self._path = Path(config_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def timezone(self) -> zoneinfo.ZoneInfo:
        """Return the configured timezone (re-read from INI each call)."""
        cfg = self._read()
        tz_name = cfg.get("scheduler_configs", "timezone", fallback=_DEFAULT_TZ)
        try:
            return zoneinfo.ZoneInfo(tz_name)
        except zoneinfo.ZoneInfoNotFoundError:
            log.warning("Unknown timezone %r in scheduler config — using %s", tz_name, _DEFAULT_TZ)
            return zoneinfo.ZoneInfo(_DEFAULT_TZ)

    def get_scheduled_times(self) -> list[datetime.time]:
        """
        Re-read the INI and return today's scheduled poll times (sorted).

        Returns an empty list if no modes match today or the config is missing.
        """
        cfg = self._read()

        tz_name = cfg.get("scheduler_configs", "timezone", fallback=_DEFAULT_TZ)
        try:
            tz = zoneinfo.ZoneInfo(tz_name)
        except zoneinfo.ZoneInfoNotFoundError:
            log.warning("Unknown timezone %r — using %s", tz_name, _DEFAULT_TZ)
            tz = zoneinfo.ZoneInfo(_DEFAULT_TZ)

        today_weekday = datetime.datetime.now(tz=tz).weekday()

        modes_raw = cfg.get("scheduler_configs", "modes", fallback="")
        modes = [m.strip() for m in modes_raw.split(",") if m.strip()]

        times: set[datetime.time] = set()
        for mode in modes:
            section = f"mode_{mode}"
            if not cfg.has_section(section):
                log.debug("Scheduler: section [%s] not found — skipping", section)
                continue

            days_raw = cfg.get(section, "days", fallback="")
            try:
                days = {int(d.strip()) for d in days_raw.split(",") if d.strip().isdigit()}
            except ValueError:
                log.warning("Scheduler: invalid days value in [%s]: %r", section, days_raw)
                continue

            if today_weekday not in days:
                continue

            times_raw = cfg.get(section, "times", fallback="")
            for t_str in times_raw.split(","):
                t_str = t_str.strip()
                if len(t_str) == 4 and t_str.isdigit():
                    hour, minute = int(t_str[:2]), int(t_str[2:])
                    if 0 <= hour <= 23 and 0 <= minute <= 59:
                        times.add(datetime.time(hour, minute))
                    else:
                        log.warning("Scheduler: out-of-range time %r in [%s] — skipped", t_str, section)
                elif t_str:
                    log.warning("Scheduler: unrecognised time format %r in [%s] — skipped", t_str, section)

        result = sorted(times)
        log.debug(
            "Scheduler: today=weekday%d modes=%s → %d scheduled time(s): %s",
            today_weekday, modes, len(result),
            [t.strftime("%H%M") for t in result],
        )
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _read(self) -> configparser.ConfigParser:
        cfg = configparser.ConfigParser()
        if not self._path.exists():
            log.warning("Scheduler config not found at %s — no times will be scheduled", self._path)
            return cfg
        cfg.read(self._path, encoding="utf-8")
        return cfg
