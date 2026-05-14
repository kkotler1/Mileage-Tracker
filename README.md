# mileage-tracker

MCP server for logging business mileage to a Google Sheet. Resolves named places ("Potomac Swim Club") against a cached distance file — first mention prompts for the one-way miles, every mention after is free. Default trip shape is round-trip from home.

Storage:
- **Trip log**: Google Sheet, one row per trip in IRS format
- **Location cache**: local JSON file (`~/.mileage_locations.json`)

Same backend pattern as the receipt extractor (Sheet + local cache file).

## Tools

| Tool | Purpose |
|---|---|
| `log_trip` | Log a trip ("Potomac Swim Club", round-trip from home). Returns `needs_input` if the place is unknown. |
| `add_location` | Register a place with `miles_from_home` + aliases. Use after a `needs_input`. |
| `resolve_location` | Look up which saved place a query matches. |
| `list_locations` | Show every saved place, most-used first. |
| `mileage_query` | Sum miles + deduction for a year or date range, optionally grouped by month/destination/purpose. |
| `mileage_status` | Quick YTD snapshot with recent trips. |

### Example flow

```
You:    "I went to the Potomac Swim Club twice this week"
Claude: log_trip(destination="Potomac Swim Club", count=2)
Server: needs_input — unknown location
Claude: "What's the one-way distance from home?"
You:    "8.5 miles"
Claude: add_location(name="Potomac Swim Club", miles_from_home=8.5)
        log_trip(destination="Potomac Swim Club", count=2)
Server: 2 round-trips logged, 34.0 miles, $23.80 deduction
        (appends 2 rows to the Trips tab in your Google Sheet)
```

Next time you say "I went to Potomac Swim Club", no question — cached.

## Sheet format

The Trips tab (auto-created on first write):

| Date | Destination | Purpose | Shape | Miles | Deduction $ | Trip ID | Logged At |
|------|-------------|---------|-------|-------|-------------|---------|-----------|
| 2026-05-12 | Potomac Swim Club | vending route | round_trip | 17.0 | 11.90 | trip_a1b2c3d4 | 2026-05-12T17:30:00+00:00 |

Round-trips are **one row** with the full round-trip miles. The `Shape` column tells you whether it was doubled.

## Setup

### 1. Google service account

If you already have one for the receipt extractor, reuse it — share your new mileage spreadsheet with that service account's email (Editor access).

Otherwise:
1. Google Cloud Console → IAM & Admin → Service Accounts → Create
2. Create a JSON key, download it, save somewhere safe
3. Enable the **Google Sheets API** and **Google Drive API** in that project
4. Create a Google Sheet, share it with the service account email (Editor)
5. Copy the spreadsheet ID from the URL: `/spreadsheets/d/<THIS_PART>/edit`

### 2. Environment

Copy `.env.example` to `.env` and fill in:

- `GOOGLE_SERVICE_ACCOUNT_JSON` — path to the key file
- `GOOGLE_SHEET_ID` — spreadsheet ID
- `MILEAGE_TAB_NAME` — defaults to `Trips`, auto-created on first write
- `LOCATIONS_FILE` — defaults to `~/.mileage_locations.json`
- `IRS_MILEAGE_RATE` — dollars per mile (verify current year at irs.gov)

### 3. Install + run

```bash
pip install -e .
python -m mileage_tracker.server      # stdio for Claude Code
```

### 4. Wire into Claude Code (stdio)

`~/.claude.json` or `.mcp.json`:

```json
{
  "mcpServers": {
    "mileage-tracker": {
      "command": "python",
      "args": ["-m", "mileage_tracker.server"],
      "cwd": "/path/to/mileage-tracker",
      "env": {
        "GOOGLE_SERVICE_ACCOUNT_JSON": "/path/to/service-account.json",
        "GOOGLE_SHEET_ID": "...",
        "IRS_MILEAGE_RATE": "0.70"
      }
    }
  }
}
```

### 5. Deploy to Minisforum (HTTP transport, remote MCP URL)

```bash
MCP_TRANSPORT=streamable-http MCP_PORT=8765 python -m mileage_tracker.server
```

Systemd unit (matching the other `open-*` services):

```ini
[Unit]
Description=mileage-tracker MCP server
After=network.target

[Service]
Type=simple
User=kyle
WorkingDirectory=/opt/mileage-tracker
EnvironmentFile=/opt/mileage-tracker/.env
ExecStart=/opt/mileage-tracker/.venv/bin/python -m mileage_tracker.server
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

Expose over Tailscale (or your existing reverse proxy) and add the URL as a remote MCP server in claude.ai.

## Notes

- `miles_from_home` is always **one-way**. `log_trip` doubles it for round-trips. Don't store round-trip values.
- Locations file is a plain JSON — safe to edit by hand if you mistyped a distance.
- If you move, update the home row's `notes` in the JSON and re-verify every distance.
- Multi-stop chains (Walmart → Sam's → home) aren't supported in v1 — log each as its own round-trip. Real fix is a pair-distance cache; punt until you actually need it.
