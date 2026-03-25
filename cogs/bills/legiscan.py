"""
LegiScan API client — pure async HTTP layer, no Discord or storage imports.

To swap data sources in the future, replace this module with one that
implements the same public interface:
  - LegiScanError
  - LegiScanClient.search_bill(bill_number, state) -> dict | None
  - LegiScanClient.get_bill(bill_id) -> dict
  - LegiScanClient.get_master_list(state) -> dict[str, dict]
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger("homebot.bills.legiscan")

_BASE = "https://api.legiscan.com/"

# LegiScan status codes -> human-readable labels
_STATUS_MAP: dict[int, str] = {
    1: "Introduced",
    2: "Engrossed",
    3: "Enrolled",
    4: "Passed",
    5: "Vetoed",
    6: "Failed",
    7: "Override",
    8: "Chaptered",
    9: "Refer",
    10: "Report Pass",
    11: "Report DNP",
    12: "Draft",
}


class LegiScanError(Exception):
    """Raised when the LegiScan API returns an error response."""


class LegiScanClient:
    """Async client for the LegiScan REST API."""

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        self._http = httpx.AsyncClient(timeout=30.0)

    async def close(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def search_bill(
        self, bill_number: str, state: str = "NJ"
    ) -> dict[str, Any] | None:
        """
        Search for a bill by number in the given state.

        Returns a dict with keys: bill_id, bill_number, title, state, url
        or None if not found.
        """
        params = {
            "key": self._key,
            "op": "search",
            "state": state,
            "query": bill_number,
            "year": 2,  # current + previous session
        }
        data = await self._get(params)
        results = data.get("searchresult", {})

        # LegiScan wraps results under numeric keys; "0" is a summary object
        for key, item in results.items():
            if key == "0":
                continue
            if not isinstance(item, dict):
                continue
            # Match exact bill number (case-insensitive)
            if item.get("bill_number", "").upper() == bill_number.upper():
                return {
                    "bill_id": item["bill_id"],
                    "bill_number": item["bill_number"],
                    "title": item.get("title", ""),
                    "state": state,
                    "url": _nj_url(item["bill_number"]),
                }

        return None

    async def get_bill(self, bill_id: int) -> dict[str, Any]:
        """
        Fetch full bill details including history, sponsors, and status.

        Returns a normalised dict ready for storage:
          bill_id, bill_number, state, title, description, status,
          last_action, last_action_date, change_hash, sponsors, url, history
        """
        params = {"key": self._key, "op": "getBill", "id": bill_id}
        data = await self._get(params)
        bill = data.get("bill", {})
        return _parse_bill(bill)

    async def get_master_list(self, state: str = "NJ") -> dict[str, dict[str, Any]]:
        """
        Return a mapping of bill_number → {bill_id, change_hash} for the
        current session.  Used to detect changes without fetching every bill.
        """
        params = {"key": self._key, "op": "getMasterList", "state": state}
        data = await self._get(params)
        master = data.get("masterlist", {})

        result: dict[str, dict] = {}
        for key, item in master.items():
            if key == "0":
                continue  # summary object
            if not isinstance(item, dict):
                continue
            bill_number = item.get("number", "")
            if bill_number:
                result[bill_number] = {
                    "bill_id": item["bill_id"],
                    "change_hash": item.get("change_hash", ""),
                }
        return result

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _get(self, params: dict) -> dict[str, Any]:
        """Make a GET request and validate the response envelope."""
        try:
            resp = await self._http.get(_BASE, params=params)
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as exc:
            raise LegiScanError(f"HTTP error: {exc}") from exc

        if payload.get("status") == "ERROR":
            msg = payload.get("alert", {}).get("message", "Unknown LegiScan error")
            raise LegiScanError(msg)

        return payload


# ------------------------------------------------------------------
# Parsing helpers
# ------------------------------------------------------------------

def _parse_bill(bill: dict) -> dict[str, Any]:
    """Normalise a raw LegiScan getBill payload into our storage schema."""
    history = [
        {
            "date": h.get("date", ""),
            "action": h.get("action", ""),
            "chamber": h.get("chamber", ""),
            "importance": h.get("importance", 0),
        }
        for h in bill.get("history", [])
    ]

    sponsors = [
        s.get("name", "") for s in bill.get("sponsors", []) if s.get("name")
    ]

    status_id = bill.get("status", 1)
    status_label = _STATUS_MAP.get(status_id, f"Status {status_id}")

    last_action = ""
    last_action_date = ""
    if history:
        last = history[-1]
        last_action = last["action"]
        last_action_date = last["date"]

    bill_number = bill.get("bill_number", "")
    state = bill.get("state", "NJ")

    return {
        "bill_id": bill.get("bill_id"),
        "bill_number": bill_number,
        "state": state,
        "title": bill.get("title", ""),
        "description": bill.get("description", ""),
        "status": status_label,
        "last_action": last_action,
        "last_action_date": last_action_date,
        "change_hash": bill.get("change_hash", ""),
        "sponsors": sponsors,
        "url": _nj_url(bill_number),
        "history": history,
    }


def _nj_url(bill_number: str) -> str:
    """Build the NJ Legislature URL for a given bill number."""
    import datetime
    year = datetime.date.today().year
    return f"https://www.njleg.state.nj.us/bill-search/{year}/{bill_number}"
