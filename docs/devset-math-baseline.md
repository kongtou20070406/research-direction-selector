# The mathematics sequence devset: 30/30 exact, and the ceiling is why

Baseline for the ten `math-` equations that commit `bd0d58b` added to
`benchmark/newtonbench/devset.py`. Three budgets (8, 16, 32), ten equations,
thirty cells, all of them produced on one frozen commit. Result file:
`benchmark/newtonbench/results/devset-math.json`.

The headline is 30/30 `claim_exact`. The interesting part is not that number, it
is that the same number falls out of the physical devset the moment you hold the
`max_nodes` ceiling fixed — so this file is really a measurement of where the
structural ceiling sits, with the mathematics batch as the sample that happens to
land entirely underneath it.

## 1. Reproduce

`devset_run.py` accepts **one** equation per invocation, as its second positional
argument (`main(out_path, only=None)` at `benchmark/newtonbench/devset_run.py:328`,
filter at `:331`). There is no subset flag and no budget flag — `BUDGETS = (8, 16, 32)`
is hardcoded at `benchmark/newtonbench/devset_run.py:34`, and a single call runs all
three budgets for that one equation.

So one equation at a time, all three budgets each:

```bash
cd "D:/Myfile/work space/RDS/rds-357"
for eq in math-square math-pronic math-triangular math-reciprocal \
          math-reciprocal-square math-sqrt-index math-hypotenuse \
          math-exp-decay math-sin-index math-square-distractor; do
  python -B benchmark/newtonbench/devset_run.py "out-$eq.json" "$eq"
done
```

Each `out-$eq.json` is a complete document in the same schema as
`results/devset-run-30.json` (three `runs`, one per budget). Ten calls give you the
30 cells. Wall clock: 4.4–5.5 s per cell, 142.6 s for the sweep.

The sweep recorded here did **not** use that loop. It used an external driver that
runs one cell per OS process and kills a cell at 900 s, following the isolation
precedent set by `results/devset-run-30-fixed.json`. The driver lives outside the
repository; `scripts/` was not touched. Every one of the 30 cells was then
re-produced through the plain `devset_run.py` CLI above and returned identical
status, `claim_exact`, `experiments` and law strings — the CLI is the reproducible
path, the driver only added process isolation and per-cell HEAD recording.

`campaign_root` in the result file points at the driver's scratch directory
(`_math_probe/cells`), which is not in the repository; the committed file is the
record, the scratch tree is disposable. This matches the existing baselines, whose
`campaign_root` is a `%TEMP%` path.

## 2. Head

All 30 cells carry the same `head`:
`839d3c83b9e138831d7e72c04bde965844ce52a7`, `cell_heads` is
`{"839d3c8…": 30}`, `head_uniform` is `true`.

A first attempt at this sweep was discarded for exactly the reason the 90-cell
baseline got burned: `cell_heads` came back `{4e41cd8…: 11, 839d3c8…: 19}`
because another agent kept committing to the branch while it ran. The sweep above
was rerun inside a detached worktree pinned to `839d3c8`
(`git worktree add --detach <path> 839d3c8`), so the tree could not move. The
kernel files that determine these cells —
`scripts/rds_compass_model.py`, `scripts/rds_compass_drive.py`,
`scripts/rds_compass_declaration.py`, `scripts/rds_compass_store.py`,
`scripts/rds_compass.py`, `benchmark/newtonbench/devset.py`,
`benchmark/newtonbench/devset_run.py` — are byte-identical between `839d3c8` and
the commits on top of it; `git diff --stat 839d3c8 HEAD -- <those files>` is empty.

## 3. Per-equation × budget

`claim_exact` rule, unchanged from the existing baselines: `status == GOAL_IDENTIFIED`
and zero counterexamples when the reported law replays over **every** devset row.

| equation | B=8 | B=16 | B=32 |
|---|---|---|---|
| `math-square` | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.5s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.5s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.7s |
| `math-pronic` | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.7s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.7s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.4s |
| `math-triangular` | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.8s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.9s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.8s |
| `math-reciprocal` | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.5s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.6s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.4s |
| `math-reciprocal-square` | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.5s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.7s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.6s |
| `math-sqrt-index` | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.4s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.4s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.7s |
| `math-hypotenuse` | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>5.3s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>5.2s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>5.5s |
| `math-exp-decay` | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>5.0s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.8s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.9s |
| `math-sin-index` | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.8s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.9s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.7s |
| `math-square-distractor` | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.9s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.9s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>5.0s |

Per-cell seconds are read straight from the result file. They are wall clock in a
shared-machine sweep, so the ordering inside a row carries no meaning.

The loop returns one process per cell and the driver recorded seconds per cell
summing to 142.7 s against a declared `elapsed_seconds` of 142.6 — a 0.1 s
difference from rounding, not two different measurements.

`false_certification_rate` 0.0 / `ZERO_FALSE_CERTIFICATIONS` over
`checkable_claimed_count` 30, and `certification_check` sampled 32 points per cell
(`seed` 357, uniform in the declared box, declared rows excluded) with
`max_abs_error` `"0"` everywhere. So the laws are not merely inside tolerance on
the eight rows — they are exact on 32 fresh points too, including the four
irrational laws below.

