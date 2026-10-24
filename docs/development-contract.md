# RDS Development Contract

Full version of the operating contract. `AGENTS.md` imports this file and keeps
only the commands, the hard limits and the stopping rules, so an agent does not
have to hold 90 lines of prose in context to start work.

Read this file before: touching the ledger, CAS, TMS, receipts, budgets,
verification, the Compass loop, or any module listed in section III.

---

## I. Architectural Invariants (Non-Negotiable)

1. **Single Authoritative Ledger**: Reuse `ProjectStore` events, Content-Addressed
   Storage (`CAS`), and the Truth Maintenance System (`TMS` persistent AND/OR
   dependencies, immutable revisions, retractions, conflict checks, and
   `--saved-dependencies`). NEVER introduce a second ledger, parallel state
   machine, alternate scheduler, or sidecar database.
2. **Task Truth != Scientific Support**: Completing a command or receiving
   `verified=true` from a candidate tool (`INPUT_REPORTED`) is NOT scientific
   confirmation. Scientific status (`SUPPORT`, `REFUTED`, `UNKNOWN`,
   `NEEDS_EVIDENCE`, `RESOLVE_PREMISE`) is determined exclusively by replaying
   original measured outputs through the frozen independent verifier.
3. **Fail-Closed Epistemics & Budgets**:
   - Unknown goals, thresholds, rival predictions, commands, or budgets stay
     explicitly `UNKNOWN`. Never guess, interpolate, or invent fallback defaults.
   - Missing, tampered, stale, or timed-out execution receipts (`r['receipt']`,
     stdout/stderr SHA256 identities, declared output byte bindings) must fail
     closed and record negative/unknown evidence. Never silently re-buy or re-run
     a failed/uncertain paid step.
   - Never widen pilot caps (e.g., 10s pilot cap / 7.2s reservation), campaign
     caps (100s), conditional forecast hard timeouts (20s), or outer drive
     timeouts (50s) to make a failing test pass.

## II. Execution Discipline (Anti-Bloat & Anti-Drift)

1. **Forensic Trace Before Any Edit**:
   - Do not design new abstractions while reading the problem.
   - First locate the exact existing functions, callers, and CLI entry points in
     `scripts/` and `tests/`. State the root cause or insertion point in <= 5
     bullet lines citing `file:line` before touching code.
2. **Strict File & LOC Budget**:
   - Edit ONLY the files explicitly named in the task scope. If an unlisted file
     appears broken, log `OUT_OF_SCOPE_DISCOVERY: <file:line> - <1 sentence>` and
     leave it untouched.
   - Keep every change at or under **250 net behaviour LOC and 8 behaviour
     files**. Split larger work into independently verified slices. Prose,
     evidence archives (`docs/`), one-off analysis scripts under `docs/`, and
     generated artifacts do not count against the budget, because they do not
     change behaviour - but they still need the commit message to say what
     evidence they add.
   - Do NOT write defensive guard-clause towers for impossible internal states.
     Prefer a single fail-closed assertion that preserves the full return object
     (`r`) for diagnostics.
3. **Concurrency, Subprocess & Path Safety**:
   - Do not introduce threads, async worker pools, or custom file locks. Ledger
     writes already serialize through SQLite `BEGIN IMMEDIATE`; Compass records
     add a head guard, not a lock.
   - On `subprocess.run(..., timeout=...)`, always catch `TimeoutExpired` only to
     record partial `stdout`/`stderr`, current phase/attempt identity, and charged
     cost into the existing ledger before re-raising or returning a structured
     failure receipt.
   - On Windows/cross-platform path checks, compare `Path(...).resolve()`
     identities rather than raw lexical strings while preserving explicit
     `--root` containment checks.
4. **Test-Fest Prohibition**:
   - During implementation, run ONLY the 1-2 focused test modules directly
     covering your change, as `python -B -m unittest discover -s tests -p
     '<module>.py'`. Tests import sibling fixtures, so `discover -s tests` is
     required.
   - Never run the full suite redundantly; the release owner runs it once per
     candidate.
   - Never write unit tests for documentation, `AGENTS.md`, or unchanged helper
     functions.
   - If an environment/harness flake occurs, retry at most **ONCE**. Never loop on
     failing tests by weakening assertions or increasing timeouts.
5. **No Diagnosis-Only Stopping**:
   - Do not stop after summarizing what should be changed. Execute the code edit,
     run the focused CLI/test verification, and output the final 6-line execution
     ledger (see section VI).

## III. Compass Architecture Map (6.0.0)

Read [compass.md](compass.md) before touching these modules.

