# The enumeration ceiling: why speed is not the problem

Status: conclusion note, 2026-10-21. Records what measurement rules out, so
that future work does not spend on optimising a search that cannot reach the
answer.

## 1. The search order is size-ascending, and that is structural

`scripts/rds_compass_model.py:918`:

```python
for total in range(2, max_nodes + 1):
    ...
    for op in operators:
        for left_size in range(1, total - 1):
```

Every expression of size `k` is considered before any expression of size
`k + 1`. This is iterative deepening, and it is the right order when a short
law exists: the first fit found is usually small, which is what the mathematics
devset shows, where all thirty cells stop after eight experiments.

The same order is fatal when no short law exists. To reach size `k` the search
must exhaust every expression of size below `k`, and that count grows
super-exponentially. A law of size 12 is therefore unreachable not because the
budget is too small in some tunable sense, but because the predecessor sets are
astronomically large.

## 2. Measured counts

From `docs/diag-enum-budget.md` and `benchmark/newtonbench/results/truth-size.json`,
for the `feynman-I.8.14` cell at B=8:

| Quantity | Value |
| --- | --- |
| Truth law size | 12 |
| Declared `max_nodes` | 7 |
| Search space, derived pool, size <= 7 | 120,134,100 |
| Derived budget used | 200,000 |
| Fraction covered | 0.1665% (1 in 601) |
| Where the budget dies | mid size 5 (cumulative 440,740) |
| Wall clock in the derived branch | 639.3 s of 723.3 s (88.4%) |
| Fits produced | 0 |

And across the whole 40-equation set, ten laws have size above 7. Four of the
ten timed-out or exhausted cells are among them: `I.8.14` (12), `I.10.7` (12),
`II.24.17` (16), `I.6.20a` (12).

## 3. What this rules out

**Do not optimise `consider`.** The enumerate-then-check loop has already had
two rounds of constant-factor work (child-value reuse, fits memoisation), worth
1.86x on the wall clock. It does not change which laws are reachable. A further
2x buys nothing on the cells that matter: they produce zero fits either way.

**Do not just raise `max_candidates`.** Going from 20,000 to 200,000 is a 10x
budget increase against a 571.7x larger space. Reaching size 6 and 7 needs
roughly three orders of magnitude more than the current cap, and size 12 is
unreachable at any cap that fits in a wall-clock budget.

**Do not raise `max_nodes` alone.** With `max_nodes = 12` the search space grows
far faster than the budget, so the search still dies in the low sizes and still
never reaches twelve.

## 4. What is left that can work

1. **Inverse decomposition** (`scripts/rds_compass_decompose.py`). Measured at
   190x on its own: 331.52 s and a full 200,000 budget for the enumerator
   versus 1.71 s for the decomposer, and it recovers the size-5 truth at a
   budget where enumeration returns nothing at all. It sidesteps the prefix
   problem because it never builds the tree bottom-up. Its general wiring was
   rejected on a pre-registered gate (`docs/compass-decompose-wiring-20261021.md`,
   commit `50c5348`) which is a statement about that gate's conditions, not
   about the method.
2. **A hypothesis proposed from outside the grammar.** The general-Compass note
   (`docs/compass-general-20261021.md`) makes the hypothesis source a seam, so
   a model or a human can put a size-12 structure into the pool without the
   enumerator having to find it.
3. **A different representation.** If a law is naturally a composition of two
   simpler laws, declaring the intermediate as a derived column shrinks the
   effective size. This is what the `derived` block is meant to do; it currently
   fails because the derived pool makes the space larger rather than the laws
   shorter, since the enumerator still has to discover the composition.

## 5. The one-line rule

A cell whose truth law is larger than `max_nodes` cannot be solved by
enumeration at any affordable budget. Detect that case and stop, rather than
spending 88% of the wall clock confirming it.
