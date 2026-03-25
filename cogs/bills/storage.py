"""
Bill storage — pure JSON persistence, no network or Discord imports.

To swap to MySQL later, replace this module with one that implements the
same public interface with identical function signatures.

Data lives in data/bills.json:
{
  "bills": {
    "NJ_S1234": { ...bill dict... }
  }
}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA_PATH = Path("data/bills.json")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read() -> dict[str, Any]:
    if not DATA_PATH.exists():
        return {"bills": {}}
    with DATA_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _write(data: dict[str, Any]) -> None:
    DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DATA_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def _make_key(bill_number: str, state: str = "NJ") -> str:
    return f"{state.upper()}_{bill_number.upper()}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_bills() -> dict[str, dict]:
    """Return all tracked bills keyed by 'NJ_S1234'."""
    return _read().get("bills", {})


def get_bill(key: str) -> dict | None:
    """Return a single bill dict by key, or None if not found."""
    return _read().get("bills", {}).get(key)


def list_bills() -> list[dict]:
    """Return all tracked bills as a flat list."""
    return list(_read().get("bills", {}).values())


def save_bill(key: str, data: dict) -> None:
    """Upsert a bill record by key.  Merges into existing data if present."""
    store = _read()
    bills = store.setdefault("bills", {})
    existing = bills.get(key, {})
    existing.update(data)
    bills[key] = existing
    _write(store)


def remove_bill(key: str) -> None:
    """Remove a bill from tracking.  No-op if not found."""
    store = _read()
    store.get("bills", {}).pop(key, None)
    _write(store)


def bill_key(bill_number: str, state: str = "NJ") -> str:
    """Canonical storage key for a bill, e.g. 'NJ_S1234'."""
    return _make_key(bill_number, state)
