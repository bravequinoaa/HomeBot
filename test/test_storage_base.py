"""
Tests for the util/storage infrastructure:
  BaseStorage, JsonFileStorage, StorageFactory, StorageManager,
  and the domain classes BillsStorage + CalendarStorage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from util.storage.base import BaseStorage
from util.storage.json_storage import JsonFileStorage
from util.storage.factory import StorageFactory, StorageManager
from cogs.bills.storage import BillsStorage
from cogs.alerts.storage import CalendarStorage


# ---------------------------------------------------------------------------
# Helpers — create instances the legitimate way (via factory)
# ---------------------------------------------------------------------------

def _make_bills_storage(path: Path) -> BillsStorage:
    factory = StorageFactory()
    factory.register("bills", BillsStorage, path=path, collection_key="bills")
    return StorageManager(factory).get("bills")


def _make_calendar_storage(path: Path) -> CalendarStorage:
    factory = StorageFactory()
    factory.register("calendar", CalendarStorage, path=path, collection_key="events")
    return StorageManager(factory).get("calendar")


# ---------------------------------------------------------------------------
# BaseStorage — cannot instantiate directly
# ---------------------------------------------------------------------------

def test_base_storage_cannot_be_instantiated_directly():
    """BaseStorage is abstract; attempting to instantiate it raises TypeError."""
    with pytest.raises(TypeError):
        BaseStorage()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# JsonFileStorage — factory sentinel enforcement
# ---------------------------------------------------------------------------

def test_json_file_storage_cannot_be_constructed_without_token(tmp_path):
    """Constructing JsonFileStorage without the factory token raises AssertionError."""
    with pytest.raises(AssertionError, match="StorageFactory"):
        JsonFileStorage("wrong_token", tmp_path / "x.json", "items")


def test_json_file_storage_subclass_cannot_be_constructed_without_token(tmp_path):
    """BillsStorage (subclass) also rejects direct construction."""
    with pytest.raises(AssertionError, match="StorageFactory"):
        BillsStorage("wrong_token", tmp_path / "bills.json", "bills")


# ---------------------------------------------------------------------------
# JsonFileStorage — read / write behaviour
# ---------------------------------------------------------------------------

def test_json_file_storage_read_missing_file_returns_empty(tmp_path):
    store = _make_bills_storage(tmp_path / "bills.json")
    assert store._read() == {}


def test_json_file_storage_write_and_read_roundtrip(tmp_path):
    store = _make_bills_storage(tmp_path / "bills.json")
    payload = {"bills": {"NJ_S1": {"bill_number": "S1", "title": "Test"}}}
    store._write(payload)
    assert store._read() == payload


def test_json_file_storage_creates_parent_directories(tmp_path):
    deep_path = tmp_path / "a" / "b" / "c" / "bills.json"
    store = _make_bills_storage(deep_path)
    store._write({"bills": {}})
    assert deep_path.exists()


def test_json_file_storage_save_get_remove(tmp_path):
    store = _make_bills_storage(tmp_path / "bills.json")
    store.save("NJ_S1", {"bill_number": "S1"})
    assert store.get("NJ_S1") == {"bill_number": "S1"}
    store.remove("NJ_S1")
    assert store.get("NJ_S1") is None


def test_json_file_storage_list_all(tmp_path):
    store = _make_bills_storage(tmp_path / "bills.json")
    store.save("NJ_S1", {"bill_number": "S1"})
    store.save("NJ_S2", {"bill_number": "S2"})
    records = store.list_all()
    assert len(records) == 2
    numbers = {r["bill_number"] for r in records}
    assert numbers == {"S1", "S2"}


def test_json_file_storage_save_merges_existing(tmp_path):
    store = _make_bills_storage(tmp_path / "bills.json")
    store.save("NJ_S1", {"bill_number": "S1", "status": "Introduced"})
    store.save("NJ_S1", {"status": "Engrossed", "last_action": "Passed"})
    record = store.get("NJ_S1")
    assert record["status"] == "Engrossed"
    assert record["bill_number"] == "S1"   # original field preserved
    assert record["last_action"] == "Passed"


def test_json_file_storage_remove_noop_on_missing(tmp_path):
    store = _make_bills_storage(tmp_path / "bills.json")
    store.remove("NJ_DOESNOTEXIST")  # must not raise


# ---------------------------------------------------------------------------
# StorageFactory
# ---------------------------------------------------------------------------

def test_factory_register_and_create(tmp_path):
    factory = StorageFactory()
    factory.register("bills", BillsStorage, path=tmp_path / "b.json", collection_key="bills")
    instance = factory.create("bills")
    assert isinstance(instance, BillsStorage)


def test_factory_raises_on_duplicate_name(tmp_path):
    factory = StorageFactory()
    factory.register("bills", BillsStorage, path=tmp_path / "b.json", collection_key="bills")
    with pytest.raises(ValueError, match="already registered"):
        factory.register("bills", BillsStorage, path=tmp_path / "b2.json", collection_key="bills")


def test_factory_raises_on_unknown_name():
    factory = StorageFactory()
    with pytest.raises(ValueError, match="No storage registered"):
        factory.create("nonexistent")


def test_factory_creates_independent_instances(tmp_path):
    factory = StorageFactory()
    factory.register("a", BillsStorage, path=tmp_path / "a.json", collection_key="bills")
    factory.register("b", BillsStorage, path=tmp_path / "b.json", collection_key="bills")
    a = factory.create("a")
    b = factory.create("b")
    assert a is not b


# ---------------------------------------------------------------------------
# StorageManager
# ---------------------------------------------------------------------------

def test_manager_get_returns_same_instance(tmp_path):
    """Repeated calls to get() return the identical object (same memory address)."""
    factory = StorageFactory()
    factory.register("bills", BillsStorage, path=tmp_path / "bills.json", collection_key="bills")
    manager = StorageManager(factory)
    first = manager.get("bills")
    second = manager.get("bills")
    assert first is second


def test_manager_get_creates_lazily(tmp_path):
    """Instance is not created until get() is first called."""
    factory = StorageFactory()
    factory.register("bills", BillsStorage, path=tmp_path / "bills.json", collection_key="bills")
    manager = StorageManager(factory)
    assert manager.get_all() == {}
    manager.get("bills")
    assert "bills" in manager.get_all()


def test_manager_get_all_snapshot(tmp_path):
    factory = StorageFactory()
    factory.register("bills", BillsStorage, path=tmp_path / "b.json", collection_key="bills")
    factory.register("cal", CalendarStorage, path=tmp_path / "c.json", collection_key="events")
    manager = StorageManager(factory)
    manager.get("bills")
    manager.get("cal")
    all_stores = manager.get_all()
    assert set(all_stores.keys()) == {"bills", "cal"}


# ---------------------------------------------------------------------------
# BillsStorage domain methods
# ---------------------------------------------------------------------------

def test_bills_storage_bill_key_normalisation(tmp_path):
    store = _make_bills_storage(tmp_path / "bills.json")
    assert store.bill_key("s1234", "nj") == "NJ_S1234"
    assert store.bill_key("A567") == "NJ_A567"


def test_bills_storage_through_factory(tmp_path):
    store = _make_bills_storage(tmp_path / "bills.json")
    key = store.bill_key("S1234")
    store.save_bill(key, {"bill_number": "S1234", "title": "A Test"})
    assert store.get_bill(key)["title"] == "A Test"
    bills = store.list_bills()
    assert len(bills) == 1
    store.remove_bill(key)
    assert store.get_bill(key) is None


def test_bills_storage_load_bills_returns_keyed_dict(tmp_path):
    store = _make_bills_storage(tmp_path / "bills.json")
    store.save_bill("NJ_S1", {"bill_number": "S1"})
    store.save_bill("NJ_S2", {"bill_number": "S2"})
    keyed = store.load_bills()
    assert isinstance(keyed, dict)
    assert "NJ_S1" in keyed
    assert "NJ_S2" in keyed


# ---------------------------------------------------------------------------
# CalendarStorage domain methods
# ---------------------------------------------------------------------------

def test_calendar_storage_through_factory(tmp_path):
    store = _make_calendar_storage(tmp_path / "calendar.json")
    assert store.load_events() == []


def test_calendar_storage_resolve_user_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("USR_MAX_DISCORD_ID", raising=False)
    monkeypatch.delenv("USR_WIL_DISCORD_ID", raising=False)
    store = _make_calendar_storage(tmp_path / "calendar.json")
    assert store.resolve_user(99999, "Bob") == "Bob"


def test_calendar_storage_resolve_user_known(tmp_path, monkeypatch):
    monkeypatch.setenv("USR_MAX_DISCORD_ID", "12345")
    store = _make_calendar_storage(tmp_path / "calendar.json")
    assert store.resolve_user(12345, "Fallback") == "Maxilia"
