"""Unit tests for cogs/bills/storage.py (BillsStorage)"""

from __future__ import annotations

from pathlib import Path

import pytest

from util.storage.factory import StorageFactory, StorageManager
from cogs.bills.storage import BillsStorage


# ---------------------------------------------------------------------------
# Fixture: create an isolated BillsStorage instance per test
# ---------------------------------------------------------------------------

@pytest.fixture()
def store(tmp_path) -> BillsStorage:
    """Return a BillsStorage instance backed by a temp file."""
    factory = StorageFactory()
    factory.register(
        "bills", BillsStorage,
        path=tmp_path / "bills.json",
        collection_key="bills",
    )
    return StorageManager(factory).get("bills")


# Convenience aliases matching the old module-level function names
# so the test bodies stay as close to the original as possible.

def load_bills(s): return s.load_bills()
def get_bill(s, key): return s.get_bill(key)
def list_bills(s): return s.list_bills()
def save_bill(s, key, data): return s.save_bill(key, data)
def remove_bill(s, key): return s.remove_bill(key)
def bill_key(s, *args): return s.bill_key(*args)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_load_bills_missing_file(store):
    """Returns empty dict when data file does not exist."""
    result = load_bills(store)
    assert result == {}


def test_save_and_load_bill(store):
    """Round-trip: save a bill, load it back."""
    key = "NJ_S1234"
    data = {"bill_number": "S1234", "state": "NJ", "title": "A test bill", "status": "Introduced"}

    save_bill(store, key, data)
    loaded = load_bills(store)

    assert key in loaded
    assert loaded[key]["title"] == "A test bill"


def test_save_bill_upsert(store):
    """Saving the same key twice updates the record without creating a duplicate."""
    key = "NJ_S1234"
    save_bill(store, key, {"bill_number": "S1234", "status": "Introduced"})
    save_bill(store, key, {"status": "Engrossed", "last_action": "Passed Senate"})

    all_bills = load_bills(store)
    assert len(all_bills) == 1
    assert all_bills[key]["status"] == "Engrossed"
    assert all_bills[key]["last_action"] == "Passed Senate"
    # Original field preserved
    assert all_bills[key]["bill_number"] == "S1234"


def test_get_bill_returns_none_when_missing(store):
    """get_bill returns None for a key that was never saved."""
    assert get_bill(store, "NJ_S9999") is None


def test_remove_bill(store):
    """remove_bill removes the entry; get_bill returns None afterward."""
    key = "NJ_A100"
    save_bill(store, key, {"bill_number": "A100"})
    assert get_bill(store, key) is not None

    remove_bill(store, key)
    assert get_bill(store, key) is None


def test_list_bills(store):
    """list_bills returns a flat list of all tracked bill dicts."""
    save_bill(store, "NJ_S1", {"bill_number": "S1", "title": "First"})
    save_bill(store, "NJ_S2", {"bill_number": "S2", "title": "Second"})

    bills = list_bills(store)
    assert len(bills) == 2
    numbers = {b["bill_number"] for b in bills}
    assert numbers == {"S1", "S2"}


def test_bill_key_normalisation(store):
    """bill_key returns uppercase state and bill number."""
    assert bill_key(store, "s1234", "nj") == "NJ_S1234"
    assert bill_key(store, "A567") == "NJ_A567"


def test_remove_nonexistent_bill_is_noop(store):
    """Removing a bill that doesn't exist should not raise."""
    remove_bill(store, "NJ_DOESNOTEXIST")  # must not raise
