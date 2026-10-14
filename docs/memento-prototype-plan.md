# Reflective rulebook prototype and real RDS agent evaluation

Date: 2026-10-14. Owner: this implementation chat. Starting public source:
`445a1379b7fa383b2fdde7a0cbd039376e892a1a`, Draft PR #325.
The existing checkout also has 75 dirty entries; they remain outside this work.

## Latest authorization and running checkpoint

This section supersedes the historical 40/1040-call budgets below. Latest human
authorization: unlimited DeepSeek v4.1 calls within one fixed 24-hour window,
ending **2026-10-15 18:02:08 Asia/Shanghai**. No training or GPU use. Existing
per-request/tool and RDS admission bounds remain intact.

Windows task `RDS-Memento-DeepSeek-24h-20261014` is registered and verified
Running with actual API responses. Frozen development source: `f13a829`.
The RDS implementation under test is the committed #325 lineage at `445a137`,
exported from `98321dc`; inherited dirty source is not included or certified.
Frozen external manifest: `C:/Users/XU/Documents/Codex/2026-10-14/rds-deepseek-real-pair/background-manifest-24h.json`,
SHA-256 `f8563e2c977518cc37ff26e77aee2a5be80f4190b709f39003eeec575298500f`.
Original outputs and old manifests remain intact. No automatic failure restart.

The frozen batch covers 11 original questions from four source groups:
meta-regression, NLS socioeconomic status, NLS incarceration, and World Bank
education/GDP. Alternating arm order and conditional repeats are explicitly
declared; same-source questions/repeats are not independent unseen confirmation.
Non-completing tasks retain their first-pass failures and are excluded from
automatic repetition. Freeze the current experiment version while implementing
the next slice in a separate branch; new source requires a new inspected study.

An hourly development heartbeat is ACTIVE only through the same deadline. It
collects evidence, chooses one small in-scope slice, creates a draft PR and
revises this plan. It does not restart unknown paid work or alter frozen tests.
See [handoff and acceptance](memento-prototype-handoff.md) for exact next work.

## Goal and boundaries

Reserve minimal bus/plugin contracts, develop a separate prototype, and test
RDS through real DeepSeek agent interactions. No training or GPU use.
ProjectStore, CAS, receipts and TMS keep their existing authority. A prototype
has no automatic CLI route, scheduler, ledger, plugin loading or adoption.

[Memento 3 v1](https://arxiv.org/html/2610.11794v1), submitted 2026-10-08,
motivates natural-language rules compiled to a model, counterexample repair,
semantic review and complete recorded-transition replay with a frozen LLM.
Public replay accuracy alone does not establish improved research decisions.
No verified author code release was located in the inspected paper links.

## Small reviewable slices

1. S1: pure receipt/history/revision-bound world-model contract; metadata uses
   the existing predictor plugin role. Files: `scripts/rds_world_model_contract.py`,
   `tests/test_rds_world_model_contract.py`, this plan. No production wiring.
2. S2: `prototypes/memento_rds/` only: rulebook compilation, replay and rejected
   revision preservation. Keep unknown rules explicit. CPU deterministic tests.
3. S3: isolated real agent-loop driver, same DeepSeek model and budgets for
   Bare/RDS; public real data, numerical checks, raw failures and tool receipts.
4. S4: inspect failures, revise plan and one implementation seam at a time;
   each meaningful slice gets its own draft PR, source hash and handoff.
5. Later handoff: research-domain adapters and larger untouched confirmation
   tasks before any adoption proposal. Training is outside this plan.

## Evaluation protocol

DeepSeek tests actual RDS use, not merely generation of prototype rules.
Approved total cap: 40 model calls, 45 minutes across all conditions. Failed
calls count; uncertain paid attempts are not automatically retried. Costs whose
API fields are absent stay UNKNOWN. Bare retains ordinary code/tools/notes.
RDS receives the same task and data plus actual RDS routes and source identity.
Freeze conditions before execution; keep gold/scoring material outside agent
inputs. Use original task scoring where available and separate deterministic
numerical checks. No fabricated benchmark score or scientific-gain claim.

Acceptance is separated into contract PASS, prototype replay, actual RDS tool
use, task correctness, comparative efficacy, and scientific benefit. One small
paired task supplies a feasibility signal, not a general gain estimate.

## Iteration 0 and next action

Baseline: this contract is absent at the frozen source; 0 new obligations pass.
S1 verify: `uv run --with pytest --no-project python -m pytest tests/test_rds_world_model_contract.py -q`.
Guard: `git diff --check`; net additions below 300 per feature slice.
Read/admission/composition work on PR #325 remains unfinished. Inspect exact
live owner and file scope before changing any existing bus implementation.

## Revision after real loop preflight

S1 (#330): 9 contract checks PASS. S2 (#331): 4 isolated compiler/revision checks
PASS; finite numeric dialect only. S3 (#332): 3 analysis/containment/no-rebuy
checks PASS. These are engineering results. Scientific gain stays UNKNOWN.

The first real DeepSeek pair consumed 2 calls, one per arm. Both returned model
responses but failed JSON command parsing, before any tool/RDS execution. The
driver's shorthand non-JSON examples were misleading. Preserve both originals;
correct command examples and CSV return semantics, retain the initial deadline,
and allow at most 38 further calls (19 per arm), without buying the same request.

The public DiscoveryBench source is frozen at
`c31fcf011e070f021a5f5b906896d0821f6880e8`, real/test/archaeology, qid 0.
Only task/query/domain descriptions and three original CSVs are agent-visible;
gold/scoring files are excluded. This first pair covers actual RDS exec only.
Advisor, long research trajectories and cross-task efficacy remain unmeasured.
