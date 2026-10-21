# The `max_nodes` ceiling is what fails ten equations, not the budget

Sizes every truth law in the 40-equation devset and asks what raising the structural
ceiling would buy. Reproduce with

```
python -B benchmark/newtonbench/truth_size.py
```

which writes `benchmark/newtonbench/results/truth-size.json`.

## Where the number comes from

`devset_run.py` never declares `synthesis`, so every cell inherits
`rds_compass_declaration.DEFAULTS['synthesis']['max_nodes'] = 7`. That value is a hard
structural bound on the enumerator: an expression with more than seven nodes is not
"expensive to find", it is **outside the search space entirely**. The declared
`max_candidates` budget cannot buy it, and neither can more experiments.

Measured with `model.parse` + `model.size` over `truth.law` for all 40 equations, the
ten laws above the ceiling are:

| equation | size |
| --- | --- |
| `feynman-I.41.16` | 24 |
| `feynman-II.24.17` | 16 |
| `feynman-III.10.19` | 14 |
| `feynman-I.40.1` | 13 |
| `feynman-I.10.7` | 12 |
| `feynman-I.6.20a` | 12 |
| `feynman-I.8.14` | 12 |
| `feynman-I.18.4` | 11 |
| `feynman-I.27.6` | 9 |
| `feynman-II.34.11` | 9 |

`I.18.4` and `I.8.14` both appear in the baseline as cells that consumed their whole
budget and returned `SEARCH_EXHAUSTED`. They were never solvable at this ceiling. The
status was correct; only the cost of reaching it was waste.

## The coverage curve is a staircase, and 8 is a step that does not exist

| `max_nodes` | laws expressible | |
| --- | --- | --- |
| 7 (declared) | 30/40 | 75.0% |
| 8 | 30/40 | **75.0% — no gain** |
| 10 | 32/40 | 80.0% |
| 12 | 36/40 | 90.0% |
| 16 | 39/40 | 97.5% |
| 20 | 39/40 | 97.5% (saturated) |

Raising the ceiling from 7 to 8 buys **exactly zero** equations, because the cheapest
unreachable law sits at 9. A one-node bump is the change most likely to be attempted
first and it would have produced no measurable difference, which is the kind of null
result that gets misread as "size does not matter".

At 12 the ceiling covers 90%, but the enumeration at size 12 is not reachable by budget
either — the derived dialect already needs 120,134,100 candidates to cover size 7, and
each extra layer multiplies that. **Raising the ceiling and paying for the search are
two different problems, and this devset has both.**

## A second, separate limitation: the derived pool has no subtraction

`scripts/rds_compass.py` builds its domain-intermediate pool as

```python
derived[left + '_plus_' + right] = left + ' + ' + right
derived[left + '_times_' + right] = left + ' * ' + right
```

Only `plus` and `times`. A law whose irreducible intermediate is a difference or a
quotient cannot be rewritten through this pool at all, so `derived` does not lower its
size. `I.8.14` is `sqrt((x2-x1)**2 + (y2-y1)**2)`; its two intermediates are both
subtractions, and its size stays 12 while the pool reissues at size 1.

Adding `sub` and `div` to the pool would cut `I.8.14` to 8 — which lands exactly in the
gap the coverage table shows, and still misses a ceiling of 7. The two fixes compose:
a pool with subtraction plus a ceiling of at least 8 would make that equation reachable
for the first time.

## What this does not say

It does not say the ten equations are unsolvable in principle, and it does not rank
`max_nodes` above the budget work. It says that until the pool can express a law, no
amount of candidate budget finds it, and that the cheapest hypothesis — that the
ceiling is one node too low — is measurably wrong.
