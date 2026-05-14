"""FastMCP server for mileage-tracker.

Stores trips in a Google Sheet (IRS-log format, one row per trip).
Caches locations + per-place miles_from_home in a local JSON file.

Tools:
    - log_trip
    - add_location
    - resolve_location
    - list_locations
    - mileage_query
    - mileage_status
"""

from __future__ import annotations

import secrets
from datetime import date, datetime, timezone
from typing import Any, Literal

from fastmcp import FastMCP

from . import locations as loc
from . import sheet
from .config import IRS_MILEAGE_RATE, MCP_HOST, MCP_PORT, MCP_TRANSPORT
from .dates import parse_date, today, year_bounds

mcp = FastMCP("mileage-tracker")


# ---------------------------------------------------------------- helpers


def _money(miles: float) -> float:
    return round(miles * IRS_MILEAGE_RATE, 2)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _trip_id() -> str:
    return "trip_" + secrets.token_hex(4)


def _needs_input(destination: str, reason: str) -> dict[str, Any]:
    return {
        "status": "needs_input",
        "reason": reason,
        "destination": destination,
        "instruction": (
            f"Ask the user how many one-way miles it is from home to '{destination}', "
            f"then call add_location(name='{destination}', miles_from_home=<that number>) "
            "and retry log_trip."
        ),
    }


def _row_miles(row: dict[str, Any]) -> float:
    raw = row.get("Miles") or 0
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _row_date(row: dict[str, Any]) -> date | None:
    raw = row.get("Date")
    if not raw:
        return None
    try:
        return parse_date(str(raw))
    except Exception:
        return None


# ---------------------------------------------------------------- tools


@mcp.tool
def log_trip(
    destination: str,
    count: int = 1,
    date: str | None = None,
    purpose: str | None = None,
    shape: Literal["round_trip", "one_way"] = "round_trip",
) -> dict[str, Any]:
    """Log one or more business trips to a destination.

    Args:
        destination: Place name, e.g. "Potomac Swim Club". Fuzzy-matched against
            the saved locations (by name and aliases).
        count: How many trips to log on this date. Default 1. ("twice this week" = 2.)
        date: When the trip(s) happened. "today" (default), "yesterday",
            "N days ago", or ISO date.
        purpose: Business reason (e.g. "vending route", "client meeting").
        shape: "round_trip" (default) doubles miles_from_home; "one_way" uses it once.

    If the destination is unknown or its miles_from_home is unset, returns a
    needs_input response instructing the caller to add_location first.
    """
    if count < 1:
        return {"status": "error", "error": "count must be >= 1"}

    trip_date = parse_date(date)
    match = loc.resolve(destination)
    if match is None:
        return _needs_input(destination, "unknown_location")
    if match.get("miles_from_home") is None:
        return _needs_input(match["name"], "missing_miles_from_home")

    one_way = float(match["miles_from_home"])
    per_trip = round(one_way * 2 if shape == "round_trip" else one_way, 2)

    trip_ids = []
    for _ in range(count):
        tid = _trip_id()
        sheet.append_trip(
            {
                "Date": trip_date.isoformat(),
                "Destination": match["name"],
                "Purpose": purpose or "",
                "Shape": shape,
                "Miles": per_trip,
                "Deduction $": _money(per_trip),
                "Trip ID": tid,
                "Logged At": _now_iso(),
            }
        )
        trip_ids.append(tid)

    loc.touch(match["id"])

    total_miles = round(per_trip * count, 2)
    return {
        "status": "logged",
        "destination": match["name"],
        "date": trip_date.isoformat(),
        "shape": shape,
        "count": count,
        "miles_per_trip": per_trip,
        "total_miles": total_miles,
        "deduction_usd": _money(total_miles),
        "trip_ids": trip_ids,
    }


