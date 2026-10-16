# Compass Advisor and goal-bound RSI (6.0.0)

Compass turns one declaration into an unattended, auditable campaign:

```text
compass init  ->  Advisor decision  ->  experiment | probe | jump | RSI  ->  exact / Lean / certificate verification  ->  report
```

It adds no second ledger, scheduler or database. Every Compass fact is a
`COMPASS_*` row in the project `events` table. Each row binds a CAS record by
SHA-256 and the digest of the previous Compass row, forming a hash chain. Each
row also carries its own digest (`row_sha256`) and a consecutive `seq`, so a
tampered tail row is detected even though no successor row binds it.
Hypothesis status, tool status and the next decision are recomputed from those
rows plus the kernel's original run receipts on every read. A stored status is
never trusted.

## Status labels

| Layer | Values | Meaning |
|---|---|---|
| Measurement | `INPUT_REPORTED` | The experiment program wrote this output under an original kernel receipt. It is not a verdict. |
| Replay | `CONSISTENT`, `REFUTED`, `UNTESTED` | Cell-exact replay of a rulebook against receipted measurements, with stated tolerances. |
| Scientific support | always `UNKNOWN` | Compass never self-certifies `SUPPORT`. |
| Goal claim | `CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS`, `CONSISTENT_BUT_UNCONFIRMED`, `NOT_ESTABLISHED` | Bound-relative. Uniqueness is stated only as `UNIQUE_CLASS_WITHIN_SYNTHESIS_BOUND_ON_DECLARED_EXPERIMENTS`. |

A law becomes the goal claim only after the configured number of novel
post-adoption predictions (`verification.confirmations`, default 2). A
measurement is novel for a rulebook only if its run started after the rulebook's
ledger row was written (by write time), and its receipt settled after that row
(by ledger event order). Ingestion order does not count: a receipt bought before
adoption and ingested later is old evidence. Only a receipt that reports the goal observable confirms; a receipt with only side observables does not.

Two novel predictions cannot rule out a sparse exception. A law that is wrong at one unmeasured input passes them as easily as the true law. So by default (`verification.spend_budget: true`) Compass keeps buying confirmation points while the experiment budget lasts, and every one of them is a chance to refute the leader. The verification reports `coverage`: measured inputs, declared inputs, and that unmeasured inputs are `PREDICTED_NOT_MEASURED`. A declaration that prefers economy sets `spend_budget: false` and stops after the minimum confirmations, accepting that risk.

A goal claim is `GOAL_IDENTIFIED` only when all of the following hold:
- replay passes;
- one live equivalence class remains;
- every receipt binding re-checks;
- the polynomial certificate (below) names no disagreeing rival.

## Modules

