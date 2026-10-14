# Reflective rulebook prototype and real RDS agent evaluation

Date: 2026-10-14. Owner: this implementation chat. Starting public source:
`445a1379b7fa383b2fdde7a0cbd039376e892a1a`, Draft PR #325.
The existing checkout also has 75 dirty entries; they remain outside this work.

## Latest authorization and runtime checkpoint

This section supersedes the historical 40/1040-call budgets below. Latest human
authorization: unlimited DeepSeek v4.1 calls within one fixed 24-hour window,
ending **2026-10-15 18:02:08 Asia/Shanghai**. No training or GPU use. Existing
per-request/tool and RDS admission bounds remain intact.

At 20:04 Asia/Shanghai, Windows task `RDS-Memento-DeepSeek-24h-20261014`
is Ready after exit code 0; no experiment runner remains. The frozen batch ended
with ALL_TASKS_REQUIRE_REVIEW after every task was excluded. Source: `f13a829`.
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

### S18: isolated native agent driver

Scope: prototypes/rds_agent_eval/native_driver.py, test_native_driver.py, this
plan and handoff. Reuse S13 terminal handling, exact resolved model identity,
20-call arm cap, max_tokens3000 and HTTP limit120. Both arms use declared finite
commands and native recipe wall budget; require remaining50s before execution.
RDS calls the original next/advance handler and S12 receipt reader; native
timeouts/costs remain ledger-owned. Bare is ordinary subprocess tooling with
the same command/timeout and raw audit output. Transcripts carry no execution
authority. Preserve initial results before decoding, reject truncated requests,
undeclared assets and pre-existing arm roots; never rerun uncertain attempts.
Four focused checks passed with mocked provider and actual native advance;
runtime pins and next selected method are rechecked/returned before new calls.
Both arms explicitly run both methods; this is finite owned-workflow feasibility,
not independent source corroboration. Next freeze/inspect and schedule one pair.
Runtime frozen at external study-s18-frozen, source af5e323; 181 file pins,
same commands, two required methods, 100s arm windows and original deadline.
Inspection evidence: acceptance-s18-freeze-20261015.json. Scientific UNKNOWN.
Parent CI38606130805 retains only the inherited QUICK KeyError; original log kept.

### S17: native owned-study output projection

Scope: prototypes/rds_agent_eval/study_bridge.py, test_study_bridge.py, this plan
and the handoff. Read the initialized native effective contract and match the
preflight freeze; reuse S14 advice and S12 original receipt decoding. Bind each
requested output to its exact frozen native manifest and original artifact
bytes. Verify successful measured outputs with S16 arithmetic; retain failed
receipts as UNKNOWN with every raw artifact and cost. Recheck the native view
after projection. No initialization, advice collection, dispatch, assessment
persistence or second ledger. Structured claims remain separate from free-text
review and scientific support. Four focused FULL checks and two subtests passed:
two original successful receipts project verified structured counts; an actual
failed receipt stays UNKNOWN; bad pins/cross-route paths/source or output changes
reject; a change after numeric reading rejects at the native view recheck.
First fixture failure incorrectly required exit 0 from the intentionally failed
advance; it now explicitly asserts exit 1. Original transcript/log retained.
Actual candidate native recipe is PREPARED_NOT_INITIALIZED under external
holdout-s17-native-preflight-v2, with identical Bare manifests and model caps.
Native preflight first rejected a 7.2s reservation with a 10s timeout; retain the
original source, log and partial directory. V2 reduces timeout to 7.2s without
widening reservation or the declared 100s CPU/wall budgets. Ten recipe assets
and 162 native source files are frozen; gold/verifier remain outside inputs.
Evidence: acceptance-s17-native-bridge-20261015.json and
holdout-s17-native-freeze.json. No candidate ledger, new DeepSeek call or old
dispatch; 988 protected original pins match. The paid driver is not frozen yet.
Next: a separately scoped native agent driver with matched tools/limits and
S13 terminal handling, then inspect its full freeze before scheduling a new study.
Parent CI 38597288014 still fails only the inherited QUICK KeyError at line 681;
original log retained outside the repo. Baseline: cumulative obligations 44.

### S16: shared plant methods and independent arithmetic verification

