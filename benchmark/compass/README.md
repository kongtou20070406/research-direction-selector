# Compass multi-trap benchmark

Several policies run the same synthetic worlds through the same kernel contract,
original receipts, grammar and synthesis bound. Only the policy differs. The
scorer holds the ground truth; arms see only the declaration and their own
receipted outputs.

```bash
python -B benchmark/compass/run.py --work <empty dir>
```

The run writes `results/compass-benchmark.json`: one row per task and arm,
with every paid run's receipt SHA-256, and a `bundle_sha256` over the whole
body. The work directory keeps each arm's full ledger for audit.

The real-model arms are opt-in. They call the Codex CLI and spend provider quota:

```bash
python -B benchmark/compass/run.py --work <empty dir> --arms codex_agent compass_codex --trials 3 \
  --out benchmark/compass/results/compass-benchmark-codex.json
```

## Arms

| Arm | Policy |
|---|---|
| `ratchet` | Karpathy-style greedy ratchet: measure a fixed three-input visible check set, keep the simplest proposal that lowers the visible error, and certify at zero. For IMPROVE it keeps the best mean. |
| `bare_agent` | **Simulated** bare frontier agent (no model is called). It buys inputs in declared order, keeps the simplest consistent law and self-certifies after one successful prediction. It has no EIG, no rival classes, no tools and no retraction. |
| `compass` | RDS v6 `compass drive`: EIG over rival partitions, basin escape (probe, jump, synthesis, RSI), confirmation gate, exact and Lean or closed-rational replay, and a router-checked polynomial certificate. Jumps use the unattended policy. |
| `codex_agent` | **Real** bare frontier agent (opt-in): Codex CLI `gpt-6.1-sol`, reasoning `xhigh`, default service tier (no Fast). It works in a fresh temporary directory holding only the public declaration and a `measure.py` bridge. Each measurement is bought through the same kernel receipts. A repeat request returns the recorded value and buys nothing. The agent answers `CLAIM` with a Python law (or an accepted arm) or `NO_CLAIM`. |
| `compass_codex` | `compass drive` with the same Codex model as its jump agent (opt-in, `codex_jump.py`). Codex runs read-only on the sealed jump packet. Its decisions and proposals pass Compass's own validation and record filter unchanged. |

Arm ids inside IMPROVE tasks are neutral (`base`, `variant_1`, `variant_2`), so
a name cannot hint at the answer.

## Traps

| Task | Trap |
|---|---|
| `visible-check-trap` | `x+1` fits the first visible inputs of `x^3+1`. |
| `late-exception` | The regime change at `x >= 7` sits away from the inputs an in-order agent buys first. |
| `capability-gap` | No arithmetic law fits without a new feature (`divisors`). It runs twice with a shared library to measure tool compounding. |
| `mean-masking` | The best mean arm collapses on one sample (IMPROVE, `max_degradation` 1). |

## Metrics

- **verified_discovery**: the arm claims a law, and that law equals the sealed truth on every declared input. For IMPROVE, the accepted arm satisfies the profile rule. A `codex_agent` law is executed with `python -I` in an empty directory and compared exactly.
- **false_certification**: the arm claims, and the claim is wrong. `NO_CLAIM` is never a false certification.
- **repeat_refutation_waste**: rulebooks bought or adopted after an observationally identical one was already refuted. Every deterministic arm shares a counterexample-filtered generator, so this stays 0 for them. The metric is an invariant check, not a margin.
- **tool_compounding**: Compass tools tried before qualification in session 1 compared with session 2, which reuses the library offer (re-qualified, `ORIGIN_VERIFIED`).

Row `status` is each arm's own outcome. For `ratchet`, `bare_agent` and
`codex_agent`, `SUPPORTED` is the arm's **self-certification** (`claim`
`SELF_CERTIFIED_SUPPORTED` or `AGENT_CLAIMED`), not an RDS verdict. Only
`score.verified` comes from the sealed truth. Every result file keeps
`scientific_support: UNKNOWN`.

## Result on this checkout (N=1, deterministic)

