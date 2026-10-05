# Fixed-precision local tool comparison

This public synthetic workload compares integer Newton iteration with Python's `math.isqrt`. Both compute

```text
floor(sqrt((10**precision + 1)**2 + offset)) - (10**precision + 1)
```

at the **same** `precision=16000` decimal scale. For offsets -1, 0 and 1 the exact answers are -1, 0 and 0: they follow directly from consecutive-square inequalities and do not depend on either implementation. The tools return small exact integers, avoiding JSON integer-string conversion limits for the large intermediate integers.

From the repository checkout, replace `<new-project>` with a new local research directory:

```text
python -B scripts/rds_cli.py --root <new-project> rsi extract --source examples/tool-comparison/newton.py --entry scaled_root --name root-newton
python -B scripts/rds_cli.py --root <new-project> rsi extract --source examples/tool-comparison/isqrt.py --entry scaled_root --name root-isqrt
python -B scripts/rds_cli.py --root <new-project> rsi compare --baseline root-newton --candidate root-isqrt --cases examples/tool-comparison/cases.json --precision-key precision --precision 16000 -t 10 --min-speedup 1.1 --json
```

Use `--ledger <existing-wall-budget-project>` during budgeted research. Each validation reserves its bounded allowance against that existing ledger; without it the overall authorized budget remains UNKNOWN. The unchanged command reuses both jobs. Failed cases and costs remain visible. No function registration, code adoption or main-repository merge follows automatically.

Inspect the actual returned ratio and reasons. A single baseline-then-candidate measurement may vary with startup and system load. `OBSERVED_SPEEDUP` is descriptive, and no cross-machine or statistical acceleration claim follows. This example demonstrates fixed-work tool qualification and measured cost consumption; it supplies no covering-radius polynomial, global proof or measured RDS research-policy gain.
