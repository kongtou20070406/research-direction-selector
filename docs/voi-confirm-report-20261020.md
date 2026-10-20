# VoI-Bounded Confirmation: execution report

Date: 2026-10-20. Base specified: HEAD `5c3666d` (release/6.0.0-compass).
Observed HEAD at end of this run: `bff241f`.

**Verdict: NOT ADOPTED — rolled back.** Pre-registered criterion 3.2 fails on the
devset cost rule, and the cap raised devset experiments from 84 to 108 (+28.6%) for
an identical 12/15 `claim_exact`.

The authoritative record of the decision lives in
`docs/benchmarks/voi-confirm-202610.md` (commit `a1c9dd2`). This file records what
*this* execution measured, and what did not match.

## 1. Three lines, restated before work

- **Improves**: stop buying spread-point `CONFIRM`s once only one hypothesis survives.
- **Falsifiable signal**: B=32 minus B=12 experiments; B=32 CONFIRM count.
- **Never allowed**: `claim_exact` drops, or `feynman-I.6.20a` becomes `GOAL_IDENTIFIED`.

## 2. Change applied

`scripts/rds_compass.py:520`, exactly as pre-registered:

```python
# Before
spending = spend and (not cap or bought < cap)
# After
max_spending = max(plan['verification']['confirmations'],
                   len(plan['candidates']) // 8)
spending = spend and (not cap or bought < cap) and bought < max_spending
```

`plan['candidates']` is the candidate list (32 here), so the bound evaluates to
`max(2, 4) = 4` total confirmations.

## 3. Criterion 3.1 — regime-double

Baseline re-measured on unmodified `5c3666d` first, to confirm the protocol's
diagnosis table is reproducible before changing anything.

| B | exp before | exp after | CONFIRM before | CONFIRM after | claim_exact (after) | status (after) |
|---|---|---|---|---|---|---|
| 12 | 12 | 12 | 4 | 4 | 32/32 | GOAL_IDENTIFIED |
| 16 | 16 | 16 | 4 | 4 | 32/32 | GOAL_IDENTIFIED |
| 24 | 24 | 22 | 8 | 4 | 32/32 | GOAL_IDENTIFIED |
| 32 | 32 | 22 | 14 | 4 | 32/32 | GOAL_IDENTIFIED |

| Criterion | Threshold | Measured | Pass |
|---|---|---|---|
| B=32 exp − B=12 exp | ≤ 8 | **10** (22 − 12; was 20) | **FAIL** |
| B=32 CONFIRM | ≤ 6 | 4 | pass |
| claim_exact, all four budgets | 32/32 | 32/32 ×4 | pass |
| status, all four budgets | GOAL_IDENTIFIED | GOAL_IDENTIFIED ×4 | pass |

B=12 and B=16 are byte-identical in behaviour to the baseline (per-action counts
unchanged, 0 cells differing over both action histograms); they already bought
only 4 `CONFIRM`s and never reached the new bound. Only B=24 and B=32 changed.

**The `≤ 8` threshold is itself unrealizable by this change.** The cap removes
10 `CONFIRM`s at B=32 (14 → 4), and the remaining difference after the cap is
PROBE (15 vs 7) + RIVALS + HOLDOUT — a non-CONFIRM floor of 22 vs 12. No
confirmation-side cap can move that. The protocol wrote a threshold that its own
mechanism cannot satisfy; the mechanism works, the number was mis-derived.

## 4. Criterion 3.2 — devset

Cmd: `python -B benchmark/newtonbench/devset_run.py benchmark/newtonbench/results/devset-voi-confirm.json`

| equation | B=8/16/32 experiments | claim_exact | status |
|---|---|---|---|
| feynman-I.12.1 | 5 / 5 / 5 | yes ×3 | GOAL_IDENTIFIED |
| feynman-I.14.3 | 6 / 6 / 6 | yes ×3 | GOAL_IDENTIFIED |
| feynman-I.9.18 | 6 / 6 / 6 | yes ×3 | GOAL_IDENTIFIED |
| feynman-II.2.42 | 7 / 7 / 7 | yes ×3 | GOAL_IDENTIFIED |
| feynman-I.6.20a | 8 / 14 / 14 | no ×3 | **SEARCH_EXHAUSTED** |

Total: 108 experiments, `claim_exact` **12/15**.

| Criterion | Threshold | Measured | Pass |
|---|---|---|---|
| 4/5 equations claim_exact at 3 budgets | all | all 12 | pass |
| experiments per equation per budget | ≤ 6 | 7 > 6 on II.2.42 | **FAIL** |
| total claim_exact | ≥ 12/15 | 12/15 | pass (at the boundary) |
| I.6.20a | SEARCH_EXHAUSTED | SEARCH_EXHAUSTED ×3 | pass |

