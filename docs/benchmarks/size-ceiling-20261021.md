# The synthesis bound, not the budget, decides which equations are reachable

Measured on `devset-run-30.json` and `truth-size.json` at `75570aa`. Reproduce with:

```
python -B benchmark/newtonbench/truth_size.py
python -B benchmark/newtonbench/size_ceiling.py
```

## What the bound is

`scripts/rds_compass_declaration.py:35` defaults the declaration to
`synthesis: {'max_nodes': 7, 'max_candidates': 20000}`. Every escalation mode in
`scripts/rds_compass.py:779` carries that same bound forward:

| mode | line | `max_nodes` | what changes |
| --- | --- | --- | --- |
| `WHOLE_LAW` | `scripts/rds_compass.py:796` | `bound['max_nodes']` | nothing, the baseline |
| `GRAMMAR_ESCALATION` | `scripts/rds_compass.py:805` | `bound['max_nodes']` | only the operator set widens |
| `DERIVED` | `scripts/rds_compass.py:822` | `bound['max_nodes']` | derived columns, same size |
| `RESIDUAL_CORRECTION` | `scripts/rds_compass.py:837` | `min(7, bound['max_nodes'])` | never larger |
| `REGIME_SPLIT` | `scripts/rds_compass_model.py:1063` | `max_nodes=5` | smaller, per side |

So "escalation" escalates the grammar and the derived columns, never the size
ceiling. `MAX_NODES` in `scripts/rds_compass_model.py:22` is 15, but nothing in the
chain ever asks for more than the declared 7.

## What that costs on the 40-equation devset

Ten of the forty truth trees are larger than 7 nodes:

**One is reachable anyway.** `feynman-I.27.6` has truth `1/(1/d1+n/d2)` at size 9,
and the drive found `(d2 / ((d2 / d1) + n))` at size 7 — an algebraic equivalent
that fits under the bound. It is `claim_exact` at all three budgets (B=8/16/32 on
8/16/27 experiments).

**Four enumerated every tree up to 7 nodes and found none.** They report
`SEARCH_EXHAUSTED`, which in `scripts/rds_compass_model.py` means the enumeration
finished, not that a candidate cap was hit:

| equation | size | truth |
| --- | --- | --- |
| `feynman-II.24.17` | 16 | `sqrt(omega**2/2.99792458e8**2-pi**2/d**2)` |
| `feynman-I.10.7` | 12 | `m_0/sqrt(1-v**2/2.99792458e8**2)` |
| `feynman-I.6.20a` | 12 | `exp(-theta**2/2)/sqrt(2*pi)` |
| `feynman-I.8.14` | 12 | `sqrt((x2-x1)**2+(y2-y1)**2)` |

None of these four has a smaller equivalent: a distance formula, a Lorentz
factor, a Gaussian density and a wave vector all need the same node count in any
spelling.

**Five were never searched at all.** They are MISSING from `devset-run-30.json`
because the declaration rejected the cell before any search ran (the
`MAX_CANDIDATES = 64` and slot-count limits fixed earlier). Their truths are also
above the bound, so a repaired declaration would most likely reproduce the same
`SEARCH_EXHAUSTED` outcome — but that is a prediction, not a measurement:

| equation | size | truth |
| --- | --- | --- |
| `feynman-I.41.16` | 24 | Planck spectrum, four constants |
| `feynman-III.10.19` | 14 | `mom*sqrt(Bx**2+By**2+Bz**2)` |
| `feynman-I.40.1` | 13 | `n_0*exp(-m*9.80665*x/(1.380649e-23*T))` |
| `feynman-I.18.4` | 11 | `(m1*r1+m2*r2)/(m1+m2)` |
| `feynman-II.34.11` | 9 | `g_factor*q*B/(2*m)` |

## Why this is not a budget story

The four exhausted equations did not run out of candidates — they ran out of
trees. Buying more experiments buys more `rows`, which changes the fit residuals
but not the enumeration ceiling: with two rows and with eight rows the same
seven-node grammar is enumerated. `best_effort_exact` on those cells is 31/32
because the search finds a near-miss and then stops, not because it needed one
more point.

This is why the ceiling belongs in the load-bearing list next to `max_candidates`:
raising the experiment budget cannot move it, and the benchmark's honest ceiling
is `(equations with a truth tree at size <= 7 or an equivalent that small) / 40`,
not `claim_exact / 40` at the current budget.

## What does not move it

`maxnodes-probe.md` (repository-external, run at `75570aa..4fabe3d`, 12 cells plus
4 at the hard cap) tested the only lever the current code offers. The answer is no.

- **`max_nodes` 7 -> 9 -> 12 changes nothing.** Under the default operator set the
  `attempted` count stays at 20000 for all three bounds and `distinct` is
  byte-identical between them (5889 / 4330 / 4285). The 20000 candidates are spent
  inside `size <= 7`, so the eighth level is never reached. Under the campaign's
  real operator set, `feynman-I.8.14` finishes at 16933 candidates for `max_nodes=7`
  (reproducing the `SEARCH_EXHAUSTED` in `truth-size.json`) but hits the 20000 cap
  at 9, with `distinct` only climbing from 5620 to 7191. `I.6.20a` and
  `II.24.17` already hit the cap at 7, so the bound has no effect on them at all.
- **`max_candidates` at its hard ceiling is not enough either.** `max_nodes=12`
  with `max_candidates=200000` (`MAX_SYNTH_CANDIDATES`) still reports
  `SEARCH_TRUNCATED` with zero fits on all four, in 26-43 seconds — 12-13x the
  baseline, linear in the cap. The enumeration of `size <= 12` is larger than the
  largest budget the code accepts.

So neither knob moves the ceiling, and the "1.0x cost" that `max_nodes` appears to
show is an artifact of never reaching the level where the cost would appear.

## Where this line ends

Saving these four equations needs a smarter enumerator — derived intermediates, or
equivalent rewrites that let a twelve-node truth be searched as an eight-node one.
That is a rewrite, not a parameter change, and the earlier `decompose` wiring
attempt measured 19 DERIVED cells with zero hits before it was rejected.

**The line is closed unless the enumerator is rewritten.** Of the forty truths,
thirty are at `size <= 7` and `feynman-I.27.6` is reachable through an equivalent
that small, so the honest ceiling for this devset on this enumerator is 31/40 —
not 40/40 at any experiment budget.
