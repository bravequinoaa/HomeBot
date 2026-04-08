"""
Excel report generation — pure openpyxl, no network or Discord imports.

To add new report types, add functions here.  cog.py calls them and
uploads the returned bytes as a Discord file attachment.

Public API:
  build_single_report(bill: dict) -> bytes
  build_all_report(bills: list[dict]) -> bytes
  build_poll_report(changed_bills: list[dict], unchanged_bills: list[dict]) -> bytes
"""

from __future__ import annotations

import io
import datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.utils import get_column_letter

# ---------------------------------------------------------------------------
# Style constants — legacy (used by build_single_report / build_all_report)
# ---------------------------------------------------------------------------

# Yellow highlight for changed / new rows
_YELLOW = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")
_HEADER_FONT = Font(bold=True)
_HEADER_FILL = PatternFill(start_color="DAEEF3", end_color="DAEEF3", fill_type="solid")

# ---------------------------------------------------------------------------
# Style constants — MBT template (used by build_poll_report)
# ---------------------------------------------------------------------------

_TITLE_FILL      = PatternFill(start_color="EDD5E2", end_color="EDD5E2", fill_type="solid")
_SEPARATOR_FILL  = PatternFill(start_color="D4A0B5", end_color="D4A0B5", fill_type="solid")
_COL_HDR_FILL    = PatternFill(start_color="BD638F", end_color="BD638F", fill_type="solid")
_DATA_FILL       = PatternFill(start_color="E4EEF7", end_color="E4EEF7", fill_type="solid")
_CHANGED_FILL    = PatternFill(start_color="FFFF00", end_color="FFFF00", fill_type="solid")

_UBUNTU_TITLE    = Font(name="Ubuntu", size=16, bold=True)
_UBUNTU_SECTION  = Font(name="Ubuntu", size=12, bold=True)
_UBUNTU_COL_HDR  = Font(name="Ubuntu", size=10, bold=True)
_UBUNTU_SEP      = Font(name="Ubuntu", size=10, bold=True)
_UBUNTU_DATA     = Font(name="Ubuntu", size=9)

_BILL_HEADERS = [
    "Bill #", "Title", "Status", "Last Action",
    "Last Action Date", "Recent History (last 3)", "Sponsors",
]
_NCOLS = len(_BILL_HEADERS)

# Column widths matching the template
_COL_WIDTHS = {"A": 10, "B": 42, "C": 14, "D": 36, "E": 16, "F": 50, "G": 48}


# ---------------------------------------------------------------------------
# Public functions
# ---------------------------------------------------------------------------

def build_single_report(bill: dict[str, Any]) -> bytes:
    """
    Build a single-bill Excel report.

    Sheets:
      Overview  — key fields (number, title, sponsors, status, description, URL)
      History   — table of all legislative actions; rows newer than
                  last_alerted_action_date are highlighted yellow
    """
    wb = Workbook()

    _build_overview_sheet(wb.active, bill)
    wb.active.title = "Overview"

    hist_ws = wb.create_sheet("History")
    _build_history_sheet(hist_ws, bill)

    return _to_bytes(wb)


def build_all_report(bills: list[dict[str, Any]]) -> bytes:
    """
    Build a multi-bill summary Excel report using the MBT template format.

    Delegates to build_poll_report with no changed bills so all bills appear
    in the "All Bills" section.  Rows where last_action_date >
    last_alerted_action_date are highlighted yellow in the unchanged section.
    """
    # Separate out any locally-changed bills (action date newer than alerted)
    # so they surface with a yellow highlight.
    changed: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    for bill in bills:
        last_date = bill.get("last_action_date", "")
        alerted_date = bill.get("last_alerted_action_date", "")
        if last_date and last_date > alerted_date:
            changed.append(bill)
        else:
            unchanged.append(bill)

    return build_poll_report(changed, unchanged)


