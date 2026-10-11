# Feedback-conditioned generation

An optional frozen search configuration makes `structure request` allocate
bounded proposal opportunities from original experiment feedback. It selects
which result to investigate or refine next, before the Agent generates a reply.
`structure propose` consumes one opportunity atomically in the existing ledger.
The original structure evidence gates, independent evaluator, command authority,
worker recovery and resource accounting still apply.

This is a declared heuristic, not calibrated information gain. The default
structure workflow is unchanged when the configuration is absent. There is no
new provider, scheduler, execution ledger or automatic installation.

## Freeze the configuration

Before `project init`, write `structure-search.json` in the project root and add
it to the existing contract `bindings` with `role: "config"` and its SHA-256.
An unbound file does not enable the feature. Include this config in the normal
protocol config-role digest and in each discriminator's frozen conditions, just
like every other config binding. The existing method revision rules preserve it.
The structure entry validates the bound contents before returning search tasks;
`project init` retains its existing generic config binding validation.

```json
{
  "schema": 1,
  "strategy": "bounded_feedback_v1",
  "baseline_proposal_id": "baseline",
  "metric": {
    "name": "finite-mse",
    "pointer": "/mse",
    "unit": "squared-output-units",
    "direction": "min",
    "min_improvement": "1"
  },
  "slots": {"explore": 2, "evidence": 1, "refine": 2},
  "min_repeats": 2
}
```

The metric must be the discriminator's numeric original measurement. The
baseline ID, direction, declared units and positive rational improvement
threshold are fixed before results. Comparisons require the same goal/contract
scope, frozen conditions, metric definition, verifier command and protocol
identity. Verifier comparisons normalize only declared result paths and
standalone proposal/candidate IDs; other arguments, including split or scoring
options, must match. Missing, Boolean or mismatched measurements cannot earn refinement.
Declared comparison identity is not a verification of the evaluator's scientific
design or the physical units of its output.

Each kind has 1–8 opportunities, with at most 16 in total. A slot limits a
proposal; it is **not** a token allowance or a worker reservation. Preparation,
model/tool calls and workers still need their existing authorization and original
resource accounting. The returned budget is the planning snapshot; use the
request's `live_budget` and original admission for current resources. No money,
tokens, CPU or GPU time are inferred from wall time.

## Consume a request

```powershell
python -B scripts/rds_cli.py --root <project> structure request
python -B scripts/rds_cli.py --root <project> structure propose --proposal <proposal.json>
python -B scripts/rds_cli.py --root <project> structure next
python -B scripts/rds_cli.py --root <project> structure advance --id <proposal-id>
```

The request's `search_allocation` contains its identity, policy digest, measured
comparisons and reasons, original feedback/receipt references, selected parent,
available slots and explicitly withheld opportunities:

- **explore** keeps room for unlisted concepts and methods. It has no prescribed
  parent; existing exact feedback-trigger requirements still apply.
- **evidence** first diagnoses UNKNOWN results, then repeats a promising result,
  then checks the selected parent or a supported baseline. The proposal must
  check the parent's same scoped hypothesis and use the exact supplied trigger.
- **refine** becomes available only after the declared number of distinct
  candidate runs all improve over the predeclared baseline by the threshold.
  Repeats share the declared hypothesis/prediction, candidate command (apart
  from declared output filenames) and protocol. The worst measured improvement
  determines ranking; ties use the stable experiment digest. An adverse repeat
  or UNKNOWN for that intervention withholds refinement. REFUTE removes every
  parent with the same scoped hypothesis from refinement, promising repeats,
  diagnosis and baseline fallback, including when another computation or goal
  produced it. This matches the existing admission gate. Positive comparisons
  and parent choice remain goal-local; `scoped_refutations` retains the original
  negative feedback and receipt references. UNKNOWN preserves diagnosis
  when an executable scoped hypothesis is available. Legacy UNKNOWN records
  without one remain visible but cannot create an unusable evidence slot.

Copy the chosen integer into `"search": {"slot": 0}` in the normal structure
proposal. For evidence/refine slots, copy the exact supplied `trigger`, including
its parent proposal and original feedback digest. Parentage is inspectable
preparation lineage; it does not prove that the generated idea is useful.
Refuted claims remain blocked by the original gate, including a renamed repeat.
A failed process, missing measurement or unresolved explanation stays UNKNOWN.

The same request cannot mint more slots by changing proposal IDs or rereading
it. The insertion transaction rejects simultaneous claims on one slot. A reply
generated against old receipts/feedback requires a fresh request; the evidence
identity is checked again at commit. Requests retain their original allocation
and budget snapshot for idempotent recovery. No absent branch or result is
invented to fill a quota. Worker execution retains the original affordable-cap
ordering; this increment changes generation opportunities and their admission.

`min_repeats` is an operational check against one-off winners, **not** a
statistical significance criterion. Repeated exposed data do not become an
independent confirmation set, and renamed semantic aliases are not universally
detectable. New generated constraints remain proposals. Goal acceptance and
scientific support remain separate from search preference.

## Reproduce the finite CLI trajectory

```powershell
python -B examples/adaptive-search/run.py --workspace <new-sibling-directory>
```

