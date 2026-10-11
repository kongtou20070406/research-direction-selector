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

An algorithm baseline completed before an adopted method change may be reused
only through this ledger's verified contract ancestry. Its original run,
receipt, output bytes and ancestor-authorized route must agree; the protected
goal, comparison data, claim and evaluator must match the current declaration.
The candidate and confirmation still bind the current contract. Recovery
consumes the original paid baseline without rerunning it or rewriting its
receipt. A caller-supplied history cannot supply ancestry, and changed original
baseline bytes remain UNKNOWN.

## Finite continuous expressions

`domain="continuous"` with `rules.kind="numerical_expression_evaluation"`
replays a bounded expression against separately frozen feature and label files.
It independently computes finite-row NRMSE, R² and RMSE rather than accepting a
candidate's metrics. A PASS establishes only the declared finite numeric fit;
it does not establish unexposed labels, generalization, symbolic identity or
causality. The current shared project bindings remain explicitly reported as
exposed. See [schema, limits and runnable controls](continuous-confirmation.md).

Continuous labels are omitted from automatic repair excerpts, and a started
final-confirmation attempt cannot trigger a fresh repair request. Use separate
development routes for validation feedback. Serialization filtering is not
filesystem isolation. The separate [opt-in benchmark runner](blind-evaluation.md)
requires live isolation checks and refuses execution when they are unavailable;
ordinary project execution and native provider transport are unchanged.

## CPU MLP with post-commit samples

An additional opt-in deep-learning declaration uses
`rules.kind="torch_postcommit_mlp"`. The default shared-data linear profile
keeps its original UNKNOWN task boundary. The new claim is exactly:

```json
{"schema":1,"kind":"torch_postcommit_mlp","layers":[1,8,8,1],"activation":"tanh","sampler":"sha256_uniform_v1","label":"cubic_mean_v1","sample_count":32,"max_mse":0.01}
```

The supported architecture has two hidden layers, each dimension in 1–16,
scalar output, CPU FP32 inference and 1–256 future samples. Training data stays
bound by the existing contract. After the candidate's successful original
receipt and weight bytes settle, ordinary registration of the preauthorized
confirmation route creates one program seed in the existing CAS and commits a
hash-bound `DOMAIN_CHALLENGE_COMMITTED` event in the same reservation transaction.
The event binds genesis, current contract, candidate receipt, original weight
artifact, declaration and confirmation manifest. Starting the worker rechecks
those identities. Ledger order requires candidate completion before challenge
commitment; a supplied seed, wall clock or `heldout` flag is not evidence.

`sha256_uniform_v1` derives each coordinate from SHA-256 of the 32-byte seed
followed by unsigned big-endian 32-bit sample and coordinate indices. Its first
seven bytes produce `2*u/2^56-1`. `cubic_mean_v1` labels a row with the mean of
`0.5*x^3-0.2*x+0.1`. These small public procedures are frozen before training.
Candidate JSON is `{"layers":[{"weight":[[...]],"bias":[...]},...]}`, matching
the declared three affine layers with tanh between them. Each retained finite
number is bounded in magnitude to 1e6. The host evaluates the hash-acquired
JSON values rather than reopening weights or importing candidate code.

The original confirmation report binds `challenge_sha256` and `weights_sha256`
in addition to the existing receipt/claim/evaluator identities, and retains
the exact `samples:{x,y}`. The host regenerates those samples and recomputes
MSE. Threshold success or a counterexample yields finite task PASS or FAIL;
identity/order/dependency gaps, contradictory self-verdicts or changed original
bytes yield UNKNOWN. Recovery uses the same committed seed, attempt and paid
receipt. Duplicate challenge events are rejected; failed confirmation cannot
redraw that candidate's samples. An uncommitted transaction can leave orphan
CAS bytes; they are not a committed challenge or a launched attempt.

The resulting `GENERATED_AFTER_WEIGHT_COMMIT` scope means new finite samples
after those weights were fixed. External secrecy, population generalization,
scientific support and measured research-policy gain remain UNKNOWN. No GPU,
new service, dataset access boundary or default goal migration is introduced.

## Mathematics

`rules.kind=polynomial_rational_evaluation` is a separate finite QQ arithmetic
profile. Its frozen claim is exactly
`{"schema":1,"kind":"polynomial_rational_evaluation","quantities":["values"]}`,
or quantities `["values","sparse_jacobian"]`. It does not use the certificate
backend below. It supports up to 128 variables, 128 polynomials, eight rational
points and 16,384 sparse terms, each of total degree at most two. These are
capacity bounds, not evidence that any particular scientific system is solved.

One frozen data JSON contains exactly `schema:1`, an ordered `variables` list,
`polynomials` and `points`. A polynomial is
`{"id":"F0","terms":[{"coefficient":"3/2","powers":[[0,2]]}]}`.
Powers contain increasing variable indices and positive integer exponents;
duplicate monomials, zero coefficients, booleans and floating indices are
rejected. An empty term list represents the zero polynomial. A point is
`{"id":"p0","coordinates":["0", "-1/2"]}`, covering all declared variables.
Variable, polynomial and point identities must be unique within their lists.

QQ inputs use `str(Fraction(value))` canonical strings: integers such as `0`
and `-1`, otherwise reduced `p/q` with a positive denominator. Decimal,
exponent, float and expression inputs are rejected, never evaluated as code.
Input numerators/denominators allow 1,024 bits, including approximately 90-digit
rational certificate coordinates. Intermediate results and submitted outputs
allow 8,192 bits. This profile bounds each original claim, data, candidate and
confirmation JSON file to 1 MiB; the other profiles retain their existing
2 MiB JSON bound. Arithmetic or size limits yield UNKNOWN.

The candidate output is exactly `schema:1`, `inputs_sha256` (the original data
file hash), `point_ids`, `polynomial_ids`, and `values`. Values form a matrix
ordered first by point and then polynomial, with every entry a canonical QQ
string. With sparse Jacobian requested it also includes `jacobian`, one ordered
list per point of `[polynomial_index, variable_index, "QQ"]` entries.
Structural derivative coordinates are independently derived from the original
terms, sorted by polynomial then variable, and all must appear even when a
derivative evaluates to zero at a particular point. Candidate declarations do
not determine sparsity. The independently executed frozen evaluator output
additionally binds `inputs_sha256`, along with the receipt/claim/evaluator
identities described above. The host independently recomputes values and
derivatives from the frozen data using exact Fraction arithmetic.

Well-formed unequal arithmetic produces FAIL with a located expected/actual
counterexample. Missing entries, malformed formats, changed identities or a
contradictory evaluator self-verdict remain UNKNOWN. PASS supports only equality
of these declared finite evaluations. It does not prove KKT feasibility, a
unique root, disk covering, global optimality or a minimal polynomial; each of
those conclusions and scientific support remain UNKNOWN. In particular, an
original goal must not equate this finite arithmetic check with solving the
13-disk covering problem. The included 119-variable quadratic regression is a
synthetic capacity case, not the original covering system or a research result.

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
