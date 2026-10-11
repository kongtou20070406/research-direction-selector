# Finite continuous-expression confirmation

`rds_domain_confirmation` supports `domain: "continuous"` with
`rules.kind: "numerical_expression_evaluation"`. The host parses a candidate's
bounded arithmetic expression, computes predictions on frozen feature rows, and
recomputes numeric fit against separately frozen labels. Candidate Python is
never imported or executed by this adapter.

This is a **finite floating-point fit check**. A `PASS` says that the submitted
expression meets the predeclared numeric thresholds on exactly those rows. It
is not a symbolic identity, historical rediscovery, generalization, causal
explanation, proof of scientific independence, or measured research-policy gain.

## Frozen input contract

Use the existing owned-project confirmation declaration, independent candidate
and confirmation route IDs, retained original receipts, and file bindings. No
new ledger or execution state is introduced. The declaration has exactly two
ordered data references:

1. `data[0]`: features, exclusively bound with role `data`
2. `data[1]`: labels, a different file exclusively bound with role `data`

The claim is bound as `config`; the directly executed confirmation script is
bound as `evaluator`. All hashes identify the actual file bytes, not a
reserialized JSON value. The candidate and confirmation must both complete
successfully with unchanged bindings. The host checks original receipt hashes,
completion events, output artifact hashes, and current frozen files before
replaying the expression.

Claim example:

```json
{"schema":1,"kind":"numerical_expression_evaluation","max_nrmse":0.1,"min_r2":0.99}
```

Both thresholds must pass. `max_nrmse` is in `[0, 1000000]` and `min_r2` in
`[-1000000000000, 1]`. Booleans, numeric strings and nonfinite values are rejected.

Feature example, with **synthetic** rows:

```json
{"schema":1,"variables":["x0"],"rows":[
  {"id":"r0","values":[-1]},
  {"id":"r1","values":[0]},
  {"id":"r2","values":[1]},
  {"id":"r3","values":[2]}
]}
```

Label example:

```json
{"schema":1,"rows":[
  {"id":"r0","target":-1},
  {"id":"r1","target":1},
  {"id":"r2","target":3},
  {"id":"r3","target":5}
]}
```

The feature, label and candidate row IDs must match completely and in the same
order. IDs are unique, nonempty strings of at most 128 characters. No row
selection, duplicate IDs, omitted predictions, ignored bad rows or reordering is
allowed. Features are named `x0`, `x1`, etc., consecutively; labels and row IDs are
not expression variables. The strict schemas reject extra fields, including a
`target` embedded in a feature row. This catches schema-level label leakage; it
cannot discover that someone manually copied a target into a numeric feature.

For real observations, prepare this same JSON from the intended source, retain
its public provenance and transformation procedure, and freeze all file hashes
before dispatch. The adapter has no built-in historical dataset, manifest,
private path, oracle, train/test split, or source-truth claim. A hash proves file
identity, not the source's correctness or the date when someone first saw it.

## Candidate and confirmation outputs

A candidate provides an expression, not scores or a self-signed verdict:

```json
{"schema":1,"status":"candidate","inputs_sha256":"<feature-file SHA-256>",
 "row_ids":["r0","r1","r2","r3"],"expression":"2*x0+1"}
```

Abstention is explicit, retains input and row identity, and has no expression:

```json
{"schema":1,"status":"abstain","inputs_sha256":"<feature-file SHA-256>",
 "row_ids":["r0","r1","r2","r3"]}
```

`check_output(claim, features, labels, candidate, feature_sha256)` returns
`status`, host-recomputed `metrics`, `row_count`, `feature_count`, and
`ast_nodes` for a valid candidate. Abstention returns `UNKNOWN`, a reason,
`row_count` and `metrics: null`. `evaluate(expression, features)` is a
feature-only helper returning row IDs, predictions and AST node count.
Malformed or undefined inputs raise `ValueError`; the owned confirmation wrapper
preserves the real process receipts and turns these into `UNKNOWN`.

The frozen confirmation script's original output must have exactly these fields:

```json
{"candidate_receipt_sha256":"<original candidate receipt SHA-256>",
 "claim_sha256":"<claim-file SHA-256>",
 "evaluator_sha256":"<evaluator-script SHA-256>",
 "inputs_sha256":"<feature-file SHA-256>",
 "labels_sha256":"<label-file SHA-256>",
 "verdict":"PASS"}
```

`verdict` can be `PASS`, `FAIL` or `UNKNOWN`, and must agree with native replay.
A successful evaluator process that asserts `PASS` for a failing expression
produces `UNKNOWN`, with the contradiction retained. Claimed metrics are not an
alternative evidence route: extra `metrics`, `predictions`, `verdict` or other
self-certification fields in candidate output are rejected. Extra metrics in
the confirmation output are rejected too. The host computes the only reported
metrics from the original expression and frozen labels.

## Arithmetic and limits

The interpreter permits real numeric literals, `x0` through the declared last
feature, constants `pi` and `e`, unary `+`/`-`, binary `+`/`-`/`*`/`/`/`**`, and
one-argument `sin`, `cos`, `sqrt`, `log`, `exp`, and `abs`.