The public example uses one frozen computation program and a separate finite
oracle. A zero predictor has MSE 24.5. A linear predictor has MSE 2; one run
opens an evidence opportunity, and the repeated original measurement opens two
refinement opportunities. A quadratic candidate then passes the unchanged
finite evaluator. All four candidates and four evaluator runs have original
receipts in one ledger. The model request/response boundary is represented by
explicitly scripted fixture proposals; this does not establish model generation
quality, token savings, or faster scientific discovery.

`results.json` and `cli-trace.jsonl` retain the allocations, chosen slots,
feedback, attempts, budgets and exact public commands. Regression tests cover
stale/tampered originals, wrong parents, incompatible metrics, noisy or UNKNOWN
repeats, renamed/shared-run proposals, concurrent slot claims, recovery and
budget exhaustion. Use [the prospective protocol](problem-structure-evaluation.md)
for separately authorized matched-model research-benefit measurements.

MDL scoring, actual model-generated follow-up trials, execution-opportunity
reallocation and matched-budget scientific efficacy remain follow-ups to #226.
The shipped claim is bounded original-evidence generation control.

## Parent-relative progress and stalled neighborhoods

Opt into `"strategy": "bounded_neighborhood_v1"` and add
`"stagnation_trials": 2` to the frozen schema-1 policy above. The latter must be
an integer from 2 through 8. Existing `bounded_feedback_v1` contracts retain
their original behavior. No graph coordinates, probability model or semantic
service are inferred.

This policy separates a point's measured quality from the observed change along
a declared preparation lineage. For a minimizing metric, these are
`baseline - child` and `parent - child`. Maximizing metrics reverse the signs.
For example, baseline error 10, parent error 2 and child error 3 describe a good
point (+7 against baseline) but an adverse direction (-1 against its parent).
Baseline 10, parent 14 and child 12 describe a worse point (-2) but an improving
direction (+2). The threshold and repeated-run requirement still apply.

Refinement automatically binds its selected parent. A repeat in an **evidence**
slot inherits the repeated intervention's original anchor, provided the
declared experiment identity matches. Thus 8 -> 6 -> repeat 6 records two +2
observations, not a second zero. An evidence proposal changing the computation
does not silently inherit a local comparison.

An **explore** proposal may explicitly declare a known comparison anchor:

```json
"search": {"slot": 0, "anchor_proposal_id": "previous-poor-result"}
```

That anchor must have original numeric feedback in this goal's current
allocation. Copy its exact feedback trigger with `purpose: "ALTERNATIVE"`.
Missing, UNKNOWN, forged or cross-goal anchors are rejected. The new hypothesis
still faces the original refutation gate. An ordinary exploration trigger is
only a reaction to feedback; without the explicit anchor, a new exploration
seed continues to use the frozen baseline. This permits a poor measured seed
to produce a useful local direction without first beating the global baseline.

`neighborhood.comparisons` exposes both differences, the exact parent and
feedback hashes. Qualification groups an original parent/child experiment pair,
counts distinct candidate runs and uses the conservative observed parent/child
envelope. UNKNOWN, incompatible repeats, mixed threshold-crossing evidence and
refuted child hypotheses cannot establish a useful direction. This is an
observational heuristic, not a confidence interval, causal effect or calibrated
gradient. Repeated exposed data are still development data.

A local trial requires the configured number of distinct runs before it can
count as stagnant. A trial is stagnant only when its entire observed difference
range falls below the declared improvement threshold. After `stagnation_trials`
distinct stagnant computations at an anchor, that anchor is paused for
refinement. Renaming a hypothesis while keeping the same candidate command and
protocol does not mint another computation for this counter. A settled useful
direction resets the ordered stagnant-trial count; other anchors remain
eligible. The originals stay available for inspection and diagnosis.

When no eligible refinement parent remains and an anchor is paused, the unused
refinement slots become **explore** slots with `reason: "LOCAL_STAGNATION"`.
The declared total proposal allowance is unchanged. Evidence and exploration
floors remain; slots reserve neither workers nor compute. A new request replays
the same originals, so rereading or renaming a request cannot reset the counter.
The policy controls generation and proposal admission; already admitted runs
retain their original execution ordering and gates.

This is discrete adaptive exploration over the existing structure workflow.
The hypergraph still represents AND dependencies; it is not a differentiable
objective. A proposed topology is based on the current original snapshot, not
automatically on its preparation parent's candidate map. Parentage therefore
does not certify graph-edit distance or scientific comparability beyond the
declared measurement protocol. Actual parameter step sizes, controlled causal
probes, execution-budget allocation and global-optimum guarantees are outside
this increment. The real CLI regression in `test_rds_search_neighborhood.py`
checks the finite worse-point/improving-direction trajectory; matched-model
scientific benefit and LLM savings remain unmeasured.

Design references: [BoTorch's TuRBO tutorial](https://botorch.org/docs/next/tutorials/turbo_1)
for evidence-driven adaptation and restarts, and
[ScienceFlow v2](https://arxiv.org/abs/2608.14354v2) for evidence-aware allocation
and re-anchoring. This policy does not implement their algorithms or inherit
their reported results.
