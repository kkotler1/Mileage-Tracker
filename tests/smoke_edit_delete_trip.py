"""Smoke test for edit_trip / delete_trip — stdlib only, no Google API, no live sheet.

Run: .venv/bin/python tests/smoke_edit_delete_trip.py
Patches sheet._ws with an in-memory fake worksheet, then exercises
sheet.locate/update/delete + the edit_trip/delete_trip tool decision branches.
"""

from __future__ import annotations

import sys

from mileage_tracker import server, sheet


class FakeWorksheet:
    """Minimal gspread.Worksheet stand-in backed by a 2D list of strings."""

    def __init__(self, values: list[list[str]]):
        self.values = [list(r) for r in values]

    def get_all_values(self):
        return [list(r) for r in self.values]

    def get_all_records(self):
        headers = self.values[0]
        return [
            {h: (r[j] if j < len(r) else "") for j, h in enumerate(headers)}
            for r in self.values[1:]
        ]

    def update_cell(self, row: int, col: int, value):
        self.values[row - 1][col - 1] = str(value)

    def delete_rows(self, index: int):
        del self.values[index - 1]

    def insert_row(self, values, index: int, value_input_option=None):
        self.values.insert(index - 1, [str(v) for v in values])

    def append_row(self, values, value_input_option=None):
        self.values.append([str(v) for v in values])


def make_sheet():
    """Four trips, date-sorted, plus a trailing TOTAL footer to ignore."""
    rows = [
        list(sheet.HEADERS),
        ["2026-05-01", "Sam's Club", "vending",  "round_trip", "10", "7.00",  "trip_aaa", "2026-05-01T08:00:00+00:00"],
        ["2026-05-10", "Walmart",    "vending",  "round_trip", "20", "14.00", "trip_bbb", "2026-05-10T08:00:00+00:00"],
        ["2026-05-10", "Costco",     "restock",  "round_trip", "30", "21.00", "trip_ccc", "2026-05-10T09:00:00+00:00"],
        ["2026-05-20", "The Club",   "meeting",  "one_way",    "5",  "3.50",  "trip_ddd", "2026-05-20T08:00:00+00:00"],
        ["TOTAL", "", "", "", '=SUM(E2:E5)', '=SUM(F2:F5)', "", ""],
    ]
    fake = FakeWorksheet(rows)
    sheet._ws = fake  # bypass auth + bootstrap
    return fake


PASS, FAIL = 0, 0


