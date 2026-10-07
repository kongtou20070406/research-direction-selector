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
  or UNKNOWN for that intervention withholds refinement. REFUTE also removes
  the intervention from promising repeat targets; UNKNOWN preserves diagnosis
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
