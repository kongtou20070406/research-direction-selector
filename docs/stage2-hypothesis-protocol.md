# Stage 2 pre-registration: a campaign may supply its own hypotheses

Status: **v2**, rewritten 2026-10-21 after an independent review found five of
v1's six acceptance checks could not be executed against these files. v1 is in
`git show 3864dfa` and is kept only as a record of what was wrong. Follows
`docs/compass-general-20261021.md` §6.

The purpose of writing the rule first is that it cannot drift toward whatever
the implementation happens to do. That only works if the rule is runnable, so
the failure mode this revision fixes is the rule itself.

## 0. What v1 got wrong

The review is `docs/review-gem-b-20261021.md`. Each finding was re-run before
the rule below was rewritten; none are taken on trust.

| v1 check | Why it could not run |
| --- | --- |
| 1 byte-identical JSON | `devset_run.py` writes `row['seconds']` and a `mkdtemp()` `campaign_root`; a strict comparison fails on every correct run |
| 2 monkeypatch `synthesize` | `devset_run.py:339-341` catches every exception into `row['error']` and exits 0, so a full collapse still reads as a clean sweep |
| 3 `law` renders to the declared expression | `model.render` normalizes the AST (parentheses, operator spacing, term order); measured match rate on the first ten physics equations is 0/10 |
| 4 provenance in the ledger | `model.compile_hypothesis` has a strict field whitelist with no `provenance` key; passing one raises `ValueError` at `initialize` |
| 5 unparseable hypothesis fails closed | **this one passed** — `model.parse` rejects it during `initialize` |
| 6 false-certification rate is present | a key merely being present is not a check; `summarize([])` returned `NO_CLAIMS` with `rate = null`, which reads as "none found" |

Two more findings shaped the design rather than just the checks:

- `_fits` is called from three places only — `generate_rivals`
  (`scripts/rds_compass.py:915`), and `escape` (`:1190`, `:1196`). Short-circuiting
  at its top would turn a rival into a copy of the surviving hypothesis, and would
  hand the same refuted hypothesis back to the escape path that just killed it.
- The v1 reversion trigger said "the seam is one `if` at the top of `_fits`; if it
  is not, the design is wrong". That trigger was written from a guess about the
  call graph. It is withdrawn.

## 1. What changes

A declaration may carry a `hypotheses` block: a list of hypothesis objects in the
shape `compile_hypothesis` already consumes (`scripts/rds_compass_model.py:517`).

`initialize` already compiles them (`scripts/rds_compass_declaration.py:229-236`)
and already marks each one `origin: 'declared'`. It also already records
`unknown.append('hypotheses')` only when the block is absent — the intent to skip
enumeration is present in the code, with nothing acting on it.

What is missing is that `decide` never asks where a hypothesis came from.
`scripts/rds_compass.py:546` sends every surviving set into `RIVALS` regardless,
so a declared hypothesis is immediately followed by an enumeration round.

## 2. The seam, stated honestly

This stage touches `scripts/rds_compass.py` in **two** places, and that is the
design, not a shortcut:

1. `:546` — the `RIVALS` branch skips the round when every surviving hypothesis is
   declared. A declared set is not missing competitors; it is a set the caller
   asserted. Enumeration still runs the moment a declared hypothesis is refuted,
   because the assertion has then failed and the evidence is the only thing left.
2. `:915` — `generate_rivals` prefers a declared hypothesis that has not been used
   as a rival yet, and only falls back to `_fits` when none remain.

`_fits` itself does not change. Nothing about `decide`'s ordering changes for
campaigns without the block.

## 3. Acceptance, decided before the run

Six checks. All must pass; any failure means revert, not adjust.

| # | Check | How it is measured |
| --- | --- | --- |
| 1 | A campaign without the block is unchanged | `devset_run.py` on `feynman-I.12.1` and `feynman-I.18.4`, before and after; `comparable(a) == comparable(b)` (`benchmark/newtonbench/devset_run.py`, added in `e435229`), which drops only `seconds`, `campaign_root`, `elapsed_seconds` |
| 2 | A campaign with only declared hypotheses never enumerates | a unit test in `tests/test_stage2_hypotheses.py` that patches `rds_compass_model.synthesize` to raise, then runs the campaign through `drive`; the test fails if enumeration happens. This is a test, not a CLI sweep, so it cannot be swallowed by the runner's per-cell handler |
| 3 | The declared hypothesis is the one reported | `report(store)['solution']` and the declared entry render to the same normalized AST. Both sides go through `model.render`; the raw strings are never compared |
| 4 | The declared set is what reached the ledger | every `COMPASS_HYPOTHESIS` row for the campaign carries `origin == 'declared'`, and their ids are exactly the declared ids. `provenance` is **not** added to `compile_hypothesis`'s whitelist in this stage — the origin marker already in the ledger is the field that exists |
| 5 | An unparseable hypothesis fails closed | already true; the check stays as a regression guard. A block naming a variable the campaign did not declare raises at `initialize` |
| 6 | The false-certification harness ran | `false_certification_status` is one of `ZERO_FALSE_CERTIFICATIONS` / `FALSE_CERTIFICATIONS_FOUND`, and `degraded_count == 0`. `NO_RUNS`, `DEGRADED`, and `NO_CLAIMS` all fail this check |

Check 1 is the reverse test: it is what proves the new path is a detour and not a
detour plus a rewrite. It must run on equations that reach `GOAL_IDENTIFIED`
today, not only on ones that fail.

Check 3 previously asked for `experiments` to drop. That was withdrawn: how few
experiments a declared hypothesis needs is a property of the hypothesis, not of
the seam, so it cannot be a release condition.

## 4. The reversion trigger

Any of the following means stop rather than work around:

- Check 1 differs on any equation in a field `comparable()` keeps.
- A campaign with the block still ends a `RIVALS` round with all-declared
  survivors — the `:546` branch did not take effect.
- A campaign with the block reports a claim while a declared hypothesis has
  `tested == 0`. That is `CONSISTENT`-as-validated arriving early.
- Making the block work requires a third file under `scripts/`. The two places in
  §2 are the seam; a third means the origin marker is not carrying the
  information and the design needs another look.

## 5. What this stage deliberately does not do

- No plug-in registry. One block, one branch.
- No agent integration. A caller can produce the JSON; nothing calls an agent.
- No dynamic candidates. The declaration still freezes the candidate grid.
- No change to `replay`, the tolerance contract, or `_fits`.