- `scripts/rds_compass_model.py` (SHARED): exact Fraction AST dialect, EIG over
  rival partitions (UNDEFINED is its own class; `EQUIVALENT_PARTITION` is never
  bought twice), observational equivalence, counterexample-filtered synthesis,
  per-sample profiles.
- `scripts/rds_compass_store.py` (L1): `COMPASS_*` rows in the existing `events`
  table, each a CAS-bound record in a parent-digest hash chain. State is folded
  from rows plus original receipts on every read.
- `scripts/rds_compass_declaration.py` (L1): declaration to kernel contract,
  protocol and plan. Unknowns stay `UNKNOWN`; controller defaults are listed in
  `defaulted`; each candidate has one declared retry slot for interrupted attempts.
- `scripts/rds_compass.py` (L3): ingest, basin, `decide` (pure), jump packet and
  agent stdin dispositions, verification (exact replay, Lean/closed-rational,
  `rds_math_router` polynomial certificate), working set, TMS mirror, report.
- `scripts/rds_compass_rsi.py` (L3): goal-bound tools.
  - Every tool passes the `extract_function` allowlist.
  - Qualification uses a receipted holdout oracle triple: known positive,
    discriminating mutant, boundary/counterexample witness.
  - Tools are never self-certified.
  - The library is create-only, and provenance is re-verified on reuse.
  - Tools are retracted when a later receipt contradicts their qualifying law.
- `scripts/rds_compass_drive.py` (L3): the unattended loop. SIGTERM/SIGINT settle
  through `rds_interrupt`: the kernel records the in-flight attempt `INTERRUPTED`
  and re-raises.

Compass invariants:
- Measurements are `INPUT_REPORTED`; replay is `CONSISTENT`/`REFUTED`/`UNTESTED`;
  `scientific_support` is always `UNKNOWN`.
- Never store a derived status as truth; recompute it.
- Never weaken the oracle triple, the confirmation gate, the receipt re-check or
  the polynomial-rival handoff to make a campaign finish.
- A missing tolerance or threshold stays `UNKNOWN` and becomes `JUDGMENT_REQUIRED`.

---

## IV. Multi-Round Development Rules

These exist so that the N-th round of agent work is as reviewable as the first.
Each rule names its enforcement layer. A rule that is only prose is a rule that
has already failed once.

**Risk is judged by what the change touches, not by how much it writes.** An
earlier draft of this contract gated on "<= 250 net behaviour LOC and <= 8
files". That was withdrawn on 2026-10-24: a line budget optimises for passing
the gate, and the cheapest way to cut lines is to cut tests, comments, or to
cram code into existing files to avoid the file cap. An open-ended task also has
a natural minimum slice of four files (module, test, wiring, doc), so a tight
budget forces the test to be the thing that gets thin. The gate is now the
*class* of the change:

| Class | What it is | What it must bring |
|---|---|---|
| **R0** | certification assets: CAS, TMS store, receipts, result records, verifiers, formal kernel, budgets | a pre-registered judgement protocol written before the run, a reverse test, and an independent recompute of the affected hash/receipt/budget |
| **R1** | decision logic: `decide`, advisor selection, thresholds, comparison arms, test contracts | a comparison arm and a holdout assertion, plus the same command before and after with both outputs kept |
| **R2** | capability: new modules, new CLI paths, benchmarks | a mapped test for every changed script, no new `upward` edges, new modules classified in `import_graph` |
| **R3** | no behaviour: prose, `docs/`, one-off analysis under `docs/`, tooling, fixtures | nothing, and it must not touch an R0-R2 path |

Net LOC is still printed, as advice. It is not a gate.

| # | Rule | Enforced by | Layer |
|---|---|---|---|
| 1 | Risk class governs the evidence, not the size (table above) | `tools/validate_plan.py --base <ref>`; `--strict` is what CI uses | mechanical |
| 2 | Every changed `scripts/*.py` at R2 or above must map to a named test | same planner; `NO_TEST_MAPPED` in `notes` | mechanical |
| 3 | Dependency direction: layers may only depend downward (`L0 < L1 < L2 < L3 < L4`), `SHARED` may be used by anyone | `tools/import_graph.py` -> `upward`, `cycles`, `mutual_pairs` counts must not increase | mechanical |
| 4 | A new `scripts/rds_*.py` must be classified in `tools/import_graph.py` `GROUPS`; an `UNKNOWN` layer is a hard failure | `tools/import_graph.py` -> `unclassified_edges` empty | mechanical |
| 5 | Same command failing the same way twice is a stop signal, not a retry cue | prose + review | judgement |
| 6 | Never replace the loop with "review your own work". A model critique is context, not a verifier | prose | judgement |
| 7 | Garbage collection: one small cleanup batch per week, one category of cleanup only, reviewable in under a minute | schedule + review | process |
| 8 | Every round ends with the handoff ledger in section VI, including remaining `UNKNOWN` items | review | process |

