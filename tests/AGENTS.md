# tests/ rules

Applies to everything under `tests/`. Root `AGENTS.md` and
`docs/development-contract.md` still apply.

## Naming and layout

- One module per target: `tests/test_<module>.py` covers `scripts/<module>.py`.
  The planner uses this convention plus a scan of `from rds_* import ...`, so a
  file named differently will report `NO_TESTS_MAPPED`.
- Fixtures live beside their consumers as `tests/<name>_fixture.py` and are
  imported as siblings. This is why tests must run through
  `python -B -m unittest discover -s tests -p '<module>.py'` rather than by
  path: importing a test module directly breaks sibling imports.
- Some CLI tests execute the command string they print, so `python` must be on
  `PATH`. One test needs a case-sensitive temp filesystem and skips otherwise.

## What a test is allowed to assert

- Assert observable behaviour: exit status, ledger rows, receipts, verdicts.
- Do not weaken an assertion, raise a timeout, or add a skip to make a failing
  change pass. That conversion is the bug, not the fix.
- Do not write tests for `docs/`, `AGENTS.md`, or an unchanged helper.
- A test that only proves "the function was called" is weaker than one that
  proves the decision changed; prefer the latter when the change is a decision.

## Running

- Focused only during implementation, 1-2 modules. The release owner runs the
  full suite once per candidate.
- If a flake occurs, retry at most once and then record it. Do not loop.