| Arm | Verified discoveries | False certifications | Experiments |
|---|---|---|---|
| ratchet | 0 / 4 | 3 | 12 |
| bare_agent (simulated) | 0 / 4 | 3 | 27 |
| compass | 5 / 5 runs | 0 | 29 |

On `capability-gap`, session 1 tried 16 generated tools before `divisors`
qualified through the receipted holdout oracle triple. Session 2 tried 1: the
library offer, re-qualified against its own receipts.

## Real-model result (N=3, `results/compass-benchmark-codex.json`)

Codex CLI `gpt-6.1-sol`, `xhigh`, default tier, three independent trials per
task. Bundle `3421f225193d84015178231ad39bf07859d1c71fdae2be85d51aff0378de6347`.

| Arm | Verified discoveries | False certifications | Contaminated | Experiments |
|---|---|---|---|---|
| codex_agent | 12 / 12 runs | 0 | 0 | 112 |
| compass_codex | 15 / 15 runs | 0 | 0 | 87 |

Experiments per run (trials 1, 2, 3):

| Task | Declared inputs | codex_agent | compass_codex |
|---|---|---|---|
| `visible-check-trap` | 8 | 8, 8, 8 | 5, 5, 5 |
| `late-exception` | 10 | 10, 10, 10 | 9, 9, 9 |
| `capability-gap` session 1 | 20 | 20, 9, 20 | 6, 6, 6 |
| `capability-gap` session 2 | 20 | not run | 6, 6, 6 |
| `mean-masking` (arms) | 3 | 3, 3, 3 | 3, 3, 3 |

What this does and does not show:

- A real frontier agent does not fall into these traps. It found every law,
  made no false certification, and never requested a repeat measurement. In
  11 of 12 runs it bought every declared input before claiming, and one
  rationale reads "This exhausts the declared domain".
- On these toy worlds the arms therefore do not separate on correctness. The
  simulated `bare_agent` row (0 / 4) is a stated policy and must not be read
  as a frontier-model result.
- The measured difference is cost. Compass's first sessions used 23
  experiments per trial; `codex_agent` used 41, 30 and 41 (mean 37.3). The
  margin exists only because exhaustive measurement is affordable here; this
  benchmark has no task where it is not.
- Compass's experiment counts with Codex as the jump agent equal the
  deterministic arm's (5, 9, 6, 6, 3). Codex adopted the synthesized item in
  every one of the 12 jumps. The filter also admitted some of Codex's own
  rival proposals (for example `quadratic_rival`, `cutoff_8`), and none
  changed the outcome.
- Token use (all trials): `codex_agent` 1,274,886 input (1,029,760 cached),
  14,208 output; `compass_codex` jumps 371,690 input (145,920 cached), 11,695
  output. Agent wall time: 575 s over 12 episodes vs 396 s over 12 jump calls.

### Audit

- Every Codex event stream is scanned for sealed material: world, experiment
  and declaration file names, the ledger directory, the benchmark and checkout
  paths. A hit marks the row `CONTAMINATED`, and it never counts as a
  discovery. There were no hits.
- The Codex sandbox (`workspace-write` for the agent, `read-only` for jumps)
  limits writes, not reads. Sealing is enforced by what the working directory
  holds and checked by this audit. It is detected, not prevented.
- The transcripts are in `results/codex-transcripts/<task>/<arm>-t<trial>[-s<session>]/`,
  with `MANIFEST.json`. Each `events.jsonl` maps to the `events_sha256` sealed
  in the result file (`sealed_events_sha256`). Twelve `codex_agent` streams had
  the host temp-directory path and the local account name redacted. Their
  pre-redaction byte hashes are listed. The jump transcripts are byte-identical.

## Limits

- These are toy worlds with exact outputs, not real research problems.
- The `bare_agent` arm is a stated policy, not a model. The real-model arms
  are N=3, with no confidence intervals and one model and provider.
- The real agent's success came largely from exhaustive measurement of small
  declared domains. A benchmark where the domain cannot be exhausted is needed
  before any claim about experiment efficiency at scale.
- Compass's claim is bound-relative: unique within its synthesis bound on the declared experiments, with `scientific_support` always `UNKNOWN`.
