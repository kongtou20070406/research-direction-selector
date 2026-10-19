# Compass research-yield evidence (6.0.0 candidate)

Three committed probes, deterministic N=1, synthetic worlds. They are mechanism
evidence, not an external research benchmark: each world is sealed, every count is
replayable from the committed JSON, and every claim remains `INPUT_REPORTED` with
`scientific_support: UNKNOWN`.

## Acceptance lines

| Line | Probe | Result |
|---|---|---|
| Correct claims | [`yield_probe.py`](../benchmark/compass/yield_probe.py) | `sin`, `cos`, `sqrt`, `exp`, `log` move from `SEARCH_EXHAUSTED` (explicit arithmetic grammar) to `GOAL_IDENTIFIED` (grammar open) at the same experiment count; the arithmetic world and the RSI-tool world are unchanged |
| False certifications | [`yield_holdout.py`](../benchmark/compass/yield_holdout.py) | sparse exception at x=14: economy rule 1 false certification (3 experiments), default `spend_budget` 1 (12 experiments, the input is never bought), `holdout: 2` 0 (8 experiments) |
| Cost per correct claim | [`yield_budget.py`](../benchmark/compass/yield_budget.py) | 64-input domain, budget 20: default 20 experiments, `max_confirmations: 5` 8, `max_confirmations: 10` 13; the same correct claim in every arm |
| Cost visibility | `compass report` | `cost` object: experiments, jumps, reported agent token spend |

Raw rows: [`yield-probe.json`](../benchmark/compass/results/yield-probe.json),
[`yield-holdout-scan.json`](../benchmark/compass/results/yield-holdout-scan.json),
[`yield-budget.json`](../benchmark/compass/results/yield-budget.json),
[`yield-ablation.json`](../benchmark/compass/results/yield-ablation.json).

## Replay

```bash
python -B benchmark/compass/yield_probe.py benchmark/compass/results/yield-probe.json
python -B benchmark/compass/yield_holdout.py benchmark/compass/results/yield-holdout-scan.json
python -B benchmark/compass/yield_budget.py benchmark/compass/results/yield-budget.json
python -B benchmark/compass/yield_ablation.py benchmark/compass/results/yield-ablation.json
```

## Limits

- No external benchmark; the worlds are synthetic and small.
- Deterministic arms: N=1 repeats identically, so there is no trial variance to report.
- The holdout costs experiments and can withhold a claim when the reserved points are
  few; `verification.holdout: 0` is the default.
- A confirmation cap below the minimum confirmations ends in `JUDGMENT_REQUIRED`;
  the declaration owns that trade-off.
- The grammar escalation only fires for an open declaration grammar; an explicit
  operator list keeps precedence.
