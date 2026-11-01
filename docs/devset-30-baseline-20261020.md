# devset 30-equation full baseline (2026-10-20)

Code under test: branch `release/6.0.0-compass`. The run was started at `0169600` with a clean tree,
and no source edits were made by this run (before and after, `git status` showed only this run's own
untracked outputs). However, the working tree was NOT frozen during the sweep — concurrent commits made
by another agent landed on the branch while it ran:

| commit | time | touched |
|---|---|---|
| `93681bf` chore: ignore the local code-review-graph index | 22:30:44 | `.gitignore` |
| `fbb4a20` fix(jump): let the jump routes ignore the token budget dimension | 22:35:50 | `scripts/rds_jump.py` (mtime 22:31:11) |
| `287943e` docs(agents): point the working contract at the code knowledge graph | 22:35:55 | `AGENTS.md` |
| `bfd583f` fix(budget): stop billing agent tokens twice in the snapshot | 22:37:59 | `scripts/rds_project.py` (mtime 22:36:35) |

Neither `benchmark/newtonbench/devset_run.py` nor `benchmark/newtonbench/devset/` changed in that
window (21:44:53 mtime, `git diff 0169600 HEAD` on them is empty). The import surface the sweep walks
is `rds_compass_declaration` / `rds_jump` / `rds_project`: `rds_jump.py` calls `load_plan` inside each
per-cell subprocess, so the cells started before 22:31:11 ran against the old `rds_jump`, cells started
after ran against the new one — the edit only relaxes `require(set(run['resource_estimates'])==
set(state['budget']))` to exclude `TOKEN_RESOURCE`, a jump-route dimension check.
Caveat, stated plainly: **the 90 cells were not all produced by the identical tree.** Post-`bfd583f`
HEAD is therefore *not* certified by this file; a rerun on a frozen checkout is the clean control.
The run's own result fields are unaffected by the budget-arithmetic fix in `rds_project.py`: no cell
raised or changed status in a way attributable to it (the `SEARCH_TRUNCATED` / budget-width errors are
declaration-side `ValueError`s, listed in §5a).

## 1. Reproduce

```bash
cd "D:/Myfile/work space/RDS/rds-357"
python -B benchmark/newtonbench/devset_run.py benchmark/newtonbench/results/devset-run-30.json
```

The first positional argument is the output path; the script has no `--help` (passing it would be
treated as a directory name). Wall clock for this run: ~3h50m (`elapsed_seconds` 13819.4). Output is a new file; `benchmark/newtonbench/results/devset-run.json` is untouched (sha256 `d75d51c8da5daafa2b75f3af5cd283aaa7bb49e49270ec44f4187bfe21511e45` before and after).

## 2. Per-equation x budget

`claim_exact_rule`: status == GOAL_IDENTIFIED and 0 counterexamples on full devset replay

