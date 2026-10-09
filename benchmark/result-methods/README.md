# Digits MLP result-method benchmark

This bounded CPU task uses the existing [UCI handwritten Digits subset exposed
by scikit-learn](https://scikit-learn.org/stable/modules/generated/sklearn.datasets.load_digits.html)
and its [MLP classifier/regressor](https://scikit-learn.org/stable/modules/neural_networks_supervised.html).
It tests analysis correctness on real trained neural-network outputs. It does
not measure RDS-driven model improvement or autonomous research benefit.

The frozen seed is 0, with stratified task-train/head-fit/heldout splits of
1,078/359/360. Baseline `(16,8)` and candidate `(32,16)` classifiers each receive
40 Adam epochs; gain-regressor and benefit-classifier `(16,8)` heads each receive
80 epochs on the separate head-fit split. Inputs are original probabilities,
without true labels as head features. The final gate is fixed at
`predicted_gain < 0 AND p_better >= 0.5`. No heldout tuning, selection, restarts or
extra seeds occur. Writer correlation is unknown, so no IID standard error is
estimated from heldout examples.

Use an isolated optional benchmark environment with scikit-learn. The recorded
run used Python 3.13.5, NumPy 2.2.6, SciPy 1.18.1 and scikit-learn 1.9.1. Normal
RDS and its unit tests require no new dependency. From the checkout, substitute
your environment's Python executable:

```powershell
python -B scripts/rds_cli.py --root . exec --name digits-methods-example --timeout 120 --bind code=scripts/rds_result_tools.py --output outputs/digits/summary.json --json -- <benchmark-python> -B benchmark/result-methods/run.py --workspace outputs/digits
```

RDS binds both code files and runs an independent worker with a 120-second
process-tree deadline. Script loop checks provide additional deadline checks;
they cannot interrupt a stalled single library call. Running the script directly
has only those cooperative checks. A fresh output directory is required;
rejected existing directories are unchanged. Failed/timeout attempts remain in
their original job, with no automatic training rerun. Preparation, qualification
and application costs from the separate owned finite example are additional
costs, not included in this neural benchmark's timing.

The independent NumPy oracle calculates support and accepted counts without
calling RDS tools. Scalar `math.log` checks all candidate losses against NumPy
losses. Both scalar and paired comparisons read the same losses; the existing
scalar comparison only returns an aggregate delta. Its time is retained as
context, with **no speedup ratio** across different output scopes. Thirty timing
repetitions reuse frozen model outputs and do not repeat training.

## Recorded run, 2026-10-10

Original records are under [`results/20261010`](results/20261010/summary.json).
The RDS worker succeeded once in **9.732614 seconds**, including process/library
startup. Internal task/analysis wall was **0.904727 seconds**; four fitting stages
totaled **0.441215 seconds**. CPU-seconds were not measured, GPU was unused.

| Operation | Workload | Median of 30 calls | Observed min–max |
| --- | --- | ---: | ---: |
| Existing scalar comparison | two mean losses | 0.030 ms | 0.029–0.106 ms |
| Paired comparison | 360 paired losses with metadata | 0.474 ms | 0.454–0.653 ms |
| Decision diagnostics | 360 rows, support/lineage/group checks | 4.983 ms | 4.934–5.902 ms |
| Residual check | 360 independent scalar-log references | 0.575 ms | 0.558–0.875 ms |

All checked counts matched the independent oracle; finite residuals were within
the declared `1e-12 + 1e-12*abs(reference)` tolerance. Mean loss improvement was
0.173882 nats. Heldout classifier accuracy was 91.11%/96.39% for the two
predeclared architectures. This architecture difference is not an RDS learning
gain and is not a general architecture recommendation.

The unfavorable decision-head result is retained:

| Head | Beneficial accepted / support | Harmful accepted / support |
| --- | ---: | ---: |
| Regression | 278 / 312 | 29 / 48 |
| Classifier | 312 / 312 | 48 / 48 |
| Conjunction gate | 278 / 312 | 29 / 48 |

The classifier accepted all examples. Nine of ten digit groups had constant
classifier decisions and mixed true benefit/harm labels; this does not identify
a causal mechanism. The gate still accepted 29 harmful examples. Software
correctness PASS therefore leaves model/gate reliability and scientific gain
unproved. The data was used to evaluate these tools, so it is no longer unseen
confirmation evidence for subsequent changes selected from this result.

Public JSON includes original predictions/rows, source/evaluator inputs,
protocol/splits, independent oracle, full returned tools and extracted original
receipt fields. Original image NPZ and model weights remain in the local frozen
job. Artifact hashes identify this run's exact files; NPZ container hashes and
floating-point outputs are not promised identical on a different host/library.
The two `source-*.py.txt` artifacts retain the exact executed code bytes and
recorded SHA256, including original host line endings. Result attributes prevent
Git newline normalization from changing those original data/artifact hashes.
No SQLite contention or end-to-end CLI latency benchmark is claimed by these
in-process tool timings. The operational wrapper's UNKNOWN protocol fields are
preserved; the actual neural protocol is the separately frozen `protocol.json`.
