"""Stage 4 of the verify gate: regression over the core write tool (log_trip).

Exercises log_trip end-to-end through the real server tool function, but against
an in-memory fake worksheet and a stubbed location cache — so the existing write
behaviour (round-trip doubling, deduction math, needs_input) is pinned WITHOUT
touching the production Google Sheet or the on-disk ~/.mileage_locations.json.
(Stage 3 covers the live protocol/auth path read-only; this stage covers write
behaviour offline.)

Why fakes, not a test sheet: per the non-destructive testing rule, no verify-gate
stage may write to production data. We substitute sheet._ws and monkeypatch
loc.resolve / loc.touch exactly as the unit smoke tests substitute fakes.

Run: .venv/bin/python tests/regression_tools.py
"""

from __future__ import annotations

import sys

from mileage_tracker import locations as loc
from mileage_tracker import server, sheet


class FakeWorksheet:
    """Minimal gspread.Worksheet stand-in backed by a 2D list of strings."""

    def __init__(self, values):
        self.values = [list(r) for r in values]

    def get_all_values(self):
        return [list(r) for r in self.values]

    def get_all_records(self):
        headers = self.values[0]
        return [
            {h: (r[j] if j < len(r) else "") for j, h in enumerate(headers)}
            for r in self.values[1:]
        ]

    def update_cell(self, row, col, value):
        while len(self.values[row - 1]) < col:
            self.values[row - 1].append("")
        self.values[row - 1][col - 1] = str(value)

    def delete_rows(self, index):
        del self.values[index - 1]

    def insert_row(self, values, index, value_input_option=None):
        self.values.insert(index - 1, [str(v) for v in values])

    def append_row(self, values, value_input_option=None):
        self.values.append([str(v) for v in values])


def _seed():
    """Fresh empty Trips fake (header only); bypasses auth + tab bootstrap."""
    fake = FakeWorksheet([list(sheet.HEADERS)])
    sheet._ws = fake
    return fake


PASS = FAIL = 0


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def main():
    # Stub the location cache so no on-disk JSON is read or written.
    loc.resolve = lambda q: {"id": "loc_x", "name": "Sam's Club", "miles_from_home": 10.0}
    loc.touch = lambda _id: None

    # --- 1) round-trip doubles miles_from_home and derives the deduction ---
    fake = _seed()
    r = server.log_trip(destination="Sam's Club", date="2026-06-02", purpose="vending")
    check("round-trip logged", r["status"] == "logged", str(r))
    check("round-trip doubles 10 -> 20 miles", r["miles_per_trip"] == 20.0, str(r))
    check("deduction derived from miles", r["deduction_usd"] == server._money(20.0), str(r))
    check("a trip row landed on the sheet", len(fake.values) == 2, str(fake.values))
    check("row carries the resolved destination", fake.values[1][1] == "Sam's Club", str(fake.values[1]))

    # --- 2) one_way uses miles_from_home once ---
    fake = _seed()
    r = server.log_trip(destination="Sam's Club", date="2026-06-02", shape="one_way")
    check("one_way logged", r["status"] == "logged", str(r))
    check("one_way uses 10 miles once", r["miles_per_trip"] == 10.0, str(r))

    # --- 3) count writes multiple rows ---
    fake = _seed()
    r = server.log_trip(destination="Sam's Club", date="2026-06-02", count=3)
    check("count=3 logged", r["status"] == "logged" and r["count"] == 3, str(r))
    check("count=3 wrote three rows", len(fake.values) == 4, str(fake.values))
    check("count=3 returns three trip_ids", len(r["trip_ids"]) == 3, str(r))

    # --- 4) unknown location → needs_input, nothing written ---
    fake = _seed()
    loc.resolve = lambda q: None
    r = server.log_trip(destination="Nowhere", date="2026-06-02")
    check("unknown location → needs_input", r["status"] == "needs_input", str(r))
    check("needs_input wrote nothing", len(fake.values) == 1, str(fake.values))

    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
