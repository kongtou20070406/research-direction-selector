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
Settled runs include original receipt/result hashes and failure diagnostics;
prior feedback includes same-scope verified measurements, exact feedback hashes and
bounded original candidate/verifier JSON excerpts. Excerpts are untrusted data;
truncation is explicit. Older scopes remain in the ledger, outside this packet.
The Agent may propose unlisted concepts, methods and decompositions. Existing
authorized tools perform literature retrieval; imported text is data. This adapter
adds no paid model provider, automatic installation or private-data transmission.
Existing method/model drivers may consume its task/proposal handoff; PR199 is
not required for this increment.

See `proposal()` in [the case builder](../examples/problem-structure/run.py).
Along with assumptions, predictions, test and outcome decisions, submit:
Proposal IDs start with an alphanumeric character and contain only alphanumerics,
hyphens or underscores (up to80characters), so printed commands contain no imported shell text.

| Field | Meaning and consumer |
| --- | --- |
| `action_kind` | Proposal action class. |
| `exploration` | Sourced limitation, representation change, testable rationale, unknown premises, search directions and total resource cap. Sources distinguish `literature_claim`, `agent_interpretation`, `local_observation`. |
| `topology.retire_hyperedges` | Dependencies omitted only in the candidate branch; owned facts and original goals are protected. |
| `topology.hyperedges` | Fresh IDs, AND premises, conclusion and source; new edges always PROPOSED. |
| `experiment.runs` | Candidate then independent verifier manifests, consumed by existing ProjectStore admission/execution/recovery. |
| `candidate_output`, `verdict_output` | Declared real files; verifier must consume original candidate output. |
| `discriminator` | Schema 1: frozen config/data conditions, stable hypothesis ID, named verifier-output JSON pointer, disjoint scalar predicates `proposal` and `rival`. Required for new structure experiments. |
| `trigger` | After prior feedback, bind `proposal_id`, `feedback_sha256`, exact `observation`, and purpose `ALTERNATIVE` or `EVIDENCE`. UNKNOWN requires EVIDENCE; it does not refute a hypothesis. |

For example, a residual measurement at `/max_residual` can distinguish
`proposal={"op":"eq","value":0}` from `rival={"op":"gt","value":0}`.
Writing equivalent predictions with different wording does not make this pair
distinct. Supported predicates are `eq`, `lt`, `lte`, `gt`, `gte` on finite
numbers, or `eq` on booleans; booleans are not numbers. Conditions bind every
frozen data/config file. The measurement path must be the independent verifier
output, with an RFC 6901 JSON pointer. Overlapping/equivalent sets are rejected.
Missing or unsupported measurements, or measurements matching neither prediction,
remain UNKNOWN. This binds an executable field; it does not prove arbitrary
prose semantics, causal identification, physical sensor honesty or universal
natural-language equivalence.

Execution checks original command authority, bindings, deadline, resources and
owned Advisor selection. New code/routes use existing `project improve` /
`project revise` admission first, then a fresh packet. Registration is not
execution authorization. Formal target replacement is unsupported here: the
original goal cannot be weakened; any future replacement requires an independently
checked correspondence theorem and authorized contract change.

## Search, observations and recovery

An optional frozen `structure-search.json` config binding adds
[feedback-conditioned generation](adaptive-search.md). Original comparable
measurements allocate exploration, evidence and refinement opportunities in the
next request; proposal insertion consumes one slot atomically. The default
workflow and worker selection below remain unchanged. This generation heuristic
does not establish scientific benefit or grant additional execution resources.

The baseline first replays verified, same-scope feedback constraints, then selects
the smallest affordable **declared distinguishing-test cap**, then stable ID.
A REFUTE blocks the same declared hypothesis/prediction in identical frozen
conditions and measurement definition, including changed proposal IDs and output
filenames. A materially changed prediction or condition is a different claim.
Stable hypothesis IDs and measurement names are declared identities; the program
does not detect every semantic alias invented under a different identity.
Both `next` and direct `advance` enforce the gate before new worker attempts.
New post-feedback proposals require an exact evidence trigger. These constraints
change eligibility; the program does not execute free-text next-decision prose.
State is original goal/snapshot, pending branches, settled feedback
and original resources; actions are open proposals and admitted tests; observations
come from the frozen evaluator. This is cost ordering, not precise information gain,
success probability or scientific value. Graph size and new terminology earn nothing.
`next` records the negative/UNKNOWN feedback consumed by its next decision.

Verifier JSON binds `scope_sha256`, `proposal_sha256`, `candidate_run_id`,
`candidate_receipt_sha256`, `output_sha256`; it returns
`observation=SUPPORT|REFUTE|UNKNOWN` and `goal_status=PASS|FAIL|UNKNOWN`.
The hypothesis observation is recomputed from the declared measurement in the
hash-bound verifier original, even if its observation enum disagrees. Task
`goal_status` remains the independent evaluator's original verdict. Reuse checks
both original hashes and recalculated predicates. Old retained proposals without
a discriminator remain readable/recoverable, with explanation UNKNOWN; an old
task PASS is preserved separately, but an unbound hypothesis cannot activate.
For already-saved legacy feedback, reads expose effective UNKNOWN alongside its
original enum and original feedback hash. Triggers use that original hash and
effective observation; raw historical records are not rewritten.
These judgments concern the frozen evaluator's scope. Original evaluator PASS
is consumed as `ORIGINAL_EVALUATOR_PASSED`; mathematical/logical support remains
separate. Definition, admission, observation and scientific acceptance are distinct.

`advance` observes/recovers original attempts before starting work; a completed
candidate is not repeated when only verifier work is missing. Duplicate feedback
and proposals are idempotent; changed IDs, manifests, original files or scope are
refused. Timeouts/lost workers retain cost, return UNKNOWN and stop that candidate.
Activation uses TMS compare-and-swap; rollback retains fresh owned evidence.
Stale snapshots, source resolution changes and intervening non-owned edits fail.
Direct `advance` checks affordability of all remaining experiment stages and
the feedback allowance before starting a new attempt.
Matching existing run reservations are already excluded from the remaining
balance and are counted once; only new registrations need fresh budget.
Retained manifests are checked before reusing their reservations. Original runner
transactions retain admission and reservation authority; concurrent budget use
can stop later stages without repeating settled work.
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