Rule 5 is the one most often broken. A failing command that is re-run with a
cosmetic change is not progress; it is evidence that the premise is wrong. Stop,
record the blocker, and report it.

Rule 6 matters because the failure mode is subtle: a model asked to review its
own diff will usually produce a plausible review, and that review is not
independent evidence. Independence has to come from a different layer (the
planner, the graph, CI, or another model that did not write the change).

### Baseline (measured 2026-10-24, `release/6.0.0-compass` @ `d6b60d6`; debt retired in `e2089af`)

```
modules 177 | edges 664 | upward 17 | cycles 3 | mutual_pairs 39 | unclassified 0
```

Rule 3 is enforced against **these numbers**, not against zero. The remaining
17 upward edges and 3 cycles are existing debt: a change may not add to them,
and the weekly cleanup batch (rule 7) retires them one at a time. A change that
reduces them is a win and should say so in the handoff ledger.

`e2089af` retired one edge and one cycle (`upward 18 -> 17`, `cycles 4 -> 3`,
`mutual_pairs 40 -> 39`). It moved the publication primitives
(`atomic_bytes` / `load_object` / `graph_lock` / `adoption_lock` /
`workspace_publication`) from `rds_meta` (L3) to `rds_workspace` (L1), and the
shared value and rule-schema contract (`require` / `json_digest` / `identity` /
`validate_rule`) to `rds_core` (SHARED). `modules` is unchanged; `edges` moved
`663 -> 664` because the new leaf imports are one-directional.

The CI gate hard-codes the same `BASE` map in
`.github/workflows/change-gate.yml`; it must be re-synced to `17 / 3 / 39`
before the tighter numbers are enforced there.

#### Retained upward edges — KNOWN_DEBT

Every remaining edge was read in place; each one either imports a real business
decision (not a helper) or would need a duplicate definition to remove. The
test applied to all 17: can the imported symbol move to a lower layer without
(a) creating a new upward edge such as `SHARED -> L1`, (b) duplicating the
implementation, or (c) widening a verifier? None passed.

