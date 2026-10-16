# Pre-registration: RDS v6 Compass on NewtonBench

Written and committed before any formal run. Results are not known to the author when this file is committed.
The pilot (below) runs only on held-out tasks that are not in the formal sample, and only checks that the
pipeline works end to end. After this file is committed, tasks, arms, budgets, Compass settings and prompts do
not change in response to results. Any deviation is listed, with its reason, in the report.

## Question

Does RDS v6 Compass, used as the experiment-selection and verification loop around a frontier model, discover
the laws of a third-party benchmark more accurately than that benchmark's own baseline agents driven by the
same model with the same query budget?

The benchmark is NewtonBench (Zheng et al., ICLR 2026; <https://github.com/HKUST-KnowComp/NewtonBench>, MIT),
pinned at commit `912a4ba5f4356ddd06acc16e44460ca30be4abc2`. It is cloned outside this repository and imported
from there; none of its source is committed here.

## Software and model

- RDS: the commit that contains this file, on branch `bench/newtonbench-compass` (based on
  `release/6.0.0-compass` at `fbff27f`). The result file records the commit of each run.
- Python 3.12.0 in a dedicated venv holding only numpy 2.5.4, scipy 1.18.1 and pandas 3.0.6 (the NewtonBench
  dependencies its code imports on the evaluated path; `openai`, `requests`, `python-dotenv` and
  `fix-busted-json` are only used by its network layer, which is replaced, and the rest are not imported).
- Every model call, in every arm and in the judge: Codex CLI 0.162.0-alpha.17.2, model `gpt-6.1-sol`,
  `model_reasoning_effort="xhigh"`, `service_tier="default"` (no Fast), `--ignore-user-config --ephemeral`,
  web search disabled, sandbox `read-only`, fresh empty working directory per call.

## Task sample

- System: `vanilla_equation` (the simulator returns the law's value at the requested inputs). Noise level 0.
- Cells: 12 modules x 3 law difficulties (easy, medium, hard) = 36 cells. One law version per cell, drawn with
  `random.Random(20261015).choice(['v0','v1','v2'])`, cells in module order then difficulty order
  (`common.formal_tasks()`).
- The 36 formal tasks:

| Module | easy | medium | hard |
|---|---|---|---|
| m0_gravity | v0 | v0 | v1 |
| m1_coulomb_force | v1 | v0 | v0 |
| m2_magnetic_force | v0 | v2 | v0 |
| m3_fourier_law | v2 | v1 | v1 |
| m4_snell_law | v2 | v2 | v2 |
| m5_radioactive_decay | v2 | v2 | v2 |
| m6_underdamped_harmonic | v1 | v0 | v1 |
| m7_malus_law | v2 | v0 | v0 |
| m8_sound_speed | v2 | v0 | v0 |
| m9_hooke_law | v2 | v1 | v0 |
| m10_be_distribution | v1 | v2 | v0 |
| m11_heat_transfer | v1 | v2 | v1 |

- Scope limit, stated in advance: the `simple_system` and `complex_system` variants return trajectories, and a
  Compass rulebook predicts a scalar observable as a closed-form function of declared inputs. Those 216 tasks
  are out of scope; a Compass result on them would need an adapter that reduces trajectories to a scalar, which
  would itself be a modelling choice. Noisy variants are also out of scope.

### Pilot (held-out, not counted)

`m0_gravity/easy/v1`, `m1_coulomb_force/medium/v1`, `m7_malus_law/easy/v0` (versions the formal draw did not
pick), one trial per arm. The pilot checks only that every arm reaches NewtonBench's evaluator, that receipts,
transcripts and audits are written, and how long a trial takes. Its scores are not reported as results.

## Arms

All arms share the model settings above and a budget of **40 simulator queries per trial** (one query = one
parameter set evaluated by NewtonBench's `run_experiment_for_module`).

1. `vanilla` - NewtonBench's `utils.vanilla_agent.conduct_exploration`, unmodified (10 rounds, then a forced
   final submission). The model has no tools: Codex runs with shell, unified exec, code mode, apps, plugins,
   browser, image and multi-agent features disabled, so a call is a pure chat completion over the rendered
   message list. (A probe before this file was written confirmed that with these flags the model cannot
   execute code.)
2. `code_assisted` - NewtonBench's `utils.code_assisted_agent.conduct_code_assisted_exploration`, unmodified:
   the same pure-chat model, plus NewtonBench's own `<python>` executor (one call per turn, 10 turns).
3. `compass` - RDS v6 `compass drive`. The NewtonBench simulator is the Compass experiment program: each query
   is one kernel run with an original receipt (`nb_experiment.py`). Compass declares 64 design points and buys
   at most 40 of them, chosen by its own decisions (probe, EIG over live rival partitions, confirmation spread).
   The same model is the jump agent (`nb_jump.py`): when Compass is stuck it receives the sealed packet plus the
   NewtonBench task text the baselines see, may run Python in a read-only sandbox, and returns dispositions
   and up to 8 rulebook proposals that pass Compass's own record filter. **Submission:** the rulebook Compass
   verified when the drive ends `GOAL_IDENTIFIED`; otherwise its highest-prior live rulebook if one exists;
   otherwise no law (NewtonBench's default `return float('nan')` submission). The rulebook is translated to a
   Python `discovered_law` mechanically (`trial.rulebook_to_python`). **Certification** means the drive ended
   `GOAL_IDENTIFIED`.
4. `compass_fallback` (derived from the same `compass` runs; no extra Compass run) - identical to `compass`,
   except that when Compass ends with no live rulebook, one more call of the same model (read-only shell)
   receives the NewtonBench task text and every measurement Compass bought, and writes the `<final_law>`.
   It is never counted as a Compass certification.

Not run: the optional ratchet ablation (same model, no Compass). Cost, decided before any result.

### Compass settings (all tasks)

- Inputs: the module's `discovered_law` parameters. Design: 64 points; every level is the square of a rational
  so that the dialect's exact `sqrt` is defined on every declared input. 3 inputs: full 4^3 factorial on
  levels {1/16, 1, 16, 256}; 2 inputs: 8^2 factorial on {1/64, ..., 256} (squares of 1/8 ... 16); 1 input: 64
  levels (k/4)^2, k = 1..64; 4 inputs: an OA(64, 4^4) of strength 3 on the 4-level set. Bounded inputs use
  bounded levels: refractive indices {1, 25/16, 9/4, 4}; incidence angle in degrees {4, 16, 36, 64};
  polariser angle in radians (5k/32)^2, k = 1..8.
- Observable `y`, relative tolerance 1e-6, no absolute tolerance. Non-finite simulator values are reported with
  no observable.
- `budget.experiments` 40; `budget.wall_seconds` 9000; `escape.probe_points` 6 (so the first jump sees the
  campaign's minimum point count, inputs + 2); `escape.max_jumps` 8; every other setting at its documented
  default (synthesis bound, RSI on, `verification.confirmations` 2, `spend_budget` true). No library.
- Jump agent timeout 600 s (Compass's maximum); the wrapper stops Codex at 540 s.

### Shared limits

- Chat call timeout 1800 s, up to 3 attempts per call. A trial that raises is restarted from scratch up to 3
  times, as NewtonBench's own `run_trial` does; all attempts are recorded.
- Hard wall budget 4 hours per trial. A trial that exceeds it is TIMEOUT; one that exits without a result is
  CRASHED. Both score as failures and are reported.
- Baselines: the query budget is enforced at the simulator and stated in one sentence appended to the task
  prompt ("At most 40 parameter sets may be evaluated in total, across all rounds. Any parameter set beyond
  this budget returns nan.").

## Repetitions and seeds

- N = 2 independent trials per (task, arm): 216 trials (72 per arm). Codex sampling cannot be seeded; trials
  are independent calls.
- NewtonBench's evaluator draws its 5000 test points with `np.random`; the harness seeds numpy and `random`
  per task (`common.eval_seed`), so every arm and trial of a task is scored on the same points.
- Bootstrap seed 20261015.

## Metrics

Primary (NewtonBench's own evaluator, `modules.<m>.evaluate_law`, unmodified):

- **Symbolic accuracy**: `exact_accuracy` from NewtonBench's LLM-judge prompt
  (`SYMBOLIC_EQUIVALENCE_JUDGE_PROMPT`). NewtonBench's runner uses `gpt41` as judge; here the judge call is
  routed to the same Codex model (no API key is used). The judge sees only the ground-truth formula and the
  submitted function, never the arm.
- **RMSLE** on NewtonBench's test sample.

Secondary:

- **False certification rate**: among `compass` trials that ended `GOAL_IDENTIFIED`, the fraction the
  NewtonBench evaluator scores `exact_accuracy` 0, with a Wilson 95% CI.
- Descriptive: queries used, budget hits, end states, Codex calls, wall time, contamination, failures.

## Scoring rules

- A FAILED, TIMEOUT or CRASHED trial, and a scheduled trial with no result file at all (MISSING), scores
  accuracy 0 and has no RMSLE.
- A contaminated trial scores accuracy 0 and has no RMSLE. Contamination: any sealed token (NewtonBench paths,
  `laws.py`, `LAW_REGISTRY`, `HIDDEN_CONSTANT`, `ground_truth`, the Compass project files, ...) in a Codex
  event stream after masking the call's own working directory; or, for `code_assisted`, executed code that
  names the module system, the law registry, `inspect`, `importlib`, `sys.modules` and similar
  (`trial.CODE_TOKENS`), since NewtonBench executes that code in the same process as the simulator.
- Compass's `scientific_support` stays `UNKNOWN`; a Compass certification is a bound-relative claim and is
  scored, like every other submission, only by NewtonBench's evaluator.

## Analysis

The unit is the task. For each submission and task, accuracy = mean over its N trials.

- **Primary comparison:** `compass` vs `code_assisted` (both arms let the model compute), symbolic accuracy, all
  36 tasks. Exact two-sided sign test over tasks with a nonzero difference; paired bootstrap 95% CI of the mean
  per-task difference (10,000 resamples of tasks).
- **Significant** means p < 0.05 **and** the bootstrap CI excludes 0. The direction is reported either way. A
  significant negative difference is reported as Compass being worse.
- Secondary comparisons, same statistics, reported without a multiplicity correction and labelled exploratory:
  `compass` vs `vanilla`, `compass_fallback` vs `code_assisted`, `compass_fallback` vs `vanilla`, and
  `code_assisted` vs `vanilla` (baseline sanity check).
- RMSLE: per task, the median over trials, with a missing RMSLE counted as +inf; sign test over tasks. The
  NewtonBench-style mean RMSLE (finite values only, modified z-score > 3.5 masked) is reported as descriptive
  only, because it silently drops failed submissions.
- Pre-specified subsets: every comparison is repeated on the expressible and non-expressible subsets below.

### Expressibility classification (a priori)

Compass rulebooks are exact rational arithmetic: `+ - * / %`, integer powers 1..8, `sqrt` only where the result
is an exact rational, `abs/floor/ceil/min/max`, conditionals, decimal or rational constants; no exp, log or
trigonometric functions. A task is **expressible** if its ground-truth law can be written in this dialect on the
declared design to within the 1e-6 tolerance (an irrational constant factor may be a decimal approximation;
half-integer exponents of a single input use `sqrt`, and quarter-integer ones nested `sqrt`, where the design
levels make them exact). The classification was made by reading NewtonBench's ground-truth source before any
run; no arm ever sees it.

Expressible (14): `m0_gravity/easy/v0`, `m0_gravity/medium/v0`, `m1_coulomb_force/easy/v1`,
`m1_coulomb_force/medium/v0`, `m2_magnetic_force/easy/v0`, `m3_fourier_law/easy/v2`,
`m6_underdamped_harmonic/easy/v1`, `m6_underdamped_harmonic/hard/v1`, `m8_sound_speed/easy/v2`,
`m8_sound_speed/medium/v0`, `m9_hooke_law/medium/v1`, `m9_hooke_law/hard/v0`, `m11_heat_transfer/easy/v1`,
`m11_heat_transfer/medium/v2`.

Not expressible (22): the other formal tasks (irrational or non-half-integer exponents such as 1.3, 2.6, 2.7,
3.4, 3.8, 3/7 or e; exp; trigonometric functions; the square root of a compound expression).

Because Compass cannot state 22 of the 36 laws at all, its strict accuracy on that subset is bounded at 0 by
construction. That is a property of the released dialect, and this comparison is designed to measure it, not to
hide it.

## Adapter changes relative to NewtonBench's own runner

No NewtonBench file is modified. The harness:

1. Replaces `utils.call_llm_api.call_llm_api` at import time with a Codex CLI backend (`common.ChatBackend`).
   The message list is rendered as a transcript and the model writes the next assistant message. Codex does
   not expose temperature, so NewtonBench's temperature arguments (0.4 agent, 0.6 judge) have no effect, and no
   reasoning text is returned, so NewtonBench's transcript holds only the final messages.
2. Uses the same model as judge instead of `gpt41`.
3. Passes the agents a proxy of the module whose `run_experiment_for_module` enforces the 40-query budget and
   logs every query in a hash chain, and whose `get_task_prompt` appends the one budget sentence.
4. Seeds numpy and `random` before each evaluation.
5. Runs each trial in its own process under `run.py` instead of NewtonBench's `run_experiments.py` pool.

The Compass arm adds its own experiment program, jump-agent wrapper and rulebook-to-Python translation; these are
in this directory and are part of what is evaluated.

## Amendments

None yet. Any amendment made before the formal runs start (only for a pipeline defect found in the pilot) is
dated and listed here; anything after that is a deviation in the report.
