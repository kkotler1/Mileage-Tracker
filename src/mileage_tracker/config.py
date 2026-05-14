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
