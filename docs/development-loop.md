# Use RDS while developing RDS

RDS development uses its own executable tools: record a concrete problem, run an actual check, read the original output, choose the next change, and repeat with the changed tool. This is the first development feedback loop for the RSI component. It remains a human-directed software development process; regression acceptance does not establish improved end-to-end scientific discovery.

Use the latest available development checkout for each new iteration rather than an older installed Skill copy. Freeze that iteration's source tree and test workload before execution, and retain its commit or file hashes, original logs, receipt, artifact import and Advisor next-step record. Upgrade the snapshot at the next meaningful boundary when new code is ready; never relabel a previous passing receipt as evidence for a changed tree. Independent checks may run in parallel when resources and budgets allow it. The goal is earlier reliable progress, not the smallest amount of computation; see [resource-aware planning](resource-planning.md).

## Current 5.8 delivery scope

Prioritize defects and friction observed in current use: low-boilerplate native execution, compact output and tolerant command entry; consistent brief progress across Skill and host instructions; source-backed method-scope clarification; native objective/assets and reusable tools; and accurate hypergraph evidence boundaries. Reproduce each actual issue, repair the general mechanism and complete the relevant engineering regressions before delivery.

Long-trajectory efficacy studies, RSI research-policy gain measurement and original ExplorationBench access are outside this release's active work and are not release gates. Preserve existing evidence records; passing software checks does not establish those scientific claims. No benchmark-dependent work is needed to complete this maintenance scope.

## Reproduce a development iteration

From the repository, choose a **new empty workspace**:

```text
python -B examples/self-development/run.py --workspace <new-empty-workspace>
```

The example copies the current programs and real regression cases into that workspace. The original checkout remains available for editing. It then uses RDS programs to:

1. Lock the copied code, test cases, evaluator, configuration, protocol and exact command.
2. Reserve a bounded CPU test attempt and save a decision checkpoint.
3. Execute the real regression workload, preserving stdout, stderr, JSON counts and an execution receipt.
4. Report actual wall time separately from unknown CPU/GPU/API costs.
5. Restore against the live budget and completed run, without restarting it.
6. Reread the hash-bound original JSON and receipt, then use Advisor to choose between inspecting failures and reviewing the next change.
7. Replay a finite rule-change example against declared development and held-out cases, adopt only an eligible report on an isolated graph, and exercise rollback.

Each CLI invocation is retained under `out/`. The raw execution logs are under `.rds/project-artifacts/`. A failing software check still consumes budget and retains its output. Repair that concrete failure in the source checkout, then start the next iteration in another empty workspace. Do not rerun unchanged passing workloads merely to obtain a better number.

For the complete public regression suite, add `--all-tests`. This includes benchmark-backed test modules and copies their support package into the isolated workspace.

## Theory before a CPU probe

The programmatic `RDSAdvisor.execute_theory_probe(run_spec, formal, theory_allowance=..., committed=...)`
checks an explicit declarative obligation with the existing independent certificate
checker before calling `ProjectStore.register`. `FAIL` and `UNKNOWN` return without
creating a run or reserving empirical budget. A supplied result is replayed; its
handwritten status has no admission authority. This path is opt-in: ordinary
project commands still use the existing project API. Backends that expose an
`application_status` must also return `PASS`; a proved conditional theorem with
unknown application premises does not admit an empirical run.

With `RDS_LEAN_EXECUTABLE` configured to a native Lean binary, reproduce the bounded
case in a new empty workspace:

```text
python -B tests/test_rds_advisor_lean_twostep.py --workspace <new-empty-workspace>
```

The case uses `L(w) = (w - 1)^2`, with twenty CPU updates. Its declared affine
contraction and fixed point have independently checked Python certificates; the
module also includes a native Lean proof of the closed rational bound. The combined
module is `CERTIFICATE_CHECKED`, not a native proof of arbitrary gradient descent.
Original loss/update trajectories, project receipts and checkpoints distinguish a
successful process from scientific gain and from an observed manipulation.

A proven side condition does not prove that executable code implements the model,
or guarantee improvement from an already optimal starting point. A failed
manipulation check requires inspecting that correspondence; a flat trajectory
cannot by itself establish an optimizer or data-distribution cause. `UNKNOWN`
blocks this probe without refuting the mathematics. Before checking, the same
project budget atomically charges a finite `theory_allowance` with every contract
resource dimension. Generation and independent replay share one owned subprocess
wall deadline; timeout terminates its process tree and returns `UNKNOWN`. Empirical
admission then checks the remaining shared budget. For example, an allowance may
be `{"wall_seconds": 10, "cpu_seconds": 3, "gpu_seconds": 0}` when those are the
contract dimensions. These are explicitly supplied conservative charges, not
measured CPU/GPU usage or a suggestion that cheaper research is always better.

Unused theory allowance is not refunded, including after rejection or controller
crash; this avoids orphan reservations and double settlement. Append-only events
bind its attempt, request and intended empirical manifest, retain observed check
wall time and charge any observed wall overrun. The actual CPU/GPU measurement
remains unknown. The hard deadline covers the complete checker subprocess;
SQLite admission and evidence collection have overhead, so an exact OS deadline
for every controller instruction is not claimed. This scalar acceptance case does
not measure autonomous research quality.

