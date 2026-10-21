# Pre-registration: reserve confirmation budget before spending it on coverage

Written before any measurement, at `f1d380c`, after `docs/sparse-tail-b32-trace.md`
resolved what actually happens in the one pareto cell that reports no claim.

## The mechanism this targets

`scripts/rds_compass.py:573` computes `spending = spend and (not cap or bought < cap)`.
`spend` defaults to `True` (`scripts/rds_compass_declaration.py:40`) and `cap` defaults to
`0`, so `not cap` is true and `spending` holds until the experiment budget runs out. The
CONFIRM branch is at `:562-581`; the HOLDOUT branch is at `:582-585`, after it. `remaining()`
(`scripts/rds_compass.py:218-221`) excludes holdout candidates, so CONFIRM cannot buy them
and will consume every non-holdout candidate first.

In `sparse-tail / rds / B=32` that means 30 CONFIRM purchases, then the two holdout points,
then — at experiment 32 of 32 — the holdout point `x31` refutes the leading `lin` and an
escape synthesises the correct law. `novel_confirmations` is 0/2 because no experiment is
left to test the law that was just learned. The claim degrades to
`CONSISTENT_BUT_UNCONFIRMED` and `claim_exact` is multiplied to 0 by
`benchmark/newtonbench/pareto.py:315-320`.

## The change

One condition on `spending` (line-level, no other branch touched):

```python
left = _cap(plan) - spent
reserve = 2 * plan['verification']['confirmations'] + len(plan.get('holdout') or [])
spending = spend and (not cap or bought < cap) and left > reserve
```

The reserve is two confirmation rounds plus the holdout points: one round for the leading
hypothesis to reach its minimum, one for a hypothesis a late escape may still produce.

## Judgement, fixed before measuring

**Primary cell**: `sparse-tail / rds / B=32`, reproduced with
`D:\Myfile\work space\RDS\trace_sparsetail.py` and then with the full pareto run.

| signal | baseline at `f1d380c` | required |
| --- | --- | --- |
| `claim` | `CONSISTENT_BUT_UNCONFIRMED` | `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS` |
| `claim_exact` | 0 | 32 |
| `novel_confirmations` | 0 | >= 2 |
| `law` | `ite((x < (61/2)), (1 + x), 1000)` | unchanged |
| `experiments` | 32 | <= 32 |

**Safety, on the full 36-cell pareto run written to `results/pareto-confirmreserve.json`**
(never overwrite `results/pareto-stage2.json`, it is the control):

1. `claim_exact` total across the rds arm does not decrease from 15 (the count before the
   false-certification exclusion) and the post-exclusion correct count does not decrease
   from 14.
2. `false_certification_count` does not increase above 1.
3. No cell that reports `GOAL_IDENTIFIED` at `f1d380c` may report `SEARCH_EXHAUSTED` or
   `JUDGMENT_REQUIRED` after the change.
4. `experiments_per_correct` does not rise more than 10% above 21.0.

**Reproduction safety, on the devset**: `python -B benchmark/newtonbench/devset_run.py
benchmark/newtonbench/results/devset-confirmreserve.json` must keep `claim_exact` at or
above the current total and keep `feynman-I.6.20a` at `SEARCH_EXHAUSTED` (it must not turn
into a false certification).

## Rollback

Any safety item failing rolls the change back. The failing item and its numbers go into
`docs/benchmark-confirmreserve-20261021.md`; the protocol and the result file stay in the
tree either way.

## Minimal reverse test

Set `spending` back to its old two-term form and re-run the primary cell: `claim_exact`
must return to 0 and `novel_confirmations` to 0. A test that only asserts the new claim
without this reverse does not establish that the reserve is what moved it.
