"""Local JSON cache of known locations and their one-way miles_from_home."""

from __future__ import annotations

import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from typing import Any

from .config import LOCATIONS_FILE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empty_store() -> dict[str, Any]:
    return {
        "home": {"name": "Home", "notes": None},
        "locations": [],
    }


def load() -> dict[str, Any]:
    if not LOCATIONS_FILE.exists():
        LOCATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        save(_empty_store())
    with LOCATIONS_FILE.open("r") as f:
        return json.load(f)


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
