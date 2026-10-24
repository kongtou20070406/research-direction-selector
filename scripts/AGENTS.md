# scripts/ rules

Applies to everything under `scripts/`. Root `AGENTS.md` and
`docs/development-contract.md` still apply.

## Adding a module

- Name it `rds_<topic>.py`. A new `scripts/rds_*.py` **must** be added to the
  matching layer in `tools/import_graph.py` `GROUPS` in the same change. An
  `UNKNOWN` layer is a hard failure: `unclassified_edges` must stay empty.
- Pick the layer from what the module does, not from what imports it:
  `L0` bounded IO / mutation / SQLite, `L1` ledger / CAS / TMS / receipts /
  verification backends, `L2` analysis and structure, `L3` decisions and loops,
  `L4` entry points (CLI, app, workers, hooks).
- Layers only depend **downward**. `SHARED` may be used by anyone. If a change
  adds an `upward` edge or a `cycles` entry, the change is wrong, not the graph.

## What does not belong here

- One-off analysis or evidence scripts go in `docs/<topic>/`, not `scripts/`.
  They are allowed to be scratch code and are not unit-tested.
- Tests go in `tests/`. Fixtures go in `tests/` next to their test module.
- No second ledger, scheduler, sidecar database, thread pool or custom lock.
  Persistence goes through the existing `ProjectStore`.

## Changing a module that has pinned tests

Some tests pin a function's AST hash or its exact output shape. Before editing
such a function body, grep `frozen_ast`, `ast.dump` and `pin` in `tests/`.
A rename inside the body is a byte change and will fail those tests by design.

Pick the acceptance scope by the symbol you changed, not by a filename wildcard.
