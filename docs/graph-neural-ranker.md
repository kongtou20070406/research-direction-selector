# Optional graph neural route preferences

An owned project can freeze `advisor_policy.graph_ranker` to give the agent a
small graph neural model's preferences over its currently eligible Pareto
frontier. This is message-passing neural inference with supplied JSON weights.
It does not train a model or establish that those weights improve research.
The example weights are hand-authored and labelled accordingly.

RDS already owns the work/evidence graph, original outputs, three-valued
predicates, candidate admission and recovery. The ranker reuses those inputs
and the existing Advisor report/CAS/event. There is no second database,
planner, model service, Torch dependency, download or GPU requirement.

## Try the real entry points

From a checkout, choose a new empty sibling workspace. The labelled synthetic
example supplies two independently executable numerical comparisons:

```powershell
python -B examples/graph-ranker/prepare.py --root ../graph-ranker-demo --mode shadow
python -B scripts/rds_cli.py --root ../graph-ranker-demo project init --contract ../graph-ranker-demo/contract.json
python -B scripts/rds_cli.py --root ../graph-ranker-demo project next --brief
python -B scripts/rds_cli.py --root ../graph-ranker-demo advise
python -B scripts/rds_cli.py --root ../graph-ranker-demo project advance --brief
```

`shadow` reports scores and retains the original selection. To allow preference
ordering, prepare a different new workspace with `--mode tie_break` before
initialization. The contract freezes model configuration and weights; editing
the preparation JSON after initialization cannot replace the live policy.
Absent `graph_ranker`, existing behavior is unchanged. Model origin is declared
metadata; every report retains `training_status=UNVERIFIED` and
`research_policy_gain_measured=false`.

## Model and graph contract

The complete JSON shape is in
[`synthetic-model.json`](../examples/graph-ranker/synthetic-model.json).
Copy that object into `advisor_policy.graph_ranker`; weights are inline data
bound by the existing contract hash. Schema 1 requires exactly `schema`, `mode`
and `model`. The model fields are `features`, `origin`, `input_weights`,
`input_bias`, `layers`, `readout`, and `readout_bias`. There are no dynamic
imports, paths to executable models or pickle loaders.

| Field | Meaning and bound |
| --- | --- |
| `features` | Fixed order: action, true, false, unknown, observed |
| `input_weights` / `input_bias` | H by 5 / H, 1 <= H <= 16 |
| `layers` | 1 or 2 objects, each with H by H `self`, H by H `message`, H `bias` |
| `readout` / `readout_bias` | H scalar weights / scalar offset |
| `origin` | 1..512 character source locator, unverified metadata |
| All weights | Finite numbers with absolute value <= 1e6; booleans rejected |

Each rule/action is a node. Its configured `preconditions` and `satisfied_when`
create predicate nodes, evaluated by the existing sourced three-valued reader.
TRUE, FALSE and UNKNOWN occupy separate channels; missing evidence is never
encoded as a negative finding. The observed channel distinguishes imported or
program-derived evidence from caller-reported input. Predicate-to-rule edges
and the direction search's `prerequisite_for` edges carry messages. Other
descriptive relations are ignored by this schema and confer no proof support.
Identical predicates and edges are deduplicated. Actual/expected values are
identified by hashes and original source locators, rather than repeated in
the feature trace. The normalized trace is capped at 1 MiB. Graphs are limited to 128
input rules/512 edges and 512 expanded nodes/2048 edges; no truncated graph is
silently scored. A missing endpoint or unrepresented frontier action abstains.

The input projection is `tanh(W_in x + b_in)`. Each synchronous layer computes
`tanh(W_self h + W_message mean(incoming h) + b)`, using zero for an empty
neighbourhood. A linear readout returns a preference score for each frontier
action. Sorted IDs make aggregation deterministic; equal scores retain the
existing frontier order. Scores are not calibrated probabilities, confidence
in a claim, causal estimates or expected scientific value. Numeric finite
bounds and tanh activations keep inference bounded on these graph limits.

## Selection, execution and audit

Admission happens first. The model cannot add routes, repair predicates,
promote scientific truth, change a goal, widen a budget, make an unready
candidate ready, or select completed/dominated/overbudget work. It only
reorders the exact current nondominated eligible pool. Active reservations
and feasibility pilot order take precedence. Human withdrawal, pause and
preference instructions take precedence where steering is available.
Selection still passes the original choice review, registration and
pre-launch transaction checks. Collection failure and confirmed goal stopping
retain authority regardless of the scores.

Each existing Advisor report contains `graph_ranker`: model/input hashes,
the normalized feature/edge/evidence trace, eligible scope, scores, preferred
ordering, abstention reason, precedence and whether ordering was applied.
The enclosing report binds the state fingerprint and dependency snapshot.
The brief view retains status, preferred order and selection effect; its
normal full-report locator leads to the original trace. Invalid model schema
is rejected before project init writes. Runtime feature incompatibility
abstains visibly and retains the original order. Inference is synchronous and
bounded; measured wall time is separate from unknown CPU time and does not
consume a new experiment attempt/reservation. It contributes real controller
overhead, which must be counted in a total-budget research comparison.

## Training and evidence still required

No pretrained research ranker is shipped. An external offline trainer may
export these exact matrices, but an origin string or successful inference
does not establish training provenance or quality. Start with shadow mode.
Freeze decision-time features and independently checked outcomes; exclude
future receipts and held-out labels from features. Split campaigns/time into
train, validation and untouched confirmation sets. Compare declaration order,
rule ranking and GNN under the same LLM, tools, information and total budget,
including scoring/training overhead. Measure scoped result quality, failed or
repeated launches, elapsed time, human corrections and abstention, with
negative results retained. A later authorized study must supply those data,
training budget and acceptance thresholds before deploying trained weights.

Trackinizer's [current typed epistemological graph](https://github.com/rekursiv-ai/trackinizer)
inspired the work/knowledge/evidence separation; it is not itself evidence of
a trained GNN. The [NeurIPS 2024 graph-learning planner](https://arxiv.org/abs/2405.19119v3)
and [2025 graph-augmented agent survey](https://arxiv.org/abs/2507.21407v2)
motivate this integration route, but their results do not transfer as an RDS
scientific-gain claim. Sources checked 2026-10-08.
