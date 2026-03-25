"""Unit tests for cogs/bills/storage.py"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import cogs.bills.storage as storage_module
from cogs.bills.storage import (
    bill_key,
    get_bill,
    list_bills,
    load_bills,
    remove_bill,
    save_bill,
)


# ---------------------------------------------------------------------------
# Fixture: redirect DATA_PATH to a temp directory for each test
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolate_storage(tmp_path, monkeypatch):
    """Point storage module at a temp file so tests don't touch real data."""
    fake_path = tmp_path / "bills.json"
    monkeypatch.setattr(storage_module, "DATA_PATH", fake_path)
    yield fake_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_load_bills_missing_file():
    """Returns empty dict when data file does not exist."""
    result = load_bills()
    assert result == {}


def test_save_and_load_bill():
    """Round-trip: save a bill, load it back."""
    key = "NJ_S1234"
    data = {"bill_number": "S1234", "state": "NJ", "title": "A test bill", "status": "Introduced"}

    save_bill(key, data)
    loaded = load_bills()

    assert key in loaded
    assert loaded[key]["title"] == "A test bill"


def test_save_bill_upsert():
    """Saving the same key twice updates the record without creating a duplicate."""
    key = "NJ_S1234"
    save_bill(key, {"bill_number": "S1234", "status": "Introduced"})
    save_bill(key, {"status": "Engrossed", "last_action": "Passed Senate"})

    all_bills = load_bills()
    assert len(all_bills) == 1
    assert all_bills[key]["status"] == "Engrossed"
    assert all_bills[key]["last_action"] == "Passed Senate"
    # Original field preserved
    assert all_bills[key]["bill_number"] == "S1234"


def test_get_bill_returns_none_when_missing():
    """get_bill returns None for a key that was never saved."""
    assert get_bill("NJ_S9999") is None


def test_remove_bill():
    """remove_bill removes the entry; get_bill returns None afterward."""
    key = "NJ_A100"
    save_bill(key, {"bill_number": "A100"})
    assert get_bill(key) is not None

    remove_bill(key)
    assert get_bill(key) is None


def test_list_bills():
    """list_bills returns a flat list of all tracked bill dicts."""
    save_bill("NJ_S1", {"bill_number": "S1", "title": "First"})
    save_bill("NJ_S2", {"bill_number": "S2", "title": "Second"})

    bills = list_bills()
    assert len(bills) == 2
    numbers = {b["bill_number"] for b in bills}
    assert numbers == {"S1", "S2"}


def test_bill_key_normalisation():
    """bill_key returns uppercase state and bill number."""
    assert bill_key("s1234", "nj") == "NJ_S1234"
    assert bill_key("A567") == "NJ_A567"


def test_remove_nonexistent_bill_is_noop():
    """Removing a bill that doesn't exist should not raise."""
    remove_bill("NJ_DOESNOTEXIST")  # must not raise