Scope: prototypes/rds_agent_eval/plant_methods.py, plant_verifier.py,
test_plant_domain.py, this plan and the handoff. Two finite methods retain all
12 period/pathway counts, with strict original TSV parsing and bin precision.
The independent verifier uses a separate positional parser over pinned source
and original measured bytes. It checks exact counts and structured period
claims, rejects precise crossover years/causal scope, and leaves free-text
review and scientific support UNKNOWN. Receipt decoding remains native;
the future bridge must supply receipt-bound output bytes. No provider, new
ledger, production route or native execution is introduced by these modules.
Four focused cases and ten subtests passed; cumulative engineering obligations
rise from 40 to 44, not a whole-suite rerun or scientific score. Two initial
format-rejection subchecks failed on csv.Error, followed by one lazy-header
failure; both original logs are retained. Parser causes now propagate behind
ValueError without weakening assertions or changing caps. Offline execution of
both finite methods over byte-identical frozen input agrees with the independent
parser and private count inventory. Earliest observed main period is 1501-1900;
later bins also have Gardening as the largest count. No exact year follows.
Evidence: external acceptance-s16-offline-domain-20261015.json and
holdout-s16-domain-freeze.json. Source/measurement byte pins are retained;
original manifest, engine, ledgers, batch summary and input pins still match.
This is offline method acceptance with a supplied structured claim, not an
agent answer or native receipt-bound study. Zero new DeepSeek calls/old dispatch.
Next: declare the owned native study bridge using these frozen methods and
private verifier, shared budgets/tools and the S13 completion protocol; bind
original outputs through native receipts before a separately inspected study.
Parent exact-head CI 38590645812 still fails only the inherited QUICK KeyError
at tests/test_rds_quick_coverage_admission.py:681; original failed log retained.

### S15: matched native study preflight

The next paid study must declare an owned project, not relabel the old QUICK
batch. Scope: prototypes/rds_agent_eval/study_preflight.py, test_study_preflight.py,
this plan and the handoff. Reuse native compile_recipe without writes. Freeze
task identity and exact agent assets (task plus data), exclude declared gold and
native evaluator paths by resolved identity, retain the prior task inventory,
and preserve model IDs, matched per-arm cap, S13 terminal protocol and original
deadline. Derive Bare manifests from the same compiled native routes/budget.
No initialization, provider call, dispatch or sidecar state occurs in preflight.
New task IDs do not prove independent sources; scientific status stays UNKNOWN.
Focused software checks: test_study_preflight.py passed four cases, including
real FULL CLI initialization/owned review without dispatch. Original authorization
review rejects a used task before recipe reading and confirms unchanged frozen
sources/results. Evidence: acceptance-s15-original-authorization-20261014.json;
preflight SHA-256 c48fc1ffe9363fef95df3203009327a6910a114843af660045aea8b1e1845716.
Then
prepare actual untouched task data and domain methods/evaluation declarations,
inspect their frozen identities and run a separately hosted matched study.

An untouched input candidate is now frozen outside the repo: DiscoveryBench
introduction_pathways_non-native_plants metadata_0 qid 0 at the same pinned
upstream commit. Agent inputs contain only task.json and the original temporal
table; hypotheses/intermediate/domain_knowledge stay outside that directory.
The csv filename holds tab-separated bytes: 12 rows, four periods, three pathways.
Initial comma-parsing and raw-download failures are retained in the candidate
records; source bytes were not repaired. See holdout-s15-input-freeze.json and
private numeric inspection. No native domain recipe or paid study exists yet.
Next: shared finite analysis methods and a frozen independent task verifier;
preserve coarse period bins rather than inventing an exact crossover year.

### S14: retained native Advisor view for a future real study

Current measurements cover QUICK exec, not owned Advisor/project continuity.
Reserve prototypes/rds_agent_eval/advisor_view.py and test_advisor_view.py, plus
this plan and the handoff. Reuse rds_steering.plan(dialogue=True) and the native
ledger-CAS report reader. Require the native plan snapshot pin; retain missing,
stale and negative diagnostics; expand the full original compatible report and
recheck the native view after expansion. No new collection, selection, ledger,
admission, model call or automatic integration. Full report expansion preserves
candidate/evidence denominators beyond the native display limits.
Focused new-reader verification uses real CLI-created temporary software
fixtures and native CAS/receipts. Four focused checks passed. The first run
retained one failed fixture assertion: the CLI adds workflow display metadata
outside the original CAS report (rds_cli.py:1907). Corrected acceptance compares
the full CAS bytes/hash/decoded report and the CLI envelope separately; no
native assertion or timeout changed. Original failure log remains retained.
Read-only inspection of all 60 original RDS arm roots returns NO_CURRENT_ADVICE,
with unchanged ledger hashes and frozen sources; no old dispatch or paid call.
Evidence: acceptance-s14-original-advisor-views-20261014.json under the external
experiment root; reader SHA-256
7f1873effb1985545ec95eba9fea12f05c2f3850c6a2e53606065c7176fa7b02.
Fixture PASS will not
measure DeepSeek, whole-RDS effects or scientific support. Then declare the
native project policy/worker/domain inputs and untouched study tasks, freeze
S13 terminal protocol equally for both arms, and retain independent verifiers.

