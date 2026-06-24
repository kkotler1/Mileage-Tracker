"""FastMCP server for mileage-tracker.

Stores trips in a Google Sheet (IRS-log format, one row per trip).
Caches locations + per-place miles_from_home in a local JSON file.

Tools:
    - log_trip
    - log_route
    - add_location
    - add_leg
    - resolve_location
    - list_locations
    - list_legs
    - edit_trip
    - delete_trip
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
from .config import (
    AUTHKIT_DOMAIN,
    IRS_MILEAGE_RATE,
    MCP_HOST,
    MCP_PORT,
    MCP_TRANSPORT,
    PUBLIC_BASE_URL,
)
from .dates import parse_date, today, year_bounds


def _build_auth():
    """WorkOS AuthKit provider for the public http server, or None (authless).

    Only enabled for http transport with both env vars set, so stdio (local
    Claude Code) and an unconfigured deployment both stay authless.
    """
    if MCP_TRANSPORT == "stdio" or not (AUTHKIT_DOMAIN and PUBLIC_BASE_URL):
        return None
    from fastmcp.server.auth.providers.workos import AuthKitProvider

    return AuthKitProvider(authkit_domain=AUTHKIT_DOMAIN, base_url=PUBLIC_BASE_URL)


mcp = FastMCP("mileage-tracker", auth=_build_auth())


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
def log_route(
    stops: list[str],
    date: str | None = None,
    purpose: str | None = None,
    leg_overrides: list[dict] | None = None,
) -> dict[str, Any]:
    """Log a multi-stop business route.

    Typical pattern: Home → stop1 → ... → stopN → Home. Include "home"
    explicitly in stops — it is not auto-appended. Any sequence of known
    locations works; home is not required.

    Home-adjacent legs use each location's miles_from_home. Inter-stop legs
    come from the cached leg-distance table. Missing inter-stop legs return
    needs_legs with all unknown pairs at once — call add_leg or pass
    leg_overrides, then retry.

    Args:
        stops: Ordered stop names. "home" resolves to your home location.
            Example: ["home", "Sam's Club", "Walmart", "the club", "home"]
        date: "today" (default), "yesterday", "N days ago", or ISO date.
        purpose: Business reason (e.g. "vending route").
        leg_overrides: Explicit miles for specific legs, overriding the cache.
            Format: [{"from": "Walmart", "to": "Sam's Club", "miles": 4.6}].
            Values are saved to the cache for future routes.
    """
    if len(stops) < 2:
        return {"status": "error", "error": "stops must have at least 2 entries"}

    trip_date = parse_date(date)
    HOME_ID = "home"

    # Resolve stops (home is a special node with no miles_from_home)
    resolved: list[dict[str, Any]] = []
    for name in stops:
        if name.strip().lower() == "home":
            resolved.append({"id": HOME_ID, "name": "Home"})
        else:
            match = loc.resolve(name)
            if match is None:
                return _needs_input(name, "unknown_location")
            resolved.append(match)

    # Resolve leg_overrides once; store (id_a, name_a, id_b, name_b, miles)
    resolved_overrides: list[tuple[str, str, str, str, float]] = []
    override_id_map: dict[frozenset, float] = {}
    if leg_overrides:
        for ov in leg_overrides:
            ov_from = (ov.get("from") or "").strip()
            ov_to = (ov.get("to") or "").strip()
            try:
                ov_miles = float(ov["miles"])
            except (KeyError, TypeError, ValueError):
                continue

            if ov_from.lower() == "home":
                ov_id_a, ov_name_a = HOME_ID, "Home"
            else:
                ra = loc.resolve(ov_from)
                if not ra:
                    continue
                ov_id_a, ov_name_a = ra["id"], ra["name"]

            if ov_to.lower() == "home":
                ov_id_b, ov_name_b = HOME_ID, "Home"
            else:
                rb = loc.resolve(ov_to)
                if not rb:
                    continue
                ov_id_b, ov_name_b = rb["id"], rb["name"]

            if ov_id_a == ov_id_b:
                continue
            override_id_map[frozenset({ov_id_a, ov_id_b})] = ov_miles
            resolved_overrides.append((ov_id_a, ov_name_a, ov_id_b, ov_name_b, ov_miles))

    # Build legs
    legs: list[tuple[str, str, float | None]] = []
    missing_legs: list[tuple[str, str]] = []

    for i in range(len(resolved) - 1):
        a, b = resolved[i], resolved[i + 1]
        pair = frozenset({a["id"], b["id"]})

        if pair in override_id_map:
            legs.append((a["name"], b["name"], override_id_map[pair]))
        elif a["id"] == HOME_ID:
            if b.get("miles_from_home") is None:
                return _needs_input(b["name"], "missing_miles_from_home")
            legs.append((a["name"], b["name"], float(b["miles_from_home"])))
        elif b["id"] == HOME_ID:
            if a.get("miles_from_home") is None:
                return _needs_input(a["name"], "missing_miles_from_home")
            legs.append((a["name"], b["name"], float(a["miles_from_home"])))
        else:
            cached = loc.get_leg_distance(a["id"], b["id"])
            if cached is not None:
                legs.append((a["name"], b["name"], cached))
            else:
                missing_legs.append((a["name"], b["name"]))
                legs.append((a["name"], b["name"], None))

    if missing_legs:
        return {
            "status": "needs_legs",
            "missing_legs": [{"from": fn, "to": tn} for fn, tn in missing_legs],
            "instruction": (
                "Call add_leg for each missing pair, or include them in "
                "leg_overrides, then retry log_route."
            ),
        }

    # Cache overrides; track which inter-stop legs are new
    newly_cached: list[dict[str, Any]] = []
    for ov_id_a, ov_name_a, ov_id_b, ov_name_b, ov_miles in resolved_overrides:
        if ov_id_a == HOME_ID:
            loc.set_miles_from_home(ov_id_b, ov_miles)
        elif ov_id_b == HOME_ID:
            loc.set_miles_from_home(ov_id_a, ov_miles)
        else:
            was_cached = loc.get_leg_distance(ov_id_a, ov_id_b) is not None
            loc.set_leg_distance(ov_id_a, ov_id_b, ov_miles)
            if not was_cached:
                newly_cached.append({"from": ov_name_a, "to": ov_name_b, "miles": ov_miles})

    # All legs are resolved here — the missing_legs guard above returns early.
    total_miles = round(sum(d for _, _, d in legs if d is not None), 2)
    route_label = " → ".join(r["name"] for r in resolved)
    tid = _trip_id()
    sheet.append_trip({
        "Date": trip_date.isoformat(),
        "Destination": route_label,
        "Purpose": purpose or "",
        "Shape": "route",
        "Miles": total_miles,
        "Deduction $": _money(total_miles),
        "Trip ID": tid,
        "Logged At": _now_iso(),
    })
    for r in resolved:
        if r["id"] != HOME_ID:
            loc.touch(r["id"])

    result: dict[str, Any] = {
        "status": "logged",
        "route": route_label,
        "date": trip_date.isoformat(),
        "legs": [{"from": fn, "to": tn, "miles": d} for fn, tn, d in legs],
        "total_miles": total_miles,
        "deduction_usd": _money(total_miles),
        "trip_id": tid,
    }
    if newly_cached:
        result["newly_cached_legs"] = newly_cached
    return result


@mcp.tool
def add_leg(
    location_a: str,
    location_b: str,
    miles: float,
) -> dict[str, Any]:
    """Register the one-way driving distance between two locations.

    Builds up the per-leg cache used by log_route. "Home" is a valid value
    for either argument — it updates miles_from_home on the other location
    instead of the inter-stop cache.

    Args:
        location_a: First location name (or "Home").
        location_b: Second location name (or "Home").
        miles: One-way driving miles between them.
    """
    if miles < 0:
        return {"status": "error", "error": "miles must be >= 0"}

    HOME_ID = "home"

    def _resolve(name: str) -> tuple[str | None, str]:
        if name.strip().lower() == "home":
            return HOME_ID, "Home"
        match = loc.resolve(name)
        if match is None:
            return None, name
        return match["id"], match["name"]

    id_a, name_a = _resolve(location_a)
    id_b, name_b = _resolve(location_b)

    if id_a is None:
        return {"status": "error", "error": f"Unknown location: '{location_a}'"}
    if id_b is None:
        return {"status": "error", "error": f"Unknown location: '{location_b}'"}
    if id_a == id_b:
        return {"status": "error", "error": "location_a and location_b must be different"}

    if id_a == HOME_ID or id_b == HOME_ID:
        other_id = id_b if id_a == HOME_ID else id_a
        loc.set_miles_from_home(other_id, miles)
    else:
        loc.set_leg_distance(id_a, id_b, miles)

    return {"status": "ok", "leg": {"from": name_a, "to": name_b, "miles": miles}}


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
def list_legs() -> dict[str, Any]:
    """List all cached inter-stop driving distances.

    Shows every A↔B pair in the leg-distance cache. Home-to-location
    distances live on each location (see list_locations) and are not shown here.
    Useful for auditing or correcting cached values before logging a route.
    """
    legs = loc.list_leg_distances()
    return {"leg_count": len(legs), "legs": legs}


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


def _trip_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "date": row.get("Date"),
        "destination": row.get("Destination"),
        "purpose": row.get("Purpose"),
        "shape": row.get("Shape"),
        "miles": _row_miles(row),
        "trip_id": row.get("Trip ID"),
    }


@mcp.tool
def edit_trip(
    trip_id: str | None = None,
    date: str | None = None,
    destination: str | None = None,
    new_destination: str | None = None,
    purpose: str | None = None,
    shape: str | None = None,
    miles: float | None = None,
    new_date: str | None = None,
) -> dict[str, Any]:
    """Retroactively edit a single logged trip.

    Locate the trip by `trip_id` (unique, preferred) or by `date` — narrow with
    `destination` (case-insensitive substring) when a date has several trips.
    Edits only when EXACTLY one row matches; returns `ambiguous` with candidates
    otherwise. Only the fields you pass change.

    Args:
        trip_id: Exact Trip ID from log_trip/log_route (e.g. "trip_1a2b3c4d").
        date: ISO date or keyword ("today", "yesterday", "N days ago") to locate by.
        destination: Substring to disambiguate among trips on the same date.
        new_destination: Rename the trip's destination.
        purpose: New business purpose.
        shape: New shape label (e.g. "round_trip", "one_way").
        miles: New mileage — the Deduction $ is recomputed automatically.
        new_date: Move the trip to a different date; the sheet is kept date-sorted.
    """
    if not trip_id and not date:
        return {"status": "error", "error": "provide trip_id or date to locate the trip"}
    if miles is not None and miles < 0:
        return {"status": "error", "error": "miles must be >= 0"}

    loc_date = parse_date(date).isoformat() if date else None
    matches = sheet.locate_trips(trip_id=trip_id, date=loc_date, destination=destination)
    if not matches:
        return {"status": "no_match", "criteria": {"trip_id": trip_id, "date": loc_date, "destination": destination}}
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "matches": [_trip_summary(r) for _, r in matches],
            "instruction": "Several trips match. Pass trip_id, or add destination, to pick one.",
        }

    row_number, row = matches[0]

    updates: dict[str, Any] = {}
    if new_destination is not None:
        updates["Destination"] = new_destination
    if purpose is not None:
        updates["Purpose"] = purpose
    if shape is not None:
        updates["Shape"] = shape
    if miles is not None:
        updates["Miles"] = miles
        updates["Deduction $"] = _money(miles)

    if not updates and not new_date:
        return {"status": "no_changes", "target": _trip_summary(row)}

    resorted = False
    if new_date:
        # A date change can break sort order — delete + reinsert in date order.
        merged = dict(row)
        merged.update(updates)
        merged["Date"] = parse_date(new_date).isoformat()
        sheet.delete_trip_row(row_number)
        sheet.append_trip(merged)
        resorted = True
        final = merged
    else:
        sheet.update_trip_row(row_number, updates)
        final = dict(row)
        final.update(updates)

    return {
        "status": "updated",
        "resorted": resorted,
        "changed_fields": sorted(set(updates) | ({"Date"} if new_date else set())),
        "trip": _trip_summary(final),
    }


@mcp.tool
def delete_trip(
    trip_id: str | None = None,
    date: str | None = None,
    destination: str | None = None,
    confirm: bool = False,
) -> dict[str, Any]:
    """Delete a single logged trip (two-step, guarded).

    Locate by `trip_id` (unique, preferred) or by `date` — narrow with
    `destination` (substring) when needed. Acts only on an EXACT single match:
    0 or >1 returns candidates to narrow. The first call returns
    `confirm_required` with the matched trip; call again with `confirm=True` to
    actually delete.

    Args:
        trip_id: Exact Trip ID from log_trip/log_route.
        date: ISO date or keyword to locate by.
        destination: Substring to disambiguate among trips on the same date.
        confirm: Must be True on the second call to perform the deletion.
    """
    if not trip_id and not date:
        return {"status": "error", "error": "provide trip_id or date to locate the trip"}

    loc_date = parse_date(date).isoformat() if date else None
    matches = sheet.locate_trips(trip_id=trip_id, date=loc_date, destination=destination)
    if not matches:
        return {"status": "no_match", "criteria": {"trip_id": trip_id, "date": loc_date, "destination": destination}}
    if len(matches) > 1:
        return {
            "status": "ambiguous",
            "matches": [_trip_summary(r) for _, r in matches],
            "instruction": "Several trips match. Pass trip_id, or add destination, to pick one.",
        }

    row_number, row = matches[0]
    if not confirm:
        return {
            "status": "confirm_required",
            "target": _trip_summary(row),
            "instruction": "Call delete_trip again with confirm=True to remove this trip.",
        }

    sheet.delete_trip_row(row_number)
    return {"status": "deleted", "deleted": _trip_summary(row)}


# ---------------------------------------------------------------- entrypoint


def main() -> None:
    if MCP_TRANSPORT == "stdio":
        mcp.run()
    else:
        mcp.run(transport="streamable-http", host=MCP_HOST, port=MCP_PORT)


if __name__ == "__main__":
    main()
