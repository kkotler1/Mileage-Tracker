# CLAUDE.md — mileage-tracker

FastMCP server that logs **Blaze Vending** business mileage to a Google Sheet
("Blaze Vending - Mileage Log", Trips tab) in IRS-log format, with a cached
per-location distance table so distances are entered once and reused. Python
3.11+, `gspread`.

This file encodes the conventions and footguns. Read it before changing a tool.

## Layout

- `src/mileage_tracker/server.py` — the FastMCP tools (the surface you edit).
- `src/mileage_tracker/sheet.py` — Google Sheet read/locate/write helpers.
- `src/mileage_tracker/locations.py` — the on-disk location + leg-distance cache.
- `src/mileage_tracker/dates.py` — date parsing ("today", "N days ago", ISO).
- `src/mileage_tracker/config.py` — env loading (reads `.env`).
- `tests/` — stdlib smoke tests + the verify-gate harness.
- `scripts/verify.sh` — the one-command gate.

## Start the server

```bash
.venv/bin/mileage-tracker                 # console script (stdio transport)
# or, equivalently:
.venv/bin/python -m mileage_tracker.server
```

Transport defaults to **stdio** (`MCP_TRANSPORT=stdio`). Set
`MCP_TRANSPORT=streamable-http` with `MCP_HOST`/`MCP_PORT` for the public server.

## Run the verify gate

```bash
bash scripts/verify.sh        # exits 0 only if every stage passes
```

Stages, in order — **0** env preflight · **1** ruff + mypy · **2** unit smoke
tests · **3** live MCP smoke (boots the real server over stdio, one read-only
`mileage_status` call — where auth/runtime errors surface) · **4** regression of
`log_trip` on in-memory fakes. First-time setup:
`.venv/bin/python -m pip install -e '.[dev]'` (installs ruff + mypy).

**Isolation rule: no stage writes to the production Google Sheet or the on-disk
`~/.mileage_locations.json`.** Stage 3 is read-only; stage 4 runs fully offline
against fakes + a stubbed location cache.

## Remote access (claude.ai)

This server is reachable on claude.ai at **`https://<host>/mcp`** (the Funnel
root) behind WorkOS AuthKit OAuth, via a shared IPv4 reverse proxy on port 443
that also fronts expense-tracker. Auth activates only when `AUTHKIT_DOMAIN` +
`PUBLIC_BASE_URL` are set and transport is http; **stdio (local Claude Code)
stays authless**. After any tool add/rename, re-sync the claude.ai connector
(Settings → Connectors → disconnect + reconnect) — claude.ai caches the tool
manifest per connector.

## Environment variables

Config loads from `.env` (see `.env.example`). `config.py` reads the **required**
ones with `os.environ[...]`, so a missing one is a hard `KeyError` at import —
stage 0 of the gate catches it first with a readable message.

| Var | Required? | Missing → | Detect |
|-----|-----------|-----------|--------|
| `GOOGLE_SERVICE_ACCOUNT_JSON` | **yes** | server won't import | stage 0 / `KeyError` |
| `GOOGLE_SHEET_ID` | **yes** | server won't import | stage 0 / `KeyError` |
| `MILEAGE_TAB_NAME` | no | defaults `Trips` | — |
| `LOCATIONS_FILE` | no | defaults `~/.mileage_locations.json` | — |
| `IRS_MILEAGE_RATE` | no | defaults `0.70` | — |
| `MCP_TRANSPORT` / `MCP_HOST` / `MCP_PORT` | no | `stdio` / `0.0.0.0` / `8765` | — |
| `AUTHKIT_DOMAIN` / `PUBLIC_BASE_URL` | no (pair) | http server runs **authless** | stage 0 **WARN** if half-set |

## Reference values (authoritative: `config.py` / `.env.example`)

- **Google Sheet:** "Blaze Vending - Mileage Log", id
  `17MnCl_I-sefPV0EcT5HiAJBKPafPJZW3ncXJ3_31F8c`, **Trips** tab.
- **Service account:** `/mnt/c/Users/kkotl/keys/mileage-tracker-sa.json` (shared
  on the sheet; the same SA the expense-tracker uses).
- **IRS rate:** `0.70`/mile default, env-overridable.

### Sheet schema (one row per trip)

`Date | Destination | Purpose | Shape | Miles | Deduction $ | Trip ID | Logged At`

A trailing **`TOTAL`** footer row carries `=SUM(...)` formulas over the data
above it. All read/locate paths skip it; `append_trip` inserts new rows in date
order *above* it.

## Tool conventions (do not regress these)

- **Distances are entered once, then cached.** A location stores one-way
  `miles_from_home`; `log_trip(shape="round_trip")` (default) **doubles** it,
  `one_way` uses it once. Unknown location or unset distance → `status="needs_input"`
  (no write) instructing the caller to `add_location` first. Inter-stop legs for
  `log_route` come from the leg-distance cache; missing ones → `status="needs_legs"`.
- **`Deduction $` is derived, never free-typed:** `miles * IRS_MILEAGE_RATE`.
  When a tool changes a row's miles, it must recompute the deduction (see
  `edit_trip`).
- **Locate a trip by `trip_id` (unique) or `date` + optional `destination`
  substring.** `edit_trip` / `delete_trip` act only on an **exact single match** —
  0 or >1 returns `no_match` / `ambiguous` candidates to narrow.
- **`edit_trip` changes only the fields you pass.** A `new_date` change re-sorts
  the sheet (delete + reinsert in date order), preserving the `TOTAL` footer.
- **`delete_trip` is two-step.** First call returns `status="confirm_required"`;
  only a second call with `confirm=True` deletes.
- **Formula-injection guard:** non-numeric cells beginning `= + - @` are written
  with a leading apostrophe (`sheet._safe_cell`). Destination/Purpose are
  free-text; keep this guard in any new write path. Numeric columns (`Miles`,
  `Deduction $`) pass through untouched.
- **Every tool returns a `status` field.** Typed args + a model-readable docstring.

## Non-destructive testing rule

**No test, script, or verify-gate stage may write to the production Google Sheet
or the on-disk location cache.** Exercise write paths against the in-memory
`FakeWorksheet` (substitute `sheet._ws`) and stub `locations.resolve` /
`locations.touch`, as the `smoke_*.py` tests and `regression_tools.py` do. The
gate honors this: stage 3 is read-only; stage 4 is fully offline. If a change
genuinely needs live write coverage, **stop and ask** for a throwaway sheet —
never point a write tool at production.

## When adding or changing a tool

1. Match existing tool style: typed args, a docstring the model reads, and a
   `status` field in every return dict.
2. Add/extend a `smoke_*.py` test against fakes (no network, no disk writes).
3. `bash scripts/verify.sh` must pass before you ship.
4. Keep diffs surgical — no unrelated refactors, no new secrets in code.
