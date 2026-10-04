# Bounded domain confirmation

An execution receipt establishes what ran and which original files it produced.
This optional owned-policy adapter independently checks a narrow declared task
over those originals. Its task verdict does not certify scientific usefulness,
novelty, an unseen population, or research-policy improvement.

`advisor_policy.confirmation` is frozen before initialization. It uses the
existing routes, receipts, observations, goal, checkpoint history and budget;
it creates no separate state database. Candidate and confirmation run IDs must
be distinct. The confirmation route directly executes the evaluator script
frozen exclusively with role `evaluator`. Method changes must preserve this
declaration, the final producer, evaluator, claim and data authorities.

```json
{
  "schema": 1,
  "domain": "mathematics",
  "candidate_runs": ["candidate"],
  "confirmation_runs": ["confirmation"],
  "scope": "Exact declared affine-rational statement only",
  "claim": {"path": "claim.json", "sha256": "<frozen SHA-256>"},
  "evaluator": {"path": "confirm.py", "sha256": "<frozen SHA-256>"},
  "data": [{"path": "inputs.json", "sha256": "<frozen SHA-256>"}],
  "rules": {
    "kind": "exact_certificate",
    "candidate_output": "outputs/proof.json",
    "confirmation_output": "outputs/confirmation.json"
  }
}
```

This is a configuration fragment, not a complete executable contract. Claim
files must be exclusively `config` bindings; data files must be exclusively
`data` bindings. Outputs must belong to the declared routes. Current support
is one candidate and one confirmation route. The algorithm profile also
requires a separate baseline route. At most 16 data references and bounded
JSON originals are accepted. The host adapter never imports candidate Python,
launches a candidate worker, installs a dependency or dispatches a GPU job.
Registered mathematical certificate checking retains its own backend semantics;
an existing native Lean backend can perform its bounded local replay. Inspection
and orchestration overhead are not the candidate worker's measured receipt cost
and must be accounted separately when claiming a total research budget.

The original confirmation output names `candidate_receipt_sha256`,
`claim_sha256`, `evaluator_sha256`, and `verdict`. These fields identify what
was checked. They do not establish correctness: the adapter independently
recomputes the supported conclusion and rejects a contradictory self-verdict.
Malformed, changed or unavailable originals stay `UNKNOWN`. Receipt failure
does not promote partial output to reliable measurement.

Inspection separates `execution`, `task_confirmation`, `assurance` and
`scientific_support`. A completed checker can report a valid task `FAIL` while
its operational receipt is `SUCCEEDED`. An evaluator should exit zero when its
checking operation completes, including a checked counterexample, and retain
the task verdict in its output. Execution faults retain unsuccessful receipts.
Scientific support and policy gain remain `UNKNOWN` and false respectively.

## Mathematics

`rules.kind=exact_certificate` reads the candidate's original framework result
or certificate and replays it with `rds_verify.checked_result` against the
frozen claim. Supported example: `x -> x/2 + 1/2` has contraction norm below one
and fixed point one under exact rational semantics. A wrong fixed point yields
a checked counterexample. A JSON object claiming PASS without a valid bound
certificate cannot confirm the task.

This checks the declared proposition. It does not prove correspondence to an
arbitrary executed Python model. Conditional statistical theorem certificates
retain unresolved empirical application status, so their theorem PASS does not
confirm an empirical premise or dataset assumption. Unsupported expressions,
backend failures and missing proof evidence remain unknown.

## Algorithms

`rules.kind=integer_sum_squares` uses claim
`{"schema":1,"kind":"integer_sum_squares"}` and one frozen data file containing
1–64 lists of integers. Each list has at most 64 values, bounded by `2**63` in
absolute value; booleans and floating-point substitutes are rejected. It also
declares `baseline_run` and `baseline_output` in rules.

Candidate and baseline original outputs contain `values` and `inputs_sha256`.
The program recomputes every expected integer result. A baseline that fails the
oracle cannot support a comparison. It reports both original measured wall
costs for the same frozen inputs and executable identity. This is a whole-run,
single-sample observation, not an asymptotic complexity or general speedup
claim. Empty inputs, negative values and an intentional omitted term exercise
the oracle's real boundaries. Universal correctness needs a separate proof;
independent algorithm effectiveness needs new cases and a matched total-budget
comparison including validation and unsuccessful work.

## Deep learning

`rules.kind=torch_linear_regression` uses a claim with exactly `schema:1`,
`kind:"torch_linear_regression"`, `input_dimension` (1–16), and nonnegative
`max_mse`. One frozen data object contains `x` rows and scalar `y`, with 1–256
rows. Candidate original weights contain a weight vector and scalar bias.
Only bounded finite numeric values are accepted. With an existing torch
installation, inspection performs actual FP32 CPU inference and recomputes
MSE from the original weights and data. No torch means UNKNOWN; no package is
downloaded. A fixture trains a real small Linear model with SGD and backprop
when torch is present.

The metric appears as `measured_mse` and `finite_evaluation`. Current project
execution declares access to all bound data for each run and does not enforce
a sealed confirmation partition. If candidate exposure includes confirmation
data, independence is `DECLARED_EXPOSED`; absent isolation evidence remains
`UNKNOWN`. Task confirmation remains UNKNOWN in either case even if finite
evaluation passes. A claim of unused independent confirmation requires a
separate enforced access boundary and preserved exposure evidence. Renaming a
file or asserting `heldout=true` is insufficient. This adapter neither proves
optimizer execution from a weight file nor extrapolates a toy model result to
scientific benefit or general deep-learning performance.

All work stays within the original authorized routes and resource allowances.
Goal consumers should use the derived task verdict, while retaining finite
metrics and failures for diagnosis. Meeting a goal threshold does not turn the
adapter's scientific UNKNOWN into acceptance.
