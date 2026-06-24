"""Stage 0 of the verify gate: fail loud on a misconfigured environment.

The server reads its config at import time (config.py does os.environ[...] for the
required vars), so a missing credential otherwise surfaces as an opaque KeyError
deep in a tool call. This check names the problem up front.

Required (missing → exit 1, the server cannot run at all):
  GOOGLE_SERVICE_ACCOUNT_JSON, GOOGLE_SHEET_ID.

Run: .venv/bin/python tests/preflight_env.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(REPO_ROOT / ".env")


def main() -> int:
    errors: list[str] = []

    sa_path = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "")
    if not sa_path:
        errors.append("GOOGLE_SERVICE_ACCOUNT_JSON is unset (service-account JSON path).")
    elif not Path(sa_path).is_file():
        errors.append(f"GOOGLE_SERVICE_ACCOUNT_JSON points at a missing file: {sa_path}")

    if not os.environ.get("GOOGLE_SHEET_ID", ""):
        errors.append("GOOGLE_SHEET_ID is unset (target spreadsheet id).")

    # Advisory: OAuth is half-configured (one of the pair set without the other).
    # Harmless offline, but on the http server it means auth silently stays off.
    authkit = os.environ.get("AUTHKIT_DOMAIN", "")
    base_url = os.environ.get("PUBLIC_BASE_URL", "")
    if bool(authkit) != bool(base_url):
        print(
            "  WARN  AUTHKIT_DOMAIN / PUBLIC_BASE_URL are half-set — the http server\n"
            "        runs AUTHLESS until BOTH are present. Fine for stdio/local use."
        )

    if errors:
        print("  FAIL  environment preflight:")
        for e in errors:
            print(f"          - {e}")
        return 1

    print("  PASS  environment preflight (required vars present, SA key found).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