The same unchanged manifest/request has one durable claim, so concurrent callers
cannot both pay for its check. Independent run identities can still check in
parallel. The request binds the checker version, selected native executable and
explicit allowance; a revised request can be reviewed under a new bounded claim.
`ProjectStore.theory_record(attempt_id)` reads the complete recorded gate and
bounded original worker stdout/stderr, even if later empirical admission failed.
It exposes evidence for review and never grants admission from a stored PASS.

Blocked checks also save structured verification-request decisions in the existing
checkpoint ledger. The next selection pass suppresses an unchanged repeat before
invoking the checker; a changed declaration reopens review. Rejecting this redundant
request does not refute an `UNKNOWN` mathematical claim, and reopened review does
not grant CPU execution authority.

## Check identity at meaningful boundaries

Hash checks serve artifact identity, execution admission/completion, control reuse, and rule adoption/rollback. Ordinary status, advice display and checkpoint saving read the operational ledger without hashing the whole project. Checkpoint restoration explicitly checks current input bindings because it is a continuation boundary. During artifact import, each unique file is read and hashed once; facts reuse its hash and original location rather than adding an unused hash to every value. Template equality uses ordinary canonical values; only the intervention fingerprint needs a compact hash for deduplication. A hash is not a research score.

The example's held-out labels and expectations are declared by its author. They demonstrate replay and adoption behavior; they are not an independently sealed benchmark or a scientific policy score. The isolated example graph is restored after adoption. It does not modify the repository's judgment graph automatically.

## Read original records into Advisor — M01

Use `rds-artifact-manifest-v1`. The importer supports JSON configuration and receipts, JSON/CSV metrics, and explicit JSONL or `key=value` logs. It reads named fields with bounded adapters rather than interpreting arbitrary prose as scientific observations.

```text
python -B scripts/rds_cli.py --root <project> artifacts import --manifest <manifest.json>
python -B scripts/rds_cli.py --root <project> advise --artifacts <manifest.json> --graph <graph>
```

Each source specifies `id`, `kind`, `path`, `expected_sha256`, `binding`, and `facts`. JSON selectors use `pointer`; CSV uses a 1-based `row` and `column`; JSONL uses a physical line `row` and `pointer`; key/value logs use `key`. Bindings record `run_id`, code/configuration/data hashes, `data_split` and the metric's definition/reduction. The report retains source locations, missing items and conflicts.

Configuration is `DECLARED`; a value read from a metric/log/valid receipt is `OBSERVED`; supported `difference`/`mean` computations are `DERIVED`; missing or conflicting inputs remain `UNKNOWN`. Observation here means that a value was read from that record. It does not prove the experiment's mechanism or the honesty of the source. Derivations retain input IDs, method and compatibility conditions.

`--research-context` remains an explicit manual input. Serialized provenance labels cannot promote a declaration into an importer observation: advice rereads original files. Contradictory manual and imported values become unknown and are reported.

## Compose finite experiment candidates — M02

```text
python -B scripts/rds_cli.py advise --research-context examples/experiment-templates/context.json --templates examples/experiment-templates/templates.json
```

Templates bind a typed intervention target and allowed choices, preconditions, invariants, control, measurement, rival explanations, result-dependent next decisions, cost record and stopping condition. Composition enumerates compatible parts, deduplicates intervention fingerprints and reports depth/candidate/combination truncation. Unknown evidence remains visible. Candidates do not authorize execution, and the program does not invent success probabilities or information gain.

New model proposals enter this finite template format and undergo the same checks. A declared intervention still needs a real runner measurement to establish that the target property changed.

## Track costs and check control identity — M03

```text
python -B scripts/rds_cli.py --root <project> project costs
python -B scripts/rds_cli.py --root <project> project control-check --candidate <completed-control-receipt.json> --current <expected-control.json>
```

Cost reports include failed attempts, preserve each resource/unit separately, and distinguish measured resources from charged estimates. Wall time is not CPU or GPU time. Unknown cost is not zero. A manifest's explicit `cost_bindings` can associate an action with a checked historical receipt/resource for Advisor; a historical measurement is not a guarantee of the next run's cost.

Control reuse requires a successful completed control receipt, matching code/configuration/data/partition, initial state, seed, checkpoint, schedule, sample-work and numerical protocol, plus reread artifacts. The current request also specifies the expected control arm and exact argv; a treatment or a bare protocol cannot establish control identity. Equal seeds alone do not establish equal random paths. No additional seeds are scheduled by these tools.

## Execute a locked project command — M04

For result-dependent continuation with program-owned evidence, follow the [owned Advisor example](program-owned-advisor.md). It freezes routes and result readers, automatically updates the existing TMS graph after settlement, and checks the current selected manifest before dispatch. Projects without this policy retain the explicit command workflow below.

