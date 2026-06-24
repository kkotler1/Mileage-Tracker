"""Stage 3 of the verify gate: a live MCP round-trip against the real server.

Boots the actual server as a subprocess over stdio (the production transport),
connects a real FastMCP client, and issues ONE read-only tool call. This is the
stage where auth/runtime errors surface — a bad service-account key, an unshared
sheet, or a gspread/google-auth breakage shows up here as a failed call, not in
production.

ISOLATION: the only tool called is `mileage_status`, which reads the sheet and
writes nothing. It runs against whatever GOOGLE_SHEET_ID the environment selects
(production by default — reads are safe). No write tool is ever invoked here.

Run: .venv/bin/python tests/mcp_smoke.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import StdioTransport

REPO_ROOT = Path(__file__).resolve().parent.parent

# Shape we require back from mileage_status — the contract a tool change must keep.
REQUIRED_KEYS = {"year", "total_miles", "deduction_usd", "rate_per_mile", "trip_count", "recent_trips"}


async def _run() -> int:
    transport = StdioTransport(
        command=sys.executable,
        args=["-m", "mileage_tracker.server"],
        env={**os.environ, "MCP_TRANSPORT": "stdio"},
        cwd=str(REPO_ROOT),
    )

    async with Client(transport) as client:
        # 1) The server booted and registered its tools over the protocol.
        tools = {t.name for t in await client.list_tools()}
        expected_tools = {"log_trip", "edit_trip", "delete_trip", "mileage_status"}
        missing = expected_tools - tools
        if missing:
            print(f"  FAIL  server did not expose expected tools: {sorted(missing)}")
            return 1
        print(f"  PASS  server booted over stdio; {len(tools)} tools registered.")

        # 2) A real read-only call completes and auth holds.
        result = await client.call_tool("mileage_status", {})
        if result.is_error:
            print("  FAIL  mileage_status returned an error result (auth/runtime?).")
            return 1
        data = result.data
        if not isinstance(data, dict):
            print(f"  FAIL  mileage_status data was {type(data).__name__}, expected dict.")
            return 1
        absent = REQUIRED_KEYS - data.keys()
        if absent:
            print(f"  FAIL  mileage_status response missing keys: {sorted(absent)}")
            return 1
        if not isinstance(data["recent_trips"], list):
            print("  FAIL  recent_trips was not a list.")
            return 1

    print(
        f"  PASS  live read-only mileage_status round-trip "
        f"(year={data['year']}, recent={len(data['recent_trips'])} rows)."
    )
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except Exception as exc:  # connection refused, auth failure, boot crash
        print(f"  FAIL  live MCP smoke raised {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