def build_poll_report(
    changed_bills: list[dict[str, Any]],
    unchanged_bills: list[dict[str, Any]],
) -> bytes:
    """
    Build a poll report using the MBT template format.

    With changes:
      Row 1  : Title (merged A:G)
      Row 2  : "Recent Updates" section label (merged A:G)
      Row 3  : Column headers
      Rows 4+: Changed bills (yellow highlight)
      Empty row (separator space)
      "— — —  All Bills (Unchanged)  — — —" (merged A:G)
      Column headers
      Unchanged bills

    No changes (standard all-bills report, changed_bills is empty):
      Row 1  : Title (merged A:G)
      "— — —  All Bills  — — —" (merged A:G)
      Column headers
      All bills
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "Bills Report"

    _set_template_col_widths(ws)

    # Row 1 — title
    _write_merged_row(
        ws, "🌸  Maxilia's Bill Tracker",
        font=_UBUNTU_TITLE,
        fill=_TITLE_FILL,
        halign="center", valign="center",
        height=36.0,
    )

    if changed_bills:
        # Section label: Recent Updates
        _write_merged_row(
            ws, "✨  Recent Updates",
            font=_UBUNTU_SECTION,
            fill=_TITLE_FILL,
            halign="left", valign="center",
            height=24.0,
        )
        # Headers
        _write_col_header_row(ws)
        # Changed bill rows
        for bill in changed_bills:
            _write_bill_data_row(ws, bill, changed=True)

        # Empty separator row
        ws.append([None] * _NCOLS)
        ws.row_dimensions[ws.max_row].height = 15.75

        # Separator label
        _write_merged_row(
            ws, "— — —  All Bills (Unchanged)  — — —",
            font=_UBUNTU_SEP,
            fill=_SEPARATOR_FILL,
            halign="center", valign="center",
            height=21.75,
        )
        # Headers for unchanged section
        _write_col_header_row(ws)
        # Unchanged bill rows
        for bill in unchanged_bills:
            _write_bill_data_row(ws, bill, changed=False)

    else:
        # Standard all-bills report — no recent updates section
        _write_merged_row(
            ws, "— — —  All Bills  — — —",
            font=_UBUNTU_SEP,
            fill=_SEPARATOR_FILL,
            halign="center", valign="center",
            height=21.75,
        )
        _write_col_header_row(ws)
        for bill in unchanged_bills:
            _write_bill_data_row(ws, bill, changed=False)

    ws.freeze_panes = "A4"
    return _to_bytes(wb)


# ---------------------------------------------------------------------------
# Private sheet builders (legacy)
# ---------------------------------------------------------------------------

def _build_overview_sheet(ws, bill: dict) -> None:
    fields = [
        ("Bill Number", bill.get("bill_number", "")),
        ("State", bill.get("state", "")),
        ("Title", bill.get("title", "")),
        ("Status", bill.get("status", "")),
        ("Last Action", bill.get("last_action", "")),
        ("Last Action Date", bill.get("last_action_date", "")),
        ("Sponsors", ", ".join(bill.get("sponsors", []))),
        ("Description", bill.get("description", "")),
        ("URL", bill.get("url", "")),
        ("Added By", bill.get("added_by", "")),
        ("Added At", bill.get("added_at", "")),
        ("Last Checked", bill.get("last_checked", "")),
    ]

    ws.column_dimensions["A"].width = 20
    ws.column_dimensions["B"].width = 80

    for label, value in fields:
        ws.append([label, str(value) if value else ""])
        label_cell = ws.cell(row=ws.max_row, column=1)
        label_cell.font = _HEADER_FONT
        value_cell = ws.cell(row=ws.max_row, column=2)
        value_cell.alignment = Alignment(wrap_text=True)


def _build_history_sheet(ws, bill: dict) -> None:
    headers = ["Date", "Chamber", "Action", "Importance"]
    _write_header_row(ws, headers)

    alerted_date = bill.get("last_alerted_action_date", "")

    for entry in bill.get("history", []):
        row = [
            entry.get("date", ""),
            entry.get("chamber", ""),
            entry.get("action", ""),
            entry.get("importance", 0),
        ]
        ws.append(row)
        # Highlight rows that are newer than the last alerted date
        if entry.get("date", "") > alerted_date:
            _highlight_row(ws, ws.max_row, len(headers))

    _auto_width(ws)
    ws.freeze_panes = "A2"


# ---------------------------------------------------------------------------
# Private helpers — MBT template style
# ---------------------------------------------------------------------------

def _set_template_col_widths(ws) -> None:
    for col_letter, width in _COL_WIDTHS.items():
        ws.column_dimensions[col_letter].width = width


def _write_merged_row(
    ws,
    text: str,
    *,
    font: Font,
    fill: PatternFill,
    halign: str,
    valign: str,
    height: float,
) -> None:
    """Append a merged-across-all-columns row with the given style."""
    ws.append([text] + [None] * (_NCOLS - 1))
    row_num = ws.max_row
    ws.merge_cells(f"A{row_num}:G{row_num}")
    cell = ws.cell(row=row_num, column=1)
    cell.font = font
    cell.fill = fill
    cell.alignment = Alignment(horizontal=halign, vertical=valign)
    ws.row_dimensions[row_num].height = height


def _write_col_header_row(ws) -> None:
    ws.append(_BILL_HEADERS)
    row_num = ws.max_row
    for col in range(1, _NCOLS + 1):
        cell = ws.cell(row=row_num, column=col)
        cell.font = _UBUNTU_COL_HDR
        cell.fill = _COL_HDR_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[row_num].height = 27.75


def _write_bill_data_row(ws, bill: dict, *, changed: bool) -> None:
    recent_history = bill.get("history", [])[-3:]
    history_text = "\n".join(
        f"{h.get('date', '')} — {h.get('action', '')}"
        for h in reversed(recent_history)
    )
    row = [
        bill.get("bill_number", ""),
        bill.get("title", ""),
        bill.get("status", ""),
        bill.get("last_action", ""),
        bill.get("last_action_date", ""),
        history_text,
        ", ".join(bill.get("sponsors", [])),
    ]
    ws.append(row)
    row_num = ws.max_row
    fill = _CHANGED_FILL if changed else _DATA_FILL
    for col in range(1, _NCOLS + 1):
        cell = ws.cell(row=row_num, column=col)
        cell.font = _UBUNTU_DATA
        cell.fill = fill
        cell.alignment = Alignment(horizontal="left", vertical="top", wrap_text=True)
    ws.row_dimensions[row_num].height = 51.75


# ---------------------------------------------------------------------------
# Private helpers — legacy
# ---------------------------------------------------------------------------

def _write_header_row(ws, headers: list[str]) -> None:
    ws.append(headers)
    for col, _ in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col)
        cell.font = _HEADER_FONT
        cell.fill = _HEADER_FILL


def _highlight_row(ws, row_num: int, num_cols: int) -> None:
    for col in range(1, num_cols + 1):
        ws.cell(row=row_num, column=col).fill = _YELLOW


def _auto_width(ws) -> None:
    for col_cells in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col_cells[0].column)
        for cell in col_cells:
            if cell.value:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[col_letter].width = min(max_len + 4, 60)


def _to_bytes(wb: Workbook) -> bytes:
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