### S13: complete terminal response protocol

Original 1,034 responses contain 1,024 tool-call ends, six nonempty text/stop
ends, and four length-truncated ends. The old driver rejected all six text
answers. S13 accepts only nonempty stop-text or complete tool calls, rejects
truncated/unknown/contradictory ends before dispatch, and records completion_mode
TEXT/FUNCTION separately from AGENT_FINISHED. This still says nothing about
answer correctness. New studies freeze finish-function-or-stop-text-v2 for
both arms; no original result is reclassified and no old paid attempt is rerun.
Scope: loop.py, test_analysis_worker.py, this plan and the handoff. Focused
verification: test_analysis_worker.py and test_suite.py passed 13 checks, with
three new terminal-protocol cases. Offline inspection of all 1,034 original
responses preserves six text candidates, rejects four truncated turns and
keeps 1,024 tool batches. No original response or summary changed, no paid call
or dispatch occurred, and all frozen source hashes match. Retained evidence:
acceptance-s13-offline-terminal-20261014.json under the external experiment root.
Parser SHA-256 f1999a06990d7b9c4d005b60244e2983074f6eeabb915d3af1d5a74a91806b04.
Exact-head parent CI 38562837305 still fails only on the inherited QUICK
KeyError at test_rds_quick_coverage_admission.py:681; its original log is retained.
Next: retain a separate offline classification of original terminal responses,
then define native Advisor/receipt-domain inputs and untouched study tasks.
Prototype completion remains distinct from whole-RDS efficacy and science.

### S12: original receipt observations, no new paid study

The final batch retained 60 pairs and 1,071 cumulative requests: 1,034 in this
batch plus 37 earlier pilot/debug requests. Bare has 54 AGENT_FINISHED arms and
six protocol stops; RDS has 53 finished, four protocol stops, two unknown tool
feedback stops and one per-arm CALL_CAP. These are protocol completion counts,
not accuracy scores. Survivors and repeated source groups remain selected.

Original responses distinguish four length-truncated JSON tool arguments from
six ordinary text answers without the required finish call. The latter are
retained protocol stops, not proof of incorrect answers. Two RDS feedback stops
retain CLI rejection stderr and syntactically invalid Python inputs; no usable
receipt exists for those rejected attempts. Preserve every original denominator.

Exact-head CI 38554880844 confirms the two S11 corrections passed; its remaining
error is the inherited QUICK KeyError at test_rds_quick_coverage_admission.py:681.
No all-green CI, kernel acceptance or whole-RDS efficacy claim follows.

Scope: prototypes/memento_rds/observations.py, test_observations.py, README.md,
this plan and the handoff. Reuse caller-supplied ProjectStore._db(readonly=True)
and _receipt; validate supplied pins and every artifact's original bytes, retain
FAILED/unstarted receipts and unknown costs, and never write or execute. Return
observations only: domain before/action/after remains UNKNOWN. Focused checks:
test_observations.py and test_model.py passed: five adapter checks plus four
compiler/revision checks. Read-only acceptance inspected 401 original RDS
receipts (164 SUCCEEDED, 237 FAILED), verified 1,102,381 artifact bytes with no
inspection failures, and confirmed unchanged ledger hashes. The frozen manifest
and every pinned source hash still match; no new API call or execution occurred.
Evidence: external acceptance-s12-original-observations-20261014.json, adapter
SHA-256 c3911cd94fc9fb546d67c835ddb9872eb36a0d8b0163a323738ae1a7d7e99e45.

Revised next step: define receipt-bound domain projection and semantic-review
inputs before any model adoption. Inspect terminal-content handling and declare
a separate study with untouched tasks before new paid comparisons; do not
restart excluded or uncertain attempts. The fixed deadline remains unchanged.

### S11: CI correction selected from original results

Original exact-head run `38547449833` on `e43d004` failed: this slice introduced
an unavailable pytest import and omitted the world-model contract's layer label.
Use the repository's existing unittest discovery, preserving all nine contract
cases; declare the new pure policy contract L3 beside `rds_predictor_contract`.
No old label, assertion, workflow or timeout is changed. Original QUICK failure
at `tests/test_rds_quick_coverage_admission.py:681` remains out of scope.

The 19:06 checkpoint had 43 settled pairs and 785 total requests. Eleven distinct
first-pass questions have mixed completion/cost outcomes; eight questions were
excluded after later non-completion. Repeated survivors cannot establish a
general gain. The original frozen experiment continues unchanged. Next remains
a receipt/history adapter or semantic-review interface after this CI seam passes.

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
S1 verify: `python -B -m unittest discover -s tests -p test_rds_world_model_contract.py -v`.
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
