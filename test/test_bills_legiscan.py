"""Unit tests for cogs/bills/legiscan.py (HTTP mocked via unittest.mock)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.bills.legiscan import LegiScanClient, LegiScanError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client() -> LegiScanClient:
    return LegiScanClient("fake-api-key")


def _mock_response(payload: dict) -> MagicMock:
    """Build a mock httpx.Response that returns the given JSON payload."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json = MagicMock(return_value=payload)
    return resp


_SEARCH_PAYLOAD = {
    "status": "OK",
    "searchresult": {
        "0": {"query": "S1234", "summary": "1 result"},
        "1": {
            "bill_id": 99,
            "bill_number": "S1234",
            "title": "A Bill About Something",
            "state": "NJ",
        },
    },
}

_GET_BILL_PAYLOAD = {
    "status": "OK",
    "bill": {
        "bill_id": 99,
        "bill_number": "S1234",
        "state": "NJ",
        "title": "A Bill About Something",
        "description": "Full description here.",
        "status": 1,
        "change_hash": "deadbeef",
        "sponsors": [{"name": "Sen. Smith"}, {"name": "Sen. Jones"}],
        "history": [
            {"date": "2026-01-10", "action": "Introduced in the Senate", "chamber": "S", "importance": 1},
            {"date": "2026-02-01", "action": "Referred to Committee", "chamber": "S", "importance": 0},
        ],
    },
}

_MASTER_LIST_PAYLOAD = {
    "status": "OK",
    "masterlist": {
        "0": {"session_id": 2026, "state": "NJ"},
        "1": {"bill_id": 99, "number": "S1234", "change_hash": "deadbeef"},
        "2": {"bill_id": 100, "number": "A567", "change_hash": "cafebabe"},
    },
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_bill_returns_result():
    """search_bill finds a bill by exact number via the master list."""
    client = _make_client()
    # search_bill now calls get_master_list internally
    client._http.get = AsyncMock(return_value=_mock_response(_MASTER_LIST_PAYLOAD))

    result = await client.search_bill("S1234")

    assert result is not None
    assert result["bill_id"] == 99
    assert result["bill_number"] == "S1234"
    assert "url" in result


@pytest.mark.asyncio
async def test_search_bill_returns_none_on_empty():
    """search_bill returns None when the bill is not in the master list."""
    client = _make_client()
    client._http.get = AsyncMock(return_value=_mock_response(_MASTER_LIST_PAYLOAD))

    result = await client.search_bill("S9999")
    assert result is None


@pytest.mark.asyncio
async def test_get_bill_parses_response():
    """get_bill returns a normalised dict with history list and sponsor names."""
    client = _make_client()
    client._http.get = AsyncMock(return_value=_mock_response(_GET_BILL_PAYLOAD))

    result = await client.get_bill(99)

    assert result["bill_id"] == 99
    assert result["bill_number"] == "S1234"
    assert result["status"] == "Introduced"
    assert result["last_action"] == "Referred to Committee"
    assert result["last_action_date"] == "2026-02-01"
    assert len(result["history"]) == 2
    assert "Sen. Smith" in result["sponsors"]
    assert result["change_hash"] == "deadbeef"


@pytest.mark.asyncio
async def test_get_master_list_parses_response():
    """get_master_list returns {bill_number: {bill_id, change_hash}} mapping."""
    client = _make_client()
    client._http.get = AsyncMock(return_value=_mock_response(_MASTER_LIST_PAYLOAD))

    result = await client.get_master_list()

    assert "S1234" in result
    assert result["S1234"]["bill_id"] == 99
    assert result["S1234"]["change_hash"] == "deadbeef"
    assert "A567" in result
    assert result["A567"]["bill_id"] == 100
    # Summary entry ("0") should be excluded
    assert "0" not in result


@pytest.mark.asyncio
async def test_api_error_raises_legiscan_error():
    """LegiScanError is raised when the API returns status='ERROR'."""
    client = _make_client()
    error_payload = {
        "status": "ERROR",
        "alert": {"message": "Invalid API key"},
    }
    client._http.get = AsyncMock(return_value=_mock_response(error_payload))

    # get_master_list (called by search_bill) raises LegiScanError on API errors
    with pytest.raises(LegiScanError, match="Invalid API key"):
        await client.get_master_list()