## 4. AST size against the ceiling

Sizes via `model.parse` + `model.size` over each equation's `truth.law`, and they
agree row for row with the already-committed
`benchmark/newtonbench/results/truth-size.json`:

| equation | truth size | reported size | status |
|---|---|---|---|
| `math-sqrt-index` | 2 | 2 | GOAL_IDENTIFIED |
| `math-sin-index` | 2 | 2 | GOAL_IDENTIFIED |
| `math-square` | 3 | 3 | GOAL_IDENTIFIED |
| `math-exp-decay` | 3 | 3 | GOAL_IDENTIFIED |
| `math-square-distractor` | 3 | 3 | GOAL_IDENTIFIED |
| `math-pronic` | 5 | 5 | GOAL_IDENTIFIED |
| `math-reciprocal` | 5 | 5 | GOAL_IDENTIFIED |
| `math-reciprocal-square` | 5 | 5 | GOAL_IDENTIFIED |
| `math-hypotenuse` | **6** | **6** | GOAL_IDENTIFIED |
| `math-triangular` | **7** | **7** | GOAL_IDENTIFIED |

`max_nodes` is 7 (`scripts/rds_compass_declaration.py`, consumed by
`synthesize(..., max_nodes=7, ...)` at `scripts/rds_compass_model.py:792`). Every
one of the ten laws is inside the enumerator's search space, and the two named in
the task sit where predicted: `math-triangular` exactly **on** the ceiling at 7,
`math-hypotenuse` one below at 6. Both solved, and neither needed a bigger budget —
so neither produced `SEARCH_EXHAUSTED` nor `TIMEOUT`. No cell in this batch is
synthesizer-boundary and no cell is budget-boundary; there is nothing here to
attribute to either limit.

Reported sizes equal truth sizes for all ten, but three laws came back as
equivalent rewrites rather than the literal source: `math-triangular` reported
`(n / (2 / (1 + n)))`, `math-pronic` reported `((n - 1) * n)`, and
`math-reciprocal` reported `(1 / (1 + n))`. Each is exact on all eight rows and on
the 32 sampled points.

`math-square-distractor` was solved as `(n ** 2)` with `q` absent from the law,
which is the intended outcome: `q` takes the values 1 and 2 while `y` depends only
on `n`, so `q` is a distractor by construction (`devset.py:639-641` builds the
`n` values 1,1,2,2,5,5,8,8 paired against `q`).

## 5. Experiments actually bought

| budget | declared `budget.experiments` | observations bought | cells |
|---|---|---|---|
| B=8 | 8 | 8 | 10 |
| B=16 | 16 | 8 | 10 |
| B=32 | **16** | 8 | 10 |

Two different clamps are stacked here, and they must not be confused:

1. **Declared budget is clamped to 16.** `campaign()` at
   `benchmark/newtonbench/devset_run.py:113` sets
   `experiments = min(budget, 2 * len(rows))`, and these equations have 8 rows, so
   B=32 declares 16 — the same declared ceiling as B=16. Read straight out of the
   generated `compass.json` for `math-square`: `experiments` = 8 / 16 / 16,
   `candidates` = 8 in all three.
2. **Actual spend is 8 in every cell at every budget.** That is below even the
   declared 8/16/16, because the candidate pool is 8 rows and this drive buys each
   row once. So the three budgets are indistinguishable at the level of
   observations bought: 8, 8, 8.

The consequence the task asked about is real and reproduced: **B=16 and B=32 are
the same experiment.** They declare the same 16 and they spend the same 8. Any
conclusion drawn from "B=32 did better than B=16" on this batch would be reading a
difference that does not exist in the data.

### Where the goal locks

Every cell bought all 8 observations and only then ran `VERIFY`. Step log for
`math-triangular` B=8 (13 steps):
`PROBE ×5` on `pt_0, pt_7, pt_3, pt_5, pt_1`, then `ESCAPE` (jump `j01` adopted),
then `CONFIRM` on `pt_6`, `pt_4`, `pt_2`, then `VERIFY`. So the run does **not**
stop buying when the goal becomes reachable — it drains the declared candidate set
first. `experiments` equals `observation_count` equals 8 in all 30 cells for that
reason, not because 8 is a floor or a ceiling in the loop.

How few points would have been enough is a separate question, and it needs a
different oracle, so it is reported as a supplement and not folded into the table
above. Re-running the same campaign with `decl['candidates']` truncated to its
first `k` entries:

| equation | smallest k that still locks, k=4…8 | reached |
|---|---|---|
| `math-square` | 5 | GOAL_IDENTIFIED, 0 counterexamples on all 8 rows |
| `math-pronic` | 5 | GOAL_IDENTIFIED |
| `math-triangular` | 5 | GOAL_IDENTIFIED |
| `math-reciprocal` | 5 | GOAL_IDENTIFIED |
| `math-reciprocal-square` | 5 | GOAL_IDENTIFIED |
| `math-sqrt-index` | 5 | GOAL_IDENTIFIED |
| `math-hypotenuse` | 5 | GOAL_IDENTIFIED |
| `math-exp-decay` | 5 | GOAL_IDENTIFIED |
| `math-sin-index` | 5 | GOAL_IDENTIFIED |
| `math-square-distractor` | **4** | GOAL_IDENTIFIED |

