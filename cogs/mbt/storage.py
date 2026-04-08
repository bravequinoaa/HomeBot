"""
Bill storage — JSON persistence via the shared util/storage framework.

Inherits read/write mechanics and generic CRUD from JsonFileStorage.
Adds bills-domain methods that the BillsCog depends on.

Data lives in data/bills.json:
{
  "bills": {
    "NJ_S1234": { ...bill dict... }
  }
}
"""

from __future__ import annotations

import logging

from util.storage.json_storage import JsonFileStorage

log = logging.getLogger("homebot.storage")


class BillsStorage(JsonFileStorage):
    """JSON-backed storage for tracked NJ Legislature bills."""

    # Expose the parent sentinel so StorageFactory can construct subclasses.
    _FACTORY_TOKEN = JsonFileStorage._FACTORY_TOKEN

    # ------------------------------------------------------------------
    # Domain helpers
    # ------------------------------------------------------------------

    def bill_key(self, bill_number: str, state: str = "NJ") -> str:
        """Canonical storage key for a bill, e.g. 'NJ_S1234'."""
        key = f"{state.upper()}_{bill_number.upper()}"
        log.debug(
            "[%s] BillsStorage.bill_key bill_number=%r state=%r → %r",
            hex(id(self)), bill_number, state, key,
        )
        return key

    # ------------------------------------------------------------------
    # Domain CRUD — thin wrappers over BaseStorage generics
    # ------------------------------------------------------------------

    def get_bill(self, key: str) -> dict | None:
        """Return a single bill dict by key, or None if not found."""
        result = self.get(key)
        log.debug(
            "[%s] BillsStorage.get_bill key=%r found=%s",
            hex(id(self)), key, result is not None,
        )
        return result

    def save_bill(self, key: str, data: dict) -> None:
        """Upsert a bill record by key.  Merges into existing data if present."""
        log.debug(
            "[%s] BillsStorage.save_bill key=%r bill_number=%r",
            hex(id(self)), key, data.get("bill_number"),
        )
        self.save(key, data)

    def remove_bill(self, key: str) -> None:
        """Remove a bill from tracking.  No-op if not found."""
        log.debug("[%s] BillsStorage.remove_bill key=%r", hex(id(self)), key)
        self.remove(key)

    def list_bills(self) -> list[dict]:
        """Return all tracked bills as a flat list."""
        bills = self.list_all()
        log.debug(
            "[%s] BillsStorage.list_bills count=%d", hex(id(self)), len(bills),
        )
        return bills

    def load_bills(self) -> dict[str, dict]:
        """Return all tracked bills keyed by 'NJ_S1234'."""
        store = self._read()
        return store.get(self._collection_key, {})
