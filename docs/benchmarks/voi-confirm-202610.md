# VoI-Bounded Confirmation: a negative result

Status: **rolled back, not merged.** Date 2026-10-20. Base HEAD `f0425c8`.

## What was tried

A cap on refutation spending. `decide()` returns `CONFIRM` while
`spending = spend and (not cap or bought < cap)`; the attempt added
`and bought < max(confirmations, len(candidates) // 8)` so that a
surviving hypothesis stops buying spread points once its marginal
information approaches zero.

Diff (since reverted, preserved here for the record):

```python
-        spending = spend and (not cap or bought < cap)
+        max_spending = max(plan['verification']['confirmations'],
+                           len(plan['candidates']) // 8)
+        spending = spend and (not cap or bought < cap) and bought < max_spending
```

## Result on the toy world: a real win

`regime-double`, law `x+1 if x < 10 else 2*x`, `max_nodes=5`,
`max_candidates=4000`. Full replay, 32/32 points.

| B | experiments before | experiments after | CONFIRM before | CONFIRM after | claim_exact |
|---|---|---|---|---|---|
| 12 | 12 | 12 | 4 | 4 | 32/32 |
| 16 | 16 | 16 | 4 | 4 | 32/32 |
| 24 | 24 | 22 | 8 | 4 | 32/32 |
| 32 | 32 | 22 | 14 | 4 | 32/32 |

B=32 falls from 32 to 22 experiments with the claim unchanged. All four
budgets stay `GOAL_IDENTIFIED` with 0 counterexamples. The self-Pareto
domination disappears: B=32 is no longer dominated by B=12.

## Result on the devset: a larger loss

Five Feynman equations at B=8/16/32, same protocol as `devset_run.py`.

| equation | base experiments | after experiments | delta |
|---|---|---|---|
| feynman-I.12.1 | 4 / 4 / 4 | 5 / 5 / 5 | +1 |
| feynman-I.14.3 | 4 / 4 / 4 | 6 / 6 / 6 | +2 |
| feynman-I.9.18 | 4 / 4 / 4 | 6 / 6 / 6 | +2 |
| feynman-II.2.42 | 4 / 4 / 4 | 7 / 7 / 7 | +3 |
| feynman-I.6.20a | 8 / 14 / 14 | 8 / 14 / 14 | 0 |
| **total** | **84** | **108** | **+24 (+28.6%)** |

`claim_exact` stays at 12/15 either way. The four equations that
previously locked in 4 experiments now spend 5 to 7, and the claim is
identical. `feynman-I.6.20a` stays `SEARCH_EXHAUSTED` throughout, which
is the correct honest failure.

## Why it loses

The cap helps exactly where the toy world lives: one surviving
hypothesis, one input axis, candidates that stop separating. On
multi-variable physical laws the same cap fires early, the confirmation
budget that would have covered the remaining input combinations is
never spent, and the drive compensates by buying PROBE and HOLDOUT
points instead. Net effect on the devset is +28.6% cost for zero gain.

**The toy world's win and the devset's loss are not the same measurement
of the same thing.** A stopping rule has to be judged on both, and if
either side gets more expensive the change is not adopted.

## What the protocol got wrong

`benchmark/newtonbench/saturation-protocol.md` set the devset threshold
at "every budget <= 6 experiments", derived from "4 experiments to lock
plus 2 holdout". That derivation was wrong: the devset baseline spends
0 holdout experiments, so the real baseline is 4, not 6. The threshold
was set 50% too loose and it let a 3-experiment regression through.

Corrected rule for the next attempt: **thresholds are read off the
measured baseline, never reconstructed from a narrative of what the
run was expected to do.**

## Evidence

Result files are kept, unreferenced by any code path:
`benchmark/newtonbench/results/voi-baseline-b{12,16,24,32}.json`,
`benchmark/newtonbench/results/voi-confirm-b{12,16,24,32}.json`,
`benchmark/newtonbench/results/devset-voi-confirm.json`,
`pareto-after-832a11f-b{12,16,24,32}.json`.

The cheapest verification anyone can redo: revert the patch, rerun the
four budgets, and confirm B=32 returns to 32.
