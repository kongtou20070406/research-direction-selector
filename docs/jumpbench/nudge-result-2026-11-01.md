# State-triggered reminders on jumpbench: result

Protocol: `nudge-protocol-2026-10-31.md`. Campaign finished 2026-11-01 01:08. `scientific_support` stays UNKNOWN.

**Decision: validation is not run.** Three of the five preregistered criteria failed.

| Criterion | Required | Measured | Pass |
|---|---|---|---|
| `state` − `bare` du_auc | ≥ 0.10 | +0.040 (25 pairs) | no |
| `state` − `bare` s_final | ≥ 0 | −0.135 (25 pairs) | no |
| `state` − `sham` du_auc | ≥ 0.05 | +0.013 (31 pairs) | no |
| Reminder fired and reached the session | ≥ 27 of 54 | 33 of 54 (fired in 37) | yes |
| `state` non-OK minus `bare` non-OK | ≤ 3 | 16 − 17 = −1 | yes |

## Added at analysis time, not preregistered

95% intervals from a bootstrap over world and seed, recomputed from the raw trial files by a separate script.

| Comparison | du_auc | s_final |
|---|---|---|
| `state` − `bare` | +0.040 [−0.137, +0.186] | −0.135 [−0.297, −0.014] |
| `state` − `sham` | +0.013 [−0.177, +0.185] | −0.048 [−0.201, +0.079] |
| `sham` − `bare` | +0.025 [−0.061, +0.103] | +0.013 [−0.227, +0.276] |

The s_final interval for `state` − `bare` excludes zero, but it is one of several unadjusted comparisons, and mean
differences between `bare` replicates on the same metric were about +0.30 to +0.35 (6 to 13 pairs). It is not
treated as evidence of harm. Nor does the run show the absence of an effect: the intervals allow a du_auc gain above 0.10.

Reminders were delivered and agents appeared to respond: 73 of 82 `state` reminder texts and 62 of 69 `sham` texts
appear in sessions. After 53 `unvaried_input` reminders, the named input took a new value within the next three
observations 41 times. There is no baseline for that rate, so it does not show the reminder caused the change.
The protocol's manual sample of next actions was replaced by this computed count for one rule.

## Validity

- All 54 sessions in each arm scanned CLEAN. One `sham` trial (rep 1, m2, seed 2) was `CONTAMINATED` by `cli_tampered`.
- `.bench-locked.json` verifies; the manifest was unchanged across the run.
- All 11 `omp_failed` `bare` trials are in rep 1: upstream Codex 502 after 10 retries. OK trials in rep 1 were
  `bare` 7, `state` 8, `sham` 15, and no `state`−`bare` pair survives there. Every `state`−`bare` pair comes from
  reps 0 and 2. No trial was rerun, as preregistered.
- 26 trials of the 162 had a timeout or no submission. OK counts were `bare` 37, `state` 38, `sham` 43.
- Rule mix: `unvaried_input` 53, `unbound_hypothesis` 23, `flat_output` 5, `repeat_structure` 1. So the two rules
  with counterparts in `rds_nudge` contributed 24 of 82 reminders, and `repeat_structure` almost none.

## Scope

This covers these 4 rules, delivered through `jb.py`, on this benchmark. It does not test `expect_mismatch`, the
ledger, the map or hook transport, and it does not settle whether reminders help real research. The reminder
feature cannot be described as improving research results.