| # | upward edge | site | why it stays |
| ---: | --- | --- | --- |
| 1 | `rds_autonomy` L3 -> `rds_autonomy_worker` L4 | `scripts/rds_autonomy.py:71` | Needs the worker's response-file naming (`output_paths`) to prove a repair route owns its `outpaths`. The only L1 candidates (`rds_outbox`, `rds_run_records`) own notification envelopes and pure run-record decoding; inserting the contract there would split one naming rule across two modules. |
| 2 | `rds_campaign` L1 -> `rds_owned_advisor` L3 | `scripts/rds_campaign.py:464` | Campaign settlement rebuilds owned-Advisor state and reads `OWNED_PREFIX` to match original attempt/completion identities. Business cross-check, not a helper import. |
| 3 | `rds_campaign` L1 -> `rds_hypergraph` L2 | `scripts/rds_campaign.py:499` | Validates a dependency snapshot read back from the ledger. `_validate` is bound to `DEFAULT_LIMITS`/`HARD_LIMITS` in the hypergraph module, so a move drags the whole limit table with it. |
| 4 | `rds_discrimination` L2 -> `rds_advisor_search` L3 | `scripts/rds_discrimination.py:13` | Needs `evaluate_condition`, the three-valued fact evaluator. It cannot sit in `SHARED` because it reaches `rds_artifacts.ArtifactFact` (L1) through `_evidence_status`, which would create a `SHARED -> L1` edge. |
| 5 | `rds_method_revision` L1 -> `rds_autonomy` L3 | `scripts/rds_method_revision.py:69` | Refuses a revision while a live controller holds a budget reservation; `controller_reservation` reads autonomy drive events and the caller PID. Real controller state. |
| 6 | `rds_method_revision` L1 -> `rds_owned_advisor` L3 | `scripts/rds_method_revision.py:106` | `validate_policy` is a 130-line policy admission check that itself calls the autonomy and domain-confirmation validators. Moving it moves the policy decision, not a utility. |
| 7 | `rds_project` L1 -> `rds_owned_advisor` L3 | `scripts/rds_project.py:438,480,735,1116` | Four of the five sites are `lifecycle_provider('advisor')` provider callbacks; the fifth is `advisor_state()` rebuilding owned state. The hook indirection is the intended seam (`rds_project_hooks`), and the scanner still records the registered provider. |
| 8 | `rds_project` L1 -> `rds_advisor_search` L3 | `scripts/rds_project.py:593` | Maintenance runs are gated on the Advisor's dependency review and goal-contribution verdict. Business decision. |
| 9 | `rds_project_hooks` L1 -> `rds_owned_advisor` L3 | `scripts/rds_project_hooks.py:18` | The lazy import *is* the cycle break: the hook module exists so providers load on demand. Replacing it with a static import would make the graph worse, and removing it would break `provider('advisor')` for callers that never call `register`. |
| 10 | `rds_project_lifecycle` L1 -> `rds_owned_advisor` L3 | `scripts/rds_project_lifecycle.py:353` | Same `validate_policy` admission check as #6. |
| 11 | `rds_steering` L1 -> `rds_dialogue` L4 | `scripts/rds_steering.py:254` | Optional presentation-layer dialogue rendering behind `if dialogue:`. Removable only by adding a builder parameter and threading it through every `plan(...)` call site; deferred as a separate change rather than bundled here. |
| 12 | `rds_theory_progression` L1 -> `rds_operators` L3 | `scripts/rds_theory_progression.py:161,386` | Runs `BoundedFiniteModelOperator` to check a Cayley property. This is the verification computation itself, not a borrowed helper. |
| 13 | `rds_tms_store` L1 -> `rds_hypergraph` L2 | `scripts/rds_tms_store.py:10` | Borrows four symbols; two (`review_hypergraph`, `audit_sources`) are the integrity review. Moving only `ASSURANCE` and `_validate` would leave the edge in place. |
| 14 | `rds_tool_applicability` L1 -> `rds_owned_advisor` L3 | `scripts/rds_tool_applicability.py:146` | Task premises must be evaluated over owned evidence, so it calls the Advisor collectors `_collect`/`_facts`. Private cross-layer borrow: real debt, but extracting a read-only evidence service is a larger design change. |
| 15 | `rds_tool_applicability` L1 -> `rds_advisor_search` L3 | `scripts/rds_tool_applicability.py:147` | Same `evaluate_condition` as #4. |
| 16 | `rds_tool_calls` L1 -> `rds_owned_advisor` L3 | `scripts/rds_tool_calls.py:166` | Tool composition asks the owned Advisor for `review`. Business decision. |
| 17 | `rds_tools` L1 -> `rds_quick` L4 | `scripts/rds_tools.py:21` | Tool execution runs through the QUICK execution channel (`execute`). This is the widest layer gap in the list; sinking it means restructuring QUICK, not moving a function. |

#### Retained cycles — KNOWN_DEBT

| cycle | modules | why it stays |
| --- | --- | --- |
| 49 modules | `rds_advisor` -> ... -> `rds_workspace` -> `rds_advisor` | Built from the L1 ledger modules calling L3 Advisor/autonomy review while the L3 modules call back into L1 reads. `docs/architecture.md` documents these callback pairs one by one. Breaking it means moving Advisor policy into L1. |
| 4 modules | `rds_affine_toolchain` -> `rds_builtin_verifiers` -> `rds_theory_progression` -> `rds_verify` | All four are L1; the cycle is verifier registration among certificate backends, not a downward violation. Splitting it needs a registration indirection that does not exist yet. |
| 2 modules | `rds_compass` <-> `rds_compass_rsi` | Genuine two-way business coupling: `compass` calls `extract`/`escalate`/`propose`/`retract_unbound`, and `rds_compass_rsi` calls back into `compass` state and builders. Merging them would grow one module past 900 lines; splitting needs a third module for the shared campaign state. |

Reproduce with:

```bash
python -B tools/import_graph.py > .ig.json
python -c "import json;d=json.load(open('.ig.json'));print(d['counts'],len(d['cycles']),len(d['unclassified_edges']))"
```

---

## V. code-review-graph (crg) MCP Tools

**This repository has a code knowledge graph. Use the `crg` MCP tools to narrow
scope first, then read the source.** The graph is cheaper than scanning flat
modules and gives structural context (callers, dependents, test coverage) that
file search cannot.

The graph is an **index, not an authority**. Everything in section I still holds,
and the graph never overrides it.

### Verify in the source

- Narrow scope with the graph, then read the source. Do not change code from
  graph output alone.
- For any non-trivial change, read the implementation and the relevant tests
  before concluding.
- Always verify the exact source when touching the ledger, CAS, TMS, receipts,
  budgets, verification or the drive loop.
- When the graph and the source disagree, the source wins. The graph may be stale
  or may not model that relationship.