def check(name: str, cond: bool, detail: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def main():
    # --- sheet-level locate ---
    make_sheet()
    check("locate by trip_id is unique", len(sheet.locate_trips(trip_id="trip_ccc")) == 1)
    one = sheet.locate_trips(trip_id="trip_ccc")
    check("locate returns correct 1-based row index (Costco = row 4)",
          one and one[0][0] == 4, detail=str(one))
    check("locate by date finds both 05-10 rows",
          len(sheet.locate_trips(date="2026-05-10")) == 2)
    check("locate by date+destination narrows to one",
          len(sheet.locate_trips(date="2026-05-10", destination="costco")) == 1)
    check("locate by date no-match returns empty",
          sheet.locate_trips(date="2099-01-01") == [])
    check("locate-all skips the TOTAL footer (4 trips, not 5)",
          len(sheet.locate_trips()) == 4, detail=str(len(sheet.locate_trips())))

    # --- edit_trip: must give a locator ---
    make_sheet()
    r = server.edit_trip(purpose="x")
    check("edit needs a locator", r["status"] == "error", detail=str(r))

    # --- edit_trip: no match ---
    make_sheet()
    r = server.edit_trip(trip_id="trip_nope", purpose="x")
    check("edit no_match", r["status"] == "no_match", detail=str(r))

    # --- edit_trip: ambiguous ---
    make_sheet()
    r = server.edit_trip(date="2026-05-10", purpose="x")
    check("edit ambiguous on 2 matches", r["status"] == "ambiguous", detail=str(r))
    check("ambiguous returns candidates", len(r.get("matches", [])) == 2)

    # --- edit_trip: no changes ---
    make_sheet()
    r = server.edit_trip(trip_id="trip_ccc")
    check("edit no_changes when no fields given", r["status"] == "no_changes", detail=str(r))

    # --- edit_trip: reject negative miles ---
    make_sheet()
    r = server.edit_trip(trip_id="trip_ccc", miles=-3)
    check("edit rejects negative miles", r["status"] == "error", detail=str(r))

    # --- edit_trip: in-place update (purpose + miles, no date change) ---
    fake = make_sheet()
    r = server.edit_trip(trip_id="trip_ccc", purpose="restock-fixed", miles=40)
    check("edit updated in place", r["status"] == "updated", detail=str(r))
    check("edit not resorted", r["resorted"] is False)
    check("changed fields recorded", set(r["changed_fields"]) == {"Purpose", "Miles", "Deduction $"})
    # row 4 = Costco; Purpose col idx 2, Miles idx 4, Deduction idx 5 (0-based)
    check("sheet Purpose updated", fake.values[3][2] == "restock-fixed", detail=fake.values[3][2])
    check("sheet Miles updated", fake.values[3][4] == "40", detail=fake.values[3][4])
    check("Deduction $ recomputed from miles", fake.values[3][5] == str(server._money(40)),
          detail=fake.values[3][5])
    check("other rows untouched", fake.values[1][1] == "Sam's Club")

    # --- edit_trip: date change triggers re-sort (delete + reinsert) ---
    fake = make_sheet()
    r = server.edit_trip(trip_id="trip_aaa", new_date="2026-05-15")
    check("edit resorted on date change", r["status"] == "updated" and r["resorted"] is True, detail=str(r))
    data_dates = [row[0] for row in fake.values[1:] if row[0] != "TOTAL"]
    check("rows remain date-sorted after move", data_dates == sorted(data_dates), detail=str(data_dates))
    check("moved row lands between 05-10 and 05-20",
          data_dates == ["2026-05-10", "2026-05-10", "2026-05-15", "2026-05-20"], detail=str(data_dates))
    check("TOTAL footer stays last", fake.values[-1][0] == "TOTAL", detail=str(fake.values[-1]))
    moved = [row for row in fake.values if row[6] == "trip_aaa"][0]
    check("moved row kept its other fields", moved[0] == "2026-05-15" and moved[1] == "Sam's Club",
          detail=str(moved))

    # --- delete_trip: must give a locator ---
    make_sheet()
    r = server.delete_trip()
    check("delete needs a locator", r["status"] == "error", detail=str(r))

    # --- delete_trip: no match ---
    make_sheet()
    r = server.delete_trip(trip_id="trip_nope")
    check("delete no_match", r["status"] == "no_match", detail=str(r))

    # --- delete_trip: ambiguous ---
    make_sheet()
    r = server.delete_trip(date="2026-05-10")
    check("delete ambiguous on 2 matches", r["status"] == "ambiguous", detail=str(r))
    check("delete ambiguous returns candidates", len(r.get("matches", [])) == 2)

    # --- delete_trip: confirm required (dry run, nothing removed) ---
    fake = make_sheet()
    before = len(fake.values)
    r = server.delete_trip(trip_id="trip_ccc")
    check("delete confirm_required without confirm", r["status"] == "confirm_required", detail=str(r))
    check("delete dry-run leaves sheet untouched", len(fake.values) == before)
    check("delete confirm_required echoes target", r["target"]["destination"] == "Costco", detail=str(r))

    # --- delete_trip: confirmed removal ---
    fake = make_sheet()
    before = len(fake.values)
    r = server.delete_trip(trip_id="trip_ccc", confirm=True)
    check("delete confirmed status", r["status"] == "deleted", detail=str(r))
    check("delete confirmed removes one row", len(fake.values) == before - 1)
    check("delete confirmed removed the right row (Costco gone)",
          all(row[1] != "Costco" for row in fake.values))
    check("delete left other 05-10 row (Walmart stays)",
          any(row[1] == "Walmart" for row in fake.values))
    check("TOTAL footer survives delete", fake.values[-1][0] == "TOTAL", detail=str(fake.values[-1]))

    # --- delete_trip via date+destination locator ---
    fake = make_sheet()
    r = server.delete_trip(date="2026-05-10", destination="walmart", confirm=True)
    check("delete by date+destination works", r["status"] == "deleted", detail=str(r))
    check("delete by date+destination removed Walmart",
          all(row[1] != "Walmart" for row in fake.values))

    print(f"\n{PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
