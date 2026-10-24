# CLAUDE.md

@AGENTS.md

## Claude Code notes

Only what differs from the imported contract.

- Focused tests: `python3 -B -m unittest discover -s tests -p '<module>.py'`.
  Tests import sibling fixtures such as `tests/compass_fixture.py`. Some
  CLI-hint tests execute the `python -B ...` command they print, so `python`
  must be on `PATH`. One test needs a case-sensitive temp filesystem and skips
  on APFS/NTFS defaults.
- Delegating a mechanical slice to a cheaper sub-agent is allowed only inside
  the contract's change budget. The delegate gets the same focused-test rule and
  the same stop rule; a delegation does not widen either.
- Architecture map: `docs/compass.md` for the 6.0.0 loop, `docs/architecture.md`
  for the measured layer inventory. Every new `scripts/rds_*.py` must be
  classified in `tools/import_graph.py`.
