# VoI-Bounded Confirmation — Pre-Registered Protocol

Date: 2026-10-20. HEAD: `5c3666d` (release/6.0.0-compass).

## 0. Three lines

- **What this improves**: eliminate self-Pareto-domination on the CONFIRM side — when only one hypothesis survives, spending confirmations buy spread points that separate no rivals, and their marginal information approaches zero.
- **Falsifiable signal**: regime-double B=24/32 experiments exceed B=12 by more than the threshold; CONFIRM count grows linearly with budget while PROBE saturates at 15.
- **Never allowed**: false certification rate rises, claim_exact drops, SEARCH_EXHAUSTED becomes GOAL_IDENTIFIED on `I.6.20a`.

## 1. Diagnosis (evidence, not speculation)

Probe data from `probe_cell.py` on `5c3666d`:

| B | experiments | CONFIRM | PROBE | HOLDOUT | claim_exact |
|---|---|---|---|---|---|
| 12 | 12 | 4 | 7 | 0 | 32/32 |
| 16 | 16 | 4 | 11 | 0 | 32/32 |
| 24 | 24 | 8 | 15 | 0 | 32/32 |
| 32 | 32 | 14 | 15 | 2 | 32/32 |

- PROBE saturates at 15 (boundary gaps ≤ `max_gap=2` exhausted).
- CONFIRM grows linearly: 4→4→8→14. The increment from 16→32 (+10) is entirely `SPEND_BUDGET_ON_REFUTATION` — spread points that separate no rivals.
- `_confirm_candidate` (`rds_compass.py:372-387`): when only 1 hypothesis is live, `rank_experiments` returns nothing (no rival to separate), so every CONFIRM falls through to `_spread(state, 1)[0]` — the farthest unmeasured point, which gets closer as coverage grows.
- B=12 achieves GOAL_IDENTIFIED 32/32 with CONFIRM=4; B=32 buys CONFIRM=14 for the same result.

## 2. Proposed change

**File**: `scripts/rds_compass.py`, the `spending` condition at line ~520.

**Change**: cap `SPEND_BUDGET_ON_REFUTATION` confirmations at `max(required, len(plan['candidates']) // 8)`.

```python
# Before
spending = spend and (not cap or bought < cap)

# After
max_spending = max(plan['verification']['confirmations'],
                   len(plan['candidates']) // 8)
spending = spend and (not cap or bought < cap) and bought < max_spending
```

**Why this threshold**: 32 candidates → `max(2, 4) = 4` total CONFIRM (2 required + 2 spending). Matches B=12 behavior where CONFIRM=4 was sufficient for GOAL_IDENTIFIED 32/32. The 1/8 ratio is a first guess; the protocol validates it on devset.

**Why not VoI on distance**: `_boundary_probe` only handles single-input laws. A distance-based VoI check would need multi-input support that doesn't exist yet. The cap is simpler and testable today.

**Why not `len(rows) > 1`**: hard-sparse-exception needs coverage confirmations even with 1 live hypothesis. The cap preserves required confirmations and allows some spending, just not unlimited spending.

## 3. Pre-registered criteria

### 3.1 Primary: regime-double pareto

Command: `python -B benchmark/newtonbench/probe_cell.py regime-double <B> results/voi-confirm-b<B>.json` for B ∈ {12, 16, 24, 32}.

| Criterion | Threshold |
|---|---|
| B=32 experiments − B=12 experiments | ≤ 8 |
| B=32 CONFIRM | ≤ 6 |
| claim_exact all four budgets | 32/32 (no drop) |
| status all four budgets | GOAL_IDENTIFIED |

### 3.2 Regression: devset 15 runs

Command: `python -B benchmark/newtonbench/devset_run.py benchmark/newtonbench/results/devset-voi-confirm.json`

| Criterion | Threshold |
|---|---|
| 4/5 equations claim_exact at B=8/16/32 | all pass |
| experiments per equation per budget | ≤ 6 |
| total claim_exact | ≥ 12/15 |
| `feynman-I.6.20a` | SEARCH_EXHAUSTED (not GOAL_IDENTIFIED) |

### 3.3 Focused tests

Command: `python -B -m unittest discover -s tests -p 'test_rds_compass_drive.py'` — all green, 0 skip.

### 3.4 Minimal reverse test

Revert the cap to `spending = spend and (not cap or bought < cap)`. Re-run regime-double B=32. CONFIRM should return to 14, experiments to 32.

## 4. Risks

- **hard-sparse-exception**: the cap may reduce coverage enough to miss a sparse exception. Mitigated by: required confirmations (2) still fire; spread points still bought up to the cap; hard_followup tests cover this scenario.
- **Multi-input laws**: `_confirm_candidate` falls to `_spread` for multi-input too. The cap applies uniformly — no special-casing needed.
- **Threshold sensitivity**: 1/8 is a guess. If devset shows claim_exact drops, raise to 1/4 and re-run. If experiments don't drop enough, lower to 1/16.
