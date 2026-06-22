import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

GOOGLE_SERVICE_ACCOUNT_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
GOOGLE_SHEET_ID = os.environ["GOOGLE_SHEET_ID"]
MILEAGE_TAB_NAME = os.environ.get("MILEAGE_TAB_NAME", "Trips")
LOCATIONS_FILE = Path(
    os.path.expanduser(os.environ.get("LOCATIONS_FILE", "~/.mileage_locations.json"))
)

IRS_MILEAGE_RATE = float(os.environ.get("IRS_MILEAGE_RATE", "0.70"))

MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "stdio")
MCP_HOST = os.environ.get("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.environ.get("MCP_PORT", "8765"))

# OAuth (WorkOS AuthKit). When BOTH are set AND transport is http, the public
# streamable-http server requires a valid WorkOS-issued token — needed for the
# claude.ai connector (which mandates OAuth 2.1 + Dynamic Client Registration).
# claude.ai registers against WorkOS, not this server; FastMCP only validates the
# tokens. Leave AUTHKIT_DOMAIN blank to run authless; stdio (local Claude Code)
# stays authless regardless. This server is served at the Funnel root, so
# PUBLIC_BASE_URL is the bare domain (no path), e.g. https://minisforum.tail2b7516.ts.net
AUTHKIT_DOMAIN = os.environ.get("AUTHKIT_DOMAIN", "")
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "")
