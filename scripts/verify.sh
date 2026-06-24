#!/usr/bin/env bash
# The verify gate for mileage-tracker.
#
# A single command that returns 0 only if every stage passes, run in order:
#   0. preflight   — required env vars + service-account key present
#   1. typecheck   — ruff lint + mypy (the MCP tool surface)
#   2. unit        — the stdlib smoke tests (in-memory fakes, no network)
#   3. mcp-smoke   — boot the real server over stdio, one READ-ONLY tool call
#                    (this is where auth/runtime errors surface)
#   4. regression  — exercise log_trip on fakes (round-trip math, needs_input)
#
# ISOLATION: no stage writes to the production Google Sheet or the on-disk
# ~/.mileage_locations.json. Stage 3 reads only; stage 4 runs entirely against
# in-memory fakes + a stubbed location cache.
#
# Usage: bash scripts/verify.sh
set -uo pipefail

cd "$(dirname "$0")/.." || exit 2
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"
RUFF="$ROOT/.venv/bin/ruff"
MYPY="$ROOT/.venv/bin/mypy"

# Smoke tests import a shared FakeWorksheet from a sibling test module.
export PYTHONPATH="$ROOT/tests${PYTHONPATH:+:$PYTHONPATH}"

fail() { echo ""; echo "GATE FAILED at stage: $1"; exit 1; }

for tool in "$PY" "$RUFF" "$MYPY"; do
    [ -x "$tool" ] || { echo "Missing tool: $tool — run: $PY -m pip install -e '.[dev]'"; exit 2; }
done

echo "== stage 0: preflight =="
"$PY" tests/preflight_env.py || fail "0 preflight"

echo ""
echo "== stage 1: typecheck + lint =="
"$RUFF" check src tests || fail "1 lint (ruff)"
"$MYPY" || fail "1 typecheck (mypy)"
echo "  PASS  ruff + mypy clean."

echo ""
echo "== stage 2: unit tests =="
unit_fail=0
for t in tests/smoke_*.py; do
    if "$PY" "$t" >/tmp/mt_unit.out 2>&1; then
        echo "  PASS  $(basename "$t") — $(tail -1 /tmp/mt_unit.out)"
    else
        echo "  FAIL  $(basename "$t"):"
        sed 's/^/        /' /tmp/mt_unit.out
        unit_fail=1
    fi
done
[ "$unit_fail" -eq 0 ] || fail "2 unit tests"

echo ""
echo "== stage 3: live MCP smoke (read-only) =="
"$PY" tests/mcp_smoke.py || fail "3 mcp smoke"

echo ""
echo "== stage 4: regression (log_trip, fakes) =="
"$PY" tests/regression_tools.py || fail "4 regression"

echo ""
echo "GATE PASSED — all stages green."
