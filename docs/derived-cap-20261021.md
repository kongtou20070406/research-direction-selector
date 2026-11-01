# derived-cap: the 20,000 cap truncates before the first fit

Status: measurement note, 2026-10-21. Re-measurement of one uncommitted hunk in the DERIVED
branch of `_fits` (`scripts/rds_compass.py:823`) on `feynman-I.18.4` at B=8. Verdict: **the
change is faster and finds nothing — reject it.**

## 1. What changed

One line, uncommitted, in the DERIVED branch of `_fits` (`scripts/rds_compass.py:823`):

```diff
-                                       max_candidates=min(200000, bound['max_candidates'] * 10),
+                                       max_candidates=min(20000, bound['max_candidates']),
```

`bound` is `plan['synthesis']`, whose default is `{'max_nodes': 7, 'max_candidates': 20000}`
(`scripts/rds_compass_declaration.py:35`; this campaign declares no `synthesis` override, so the
default applies). The effective cap therefore moves 200000 → 20000, a 10x cut.

## 2. Baseline (not re-measured here)

Measured by the main conversation with the same harness, before the change. Quoted from that
run, not re-measured in this one:

| field | value |
| --- | --- |
| equation | `feynman-I.18.4`, B=8 |
| cap | 200000 |
| seconds | 280.8 |
| attempted | 84231 |
| status | `FIT_FOUND` |
| fits | 4 |
| rows | 2 |
| probes | 52 |

## 3. This run

Harness: `D:\Myfile\work space\RDS\time_derived_branch.py`, logic untouched. Only its budget
parameter was set to mirror the hunk — line 80 becomes
`cap = min(20000, state['plan']['synthesis']['max_candidates'])` instead of
`min(200000, state['plan']['synthesis']['max_candidates'] * 10)`; the equation and budget were
already `feynman-I.18.4` / 8.

```
cd "D:\Myfile\work space\RDS"
python -B time_derived_branch.py feynman-I.18.4 8
```

Output, verbatim:

```json
{
 "cap": 20000,
 "seconds": 32.5,
 "status": "SEARCH_TRUNCATED",
 "attempted": 20000,
 "fits": 0,
 "rows": 2,
 "probes": 52,
 "names": ["m1", "m2", "r1", "r2"],
 "texts": []
}
```

Measured revision: `scripts/rds_compass.py` sha256
`1069ef67715e3b5dd9fd8c5ba251838b51b6a955f5aa0715a0e4b0e35cc65bf0`, verified identical before
and after the run. The same configuration run once more earlier in the session returned
`seconds=32.8` with every deterministic field identical (`SEARCH_TRUNCATED`, `attempted=20000`,
`fits=0`, `rows=2`, `probes=52`).

## 4. Verdicts

| # | Rule | Result | Evidence |
| --- | --- | --- | --- |
| 1 | `status` is still `FIT_FOUND` | **FAIL** | `SEARCH_TRUNCATED` |
| 2 | `fits` is at least 4 | **FAIL** | 0 |
| 3 | `attempted` strictly below 84231 | **PASS** | 20000 |

Rule 1 is evaluated as "not truncated": the vocabulary of `model.synthesize` is
`FIT_FOUND` / `SEARCH_TRUNCATED` / `NO_FIT` (`scripts/rds_compass_model.py:1044`), and there is
no `SEARCH_EXHAUSTED` state to compare against.

## 5. Control: same harness, same revision, old cap

To separate "the cap broke it" from "the harness or the working tree is wrong", the identical
script with the pre-change cap (`min(200000, max_candidates * 10)`)
(`D:\Myfile\work space\RDS\time_derived_branch_control.py`, a copy of the harness) was run on
the same revision (`1069ef6…`, checked before and after):

| field | value |
| --- | --- |
| cap | 200000 |
| seconds | 167.3 |
| attempted | 84231 |
| status | `FIT_FOUND` |
| fits | 4 |
| rows | 2 |
| probes | 52 |

`status`, `attempted`, `fits`, `rows` and `probes` reproduce the baseline exactly. The four
texts recovered:

```
(m1_plus_r2 / (2 + m1))
(m1_plus_r2 / (m1 + m1_plus_r1))
(m1_plus_r2 / (m1 + r1_plus_r2))
(m1_plus_r2 / (1 + m2))
```

So the failure is attributable to the cap, not to the harness: all four fits appear after
candidate 20000 and before candidate 84231, and a 20000 cap stops enumeration before the first
of them. The working tree also carries two unrelated hunks that landed mid-session
(`_all_declared` in `decide`); both runs in this table were made on the tree that includes them,
and the earlier single-hunk run of cap 20000 gave the same deterministic fields.

## 6. Reading

Wall clock: 32.5 s at cap 20000 versus 167.3 s at cap 200000 on the same revision (5.1x), and
versus the 280.8 s baseline (8.6x). Coverage: 20000 / 84231 = 23.7% of the candidates the old cap
reached. Cost: zero fits, no answer.

A 10x budget cut that buys 5x on a cell where the baseline finds the law is not a speed/quality
trade; it is a different search that returns nothing. The uncommitted hunk does not improve
`feynman-I.18.4` at B=8.

## 7. Limitation

Single measurement per configuration, no seed repeats. Only the wall clock is noisy: the same
cap=200000 configuration took 280.8 s in the baseline and 167.3 s twice in this session, a 1.68x
spread; the cap=20000 configuration took 32.8 s and 32.5 s. The deterministic fields
(`attempted`, `status`, `fits`, `rows`, `probes`) matched exactly across repeats, so the verdict
does not rest on timing, and the `seconds` column should not be read as a precise number.
Whether a cap between 20000 and 84231 would still recover all four fits was not measured.

## 8. Reproduce

The cap line is the only difference between the two runs:

```
# measured (post-change hunk)
cap = min(20000,  state['plan']['synthesis']['max_candidates'])
# control (pre-change hunk)
cap = min(200000, state['plan']['synthesis']['max_candidates'] * 10)
```

Both at `python -B time_derived_branch.py feynman-I.18.4 8` from `D:\Myfile\work space\RDS`.
