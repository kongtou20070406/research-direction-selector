# State-triggered reminders on jumpbench: rule written before the run

Question: does a reminder fired from computed state help an isolated agent, beyond a reminder at the same moments
that ignores state? `scientific_support` stays UNKNOWN.

No earlier benchmark fits as is. Compass and NewtonBench m2 used RDS through programs, not an agent working in a
loop, and the Compass domains are saturated. jumpbench has the same omp host, a measured bare baseline and an
unused validation split, but no earlier RDS arm. The earlier plugins were not RDS.

## Benchmark changes made first

These change the benchmark, so old noise numbers do not carry over and bare is measured again.
1. `expr.py` allows asin, acos and atan.
2. A non-finite observation is logged and returned as text, not lost after being charged.
3. A refused submit is logged as `submit_rejected` and does not crash scoring.
4. A malformed observation is not charged.

## Design

- Host gpt-5.6-luna, hard/complex_system, 6 dev domains, seeds 0, 1, 2, timeout 900 s, parallel 3, under the omp
  guard with one session per trial. 3 replicates, 162 trials. No trial is rerun, whatever its status.
- Each replicate runs bare, then state, then sham, because sham depends on state.
- All arms get the same prompt. Reminders appear as a `note` field in the `jb.py` response, so this tests content
  and timing, not hook transport. Transport was tested once on Claude Code and is not tested here.
- `state`: fires from the oracle's own events, at most one per event, each key once, at most 4 per trial:
  - `repeat_structure`: 3 consecutive accepted hypotheses share a structure once numbers are removed.
  - `unbound_hypothesis`: 2 consecutive different hypotheses with no observation between them.
  - `unvaried_input`: at least 4 observations and one input has never changed. The question names it.
  - `flat_output`: the last 3 observations returned the same output for different inputs.
- `sham`: fires after the same observation counts as the matching `state` trial, with a question of the same
  form chosen by seed, without reading that trial's state.
- Only `repeat_structure` and `unbound_hypothesis` have counterparts in `rds_nudge`. Automatic expectation checks
  are impossible here because observation inputs differ from the law's variables. So this does not test the
  `expect_mismatch` rule, the ledger or the map.

## Decision

Mean over all OK pairs by cell and replicate. From earlier A/A data the SD of a mean difference at this size is
roughly 0.05, so 0.10 is about 2 SD. That estimate came before the benchmark changes.

Validation is run only if all of these hold:
- `state` minus `bare` du_auc is at least 0.10, and s_final is not lower.
- `state` minus `sham` du_auc is at least 0.05.
- Reminders fired, and their text appears in the session, in at least half of the `state` trials. Smoke trials
  showed a fired reminder can miss the session, so reach is reported separately for `state` and `sham`. Added
  after the smoke run and before the campaign.
- Trials that are not OK in `state` do not exceed `bare` by more than 3.

Also reported: statuses, scan outcomes, reminders per trial by rule, A/A spread among the 3 bare replicates, and
whether an agent's next action followed each reminder, by reading a sample.

If `sham` matches `state`, any gain comes from being interrupted, not from state. If reminders rarely fire, the
run says nothing about the mechanism. A failed result here covers these 4 rules on this benchmark only, and does
not settle whether reminders help real research.

Sealed domains are not touched. Results stay on the worktree until the maintainer asks to commit.
