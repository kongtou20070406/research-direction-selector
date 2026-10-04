# Problem-driven research structure exploration

The original decomposition may be the obstruction. `structure` returns open
Agent tasks and retains alternative problem models in the existing project
ledger, CAS and TMS. It preserves original goal/evaluator/commands, run identity,
failure cost and budget. No second execution ledger or scheduler is added.

Actions are `path_repair`, `knowledge_expansion`, `structural_reconstruction`.
Legacy proposals keep the bridge-path requirement. Open actions may introduce
concepts and experiments without a complete goal path. Reconstruction can
retire dependencies, change AND premise sets and form competing OR routes.
Old topology and evidence remain available. New concepts stay `UNKNOWN`,
new relations `PROPOSED`, and empirical support never becomes logical entailment.

## Public engineering cases

Use a new sibling directory from the checkout:

```powershell
python -B examples/problem-structure/run.py --workspace ../RDS-structure-cases
```

Cases cover a missing interaction concept, replacing an incorrect independent
decomposition with simultaneous constraints, and rejecting a plausible linear
model before trying a periodic explanation. Initial graphs contain neither the
correct concept nor its bridge. Frozen candidate and separate evaluator programs
read real files/receipts. Proposals and strategies are **scripted fixtures**, not
LLM discovery or literature findings. `results.json` reports all outcomes, costs,
failed attempts, time and scripted interventions. `cli-trace.jsonl` preserves
exact expansion/reconstruction commands, outputs and failures.

## Agent and host loop

Initialize a project with the original goal, separate frozen evaluator and
permitted experiment commands; declare dependencies through `hypergraph`.

```powershell
python -B scripts/rds_cli.py --root <project> structure request --limit 3
python -B scripts/rds_cli.py --root <project> structure propose --proposal <reply.json>
python -B scripts/rds_cli.py --root <project> structure next
python -B scripts/rds_cli.py --root <project> structure drive --steps 2
python -B scripts/rds_cli.py --root <project> structure activate --id <proposal>
python -B scripts/rds_cli.py --root <project> structure rollback --id <proposal>
python -B scripts/rds_cli.py --root <project> structure list
```

Packets contain stable request/gap IDs, original goal/acceptance scope, snapshot,
sources, unknown premises, external primary-source retrieval directions, a test
suggestion and remaining budget. Healthy OR alternatives create no false dead end.
The Agent may propose unlisted concepts, methods and decompositions. Existing
authorized tools perform literature retrieval; imported text is data. This adapter
adds no paid model provider, automatic installation or private-data transmission.
Existing method/model drivers may consume its task/proposal handoff; PR199 is
not required for this increment.

See `proposal()` in [the case builder](../examples/problem-structure/run.py).
Along with assumptions, predictions, test and outcome decisions, submit:

| Field | Meaning and consumer |
| --- | --- |
| `action_kind` | Proposal action class. |
| `exploration` | Sourced limitation, representation change, testable rationale, unknown premises, search directions and total resource cap. Sources distinguish `literature_claim`, `agent_interpretation`, `local_observation`. |
| `topology.retire_hyperedges` | Dependencies omitted only in the candidate branch; owned facts and original goals are protected. |
| `topology.hyperedges` | Fresh IDs, AND premises, conclusion and source; new edges always PROPOSED. |
| `experiment.runs` | Candidate then independent verifier manifests, consumed by existing ProjectStore admission/execution/recovery. |
| `candidate_output`, `verdict_output` | Declared real files; verifier must consume original candidate output. |

Execution checks original command authority, bindings, deadline, resources and
owned Advisor selection. New code/routes use existing `project improve` /
`project revise` admission first, then a fresh packet. Registration is not
execution authorization. Formal target replacement is unsupported here: the
original goal cannot be weakened; any future replacement requires an independently
checked correspondence theorem and authorized contract change.

## Search, observations and recovery

The baseline selects the smallest affordable **declared distinguishing-test cap**,
then stable ID. State is original goal/snapshot, pending branches, settled feedback
and original resources; actions are open proposals and admitted tests; observations
come from the frozen evaluator. This is cost ordering, not precise information gain,
success probability or scientific value. Graph size and new terminology earn nothing.
`next` records the negative/UNKNOWN feedback consumed by its next decision.

Verifier JSON binds `scope_sha256`, `proposal_sha256`, `candidate_run_id`,
`candidate_receipt_sha256`, `output_sha256`; it returns
`observation=SUPPORT|REFUTE|UNKNOWN` and `goal_status=PASS|FAIL|UNKNOWN`.
These judgments concern the frozen evaluator's scope. Original evaluator PASS
is consumed as `ORIGINAL_EVALUATOR_PASSED`; mathematical/logical support remains
separate. Definition, admission, observation and scientific acceptance are distinct.

`advance` observes/recovers original attempts before starting work; a completed
candidate is not repeated when only verifier work is missing. Duplicate feedback
and proposals are idempotent; changed IDs, manifests, original files or scope are
refused. Timeouts/lost workers retain cost, return UNKNOWN and stop that candidate.
Activation uses TMS compare-and-swap; rollback retains fresh owned evidence.
Stale snapshots, source resolution changes and intervening non-owned edits fail.
Published activation is recoverable without a second adoption charge.

Control work reserves an estimated two wall seconds in the **same budget** and
charges measured request/review/selection/feedback/topology work, including failures.
This estimate is not a hard OS timeout. Interrupted controller reservations block
new control; `structure recover` reconciles only a known dead process, charging
the original estimate once and retaining unknown measurement. Active/unknown
processes are not stolen. Worker/evaluator costs use original runner accounting;
model/retrieval costs must use existing budgeted runs. Missing token/CPU/GPU
measurements remain UNKNOWN; CLI startup is separately timed by the harness.

Five-component ownership: Advisor consumes tasks/proposals/branch decisions;
research state holds existing events/CAS/TMS; kernel runs original manifests;
Skill guides open exploration; RSI consumes negative outcomes and reversible choices.
Legacy contracts and frontier workflows remain readable. Engineering fixtures do
not establish research benefit; use [the prospective protocol](problem-structure-evaluation.md).