It does not use `eval`, `exec`, compilation, pickle, symbolic algebra, candidate
imports, attribute access, subscripts, comparisons, conditionals, comprehensions
or arbitrary calls. Power exponents must be signed numeric literals with
absolute value at most 12. Each intermediate and prediction must be a finite
real number with magnitude at most `1e150`. An `exp` argument has absolute value
at most 300. Undefined division, logs, roots and powers fail closed.

- Each claim, features, labels, candidate or confirmation JSON is at most 1 MiB
- 2–8192 rows, 1–64 features, exact feature dimension on every row
- Expression at most 4096 UTF-8 bytes, 128 evaluated AST nodes, depth 16
- Duplicate JSON fields, NaN, infinity, booleans and malformed evidence rejected
- Every row must evaluate successfully; there is no partial-coverage score

Calculations use Python binary64 floating point and `math` functions, rather
than exact rational or interval arithmetic. Numeric platform roundoff near a
threshold can matter. There is no undisclosed tolerance around the gates.

With `SSE = sum((prediction - target)^2)` and
`SST = sum((target - mean(target))^2)`:

- `NRMSE = sqrt(SSE / SST)`, normalized by population target standard deviation
- `R² = 1 - SSE / SST`
- `RMSE = sqrt(SSE / row_count)`

The implementation scales by the maximum absolute target before accumulation
and uses `math.fsum` for the mean and `math.hypot` for norms to reduce avoidable
overflow, underflow and summation error. Constant targets, unrepresentable
normalized metrics, arithmetic overflow and normalization underflow remain
`UNKNOWN`; zero error on constant labels is not promoted to a normalized-score
pass. A well-formed finite expression missing either gate is `FAIL`.

## Independence and disclosure boundary

Native recomputation is independent of candidate-reported scores. That does
**not** establish that the candidate was blind to the labels. Shared project
bindings are not an operating-system sandbox. The adapter reports
`confirmation_independence: "DECLARED_EXPOSED"` if the ledger lists the label
hash in candidate data exposure, otherwise `"UNKNOWN"`. It never upgrades either
case to independently held-out evidence.

Results expose `assurance: "RECOMPUTED_FINITE_FLOAT_EXPRESSION"`,
`finite_evaluation` with the recomputed status and aggregate metrics, and
`symbolic_identity`, `causal_support`, `generalization`, and
`scientific_support` as `UNKNOWN`. Finite-fit `task_confirmation: "PASS"`
does not change those limitations. The replay result does not echo targets,
per-row errors, or label-derived counterexamples.

The autonomy request builder omits this adapter's label binding and final
confirmation output from evidence excerpts, matching protected labels by both
canonical path and SHA-256. It refuses a new repair request after any final
confirmation attempt with `FINAL_CONFIRMATION_REACHED`. This is a serialization
filter and final-use guard, not filesystem isolation. Use separate explicitly
declared validation routes for adaptive feedback.

Keep label files and confirmation feedback out of candidate generation context.
For a genuinely prospective final test, freeze a candidate before accessing the
labels and enforce the relevant access boundary separately; packet omission or
a candidate's own isolation claim is insufficient. Do not reuse final-test
feedback for an adaptive repair while still calling that test unused.

## Executable control and verification

The [continuous confirmation control](../examples/continuous-confirmation/run.py)
runs real CLI project initialization, autonomous dispatch, and a repeat dispatch
to check receipt reuse. Its four public synthetic rows and deterministic
candidate scripts are software controls; it makes no model call and measures
no scientific gain. Use a different empty sibling workspace for each mode:

```sh
python -B examples/continuous-confirmation/run.py --workspace ../continuous-correct-check --mode correct
python -B examples/continuous-confirmation/run.py --workspace ../continuous-wrong-check --mode wrong
python -B examples/continuous-confirmation/run.py --workspace ../continuous-undefined-check --mode undefined
python -B examples/continuous-confirmation/run.py --workspace ../continuous-abstain-check --mode abstain
```

The finite confirmations are respectively `PASS`, `FAIL`, `UNKNOWN` and
`UNKNOWN`. Inspect the workspace's `out/trajectory.json` for original CLI output
and `out/summary.json` for receipts, repeat execution count, and bounded
confirmation evidence. Replace an already used workspace name instead of
reinitializing its ledger.

Focused regression checks:

```sh
python -B -m unittest discover -s tests -p test_rds_continuous_confirmation.py -v
python -B -m unittest discover -s tests -p test_rds_domain_confirmation.py -v
python -B -m unittest discover -s tests -p test_rds_polynomial_confirmation.py -v
```

The synthetic regression suite covers actual owned executions and original
receipts, correct and wrong expressions, self-certified success, abstention,
malformed, excessively nested and duplicate JSON, NaN, undefined and overflowing arithmetic,
constant targets, complete row identity, label-schema leakage, changed files,
forged receipts, bounded inputs and no-repeat recovery. These tests do not
measure scientific discovery or held-out generalization.
