"""Unit tests for the _poll method in BillsCog — verifying bill update detection."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cogs.bills.cog import BillsCog
from cogs.bills.legiscan import LegiScanError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stored_bill(bill_number="S1234", change_hash="abc", last_action_date="2026-01-15"):
    """Return a minimal stored bill record."""
    return {
        "bill_number": bill_number,
        "state": "NJ",
        "title": "A Test Bill",
        "status": "Introduced",
        "last_action": "Referred to Committee",
        "last_action_date": last_action_date,
        "last_alerted_action_date": last_action_date,
        "change_hash": change_hash,
        "url": f"https://www.njleg.state.nj.us/bill-search/2026/{bill_number}",
        "sponsors": [],
        "history": [{"date": last_action_date, "action": "Introduced", "chamber": "S", "importance": 1}],
    }


def _make_cog(storage_mock, client_mock):
    """Construct a BillsCog instance without triggering __init__ or the Discord task loop."""
    cog = object.__new__(BillsCog)
    cog.storage = storage_mock
    cog.client = client_mock
    cog.alert_channel_id = 111
    cog.reports_channel_id = 222
    cog.channel_id = 333
    cog._last_poll_was_no_change = False
    cog.bot = MagicMock()
    return cog


def _make_updated_bill_data(bill_number="S1234", new_action_date="2026-03-01"):
    """Return a LegiScan-fetched bill dict with a newer action date."""
    return {
        "bill_number": bill_number,
        "state": "NJ",
        "title": "A Test Bill",
        "status": "Passed Senate",
        "last_action": "Passed Senate",
        "last_action_date": new_action_date,
        "change_hash": "xyz",
        "url": f"https://www.njleg.state.nj.us/bill-search/2026/{bill_number}",
        "sponsors": [],
        "history": [
            {"date": "2026-01-15", "action": "Introduced", "chamber": "S", "importance": 1},
            {"date": new_action_date, "action": "Passed Senate", "chamber": "S", "importance": 1},
        ],
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unchanged_bill_not_fetched_or_saved():
    """Bill with identical change_hash in master list is skipped entirely."""
    stored = _make_stored_bill(change_hash="abc")
    storage = MagicMock()
    storage.list_bills.return_value = [stored]
    storage.bill_key.return_value = "NJ_S1234"

    client = MagicMock()
    client.get_master_list = AsyncMock(return_value={
        "S1234": {"bill_id": 1, "change_hash": "abc"},  # same hash
    })

    cog = _make_cog(storage, client)
    cog._send_alert = AsyncMock()
    cog._post_poll_report = AsyncMock()

    await cog._poll()

    client.get_bill.assert_not_called()
    storage.save_bill.assert_not_called()
    cog._send_alert.assert_not_called()


@pytest.mark.asyncio
async def test_changed_bill_is_fetched_and_saved():
    """Bill whose change_hash differs from the master list is fetched and persisted."""
    stored = _make_stored_bill(change_hash="old")
    storage = MagicMock()
    storage.list_bills.return_value = [stored]
    storage.bill_key.return_value = "NJ_S1234"

    updated = _make_updated_bill_data()
    client = MagicMock()
    client.get_master_list = AsyncMock(return_value={
        "S1234": {"bill_id": 1, "change_hash": "new"},
    })
    client.get_bill = AsyncMock(return_value=updated)

    cog = _make_cog(storage, client)
    cog._send_alert = AsyncMock()
    cog._post_poll_report = AsyncMock()

    await cog._poll()

    client.get_bill.assert_awaited_once_with(1)
    storage.save_bill.assert_called_once()
    saved_record = storage.save_bill.call_args[0][1]
    # change_hash comes from the fetched bill data (merged into stored record)
    assert saved_record["change_hash"] == "xyz"
    assert saved_record["last_action"] == "Passed Senate"


@pytest.mark.asyncio
async def test_alert_sent_when_action_date_advances():
    """Alert is triggered when the new last_action_date is after last_alerted_action_date."""
    stored = _make_stored_bill(change_hash="old", last_action_date="2026-01-15")
    stored["last_alerted_action_date"] = "2026-01-15"

    storage = MagicMock()
    storage.list_bills.return_value = [stored]
    storage.bill_key.return_value = "NJ_S1234"

    updated = _make_updated_bill_data(new_action_date="2026-03-01")
    client = MagicMock()
    client.get_master_list = AsyncMock(return_value={
        "S1234": {"bill_id": 1, "change_hash": "new"},
    })
    client.get_bill = AsyncMock(return_value=updated)

    cog = _make_cog(storage, client)
    cog._send_alert = AsyncMock()
    cog._post_poll_report = AsyncMock()

    await cog._poll()

    cog._send_alert.assert_awaited_once()
    alerted_bill = cog._send_alert.call_args[0][0]
    assert alerted_bill["last_action_date"] == "2026-03-01"


@pytest.mark.asyncio
async def test_last_alerted_action_date_updated_after_alert():
    """last_alerted_action_date in saved record is updated to the new action date."""
    stored = _make_stored_bill(change_hash="old", last_action_date="2026-01-15")
    stored["last_alerted_action_date"] = "2026-01-15"

    storage = MagicMock()
    storage.list_bills.return_value = [stored]
    storage.bill_key.return_value = "NJ_S1234"

    updated = _make_updated_bill_data(new_action_date="2026-03-01")
    client = MagicMock()
    client.get_master_list = AsyncMock(return_value={
        "S1234": {"bill_id": 1, "change_hash": "new"},
    })
    client.get_bill = AsyncMock(return_value=updated)

    cog = _make_cog(storage, client)
    cog._send_alert = AsyncMock()
    cog._post_poll_report = AsyncMock()

    await cog._poll()

    saved_record = storage.save_bill.call_args[0][1]
    assert saved_record["last_alerted_action_date"] == "2026-03-01"


@pytest.mark.asyncio
async def test_no_alert_when_action_date_unchanged():
    """No alert when change_hash differs but last_action_date has not advanced."""
    stored = _make_stored_bill(change_hash="old", last_action_date="2026-03-01")
    stored["last_alerted_action_date"] = "2026-03-01"

    storage = MagicMock()
    storage.list_bills.return_value = [stored]
    storage.bill_key.return_value = "NJ_S1234"

    # New fetch returns same action date (e.g. only metadata changed)
    updated = _make_updated_bill_data(new_action_date="2026-03-01")
    client = MagicMock()
    client.get_master_list = AsyncMock(return_value={
        "S1234": {"bill_id": 1, "change_hash": "new"},
    })
    client.get_bill = AsyncMock(return_value=updated)

    cog = _make_cog(storage, client)
    cog._send_alert = AsyncMock()
    cog._post_poll_report = AsyncMock()

    await cog._poll()

    storage.save_bill.assert_called_once()
    cog._send_alert.assert_not_called()


@pytest.mark.asyncio
async def test_bill_missing_from_master_list_skipped():
    """Bill not present in the LegiScan master list is left unchanged."""
    stored = _make_stored_bill(bill_number="S9999", change_hash="abc")
    storage = MagicMock()
    storage.list_bills.return_value = [stored]
    storage.bill_key.return_value = "NJ_S9999"

    client = MagicMock()
    client.get_master_list = AsyncMock(return_value={})  # empty — bill not found

    cog = _make_cog(storage, client)
    cog._send_alert = AsyncMock()
    cog._post_poll_report = AsyncMock()

    await cog._poll()

    client.get_bill.assert_not_called()
    storage.save_bill.assert_not_called()


@pytest.mark.asyncio
async def test_legiscan_error_on_get_bill_skips_update():
    """If get_bill raises LegiScanError the bill is not saved and no alert is sent."""
    stored = _make_stored_bill(change_hash="old")
    storage = MagicMock()
    storage.list_bills.return_value = [stored]
    storage.bill_key.return_value = "NJ_S1234"

    client = MagicMock()
    client.get_master_list = AsyncMock(return_value={
        "S1234": {"bill_id": 1, "change_hash": "new"},
    })
    client.get_bill = AsyncMock(side_effect=LegiScanError("timeout"))

    cog = _make_cog(storage, client)
    cog._send_alert = AsyncMock()
    cog._post_poll_report = AsyncMock()

    await cog._poll()

    storage.save_bill.assert_not_called()
    cog._send_alert.assert_not_called()


@pytest.mark.asyncio
async def test_multiple_bills_only_changed_ones_updated():
    """Only bills with a new change_hash are fetched; unchanged bills are left alone."""
    bill_a = _make_stored_bill("S1234", change_hash="same")
    bill_b = _make_stored_bill("A567", change_hash="old")

    storage = MagicMock()
    storage.list_bills.return_value = [bill_a, bill_b]
    storage.bill_key.side_effect = lambda num, *_: f"NJ_{num}"

    updated_b = _make_updated_bill_data("A567", "2026-03-15")
    client = MagicMock()
    client.get_master_list = AsyncMock(return_value={
        "S1234": {"bill_id": 1, "change_hash": "same"},   # unchanged
        "A567": {"bill_id": 2, "change_hash": "new"},     # changed
    })
    client.get_bill = AsyncMock(return_value=updated_b)

    cog = _make_cog(storage, client)
    cog._send_alert = AsyncMock()
    cog._post_poll_report = AsyncMock()

    await cog._poll()

    client.get_bill.assert_awaited_once_with(2)
    storage.save_bill.assert_called_once()
    saved_key = storage.save_bill.call_args[0][0]
    assert saved_key == "NJ_A567"


@pytest.mark.asyncio
async def test_poll_skipped_when_no_bills_tracked():
    """_poll exits immediately without hitting the API when the bill list is empty."""
    storage = MagicMock()
    storage.list_bills.return_value = []

    client = MagicMock()
    client.get_master_list = AsyncMock()

    cog = _make_cog(storage, client)
    cog._post_poll_report = AsyncMock()

    await cog._poll()

    client.get_master_list.assert_not_called()
