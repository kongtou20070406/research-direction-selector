# Forecast a complete plan and improve its tools

Optional `advisor_policy.feasibility` checks the remaining solve, independent
verification, recovery and delivery steps before selecting or starting a worker.
It uses successful measured pilot receipts and a declared scaling model. A timeout
or resource reservation is never an ETA. Unknown estimates admit only informative
pilots sharing at most 10% of the original wall allowance. Failed and historical
pilots consume that cumulative cap after method revisions too.

The public CPU example generates the complete contract:

```text
python -B examples/predictive-feasibility/prepare.py --root <new-empty-project>
python -B scripts/rds_cli.py --root <project> project init --contract <project>/contract.json
python -B scripts/rds_cli.py --root <project> project next
python -B scripts/rds_cli.py --root <project> project advance
```

Repeat `advance` for one selected step. Small pilots measure the slow solver, fast
solver and independent verifier. The expensive route is denied before its child
starts. A fast route produces the exact sum of integers 0 through 99 and a
separately frozen evaluator checks it by enumeration. Receipts and
`outputs/launches.txt` show what actually ran. This synthetic software acceptance
case is not an n=13 result or a comparison against an unaided model.

Complete plans name ordered `steps`, all frozen `goal_facts`, and explicit
`recovery_wall_seconds` and `delivery_wall_seconds`. Models name `pilot_runs`, an
exponent, safety factor, extrapolation limit and conditional assumptions. The
protocol binds `sample_work.units` and `runtime_model_identity` (algorithm,
precision, hardware). Other identity fields and work-domain metadata must match
the pilot. Reports contain source receipt hashes, measurements, scales and
conditional lower/upper predictions; these are not statistical confidence bounds.

Only the first remaining step of a feasible plan is admitted. Its complete
CPU/GPU/other accounting allowances must fit too; these allowances are distinct
from physical measurements. Existing reservations are attributed to their owning
steps. A plan cannot order a step before another step whose completion it directly
requires. Cost feasibility also remains separate from execution readiness: if
ordinary prerequisites block every admitted route, `next` and `advance` return an
explicit prerequisite repair request without launching a worker or bypassing the
dependency gate. External evidence and disjunctive alternatives are assessed by
the ordinary runtime checks.

The original campaign T0 and deadline apply to all methods. Input, code,
numerical protocol or runtime changes invalidate old pilots. Measured device scope
currently supports `hardware: "local-cpu"` only. Accelerator/backend forecasts
remain `UNKNOWN`; the host fingerprint does not instrument GPU devices, clocks or
library versions.

## Program assistance for creating or improving a tool

A blocked/failed method returns a repair request pointing to this entry:

```text
python -B scripts/rds_cli.py --root <project> project improve --code-path solver.py --id faster-tool-1
```

The program creates editable candidate code and a revision proposal under the
existing `.rds/tool-workbench/` directory, with immutable, checked guidance:

- Original failure/timeout stderr tails and their receipt/artifact identities.
- Dominant measured costs by resource/unit, retaining unknown dimensions.
- Function, loop and imported-call locations as static hints, not profiler measurements.
- Exact goals, frozen evaluator/data, permitted commands, live costs and campaign T0.
- Pilot routes and final evidence producers needed to test a replacement.

Edit the candidate and the proposal's policy/reason. A new solver, parser, batching
method or local adapter can be implemented inside its preauthorized code entry
point. Repeat `improve` to refresh the source hash, then use its returned
`project revise --proposal ...` command. Preparation neither starts a worker nor
changes active code. Identical candidates are rejected. Python comments,
docstrings, local renaming and numeric tuning alone also fail the conservative
structure check. That check cannot prove arbitrary code correct or faster; fresh
bounded pilots and the unchanged independent evaluator must test its usefulness.

Genesis must opt in with
`method_evolution: {"schema":1,"max_revisions":4,"code_paths":["solver.py"]}`.
Only existing exclusively-code bindings can change. Goals, final evidence
readers/producers, evaluator/config/data, total budget, allowed argv, output roots,
execution policy and deadline stay fixed. Preauthorize later pilot commands and
unused output paths at genesis; proposals cannot grant themselves commands.
Registered manifests/observations stay byte-identical. New pilots need current
input identities. Protocol code hashes are derived by the program.

Adoption retains old/new bytes in CAS and appends PREPARED/ADOPTED events to the
same ledger. Pending adoption blocks execution and resumes only with the exact
proposal; partial copies accept only retained old/new bytes. Original attempts,
exposure and spending survive. Verified historical checkpoints in the owning
ledger supply context while restoration uses live costs and current bindings.
Inherited history through `supersedes` remains conservatively unavailable
when the predecessor's pinned contract history or checkpoints cannot be verified;
the Advisor reports that warning and continues using local history. Intact method
revisions are verified through their native contract history, and each pinned
checkpoint is checked against its own revision. Legacy contracts without
these optional fields retain their existing workflow.

The agent must propose an executable improvement or state that no authorized
viable route remains. More timeout, renamed failures, reduced precision, invented
success fields and changed verification predicates do not establish improvement.
Unknown applicability and exhausted resources remain explicit. Independent review
still assesses the actual tool change. See [n=13 output recovery](n13-output-recovery.md)
for the separate directory/GBK integration regression scope.