At `k=4` nine of the ten equations return `JUDGMENT_REQUIRED` instead, with the
same law string in hand and zero counterexamples — the law is found but the
distinguishing evidence is not yet enough to certify it.
`math-square-distractor` locks one observation earlier because its eight rows
contain only four distinct `n` values (1,1,2,2,5,5,8,8); four points pin it, and
its remaining four rows are duplicates under the law.

Read together with §5's spend: the drive buys 8 observations to settle a question
that 5 observations already settle. That is 3 wasted observations per cell, 90 of
them across this sweep. It is not expensive here (4–5 s per cell, and the surplus
is the cheapest part of the run), but it is a fixed 60% overhead on the experiment
count for any 8-row equation, and it will scale differently on a devset whose rows
are costly to evaluate. Recording it as a measurement, not recommending a change:
the surplus-buying behaviour is in the drive loop, not in `devset_run.py`.

## 6. Against the physical baseline

Claim-exact ratio, sliced several ways over the same 90-cell physical baseline
(`results/devset-run-30.json` for the 57/90 first pass, merged with
`results/devset-run-30-fixed.json` for the 67/90 figure — the merge only replaces
cells whose first-pass `status` was `null`, i.e. the `ValueError` declarations that
never ran a measurement):

| domain | slice | claim_exact |
|---|---|---|
| mathematics | all cells | **30 / 30 = 100%** |
| physical | all cells | 67 / 90 = 74.4% |
| physical | completed only (status not null) | 67 / 78 = 85.9% |
| physical | truth law `size ≤ 7` | **60 / 60 = 100%** |
| physical | truth law `size > 7` | 7 / 30 = 23.3% |
| physical | `rows ≤ 9` | 12 / 18 = 66.7% |
| physical | `size ≤ 7` **and** `rows ≤ 9` | **9 / 9 = 100%** |

The two 100% rows are the comparison that matters. Ten of the thirty physical
laws are above `max_nodes = 7` (`truth-size.json`: sizes 9, 9, 11, 12, 12, 12, 13,
14, 16, 24), and every one of the 23 non-exact cells in the physical devset belongs
to that group — 11 `SEARCH_EXHAUSTED`, 12 `null` from declaration-side
`ValueError`s. Not one cell among the 60 whose law fits under the ceiling failed.
Drop those ten equations and the physical devset is 60/60, indistinguishable from
the mathematics batch.

The `size > 7` row is not zero because the ceiling blocks search, it is 23.3%
because a handful of above-ceiling *truths* have equivalent rewrites under the
ceiling: `feynman-I.27.6` (truth 9) was reported as `(d2 / ((d2 / d1) + n))` at
size 7 in all three budgets. And `feynman-I.41.16` (truth 24) checked out as
`0` — its nine rows all evaluate to ~2·10⁻²³, well inside the 1/1000 absolute
tolerance, so the constant zero is a correct answer under the declared tolerance
even though it carries no physics. Both are genuine results of the declared rules;
neither should be read as the synthesizer reaching a 24-node law.

Two caveats on the physical side that do not apply to the mathematics batch, and
that make its 100% slightly softer than it looks:

- **Duplicate rows cost nothing on this batch but would elsewhere.** Mathematics
  equations carry 8 distinct inputs except `math-square-distractor`, whose 8 rows
  are 4 distinct inputs twice. A physical equation with duplicated or nearly
  duplicated rows would spend observations on information it already had.
- **`size ≤ 7` is a property of the sample, not of the domain.** The ten
  mathematics laws were authored with sizes 2, 2, 3, 3, 3, 5, 5, 5, 6, 7. Nothing
  in `n`-indexed sequences forces a law this small; it is a property of which ten
  sequences were chosen. A 12-node trigonometric identity over `n` would have
  landed in the `size > 7` bucket and read exactly like `feynman-III.10.19`.

## 7. What the numbers say about domain difficulty

Within the synthesizer's declared search space, the mathematics batch is 30/30 and
the matched physical slice is 60/60 — the same ratio, so this sweep gives no
evidence that the mathematics domain is easier or harder for this kernel than the
physical one. The difference between 30/30 and 67/90 is fully accounted for by the
ten physical laws whose AST size exceeds `max_nodes = 7`, not by anything specific
to `n`-indexed sequences: no mathematics law reached the ceiling from outside, and
the one law sitting exactly on it (`math-triangular`, size 7) was solved at all
three budgets.

The one place the mathematics batch differs structurally is the observation count.
Its eight rows are all the data there is, so B=8 already buys the whole candidate
pool and the loop runs out of observations before it runs out of budget — which is
why all three budgets are the same experiment here. That is a property of an 8-row
devset, not of mathematics, and it means these ten equations cannot measure what a
budget increase buys. A domain-difficulty claim would need laws whose sizes
straddle 7 on the mathematics side, or a mathematics devset with more than 8 rows.
