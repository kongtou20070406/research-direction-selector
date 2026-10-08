# Finite continuation comparison

```powershell
python -B examples/deterministic-continuation/measure.py --workspace ../RDS-continuation-measurement
```

Use a new empty sibling workspace. The script freezes its protocol before the
first run and retains every command, original receipt, result and failure. It
performs three repetitions per arm with alternating order. The workload is the
unchanged public six-row `owned-advisor` example: same commands, goal, data,
wall/CPU caps and evaluator. Its original goal remains FALSE.

The ordinary arm consumes the returned `advance --brief` advice immediately and
stops when no route is selected; it does not add a redundant final inspection.
The new arm runs one `drive --until-judgment` pass, reserving two seconds from the
same wall budget for control work. Both run the same two workers. The report
separates control CLI elapsed time, setup/evaluation elapsed, worker costs and
the new explicit controller charge. `commands.json`, `final-state.json` and
`final-advice.json` retain detailed evidence outside the checkout.

These public deterministic development cases test call reduction and retained
outcomes. They call no model, measure no token savings and establish no research
policy gain. Do not reinterpret the unmet goal as success or rerun failures to
obtain a better number.

## Recorded local observation — 2026-10-08

The [original report](results/2026-10-08-report.json) retains all six runs and
source hashes. Both arms have the same frozen contract hash and two successful
worker receipts; all six goal outcomes are FALSE.

| Arm | Control calls per run | CLI elapsed min / median / max (seconds) |
| --- | --- | --- |
| Efficient `advance` | 2, 2, 2 | 2.827 / 3.201 / 4.464 |
| `drive --until-judgment` | 1, 1, 1 | 2.581 / 4.098 / 4.195 |

This sample shows fewer caller interventions, **not a latency improvement**.
The drive control measurements were 2.124, 2.177 and 1.382 seconds; the first
two include charged projection overruns beyond the two-second hold. Those
overruns and original worker costs remain in the reported ledger. The old
one-step path's absent controller measurement is not zero outer control cost;
its full CLI times are reported separately. Neither arm called a model, so a
reduction in model cost or scientific time-to-result remains unmeasured.
