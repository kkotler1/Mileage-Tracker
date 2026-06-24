---
name: ship-feature
description: >-
  Autonomous add-or-update-a-tool loop for the mileage-tracker MCP server. Takes a
  feature/fix ticket, makes the smallest credible change, and drives the verify
  gate to two consecutive clean runs before opening a PR. Manual-invoke only.
disable-model-invocation: true
allowed-tools:
  - Read
  - Edit
  - Write
  - Bash
  - mcp__mileage-tracker__*
---

# ship-feature

A disciplined loop for shipping one change to the mileage-tracker MCP server.
Invoked manually with a ticket: the feature to add or the bug to fix.

**Read `CLAUDE.md` first** — it holds the conventions and footguns this loop must
not regress (round-trip doubling, the derived `Deduction $`, locate-then-act on an
exact single match, the two-step delete, the TOTAL footer, the formula-injection
guard, the non-destructive testing rule).

This skill never manages secrets or credentials. It has no access to a secret
store, and it must not create, read, edit, or print credential values. If a
change needs one, it **stops and asks**.

## The loop

1. **Take the ticket.** Restate the feature/fix in one sentence and the success
   criterion as something the verify gate can check. If the ticket is ambiguous
   in a way that changes the implementation, ask before coding.

2. **Establish a baseline.** Run `bash scripts/verify.sh` once to confirm the gate
   is green before you touch anything. Reproduce the current behavior the ticket
   refers to — for a bug, write or identify a test that fails on it; for a feature,
   note what the tools do today. A change with no before-state is not yet understood.

3. **Implement the smallest credible change.** Match existing tool style (typed
   args, a model-readable docstring, a `status` field in every return). Touch only
   what the ticket requires — no adjacent refactors, no speculative options. Add or
   extend a `smoke_*.py` test against the in-memory fakes; never add a test that
   writes to the production sheet or the on-disk location cache.

4. **Run the verify gate.** `bash scripts/verify.sh`.
   - **On failure: read the actual error and trace it to root cause.** Fix the
     cause, then rerun. **Never** mask a failure — no `sleep`, no retry-until-green,
     no `--force`, no skipping a stage, no loosening an assertion to pass. If a
     test is genuinely wrong, fix the test for the right reason and say so.

5. **Repeat 3–4 until the gate passes two consecutive clean runs** (a second run
   guards against order-dependent or flaky passes). Only then is the change done.

## Stop-and-ask (do not guess)

Halt the loop and ask the operator when you hit any of:

- **A missing secret or credential** — e.g. stage 0 shows a required var unset, or
  the change needs an API key. Report what's missing; do not invent or place it.
- **Anything that would write to production data** — the production Google Sheet or
  the real `~/.mileage_locations.json`. If verifying the change seems to *require* a
  live write, stop and ask for a dedicated throwaway sheet. Never point a write tool
  at production.
- **Any irreversible action** — deleting data, force-pushing, rewriting history,
  merging.

## Stop as BLOCKED

If the **same failure** recurs for **three rounds** with no forward progress, stop
and report `BLOCKED`: the failing stage, the exact error, what you tried each
round, and your best hypothesis. Do not keep grinding or paper over it.

## Finish

When the gate is twice-green, produce:

- **Root cause** — what was actually wrong / what the feature needed, in one or two
  sentences.
- **Changed files** — the list, each with a one-line why.
- **Before/after proof** — the relevant `scripts/verify.sh` output: the failing
  stage before (or the new test failing on old code) and the all-green run after.
- **PR summary** — what changed, the isolation note (no production writes), and
  anything the reviewer should scrutinize.

Then **open a PR** (`git push` the feature branch + `gh pr create`). **Do not
merge** — leave it for review. Branch from `main`; never commit straight to it.