```text
python -B scripts/rds_cli.py --root <project> project init --contract <contract.json>
python -B scripts/rds_cli.py --root <project> project create --manifest <run.json>
python -B scripts/rds_cli.py --root <project> project execute --id <run-id>
python -B scripts/rds_cli.py --root <project> project recover --id <run-id>
python -B scripts/rds_cli.py --root <project> project status
```

Commands emitted by `project next` and `project status --brief` are rendered for PowerShell on Windows and POSIX shells elsewhere. Run them from the repository checkout with `python` on PATH. For a template step, replace the quoted `<...>` slots with your actual manifest, decision ID or decision JSON file, keeping each replacement as one literal shell argument. Roots containing spaces or shell punctuation are already quoted; accepted run IDs beginning with `-` use `--id=`.

The version-1 contract locks `bindings` with code/config/data/evaluator/protocol roles, exact `allowed_commands`, `output_roots` and a resource budget. A root-level file output that cannot sit strictly below a directory root is authorized by an optional `output_files` list of exact project-relative paths; the quick entry fills it for single-component outputs. The run manifest binds its protocol, argv, expected output paths, timeout and resource reservations. Execution uses the project directory and `shell=False`. Before and after execution, input and executable identities are checked; missing outputs, nonzero exit, timeout or changed bindings prevent successful completion.

Before project initialization, an explicitly configured [execution policy](execution-policy.md) can freeze an attempt limit for bound requests. Project and quick entry then refuse duplicate spending or observe the existing attempt; the guide explains identity, recovery and coverage boundaries. This optional policy is separate from scientific acceptance.

A workspace can also bind itself to one canonical project ledger (#282): `python -B scripts/rds_cli.py --root <project> workspace bind` writes a one-time identity pointer outside `.rds` and one append-only `WORKSPACE_BOUND` event into the ledger. Afterwards, project initialization in the bound tree refuses `--supersedes` and `--separate-project`, a replaced or missing ledger is refused with `workspace coverage` reporting `MISMATCH` (exit code 2), and `project status` includes a `workspace_binding` section with a `next_command`. Binding dispatches no work, grants no budget, and changes no attempt, receipt or evidence; it is enforced by supported RDS APIs, not by OS-level protection of the pointer file (see issue #120 for host-supplied protection). Unbound workspaces keep their existing behavior.

The command is trusted project code, not an OS security sandbox. Receipts keep process/run status, `task_gain` and `mechanism` separate; the latter two remain unknown until separately assessed. The adapter currently has CPU acceptance evidence. An existing GPU training project still needs its own locked command, instrumentation and scientific evaluator.

For an authorized Windows task that must survive the conversation, add `--background`. RDS creates a uniquely identified Task Scheduler job with a hidden worker and records the task ID. Recovery reconciles that existing task; it does not launch a duplicate. Other platforms currently support foreground execution. If a worker disappears, the final duration is unknown: the reservation is conservatively charged and the last observed lower bound is retained.

## Evaluate, adopt and roll back a rule — M05

```text
python -B scripts/rds_cli.py meta evaluate-rule --rule <candidate.json> --graph <isolated-graph> --cases <cases.json> --output <report.json>
python -B scripts/rds_cli.py --root <project> meta apply-rule --rule <candidate.json> --graph <isolated-graph> --cases <cases.json> --evaluation <report.json> --force
python -B scripts/rds_cli.py meta rollback-rule --record <adoption-record> --graph <isolated-graph>
```

Proposal, evaluation and adoption are separate. A case pack declares its development/held-out partition, precommitted assertions and CPU/resource bounds. The evaluator actually executes both original and candidate search/composition. Zero cases, overlapping partitions, candidate failures, regressions, cycles and exceeded limits cannot become adoption acceptance.

`meta reflect` reads the current project or reference ledger and proposes scoped candidates from applicable recorded evidence. It does not adopt them. `--terms` records a pending scoped Obelisk request; supplying search terms is not historical evidence. The older `auto-repair` diagnostic remains limited to the reference ledger and does not bypass rule adoption.

Adoption binds the candidate, original graph, case pack and replay-program version. It rereplays before writing, retains a backup and an adoption record, and exposes rollback. `--force` permits replacing an existing rule ID; it does not bypass evidence. Structure-only `evaluate-alignment` remains a lint diagnostic. Acceptance establishes the finite software behavior checked by these cases, not a general research-policy improvement. Equal-total-cost scientific trajectories remain a separate future evaluation.

## Resume the live research decision — M06

```text
python -B scripts/rds_cli.py --root <project> checkpoint save --id <checkpoint-id> --decision <decision.json>
python -B scripts/rds_cli.py --root <project> checkpoint restore --id <checkpoint-id>
```

Checkpoints are append-only records in the existing operational ledger. They retain the question, objective, pending evidence and snapshot at a decision boundary. Restoration first reads current state, compares contract/run identities, reports forward updates or conflicts, and returns current budget, exposures and existing runs. It neither replaces state nor creates authorization. Relevant older original evidence can then be retrieved through Obelisk in the exact project scope; this does not create another chat mirror.

`--kind reference` chooses the scalar reference ledger; `--kind project` chooses the external-project ledger. Automatic selection uses an existing project ledger when present.
