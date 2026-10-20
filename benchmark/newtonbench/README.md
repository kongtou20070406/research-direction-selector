# NewtonBench adapter

Two small CLIs expose the RDS mechanism to [NewtonBench](https://github.com/HKUST-KnowComp/NewtonBench)
(ICLR 2026) without importing Compass or a ledger:

- [`rds_tool.py`](rds_tool.py): the expression synthesizer. Given measured
  points and a grammar, it returns counterexample-filtered candidate laws.
- [`eig_advisor.py`](eig_advisor.py): the EIG advisor. Given measured points
  and a rival set, it ranks the unmeasured inputs by expected information gain
  per cost.

Both are pure Python and make no model call.

## What the adapter established

- `m0_gravity` easy v1 (`C * mass1 / distance ** 2`) and medium v1
  (`C * mass1 / distance ** 2.6`) are recovered from six measured points.
- `m0_gravity` hard v0 (`C * (mass1 + mass2) ** 2 / distance ** 1.5`) does
  not fit: the synthesizer truncates at 200,000 candidates with a
  three-variable grammar and a hidden constant. That is a bound of the current
  search, not a statement about the task.
- `mass1 ** 1.3` (medium v1's exponent) is outside the dialect: the rational
  exponent must have a denominator at most 8, and `13/10` has denominator 10.

## Limits

- The hidden constant has to be known to the grammar or fitted outside the
  synthesizer; the adapter does not invent it.
- Input domains are the discrete candidate points the caller supplies.
- The adapter is a mechanism probe, not a benchmark score.

## Two-point probe (m2 hard v0)

With two measured points the bare agent (`gpt-6.1-sol`, reasoning max) fits
both exactly but extrapolates the wrong law: RMSLE **0.099** on a 125-point
grid (pass threshold 0.05). The synthesizer does not recover it either: with
the true constant and exponents in the grammar it truncates at 200,000
candidates. Replayable files are in [`results/`](results/):

- `req-m2-2pt.json` — the two points and the grammar;
- `baseline-6.1sol-m2-2pt.json` — the agent's law and its score;
- `m2-2pt-tool.json` — the synthesizer result (`SEARCH_TRUNCATED`).

This is the motivating case for staged synthesis and for an LLM-proposal
plus RDS-verification loop: both need to close the two-point gap.

## Proposal-and-verify probe (m2 hard v0, three points)

One `gpt-6.1-sol` call proposed five laws from the two initial points. The EIG
advisor chose `(current1=1, current2=5, distance=3)` (2.09 bits), the third
measurement filtered the proposals by replay, and exactly one survived:

`4.78e-02 * (current1 + current2) ** (3 / 2) / distance ** 3`

The bare two-point arm failed (RMSLE 0.099); this arm reaches the correct law
at three measured points, with the third point chosen by RDS rather than
supplied by a human. Result: `results/llm-verify-m2.json`.

## Agent-chosen point control

A bare `gpt-6.1-sol` was given the same two points and one self-chosen
measurement. On m2 hard v0 it chose (1,1,1) and recovered the law; on m6 hard
v0 it chose (1,1.5,1) and recovered the law. On the popcount task it wrote the
bit-count loop itself (17/17 exact). The EIG advisor is therefore not yet shown
to beat the agent's own point choice on these tasks; the next distinction has
to come from a larger candidate set or an interactive complex system.

## Selection comparison (EIG vs random)

With the five committed candidates, the two initial points already leave a
single survivor under the relative tolerance, so both arms need no extra
measurement (EIG 2 points; random 30/30 trials at 2 points). The comparison is
therefore not discriminative yet. `compare_selection.py` is committed so the
same test can be rerun once a larger candidate pool exists.

## Discrete sparse-exception probe

Six points of `x + 1`; the truth differs at x=31. The bare `gpt-6.1-sol`
claims `x + 1` and scores 31/32. Compass with `verification.holdout: 2` buys
x=30 and x=31 after the fit, the x=31 measurement refutes the leader, and the
drive ends `SEARCH_EXHAUSTED` with no claim. Result:
`results/holdout-discrete.json`; reproduction: `holdout_discrete.py`.

## Ten-task suite (sparse and regime changes)

Ten 32-input worlds, six initial points, holdout of two, `max_confirmations: 4`.
Three recover the exact law (all exceptions at the domain tail); two certify
`x+1` on a hidden mid-domain exception (31/32 and 30/32); five longer regime
changes end without a claim. The regime-split fix is therefore tail-specific,
not general: mid-domain holdout coverage and the longer splits are the next
repairs. Raw rows: `results/sparse-suite.json`.
