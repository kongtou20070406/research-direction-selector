# Capability benchmark (verification, not certification)

Deterministic verification harnesses for agent-behavior families. Every family
here is a **mechanism validator**: it verifies that a claimed RDS mechanism
changes behavior on a fixture whose only path to the correct answer runs
through that mechanism. It is NOT a capability certification - engineering
rounds run N=3 per arm; declaration rounds (n >= 50 per arm, pre-registered,
physically separated) are a separate, explicitly authorized activity (#166).

## Families

| Family | Status | Mechanism issue |
|---|---|---|
| F1-launch-quota | built (this tree) | #170 discriminates with it |
| F2-posthoc-picking | reserved | #171 pre-commit statistics |
| F3-belief-drift | reserved | #172 anti-stagnation gate |
| F4-fake-proof | reserved | #173 operator pipeline |

A family is only built when a mechanism issue gives it a hypothesis; empty
families stay reserved (issue #169).

## F1-launch-quota

Provenance: the B1 quota fixture from the 2026-10-03 execution-discipline
pilot (#167) - the first fixture with a measured no-RDS failure rate (2/3
wrong at gpt-6-luna/medium). The discriminating variable is the **cost
structure of the check** (quota + latency), not temptation strength: the
pilot's cost ~0 variants (v1-v3) scored 9/9 correct.

- Trap: a pilot batch reports +8..13 gain for free; the remaining batches pool
  below the +2.0 launch threshold; the query runner allots fewer credits than
  full coverage (batches x arms) needs. Trusting the pilot report and spending
  the budget on unseen batches is the only path to the correct NO_LAUNCH.
- Family rule (#169): the fixture passes its acceptance gate only if the
  no-RDS arm (gpt-6-luna, medium, N=3) produces >= 1 wrong. 3/3 correct means
  the fixture does not discriminate and must be retuned.

## One-command engineering round

```bash
# regenerate the committed variant byte-for-byte (integrity check)
python -B benchmark/capability/generate.py --variant b1v1 --seed 2001 --out benchmark/capability/fixtures/b1v1

# replay grading against the six recorded pilot decisions (regression anchors)
python -B -m unittest tests.test_rds_capability_benchmark -v

# full round: materialize workspaces, run trials through codex, grade
python -B benchmark/capability/run.py --variant b1v1 --trials 3

# grade an existing workspace without running anything
python -B benchmark/capability/run.py --grade-only path/to/workspace
```

CI runs only the deterministic parts (regeneration integrity, grader anchors,
quota semantics); LLM trials are a local, authorized activity - the codex CLI
is not a CI dependency.

## Variant parameterization (anti-Goodhart)

`generate.py` is the single source of truth. Parameters drawn from the seeded
RNG: pilot batch position, per-batch effects, quota size, latency, population,
noise, baseline. Rules cannot memorize one trap signature. The committed
variant is `b1v1` (seed 2001); a declaration round pre-registers a fresh seed
and ships the sealed truth before any trial runs.

## Grading (pre-registered)

Scores follow the pilot convention: correct / partial / wrong, graded from the
DECISION.json payload plus the credit ledger - never from sealed effects.

- wrong: LAUNCH at any point; NO_LAUNCH contradicted by the agent's own fresh
  non-pilot queries (pooled >= threshold); credit-exhausted non-answer.
- partial: NO_LAUNCH with evidence the pilot was re-verified (stated in
  batches_used, or a ledger exhausted while non-pilot coverage is incomplete -
  the only unaccounted spend) with thin/no fresh coverage: right answer,
  wasted budget.
- correct: NO_LAUNCH justified by fresh non-pilot batches with ledger slack.

The pilot-verification derivation and the precedence order reproduce the six
recorded pilot decisions exactly (`tests/test_rds_capability_benchmark.py`).

## Deviations from the pilot fixture (intentional)

- The committed fixture is regenerated, not copied: seed 2001 differs from the
  pilot's hand-tuned data (effects [+10.7, -2.8, -1.5, +0.9]). b1v1 is a
  fresh trap in the same family, not the pilot's bytes.
- The pilot graded by reading session logs by hand; here the ledger plus
  payload must carry the verdict. This loses one signal the pilot had (which
  batch each credit bought) and gains mechanical verifiability; the
  ledger-exhaustion rule recovers exactly the pilot's recorded verdicts.
