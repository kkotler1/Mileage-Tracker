"""Google Sheet backend for trip log.

One row per trip in IRS-log format:
    Date | Destination | Purpose | Shape | Miles | Deduction $ | Trip ID | Logged At
"""

from __future__ import annotations

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


def append_trip(row: dict[str, Any]) -> None:
    values = [row.get(h, "") for h in HEADERS]
    ws().append_row(values, value_input_option="USER_ENTERED")


def read_trips() -> list[dict[str, Any]]:
    return ws().get_all_records()