- An empty graph result can mean "not indexed" or "not statically visible", not
  "does not exist".

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes_tool` or `query_graph_tool` instead
  of Grep
- **Understanding impact**: `get_impact_radius_tool` instead of manually tracing
  imports
- **Code review**: `detect_changes_tool` + `get_review_context_tool` instead of
  reading entire files
- **Finding relationships**: `query_graph_tool` with
  callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview_tool` +
  `list_communities_tool`

### Workflow

No hooks are installed here, so the graph does **not** auto-update. Refresh it
explicitly before a review:

```bash
code-review-graph build            # full rebuild
code-review-graph update           # incremental
```

The database lives in `.code-review-graph/`, which is gitignored.

---

## VI. Round Handoff Ledger

Every round ends with these six lines. Nothing longer, nothing looser.

```
changed   : <files, and the one judgement-branch each touches>
evidence  : <command + result file, or the receipt path>
tests     : <focused modules run, and their exit status>
risk      : <R0-R3 from tools/validate_plan.py, plus the --evidence pointer used>
unknown   : <what stayed unresolved, or "none">
next      : <the single next action, or "stop; needs maintainer decision">
```

`unknown: none` is a claim, not a default. If a check did not run, say so here.

---

## VII. CI gate

`tools/validate_plan.py` never hard-fails by itself. `--strict` is what turns it
into a gate, and exactly one caller passes `--strict`:
`.github/workflows/change-gate.yml`, on every `pull_request`. It is a separate
workflow file, not a job inside `test.yml`, so the native/lean/test matrices
cannot be slowed down by it and a broken gate cannot mask them.

### What fails, and where

| risk | step | fails when | local equivalent |
| --- | --- | --- | --- |
| R0 / R1 | `risk` | the PR description has no non-empty `## Evidence` section, so no `--evidence` pointer is passed and `missing` is non-empty | `--strict` with no `--evidence` |
| R2 | `risk` | a changed `scripts/*.py` has no test importing it (`NO_TEST_MAPPED` in `notes`) | same command; the entry appears in `notes` |
| R3 | `risk` | never; it still runs and passes | same |
| any | `graph` | `upward_edges > 17`, `cycles > 3`, or `unclassified > 0` | compare the counts by hand |

An empty diff is not a failure: `overall` stays R3 and `verdict` stays `ready`,
so a PR that touches nothing does not get blocked.

The base is `git merge-base origin/<base_ref> HEAD`, not the base branch tip.
Two-dot `git diff origin/<base_ref>` also counts commits that landed on the base
branch after the PR forked and reports them as deletions in this PR.

The `## Evidence` section must hold at least 12 non-space characters. A heading
with nothing under it is rejected, so a placeholder cannot satisfy the gate.

Reductions always pass and should be claimed in the handoff ledger. A PR that
drops an upward edge or a cycle is a win, not a gate failure:
`modules`, `edges` and `mutual_pairs` are reported in the job summary but never
gated, because a new classified module legitimately moves `modules` and `edges`.

### Local rehearsal

Two commands, same as the workflow. Drop `--strict` while iterating, keep it when
you want the exact CI verdict:

```bash
base="$(git merge-base origin/release/6.0.0-compass HEAD)"
python -B tools/validate_plan.py --base "$base"                    # advice only
python -B tools/validate_plan.py --base "$base" --strict; echo $?   # the gate

python -B tools/import_graph.py > .import-graph.json
python -c "import json;d=json.load(open('.import-graph.json'));print(d['counts'],len(d['cycles']),len(d['unclassified_edges']))"
```

`17` in the baseline is `counts.upward_edges`: de-duplicated `(source, target)`
pairs, not `len(d['upward'])` (21 import sites). Compare the field the gate
compares, or the comparison is meaningless.

`--evidence PR:Evidence` is the pointer CI uses when the PR description has the
section; locally, pass a real path instead:
`python -B tools/validate_plan.py --base "$base" --strict --evidence docs/evidence/<file>.md`.

### Why local is soft and CI is hard

A local run is a rehearsal, not a merge decision. If it hard-failed, every
intermediate run of an R0/R1 edit would abort, and the predictable response is
to stop calling the tool or to add a bypass flag, which is the same as deleting
the check. The soft default keeps the advice visible while the work is still
moving, which is exactly when it can still be acted on.

CI runs once, at the merge point, where the cost of skipping a check is the
check's entire value. That is also the only place where `--evidence` can be
supplied honestly: the pointer is the PR description, which a reviewer reads and
which becomes part of the merged record. A local run cannot supply it, because
the evidence does not exist yet while the edit is being written.

The asymmetry is deliberate: local optimizes for iteration speed, CI optimizes
for not merging an unbacked R0/R1 change. Both are written down here so the
difference is a contract, not an accident.
