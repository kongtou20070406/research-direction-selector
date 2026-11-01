# RDS agent contract

@docs/development-contract.md

RDS is an evidence-driven academic autoresearch kernel (Python 3.11+, library + CLI,
no server). The imported contract is authoritative for invariants and the Compass
architecture. This file keeps only what changes agent behaviour turn to turn.

## Commands

- focused test: `python -B -m unittest discover -s tests -p 'test_<module>.py'`
- plan check:   `python -B tools/validate_plan.py --base <ref>`
- layers:       `python -B tools/import_graph.py > .import-graph.json`
- graph:        `code-review-graph update` (no hooks; never auto-updated)

## Hard limits

- <= 250 net behaviour LOC and <= 8 behaviour files per change. Over -> split.
- One ledger only. No second scheduler, sidecar db, threads, or custom locks.
- Never widen a cap, skip a receipt re-check, or weaken a verifier to make
  something pass.
- Never report `INPUT_REPORTED` as `SUPPORT`/`REFUTED`.

## Stop rules

- Same command failing the same way twice -> stop, record `UNKNOWN`, report.
- A model reviewing its own diff is context, not a verifier.
- Do not touch: secrets / `.env`, release tags, `main`, force-push.
- Commits stay local unless the maintainer asks to push.

## Where things are

Contract `docs/development-contract.md` · Compass `docs/compass.md` ·
**DL priors `docs/neural-frontier-INDEX.md`** - one page, organised by symptom
(S1-S10), each with the first action and the ids to pull. Read it before
designing a DL run; detail by id via `python -B tools/frontier_search.py A4`.
local rules `scripts/AGENTS.md` · `tests/AGENTS.md` · `benchmark/AGENTS.md`
