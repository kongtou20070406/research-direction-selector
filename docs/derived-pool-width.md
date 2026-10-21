# Widening the derived pool costs 16× the search and buys one equation

The derived pool in `_fits` forms only `left + right` and `left * right`, so a law whose
irreducible intermediate is a difference or a quotient cannot be rewritten through it.
This measures what adding `sub` and `div` to the pool would buy, and then whether it is
worth the space.

```
python -B benchmark/newtonbench/truth_size_derived.py
```

writes `benchmark/newtonbench/results/truth-size-derived.json`.

The rewrite is deliberately as narrow as the real pool: a derived name stands for
`left <op> right` over two *declared variables*, so only a two-leaf subtree can collapse,
and it collapses to one node. Greedy, largest first.

## The pool already does most of the work

| | `max_nodes=7` | `max_nodes=8` |
| --- | --- | --- |
| no derived pool (raw sizes) | 30/40 | 30/40 |
| today's pool (plus/times) | **32/40** | 32/40 |
| pool with sub/div | 33/40 | 34/40 |

Today's pool collapses eleven laws. `I.18.4` goes 11 → 5, `I.12.1` 3 → 1, `I.39.10` and
`I.9.18` and `II.34.11` 7 → 5. That is not a rounding gain; it is why the devset reports
a 75% baseline at a ceiling that looks far too small for these laws.

Adding subtraction and division moves exactly one law into range at the current
ceiling (`I.27.6`, `1/(1/d1+n/d2)`) and two at a ceiling of 8 (`I.27.6` and `I.8.14`).

## The cost is not one equation's worth

The pool grows from `2·C(V,2)` entries to `4·C(V,2)`, so the leaf pool at V=4 goes from
16 to 28 — a 75% wider leaf set. Enumeration cost scales as the leaf pool to the power
of the depth being searched, so at size 5 that is roughly 1.75⁵ ≈ 16×. For one law.

Worse, it lands on exactly the resource that is already exhausted: the derived dialect
needs 120,134,100 candidates to cover size 7 today, and the budget of 200,000 covers
0.17% of it. Making the space 16× larger to gain one law is the wrong trade in a
component whose failure mode is already "the search does not fit".

**Decision: do not widen the pool.**

## What the ten unreachable laws actually need

Sizing them was the useful part, because the reasons are not uniform:

| equation | size | why it cannot be reached |
| --- | --- | --- |
| `feynman-I.10.7` | 12 | needs `c = 2.99792458e8`; grammar declares `constants=['0','1','2']` |
| `feynman-I.40.1` | 13 | needs `g = 9.80665` and `k_B = 1.380649e-23` |
| `feynman-I.41.16` | 24 | needs `ℏ`, `c`, `k_B` |
| `feynman-II.24.17` | 16 | needs `c` |
| `feynman-III.10.19` | 14 | no constants; `mom*sqrt(Bx**2+By**2+Bz**2)` is structurally deep |
| `feynman-I.8.14` | 12 → 8 | no constants; needs two subtractions, so only the pool helps |
| `feynman-I.27.6` | 9 → 7 | no constants; needs a quotient |
| `feynman-I.41.16` | 24 | as above |

**Four of the eight are unreachable because a physical constant is in neither the
variable list nor the constant pool.** `I.10.7` is `m_0/sqrt(1-v**2/c**2)` and its
declaration offers `['0','1','2']`. No ceiling and no pool width finds that law; the
expression it is looking for cannot be spelled in the declared grammar.

That is a devset defect, not a search defect, and it is cheaper to fix than any of the
above: either declare the constants, or non-dimensionalise the equation so the constant
absorbs into a measured variable. Note that non-dimensionalising `I.10.7` to
`m_0/sqrt(1-beta**2)` still lands at size 8, so it fixes the expressibility problem
without fixing the depth problem — the two are independent.

## What this does not say

It does not say the pool should stay at plus/times forever. It says widening it is the
most expensive way to buy the least coverage, and that the search strategy — not the
pool — is what has to change before size 7 becomes affordable at all.