@mcp.tool
def add_location(
    name: str,
    miles_from_home: float,
    aliases: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    """Register or update a location with its one-way miles from home.

    Use this when log_trip returns needs_input, or to pre-populate places
    you visit often.

    Args:
        name: Canonical name (e.g. "Potomac Swim Club").
        miles_from_home: One-way driving miles from home. Round-trips double this.
        aliases: Other names you might use ("Potomac Swim", "the club").
        notes: Optional context.
    """
    if miles_from_home < 0:
        return {"status": "error", "error": "miles_from_home must be >= 0"}
    row = loc.upsert(
        name=name,
        miles_from_home=miles_from_home,
        aliases=aliases or [],
        notes=notes,
    )
    return {"status": "ok", "location": row}


@mcp.tool
def resolve_location(query: str) -> dict[str, Any]:
    """Look up a saved location by name or alias.

    Returns a single match, a list of candidates if ambiguous, or not_found.
    """
    match = loc.resolve(query)
    if match:
        return {"status": "match", "location": match}
    candidates = loc.find_candidates(query)
    if not candidates:
        return {"status": "not_found"}
    return {"status": "ambiguous", "candidates": candidates}


@mcp.tool
def list_locations() -> dict[str, Any]:
    """List all saved locations, most-used first."""
    rows = loc.all_locations()
    return {"locations": rows, "count": len(rows)}


@mcp.tool
def mileage_query(
    year: int | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    group_by: Literal["month", "destination", "purpose"] | None = None,
) -> dict[str, Any]:
    """Sum miles + deduction for a date range.

    Args:
        year: Calendar year (defaults to current). Ignored if start_date/end_date set.
        start_date: ISO date or keyword ("today", etc.).
        end_date: ISO date or keyword.
        group_by: Optional breakdown by month, destination, or purpose.
    """
    if start_date or end_date:
        start = parse_date(start_date) if start_date else date(today().year, 1, 1)
        end = parse_date(end_date) if end_date else today()
    else:
        y = year or today().year
        start, end = year_bounds(y)

    all_rows = sheet.read_trips()
    rows = []
    for r in all_rows:
        d = _row_date(r)
        if d is None:
            continue
        if start <= d <= end:
            rows.append(r)

    total_miles = round(sum(_row_miles(r) for r in rows), 2)
    summary: dict[str, Any] = {
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "trip_count": len(rows),
        "total_miles": total_miles,
        "deduction_usd": _money(total_miles),
        "rate_per_mile": IRS_MILEAGE_RATE,
    }

    if group_by is None:
        return summary

    buckets: dict[str, float] = {}
    if group_by == "month":
        for r in rows:
            d = _row_date(r)
            if d is None:
                continue
            key = f"{d.year:04d}-{d.month:02d}"
            buckets[key] = buckets.get(key, 0) + _row_miles(r)
        summary["by_month"] = {
            k: {"miles": round(v, 2), "deduction_usd": _money(v)}
            for k, v in sorted(buckets.items())
        }
    elif group_by == "destination":
        for r in rows:
            key = (r.get("Destination") or "(none)").strip() or "(none)"
            buckets[key] = buckets.get(key, 0) + _row_miles(r)
        summary["by_destination"] = {
            k: {"miles": round(v, 2), "deduction_usd": _money(v)}
            for k, v in sorted(buckets.items(), key=lambda kv: -kv[1])
        }
    elif group_by == "purpose":
        for r in rows:
            key = (r.get("Purpose") or "").strip() or "(none)"
            buckets[key] = buckets.get(key, 0) + _row_miles(r)
        summary["by_purpose"] = {
            k: {"miles": round(v, 2), "deduction_usd": _money(v)}
            for k, v in sorted(buckets.items(), key=lambda kv: -kv[1])
        }

    return summary


@mcp.tool
def mileage_status() -> dict[str, Any]:
    """Quick YTD snapshot: total miles, deduction, last few trips."""
    start, end = year_bounds(today().year)
    rows = []
    for r in sheet.read_trips():
        d = _row_date(r)
        if d and start <= d <= end:
            rows.append((d, r))
    rows.sort(key=lambda dr: dr[0], reverse=True)

    total_miles = round(sum(_row_miles(r) for _, r in rows), 2)
    return {
        "year": today().year,
        "total_miles": total_miles,
        "deduction_usd": _money(total_miles),
        "rate_per_mile": IRS_MILEAGE_RATE,
        "trip_count": len(rows),
        "recent_trips": [r for _, r in rows[:5]],
    }


# ---------------------------------------------------------------- entrypoint


def main() -> None:
    if MCP_TRANSPORT == "stdio":
        mcp.run()
    else:
        mcp.run(transport="streamable-http", host=MCP_HOST, port=MCP_PORT)


if __name__ == "__main__":
    main()
