# benchmark/ rules

Applies to everything under `benchmark/`. Root `AGENTS.md` and
`docs/development-contract.md` still apply.

## A benchmark change is an experiment, not a code change

- Write the judgement rule **before** the run and do not edit it afterwards.
  Copy the pattern from `docs/holdout-first-ab/` and from the pre-registration in
  `benchmark/newtonbench/holdout-first-protocol.md`.
- Pre-register: the metric, the comparison arm, the threshold as a relative
  ratio where possible, the denominator scope, and what result would count as
  failure. A rule written after seeing the result is not a rule.
- Keep the runner, the raw outputs and the summariser separate. A summariser
  that can silently drop failed cells is a bug, not a convenience.
- Record the number of runs and the seeds. A single run is an anecdote; say so
  in the output rather than letting the reader assume repetition.

## Reporting

- Results go under `docs/<topic>/` with the date, so the run is reproducible from
  the repository alone.
- Report failures and negative results with the same weight as wins. A cell that
  timed out is `UNKNOWN`, not zero and not excluded.
- Never compare two numbers produced under different budgets, seeds or hardware
  without saying so on the same line as the numbers.

## Dependencies

- `benchmark/` code sits at the analysis layer. It must not import L3/L4 modules
  (decisions, loops, CLI, app). If a benchmark needs the production decision
  path, call it through the same public entry point a user would, and say which.
- No hidden dependency on a local absolute path. Benchmarks must run from a
  fresh clone.
