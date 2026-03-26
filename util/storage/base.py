"""
Abstract base class for all storage backends.

Subclasses must implement _read() and _write().  The generic CRUD methods
(get, save, remove, list_all) are built on top of those two primitives and
are available to every concrete storage class automatically.

All methods emit structured DEBUG logs via the 'homebot.storage' logger,
including the object's memory address (hex(id(self))) so behaviour can be
traced at the hardware/object level.
"""

from __future__ import annotations

import logging
import sys
from abc import ABC, abstractmethod
from typing import Any

log = logging.getLogger("homebot.storage")


class BaseStorage(ABC):
    """Abstract storage backend.  Subclass and implement _read / _write."""

    # ------------------------------------------------------------------
    # Abstract primitives — must be overridden by concrete subclasses
    # ------------------------------------------------------------------

    @abstractmethod
    def _read(self) -> dict[str, Any]:
        """Load the full persisted document and return it as a dict."""

    @abstractmethod
    def _write(self, data: dict[str, Any]) -> None:
        """Persist the full document dict."""

    # ------------------------------------------------------------------
    # Generic CRUD — built on _read / _write
    # ------------------------------------------------------------------

    def get(self, key: str) -> dict | None:
        """Return the record stored under *key*, or None if absent."""
        data = self._read()
        collection = data.get(self._collection_key, {})
        result = collection.get(key)
        log.debug(
            "[%s] %s.get key=%r → %s (collection_size=%d, obj_bytes=%d)",
            hex(id(self)), type(self).__name__, key,
            "hit" if result is not None else "miss",
            len(collection), sys.getsizeof(result or {}),
        )
        return result

    def save(self, key: str, data: dict) -> None:
        """Upsert *data* under *key*, merging into any existing record."""
        store = self._read()
        collection = store.setdefault(self._collection_key, {})
        existing = collection.get(key, {})
        existing.update(data)
        collection[key] = existing
        log.debug(
            "[%s] %s.save key=%r obj_bytes=%d total_keys=%d",
            hex(id(self)), type(self).__name__, key,
            sys.getsizeof(data), len(collection),
        )
        self._write(store)

    def remove(self, key: str) -> None:
        """Delete the record for *key*.  No-op if not present."""
        store = self._read()
        removed = store.get(self._collection_key, {}).pop(key, None)
        log.debug(
            "[%s] %s.remove key=%r found=%s",
            hex(id(self)), type(self).__name__, key, removed is not None,
        )
        self._write(store)

    def list_all(self) -> list[dict]:
        """Return all records as a flat list."""
        store = self._read()
        collection = store.get(self._collection_key, {})
        records = list(collection.values())
        log.debug(
            "[%s] %s.list_all count=%d",
            hex(id(self)), type(self).__name__, len(records),
        )
        return records
