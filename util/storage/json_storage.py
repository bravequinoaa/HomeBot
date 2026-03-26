"""
JSON-file-backed storage implementation.

JsonFileStorage reads and writes a single JSON document to disk.  It is the
standard concrete backend for HomeBot — one instance per JSON file.

Constructor access is intentionally restricted: only StorageFactory may
create instances by passing the _FACTORY_TOKEN sentinel.  Calling
JsonFileStorage(...) directly (without the token) raises AssertionError.

  Why sentinels instead of a private __init__?
  Python has no true private constructors.  A module-level sentinel object
  lets us enforce "factory only" at runtime: the object is defined once,
  lives at a fixed memory address, and cannot be recreated from outside the
  module.  Subclasses re-expose the same sentinel via class attribute
  inheritance so StorageFactory works uniformly for all subclasses.

Disk layout (one document per file):
  {
    "<collection_key>": {
      "<record_key>": { ...record... },   # dict-of-dicts (bills)
      ...
    }
  }
  OR
  {
    "<collection_key>": [ ...records... ] # list (events)
  }
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from .base import BaseStorage

log = logging.getLogger("homebot.storage")


class JsonFileStorage(BaseStorage):
    """Concrete storage backend that persists data to a single JSON file."""

    # Sentinel object — StorageFactory passes this as the first argument.
    # Any code that tries to construct JsonFileStorage (or a subclass)
    # without going through the factory will hit the assertion below.
    _FACTORY_TOKEN = object()

    def __init__(self, token: object, path: Path, collection_key: str) -> None:
        assert token is JsonFileStorage._FACTORY_TOKEN, (
            f"{type(self).__name__} must be created via StorageFactory, "
            "not instantiated directly."
        )
        self._path = path
        self._collection_key = collection_key
        log.debug(
            "[%s] %s.__init__ path=%s collection_key=%r addr=0x%x",
            hex(id(self)), type(self).__name__,
            self._path, self._collection_key, id(self),
        )

    # ------------------------------------------------------------------
    # BaseStorage primitives
    # ------------------------------------------------------------------

    def _read(self) -> dict[str, Any]:
        if not self._path.exists():
            log.debug(
                "[%s] %s._read path=%s → file missing, returning empty",
                hex(id(self)), type(self).__name__, self._path,
            )
            return {}
        with self._path.open("r", encoding="utf-8") as fh:
            raw = fh.read()
        data = json.loads(raw)
        log.debug(
            "[%s] %s._read path=%s bytes=%d obj_bytes=%d top_keys=%s",
            hex(id(self)), type(self).__name__, self._path,
            len(raw.encode()), sys.getsizeof(data), list(data.keys()),
        )
        return data

    def _write(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        serialised = json.dumps(data, indent=2, default=str)
        with self._path.open("w", encoding="utf-8") as fh:
            fh.write(serialised)
        log.debug(
            "[%s] %s._write path=%s bytes=%d obj_bytes=%d",
            hex(id(self)), type(self).__name__, self._path,
            len(serialised.encode()), sys.getsizeof(data),
        )