| equation | B=8 | B=16 | B=32 |
|---|---|---|---|
| feynman-I.10.7 | SEARCH_EXHAUSTED<br>ex=false<br>exp=8<br>814.6s | SEARCH_EXHAUSTED<br>ex=false<br>exp=9<br>1006.7s | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: budget.experiments must be an integer from 0 to 18` |
| feynman-I.12.1 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>7.3s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>8.6s | GOAL_IDENTIFIED<br>ex=true<br>exp=25<br>14.6s |
| feynman-I.12.5 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.1s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>8.5s | GOAL_IDENTIFIED<br>ex=true<br>exp=20<br>11.1s |
| feynman-I.14.3 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>5.1s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>10.7s | GOAL_IDENTIFIED<br>ex=true<br>exp=27<br>20.0s |
| feynman-I.14.4 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>6.1s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>12.7s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>12.7s |
| feynman-I.18.12 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>32.8s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>70.0s | GOAL_IDENTIFIED<br>ex=true<br>exp=32<br>147.3s |
| feynman-I.18.4 | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: Declare 1..64 candidates` | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: Declare 1..64 candidates` | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: Declare 1..64 candidates` |
| feynman-I.25.13 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.2s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>8.7s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>8.7s |
| feynman-I.26.2 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>14.3s | GOAL_IDENTIFIED<br>ex=true<br>exp=9<br>16.3s | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: budget.experiments must be an integer from 0 to 18` |
| feynman-I.27.6 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>23.1s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>63.1s | GOAL_IDENTIFIED<br>ex=true<br>exp=27<br>120.1s |
| feynman-I.39.10 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>8.3s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>17.6s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>17.6s |
| feynman-I.40.1 | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: Declare 1..64 candidates` | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: Declare 1..64 candidates` | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: Declare 1..64 candidates` |
| feynman-I.41.16 | ERROR (no status)<br>ex=false<br>exp=—<br>4.1s<br>`KeyError: 'op'` | ERROR (no status)<br>ex=false<br>exp=—<br>4.6s<br>`KeyError: 'op'` | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: budget.experiments must be an integer from 0 to 18` |
| feynman-I.47.23 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>23.2s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>49.6s | GOAL_IDENTIFIED<br>ex=true<br>exp=27<br>87.4s |
| feynman-I.6.20a | SEARCH_EXHAUSTED<br>ex=false<br>exp=8<br>120.5s | SEARCH_EXHAUSTED<br>ex=false<br>exp=14<br>203.7s | SEARCH_EXHAUSTED<br>ex=false<br>exp=14<br>203.1s |
| feynman-I.8.14 | SEARCH_EXHAUSTED<br>ex=false<br>exp=8<br>1277.9s | SEARCH_EXHAUSTED<br>ex=false<br>exp=14<br>2139.5s | SEARCH_EXHAUSTED<br>ex=false<br>exp=14<br>2141.7s |
| feynman-I.9.18 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>14.5s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>31.2s | GOAL_IDENTIFIED<br>ex=true<br>exp=32<br>65.8s |
| feynman-II.10.9 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>24.7s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>53.1s | GOAL_IDENTIFIED<br>ex=true<br>exp=18<br>60.0s |
| feynman-II.2.42 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>87.4s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>213.4s | GOAL_IDENTIFIED<br>ex=true<br>exp=32<br>467.8s |
| feynman-II.24.17 | SEARCH_EXHAUSTED<br>ex=false<br>exp=8<br>857.2s | SEARCH_EXHAUSTED<br>ex=false<br>exp=9<br>1073.6s | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: budget.experiments must be an integer from 0 to 18` |
| feynman-II.27.18 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.3s | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>4.2s | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: budget.experiments must be an integer from 0 to 16` |
| feynman-II.34.11 | ERROR (no status)<br>ex=false<br>exp=—<br>292.6s<br>`ValueError: Undeclared variable: B_times_g_factor` | ERROR (no status)<br>ex=false<br>exp=—<br>291.4s<br>`ValueError: Undeclared variable: B_times_g_factor` | ERROR (no status)<br>ex=false<br>exp=—<br>291.9s<br>`ValueError: Undeclared variable: B_times_g_factor` |
| feynman-II.34.2 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>11.2s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>23.9s | GOAL_IDENTIFIED<br>ex=true<br>exp=27<br>42.9s |
| feynman-II.37.1 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>9.3s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>19.9s | GOAL_IDENTIFIED<br>ex=true<br>exp=27<br>35.8s |
| feynman-II.38.14 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>8.0s | GOAL_IDENTIFIED<br>ex=true<br>exp=9<br>9.1s | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: budget.experiments must be an integer from 0 to 18` |
| feynman-II.38.3 | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: Declare 1..64 candidates` | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: Declare 1..64 candidates` | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: Declare 1..64 candidates` |
| feynman-III.10.19 | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: Declare 1..64 candidates` | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: Declare 1..64 candidates` | ERROR (no status)<br>ex=false<br>exp=—<br>0.0s<br>`ValueError: Declare 1..64 candidates` |
| srsd-hard-feynman-I.14.3 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>6.3s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>13.1s | GOAL_IDENTIFIED<br>ex=true<br>exp=27<br>24.7s |
| srsd-hard-feynman-I.9.18 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>29.4s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>62.7s | GOAL_IDENTIFIED<br>ex=true<br>exp=32<br>131.8s |
| srsd-hard-feynman-II.2.42 | GOAL_IDENTIFIED<br>ex=true<br>exp=8<br>92.2s | GOAL_IDENTIFIED<br>ex=true<br>exp=16<br>224.9s | GOAL_IDENTIFIED<br>ex=true<br>exp=32<br>491.1s |

Column legend: `status` / `claim_exact` / `experiments` / `seconds`.

## 3. Aggregate

- cells: **90** (30 equations x 3 budgets = 90)
- `claim_exact`: **57/90**
- `false_certification.rate`: **0.0**
- `false_certification_count`: **0**
- `claimed_count`: **57**
- `checkable_claimed_count`: **57**
- `false_certification_status`: `ZERO_FALSE_CERTIFICATIONS`
- `elapsed_seconds`: **13819.4** (3.84 h)
- `campaign_root`: `C:\Users\XU\AppData\Local\Temp\devset-run-gjl8o68e`

- status histogram: `GOAL_IDENTIFIED` 57, `<ERROR: no status>` 23, `SEARCH_EXHAUSTED` 10

## 4. The 5 equations shared with the old baseline

Old file `benchmark/newtonbench/results/devset-run.json`: 15 cells, `claim_exact` 12/15, `elapsed_seconds` 802.5.

| equation | B | old status | new status | old claim_exact | new claim_exact | old exp | new exp | old s | new s | agrees |
|---|---|---|---|---|---|---|---|---|---|---|
| feynman-I.12.1 | 8 | GOAL_IDENTIFIED | GOAL_IDENTIFIED | true | true | 4 | 8 | 4.9 | 7.3 | same |
| feynman-I.12.1 | 16 | GOAL_IDENTIFIED | GOAL_IDENTIFIED | true | true | 4 | 16 | 4.6 | 8.6 | same |
| feynman-I.12.1 | 32 | GOAL_IDENTIFIED | GOAL_IDENTIFIED | true | true | 4 | 25 | 4.1 | 14.6 | same |
| feynman-I.14.3 | 8 | GOAL_IDENTIFIED | GOAL_IDENTIFIED | true | true | 4 | 8 | 5.4 | 5.1 | same |
| feynman-I.14.3 | 16 | GOAL_IDENTIFIED | GOAL_IDENTIFIED | true | true | 4 | 16 | 5.3 | 10.7 | same |
| feynman-I.14.3 | 32 | GOAL_IDENTIFIED | GOAL_IDENTIFIED | true | true | 4 | 27 | 6.0 | 20.0 | same |
| feynman-I.6.20a | 8 | SEARCH_EXHAUSTED | SEARCH_EXHAUSTED | false | false | 8 | 8 | 141.6 | 120.5 | same |
| feynman-I.6.20a | 16 | SEARCH_EXHAUSTED | SEARCH_EXHAUSTED | false | false | 14 | 14 | 229.7 | 203.7 | same |
| feynman-I.6.20a | 32 | SEARCH_EXHAUSTED | SEARCH_EXHAUSTED | false | false | 14 | 14 | 219.5 | 203.1 | same |
| feynman-I.9.18 | 8 | GOAL_IDENTIFIED | GOAL_IDENTIFIED | true | true | 4 | 8 | 11.7 | 14.5 | same |
| feynman-I.9.18 | 16 | GOAL_IDENTIFIED | GOAL_IDENTIFIED | true | true | 4 | 16 | 11.2 | 31.2 | same |
| feynman-I.9.18 | 32 | GOAL_IDENTIFIED | GOAL_IDENTIFIED | true | true | 4 | 32 | 11.4 | 65.8 | same |
| feynman-II.2.42 | 8 | GOAL_IDENTIFIED | GOAL_IDENTIFIED | true | true | 4 | 8 | 52.3 | 87.4 | same |
| feynman-II.2.42 | 16 | GOAL_IDENTIFIED | GOAL_IDENTIFIED | true | true | 4 | 16 | 50.8 | 213.4 | same |
| feynman-II.2.42 | 32 | GOAL_IDENTIFIED | GOAL_IDENTIFIED | true | true | 4 | 32 | 44.1 | 467.8 | same |

Shared subset (5 equations x 3 budgets): old `claim_exact` 12/15, new `claim_exact` 12/15.

`agrees` compares `status` only (claim_exact is read from the same stored status).

## 5. Anomalies (itemised)

All 23 non-`GOAL_IDENTIFIED`, non-`SEARCH_EXHAUSTED` cells are per-cell exceptions caught by
`devset_run.py` (`except Exception` around `run_one`); the row is stored WITHOUT a `status`,
`claimed=False`, `false_certification=None`, so `backfill()` counts it UNKNOWN, never checkable.
The sweep itself completed: process exit code 0, 90 rows written.

### 5a. Cells that raised (23) — grouped by exception message, each cell listed

- **12 cells** — `ValueError: Declare 1..64 candidates`
  - `feynman-I.18.4` B=8 (0.0s)
  - `feynman-I.18.4` B=16 (0.0s)
  - `feynman-I.18.4` B=32 (0.0s)
  - `feynman-I.40.1` B=8 (0.0s)
  - `feynman-I.40.1` B=16 (0.0s)
  - `feynman-I.40.1` B=32 (0.0s)
  - `feynman-II.38.3` B=8 (0.0s)
  - `feynman-II.38.3` B=16 (0.0s)
  - `feynman-II.38.3` B=32 (0.0s)
  - `feynman-III.10.19` B=8 (0.0s)
  - `feynman-III.10.19` B=16 (0.0s)
  - `feynman-III.10.19` B=32 (0.0s)

- **5 cells** — `ValueError: budget.experiments must be an integer from 0 to 18`
  - `feynman-I.10.7` B=32 (0.0s)
  - `feynman-I.26.2` B=32 (0.0s)
  - `feynman-I.41.16` B=32 (0.0s)
  - `feynman-II.24.17` B=32 (0.0s)
  - `feynman-II.38.14` B=32 (0.0s)

- **3 cells** — `ValueError: Undeclared variable: B_times_g_factor`
  - `feynman-II.34.11` B=8 (292.6s)
  - `feynman-II.34.11` B=16 (291.4s)
  - `feynman-II.34.11` B=32 (291.9s)

- **2 cells** — `KeyError: 'op'`
  - `feynman-I.41.16` B=8 (4.1s)
  - `feynman-I.41.16` B=16 (4.6s)

- **1 cells** — `ValueError: budget.experiments must be an integer from 0 to 16`
  - `feynman-II.27.18` B=32 (0.0s)

### 5b. `SEARCH_EXHAUSTED` cells (10)

- `feynman-I.10.7` B=8: experiments=8, seconds=814.6, law=`—`
- `feynman-I.10.7` B=16: experiments=9, seconds=1006.7, law=`—`
- `feynman-I.6.20a` B=8: experiments=8, seconds=120.5, law=`—`
- `feynman-I.6.20a` B=16: experiments=14, seconds=203.7, law=`—`
- `feynman-I.6.20a` B=32: experiments=14, seconds=203.1, law=`—`
- `feynman-I.8.14` B=8: experiments=8, seconds=1277.9, law=`—`
- `feynman-I.8.14` B=16: experiments=14, seconds=2139.5, law=`—`
- `feynman-I.8.14` B=32: experiments=14, seconds=2141.7, law=`—`
- `feynman-II.24.17` B=8: experiments=8, seconds=857.2, law=`—`
- `feynman-II.24.17` B=16: experiments=9, seconds=1073.6, law=`—`

### 5c. False certifications

- count: 0

### 5d. Timeouts

None. `devset_run.py` sets no per-cell timeout; nothing was killed. The runner was started once
and left to completion (no short `timeout`, no piped `tail` in the invocation).

### 5e. Runtime deviations vs the 4-14 s/cell expectation

| equation | B | status | seconds |
|---|---|---|---|
| feynman-I.8.14 | 32 | SEARCH_EXHAUSTED | 2141.7 |
| feynman-I.8.14 | 16 | SEARCH_EXHAUSTED | 2139.5 |
| feynman-I.8.14 | 8 | SEARCH_EXHAUSTED | 1277.9 |
| feynman-II.24.17 | 16 | SEARCH_EXHAUSTED | 1073.6 |
| feynman-I.10.7 | 16 | SEARCH_EXHAUSTED | 1006.7 |
| feynman-II.24.17 | 8 | SEARCH_EXHAUSTED | 857.2 |
| feynman-I.10.7 | 8 | SEARCH_EXHAUSTED | 814.6 |
| srsd-hard-feynman-II.2.42 | 32 | GOAL_IDENTIFIED | 491.1 |
| feynman-II.2.42 | 32 | GOAL_IDENTIFIED | 467.8 |

These 9 cells alone account for 10761.2 s of 13819.4 s total (77.9%), which is why the sweep ran
~3h50m instead of the estimated 1.5-2h. `SEARCH_EXHAUSTED` is not the only slow class:
`feynman-II.34.11` B=8/16/32 each burn ~292 s before raising `Undeclared variable: B_times_g_factor`
(three failed cells, ~876 s total), and `srsd-hard-feynman-II.2.42` B=32 took 491.1 s while succeeding.

Slowest-cell accounting, cell classes:
- `SEARCH_EXHAUSTED` (10 cells): 9859.5 s
- raised after work (3 cells, `feynman-II.34.11`): 876.0 s
- slow but `GOAL_IDENTIFIED` (>300 s): the 2 cells above
- the remaining 75 cells: the balance, mostly <150 s each.
