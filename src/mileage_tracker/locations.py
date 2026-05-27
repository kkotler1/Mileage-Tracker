"""Local JSON cache of known locations and their one-way miles_from_home."""

from __future__ import annotations

import json
import logging
import os
import secrets
import tempfile
from datetime import datetime, timezone
from typing import Any

from .config import LOCATIONS_FILE

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_store() -> dict[str, Any]:
    return {
        "home": {"name": "Home", "notes": None},
        "locations": [],
        "distances": {},
    }


def _leg_key(id_a: str, id_b: str) -> str:
    """Canonical key for a pair of location IDs — order-independent."""
    return ":".join(sorted([id_a, id_b]))


def load() -> dict[str, Any]:
    if not LOCATIONS_FILE.exists():
        LOCATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        save(_empty_store())
        return _empty_store()
    with LOCATIONS_FILE.open("r") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as e:
            backup = LOCATIONS_FILE.with_suffix(f".corrupt.{_now_iso().replace(':', '-')}.json")
            LOCATIONS_FILE.rename(backup)
            log.error(
                "locations.json is corrupt (%s) — backed up to %s, starting fresh",
                e, backup,
            )
            save(_empty_store())
            return _empty_store()


def save(store: dict[str, Any]) -> None:
    LOCATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".mileage_locations.", dir=str(LOCATIONS_FILE.parent)
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(store, f, indent=2)
        os.replace(tmp_path, LOCATIONS_FILE)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def get_home() -> dict[str, Any]:
    return load()["home"]


def find_candidates(query: str) -> list[dict[str, Any]]:
    needle = query.strip().lower()
    if not needle:
        return []
    store = load()
    exact: list[dict[str, Any]] = []
    partial: list[dict[str, Any]] = []
    for row in store["locations"]:
        name = (row.get("name") or "").lower()
        aliases = [a.lower() for a in (row.get("aliases") or [])]
        if name == needle or needle in aliases:
            exact.append(row)
        elif needle in name or any(needle in a for a in aliases):
            partial.append(row)

    def _sort_key(r: dict[str, Any]) -> tuple[int, str]:
        return (-(r.get("use_count") or 0), r.get("last_used_at") or "")

    return sorted(exact, key=_sort_key) + sorted(partial, key=_sort_key)


def resolve(query: str) -> dict[str, Any] | None:
    candidates = find_candidates(query)
    if not candidates:
        return None
    if len(candidates) == 1:
        return candidates[0]
    needle = query.strip().lower()
    for c in candidates:
        if (c.get("name") or "").lower() == needle:
            return c
    return None  # ambiguous


def upsert(
    name: str,
    miles_from_home: float | None = None,
    aliases: list[str] | None = None,
    notes: str | None = None,
) -> dict[str, Any]:
    store = load()
    same = next(
        (r for r in store["locations"] if (r.get("name") or "").lower() == name.lower()),
        None,
    )
    if same:
        if miles_from_home is not None and same.get("miles_from_home") is None:
            same["miles_from_home"] = miles_from_home
        if aliases:
            same["aliases"] = sorted({*(same.get("aliases") or []), *aliases})
        if notes and not same.get("notes"):
            same["notes"] = notes
        save(store)
        return same

    row = {
        "id": "loc_" + secrets.token_hex(4),
        "name": name,
        "aliases": sorted(aliases or []),
        "miles_from_home": miles_from_home,
        "notes": notes,
        "use_count": 0,
        "last_used_at": None,
        "created_at": _now_iso(),
    }
    store["locations"].append(row)
    save(store)
    return row


def get_leg_distance(id_a: str, id_b: str) -> float | None:
    """Look up cached one-way miles between two location IDs (order-independent)."""
    distances = load().get("distances", {})
    val = distances.get(_leg_key(id_a, id_b))
    return float(val) if val is not None else None


def set_leg_distance(id_a: str, id_b: str, miles: float) -> None:
    """Store one-way miles between two location IDs (order-independent)."""
    store = load()
    store.setdefault("distances", {})[_leg_key(id_a, id_b)] = miles
    save(store)


def set_miles_from_home(location_id: str, miles: float) -> None:
    """Overwrite miles_from_home for a location by ID."""
    store = load()
    for r in store["locations"]:
        if r.get("id") == location_id:
            r["miles_from_home"] = miles
            break
    save(store)


def touch(location_id: str) -> None:
    store = load()
    for r in store["locations"]:
        if r.get("id") == location_id:
            r["use_count"] = (r.get("use_count") or 0) + 1
            r["last_used_at"] = _now_iso()
            break
    save(store)


def all_locations() -> list[dict[str, Any]]:
    rows = load()["locations"]
    return sorted(
        rows,
        key=lambda r: (-(r.get("use_count") or 0), r.get("name") or ""),
    )


def list_leg_distances() -> list[dict[str, Any]]:
    """Return all cached inter-stop distances with resolved location names."""
    store = load()
    loc_index = {r["id"]: r["name"] for r in store["locations"]}
    result = []
    for key, miles in sorted(store.get("distances", {}).items()):
        id_a, id_b = key.split(":", 1)
        result.append({
            "from": loc_index.get(id_a, id_a),
            "to": loc_index.get(id_b, id_b),
            "miles": miles,
        })
    return result
