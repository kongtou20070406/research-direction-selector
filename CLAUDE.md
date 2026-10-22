# CLAUDE.md

The operational contract for every agent in this repository is in AGENTS.md.
It is imported here so the two files never drift:

@AGENTS.md

## Claude Code notes

- Architecture: [docs/compass.md](docs/compass.md) covers the 6.0.0 Compass loop (Advisor, jumps, goal-bound RSI, verification, interrupts). [docs/architecture.md](docs/architecture.md) covers the measured layer inventory, and `tools/import_graph.py` must classify every new `scripts/rds_*.py` module.
- Run focused tests with `python3 -B -m unittest discover -s tests -p '<module>.py'`; tests import sibling fixtures such as `tests/compass_fixture.py`.
  - Some CLI-hint tests execute the printed `python -B ...` command, so they need `python` on `PATH`.
  - One test needs a case-sensitive temp filesystem. It skips on APFS/NTFS defaults.
- Delegate mechanical slices to cheaper sub-agents (for example GPT-6.1 Sol) under the AGENTS.md limits:
  - at most 250 net LOC per change;
  - no custom concurrency locks;
  - focused tests only, never a redundant full-suite run.

  The release owner runs the full suite once per candidate.
- Epistemic red line: never fabricate results, weaken an independent oracle or verifier, or report `INPUT_REPORTED` (a script ran) as scientific `SUPPORT`/`REFUTED`.
- Commits stay local unless the maintainer asks to push. Never push to `main`, force-push, or publish releases from an agent session.