The `≤ 6` per-equation threshold is what let this through: the true devset
baseline is **4 experiments per locked equation with 0 holdouts**, not 6.
`docs/benchmarks/voi-confirm-202610.md` records the derivation error
("4 to lock plus 2 holdout"), which set the bar 50% too loose.

Against the measured baseline of 4, the regression is +1/+2/+2/+3 on four
equations, total **+24 experiments (+28.6%)** for an unchanged claim.

## 5. Criterion 3.3 — focused tests

`python -B -m unittest discover -s tests -p 'test_rds_compass_drive.py'`
(needs `PYTHONPATH="C:\Users\XU\miniconda3\Lib;<repo>\tests"`; fixture import
fails without it).

With the cap applied: **31 tests, 1 failure, 0 skips.**

```
FAIL: test_later_counterexample_retracts_the_tool_its_dependents_and_its_library_offer
  File "tests/test_rds_compass_drive.py", line 398
  self.assertEqual(result['status'], 'SEARCH_EXHAUSTED')
AssertionError: 'GOAL_IDENTIFIED' != 'SEARCH_EXHAUSTED'
```

This is the failure mode the project treats as a red line: a scenario that must
honestly report no law within bound instead certified a goal. In that scenario
the cap removes `SEARCH_EXHAUSTED`'s route to staying honest.

**Disclosure — this observation is incomplete.** While isolating it I ran
`git stash push -- scripts/rds_compass.py`, which reported
`No local changes to save`. The edit had already been discarded by a concurrent
checkout in this shared workspace, so the "pass" that followed was measured on
unmodified code, not on a stash-reverted intermediate state. I therefore do
**not** have evidence for how that test behaves on unmodified `5c3666d`, and the
single red above is attributed to the capped build.

## 6. Criterion 3.4 — minimal reverse test

Not run as a separate experiment. `scripts/rds_compass.py` was already restored
to the pre-change content by that checkout (line 520 reads
`spending = spend and (not cap or bought < cap)`; `grep max_spending` returns
nothing; `git diff` against HEAD is clean). The reverse behaviour is therefore
already the live state, and it was measured: the baseline column in §3, taken on
unmodified code, shows B=32 at 32 experiments and CONFIRM=14 — the expected return
to old behaviour.

## 7. Workspace integrity disclosure

This run executed in a shared working tree that other agents were modifying.
Observed timeline:

- Start: HEAD `5c3666d`, `scripts/rds_compass.py` clean, several untracked
  scratch files present (`%TEMP%ig.json`, `NUL`, `import-graph-tmp.json`,
  `pareto-after-832a11f-b*.json`).
- Baseline runs: measured on `5c3666d`, unmodified.
- Capped runs: measured with the patch applied.
- Later: those untracked scratch files vanished, and HEAD advanced to
  `bff241f` via `f0425c8` → `a1c9dd2` → `bff241f`. `scripts/rds_compass.py`
  mtime `2026-10-20 20:42:41`, content clean.

Consequence: the reverse test in §6 could not be run as an independent
experiment, because reverting had already happened outside this session. The
baseline column stands in for it and is the same measurement.

Untracked `NUL` and `%TEMP%ig.json` are DSH file-tool lock residue in the repo
root, unrelated to this task and left untouched.

## 8. Verdict

Rolled back. The mechanism does what it was designed to do on the toy world
(CONFIRM 14 → 4 at B=32, claim unchanged, self-Pareto-domination removed), but
it costs +28.6% on the real devset for zero gain and it raised a
`GOAL_IDENTIFIED` where an honest `SEARCH_EXHAUSTED` is required.

Two of the four pre-registered thresholds were mis-derived rather than merely
unmet:

- §3.1 `B32 − B12 ≤ 8` is unreachable through the confirmation side at all.
- §3.2 `≤ 6` per equation was reconstructed from "4 lock + 2 holdout" while the
  measured baseline is 4 with 0 holdouts.

Both were written before the run, and both were wrong going in. The lesson is
recorded in `docs/benchmarks/voi-confirm-202610.md`, not re-derived here.

## 9. Artifacts

Kept, unreferenced by any code path:

- `benchmark/newtonbench/results/voi-baseline-b{12,16,24,32}.json` — unmodified `5c3666d`
- `benchmark/newtonbench/results/voi-confirm-b{12,16,24,32}.json` — with cap
- `benchmark/newtonbench/results/devset-voi-confirm.json` — with cap, 15 runs

Reproduce the baseline column (expected: B=32 at 32 experiments, CONFIRM=14):

```
python -B benchmark/newtonbench/probe_cell.py regime-double 32 /tmp/b32.json
```