| Module | Layer | Responsibility |
|---|---|---|
| `rds_compass_model` | SHARED | Exact `Fraction` AST dialect, description-length prior `2^-size`, EIG over rival outcome partitions, observational equivalence, bottom-up synthesis with counterexample filtering, per-sample profiles. |
| `rds_compass_store` | L1 | Hash-chained `COMPASS_*` records in the existing ledger and CAS, written idempotently under `BEGIN IMMEDIATE`. |
| `rds_compass_declaration` | L1 | Declaration compiler for the kernel contract, frozen protocol and plan. Every undeclared field stays `UNKNOWN`, and controller defaults are listed in `defaulted`. |
| `rds_compass` | L3 | Advisor: receipt ingestion, basin detection, decisions, abductive jumps, verification, compact working set, TMS mirror, report. |
| `rds_compass_rsi` | L3 | Goal-bound tool synthesis, oracle-triple qualification, cross-session library, retraction. |
| `rds_compass_drive` | L3 | The unattended loop; SIGTERM and deadline handling. |
| `rds_compass_cli` | L4 | `compass init/drive/status/decide/report/tools`, plus the `project init --declaration` and `project drive --compass` front doors. |
| `rds_interrupt`, `rds_path_identity` | SHARED | Outer interrupt as a settled exit (#215); spelling-independent root identity (#207). |

## Advisor decisions

`decide(state)` is pure. The drive records each decision against the state
head it was computed from, then acts on it.

- `RECOVER`: an original attempt is reserved or running. Compass finishes or recovers it and never starts a second attempt of the same run.
- `EXPERIMENT`: buys the candidate with the highest EIG per cost among adjudicable rivals. `rank_experiments` excludes three kinds of candidate:
  - `ALREADY_OBSERVED` inputs;
  - `NON_DISCRIMINATING` candidates, where every rival predicts the same outcome. Outcomes are grouped with the same two-way agreement as equivalence, so predictions that one measurement could separate are separate outcomes;
  - `EQUIVALENT_PARTITION` candidates, which split the rivals exactly like a better-ranked candidate. Equivalent predictions are never bought twice.

  Equal EIG per cost is broken by coverage: the candidate farthest (per-input range normalized L1) from every observed input wins, then the candidate id. An id tie-break once bought `x10` in a 999-input domain, where a digit-dependent law happens to agree with `x+5`.

  An UNDEFINED prediction is its own outcome class, because replay counts it as a counterexample.
  A FAILED or INTERRUPTED purchase yields no measurement, so its partition was never observed and an equivalent candidate stays eligible.
- `PROBE` / `ESCAPE`: a basin trigger (`NO_RIVALS`, `ALL_REFUTED`, `REFUTED_STREAK`, `RESIDUAL_STAGNATION`) first buys spread probe points when evidence is thin, then escapes.
- `RIVALS`: synthesize consistent rivals at new evidence.
- `CONFIRM`: buy the spread point farthest from the evidence, a prediction the leader did not see. The reason is `MINIMUM_CONFIRMATIONS` or `SPEND_BUDGET_ON_REFUTATION`.
- `VERIFY`, `HANDOFF`.

## Basin escape: abductive jump

1. **Synthesize.** Counterexample-filtered synthesis runs in three modes:
   - `WHOLE_LAW`;
   - `RESIDUAL_CORRECTION` against the best refuted rulebook;
   - `REGIME_SPLIT`, a piecewise law with an explicit applicability guard.

   Every fit reproduces every original measurement. A fit equivalent to a refuted or live rulebook is rejected as `REPEAT_OF_REFUTED` or `DUPLICATE` before it can cost an experiment.
2. **Escalate.** If synthesis finds nothing within its bound, Compass escalates to RSI (below) and synthesizes again over the new feature.
3. **Seal the packet.** Compass seals an `rds-jump-packet-v1` (status `CURRENT`, SHA-256, ≤32 KiB, explicit omissions) and writes it to the declared agent's stdin. With fits, the packet's `request` is `DISPOSITIONS`. With none, and an agent declared, the request is `PROPOSALS` with empty `items`, and the jump is recorded `PROPOSALS_REQUESTED`: the agent is asked exactly when Compass's own bounded search has nothing. Without an agent the jump is `NO_RIVAL_WITHIN_BOUND`. A proposal request that admitted nothing is not repeated until new evidence arrives.
4. **Validate the response.** The agent must address every item exactly once with `adopt`, `adapt`, `reject` or `defer`, plus substantive reasons and next steps. Adapted rulebooks and free proposals pass the same record filter: a fresh id (one no retained or same-response rulebook holds, else `ID_TAKEN`), replay, then equivalence. Two rulebooks are equivalent only if, at every declared experiment, their values agree within tolerance or both share the same non-predicting status. Values agree only if each would survive the other as the measured value, because a relative band scales with the measurement; `NOT_APPLICABLE` and `UNDEFINED` separate them. Any rulebook that reads a qualified tool feature is bound to that tool as a premise, whoever wrote it, so a tool retraction also retracts it. A malformed or failed response is recorded, and the unattended policy continues. The `DISPOSITION` binds the agent's input and both output streams by raw-byte SHA-256. A timeout records `AGENT_TIMEOUT` with the hashes of the partial streams. If the Compass head or the receipt set changed while the agent worked, the response is `STALE_RESPONSE` and nothing from that jump is adopted. No agent means `COMPASS_AUTO` adoption of the already filtered fits.
5. **Stop at the bound.** `escape.max_jumps` bounds the search, after which the drive ends `SEARCH_EXHAUSTED`. That exhaustion speaks only about the declared grammar and bound.

## Goal-bound RSI

Compass treats a gap the grammar cannot close as a missing capability and closes it in-campaign:

1. **Extract.** Library offers come first, then bounded generated families: digit folds, valuations, divisors, sigma, primality, Fibonacci.
2. **Validate.** Each source passes the existing `rds_tools.extract_function` allowlist and runs in an isolated `python -I -B` subprocess with a step budget and a 60-second timeout. This is labeled `SUBPROCESS_ISOLATED_NOT_OS_SANDBOX`. A runner timeout or non-zero exit is recorded as `TOOL_REJECTED` (`TOOL_RUNNER_TIMEOUT` or `TOOL_RUNNER_FAILED`). The record carries the phase, the stream hashes and the wall time. The drive continues instead of aborting, and the same tools are re-tried only after the evidence grows.
3. **Qualify** against an independent oracle triple. Expected values come only from original receipts, never from the tool:
   - **known positive**: a law fitted without the holdout receipts, using the tool, predicts every holdout measurement;
   - **discriminating negative**: a mutant of the tool, substituted into that law, is contradicted by a receipt;
   - **boundary and counterexample witness**: the killing receipt is retained, and the tool must be defined on every declared input. Otherwise it is rejected as `UNDEFINED_ON_DECLARED_DOMAIN`.
4. **Register and use.** Compass writes `.rds/tools/compass/<name>-<sha12>.py` and a `TOOL_QUALIFIED` record (`QUALIFIED_FOR_SCOPE`, `self_certified: false`, `scientific_support: UNKNOWN`). The tool's values become a derived input `<tool>_<input>` that the next Advisor decision and synthesis use.
5. **Persist.** With `library` declared (or the `RDS_COMPASS_LIBRARY` default, recorded in `defaulted`), the tool is exported create-only together with its provenance: origin ledger root, qualifying record SHA-256, sequence number and head. A later session re-reads the origin ledger and labels the offer `ORIGIN_VERIFIED` or `ORIGIN_UNVERIFIED`. Verified offers come first, and every reused tool is re-qualified against the new campaign's own receipts.
6. **Retract.** If a newer receipt contradicts the qualifying law, Compass writes `TOOL_RETRACTED` with that witness. It also:
   - marks every dependent hypothesis `RETRACTED`;
   - turns the TMS node `CONTRADICTED`;
   - leaves a create-only `.retracted.json` marker beside the library record, so later sessions stop offering the tool.

## Verification

`verify` re-derives every measurement from its receipt and re-hashes every artifact that receipt binds: the declared output, `stdout.bin`, `stderr.bin` and the manifest. A changed output is reported as `OUTPUT_HASH_MISMATCH`; any other changed or missing artifact is reported as `ARTIFACT_HASH_MISMATCH` or `ARTIFACT_MISSING`, and a missing receipt row as `RECEIPT_MISSING`. Either way the measurement is stale, never trusted, and the goal claim is withheld. `compass report` re-checks these bindings at report time instead of copying the stored verification. Before any rival, ranking or purchase, the drive re-checks the bindings of every ingested measurement; a stale one sends it straight to `VERIFY`, so changed evidence spends no more budget. When a binding has gone stale since verification, the report's law claim is `NOT_ESTABLISHED`, IMPROVE verdicts are recomputed on the current stale set and no arm is recommended; the stored conclusion stays under `claim_at_verification` or `recommended_at_verification`. Verification then:
- groups live rulebooks into equivalence classes over the declared space. Equivalence checks each pair in both directions, so the order of the rulebooks does not matter. Tolerance equivalence is not transitive, so a class holds only pairwise-equivalent rulebooks;
- replays the simplest member exactly;
- runs a `lean_vector_obligation`. Without a local Lean 4 toolchain this falls back to `rds_python_closed_rational` with `CERTIFICATE_CHECKED`, and the fallback is stated in the result.

For one-input unconditional laws, Compass also builds the unique polynomial of degree `≤ m-2` through `m` receipted points (one spare equation). The independent `rds_math_router.check_certificate` replays that certificate (#194), and the result is compared with the law on every declared input. The two agree at an input only if each value would survive the other as the measurement. If they disagree (`POLYNOMIAL_RIVAL_DISAGREES`), some rival outside the synthesis bound fits every receipt, and the drive returns `JUDGMENT_REQUIRED` instead of `GOAL_IDENTIFIED`.

## IMPROVE goals

An IMPROVE declaration compares arms that report per-sample metrics. Mean gain alone never accepts an arm. An arm is `ACCEPT` only if:
- its mean gain is positive and at least `min_gain`, and
- its worst per-sample degradation is at most `max_degradation`.

An arm may declare independent re-runs: a candidate with `replicates: <arm>` is one more run of that arm, never an arm of its own. With replicates declared, the arm is `ACCEPT` only if the rule holds on every declared run. If a declared run was never bought, the arm is `UNREPLICATED`, never `ACCEPT`. Compass screens every first run, then buys re-runs only for arms whose every bought run passes, best minimum mean gain first. An arm that failed a run is never re-run. If the budget leaves only `UNREPLICATED` arms, the drive ends `JUDGMENT_REQUIRED` and names them.

`min_gain` must be at least 0; a negative floor is rejected at `init`. A mean gain that masks one collapsed sample is `LOCAL_REGRESSION`. Against the metric's tolerance the profile is one-sided: a sample's gain counts only by what it exceeds the tolerance band around its baseline value, and a degradation counts in full. A missing threshold stays `UNKNOWN`. The arm's verdict is `JUDGMENT_REQUIRED`, and the drive ends with `JUDGMENT_REQUIRED`, naming the unknown thresholds. An unmeasured baseline ends the drive the same way, and so does an arm that is unmeasured, including one the budget never reached, or reports other samples than the baseline (`UNMEASURED_OR_SAMPLE_MISMATCH`). An arm whose receipt, or whose baseline's receipt, no longer verifies is `STALE_EVIDENCE`, never `ACCEPT`. Stale evidence ends the drive with `JUDGMENT_REQUIRED`.

## Interrupts, deadlines and resume (#207, #215)

`compass drive` converts SIGTERM and SIGINT into a settled exit, and on Windows SIGBREAK too. Windows cannot deliver SIGTERM to another process: `terminate()` there is `TerminateProcess`, which no handler sees, like SIGKILL. A Windows harness asks for a settled stop by sending `CTRL_BREAK_EVENT` to the drive's process group. The settled exit works like this:
- the kernel's `execute` records the in-flight attempt as an `INTERRUPTED` receipt and re-raises. This includes the window after the attempt enters RUNNING but before the child launches;
- the drive writes an `INTERRUPTED` record naming the in-flight run, the receipts so far and the head;
- the drive ends with an `INTERRUPTED` handoff and exit code 2.

An interrupted attempt is not an experimental outcome. It is neither a measurement nor a failure of the system under study. Its candidate uses the one retry slot declared at compile time (`compass-<id>-a2`). The kernel still never starts a second attempt of the same run.

`--wall-seconds` ends the pass with `CONTROLLER_DEADLINE` and partial evidence. Rerun `compass drive` to resume from the recorded head.

## Declaration

```json
{"schema": "rds-compass-declaration-v1",
 "goal": {"kind": "IDENTIFY_LAW", "statement": "Identify the law mapping x to y", "observable": "y"},
 "inputs": {"x": {"min": -4, "max": 4, "integer": true}},
 "observables": {"y": {"tolerance": {"abs": 0}}},
 "experiment": {"argv": ["{python}", "-B", "experiment.py", "--x", "{x}", "--output", "{output}"],
                "files": {"code": ["experiment.py"], "data": ["task.json"], "evaluator": ["experiment.py"]},
                "timeout_seconds": 20, "wall_seconds": 25},
 "candidates": [{"id": "x0", "inputs": {"x": 0}}, {"id": "x1", "inputs": {"x": 1}}],
 "hypotheses": [{"id": "lin", "predict": {"y": "x+1"}}],
 "budget": {"wall_seconds": 900}}
```

Optional fields: `grammar`, `synthesis`, `escape`, `rsi`, `verification` (`confirmations`, `spend_budget`),
`agent` (`{argv, timeout_seconds}`), `library`, `protocol` (identity fields),
and `profile` (IMPROVE). `higher_is_better` and `inputs.<name>.integer` must be JSON booleans. An IMPROVE profile metric must declare `higher_is_better` and a tolerance: without either, the plan lists it as unknown and every arm is `JUDGMENT_REQUIRED`. An observable without a tolerance is never compared, in replay, in equivalence or in the claim: the verification names it under `unadjudicated` and leaves it out of `predict`. Every timeout and wall budget must be a JSON number, not a boolean. `budget.experiments` must be an integer from 0 to the number of attempt slots, which is twice the number of candidates because each candidate has one retry slot. For IDENTIFY_LAW, every declared, adapted or proposed hypothesis must predict `goal.observable`. A rulebook may have up to 64 AST nodes (`MAX_RULEBOOK_NODES`); synthesis builds each part within `synthesis.max_nodes` (at most 15). Candidate ids that differ only in case are rejected, since their output files would collide on a case-insensitive filesystem, and so are Windows reserved device names (`CON`, `NUL`, `COM1`, …), on every platform. A candidate output or retry path that names a bound experiment file or the protocol file, compared case-insensitively, is rejected at `init`; otherwise the kernel would refuse the purchase only after the ledger exists. An IMPROVE declaration needs at least one treatment arm besides the baseline that is not a replicate. An IMPROVE candidate may declare `replicates: <arm>`. The experiment writes `{"outputs": {...},
"samples"?: {...}}` to `{output}`.

```bash
python -B scripts/rds_cli.py --root <project> project init --declaration compass.json
python -B scripts/rds_cli.py --root <project> project drive --compass
python -B scripts/rds_cli.py --root <project> compass report
```

## Benchmark

[`benchmark/compass`](../benchmark/compass/README.md) runs arms through identical kernel receipts on four traps:
- by default, a greedy ratchet, a simulated bare agent and Compass;
- opt-in, two real-model arms backed by the Codex CLI:
  - a real bare frontier agent;
  - Compass with that model as its jump agent.

It reports verified discovery, false certification, repeat-refutation waste, contamination and tool compounding, with a sealed bundle SHA-256.

In the recorded N=3 run, the real agent matched Compass on correctness, with 12 of 12 runs verified and no false certification. It got there by measuring almost every declared input. That run used the earlier stopping rule, which stopped after two novel predictions and so used fewer experiments. Under `spend_budget`, Compass also spends the budget on these small domains. The toy worlds cannot separate the two on correctness; the README states the numbers and limits.

A pre-registered hard tier then exposed four Compass defects, each now fixed with a regression in `tests/test_rds_compass_hard_followup.py`:
- the id tie-break;
- the stopping rule;
- the jump agent was never asked when synthesis had nothing, so the Compass-with-Codex arm never called the model;
- IMPROVE had no replication.

The release notes give the hard tier's unpatched outcomes.

`tests/test_rds_compass_adversarial.py` holds the reproductions from an independent red-team pass against these invariants. Each one failed before its fix.
