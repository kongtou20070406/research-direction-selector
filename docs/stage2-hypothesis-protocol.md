# Stage 2 pre-registration: a campaign may supply its own hypotheses

Status: pre-registration, written 2026-10-21 before the code lands. Follows
`docs/compass-general-20261021.md` §6. The purpose of writing it first is that
the acceptance rule cannot drift toward whatever the implementation happens to
do.

## 1. What changes

A declaration may carry a `hypotheses` block: a list of hypothesis objects in
the same shape `compile_hypothesis` already consumes
(`scripts/rds_compass_model.py:517`), each with a `provenance` string naming
where it came from (`human`, `agent`, `tool:<name>`, `decompose`).

When the block is present and non-empty, the campaign compiles those hypotheses
instead of enumerating. When it is absent or empty, nothing about today's
behaviour changes.

## 2. Why this is the first real seam

`docs/compass-general-20261021.md` §3 locates the only domain-bound layer as
the hypothesis source. This block is that layer's first cut: it makes "where a
hypothesis comes from" a declaration field rather than a code path. It is
deliberately the narrowest possible version, because it adds no plug-in
machinery, no registry and no new module.

## 3. Acceptance, decided before the run

Six checks. All must pass; any failure means revert, not adjust.

| # | Check | How it is measured |
| --- | --- | --- |
| 1 | A campaign with no `hypotheses` block is byte-identical | Run `benchmark/newtonbench/devset_run.py` on two equations before and after; compare the two result JSONs' `runs` entries field by field |
| 2 | A campaign with the block never enumerates | Monkeypatch `scripts/rds_compass_model.py:synthesize` to raise; the run must complete without raising |
| 3 | The declared hypothesis is actually used | The result's `law` renders to the declared expression, and `experiments` drops to the number needed to test it |
| 4 | Provenance reaches the ledger | `COMPASS_HYPOTHESIS` rows carry the declared `provenance` string verbatim |
| 5 | An unparseable hypothesis fails closed | A block naming a variable the campaign did not declare raises at ingest, not at replay |
| 6 | The false-certification harness still runs | `false_certification_rate` is present in the result file, not inferred |

Check 1 is the reverse test: it is the check that proves the new path is a
detour and not a detour plus a rewrite. It must be run on equations that
currently reach `GOAL_IDENTIFIED`, not only on ones that fail.

## 4. The reversion trigger

Any of the following, observed during implementation, means stop rather than
work around:

- Check 1 fails on any equation, even by one field, and the difference is not
  explainable by provenance alone.
- Implementing the block requires touching `scripts/rds_compass.py` more than
  once. The seam is one `if` at the top of `_fits`; if it is not, the design is
  wrong.
- A campaign with the block produces a claim while any declared hypothesis was
  never actually tested (`tested == 0`). That is the `CONSISTENT`-as-validated
  failure mode from the design note §7, arriving early.

## 5. What this stage deliberately does not do

- No plug-in registry. One block, one branch.
- No agent integration. An agent can produce the JSON; nothing calls an agent.
- No dynamic candidates. The declaration still freezes the candidate grid.
- No change to `decide`, `replay`, or the tolerance contract.
