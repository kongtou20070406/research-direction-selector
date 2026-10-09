# Five historical RDS decisions

These are retrospective decision replays extracted from the existing
`research-direction-selector-eval` corpus. They do not measure autonomous
research quality or reproduce historical GPU experiments. The historical PSNR
numbers are session reports; the original training artifacts are unavailable here.

## Capability families (verification harnesses)

`benchmark/capability/` holds deterministic verification fixtures for agent
behavior families. Each family is a **mechanism validator, not a capability
certification**: it verifies that a claimed RDS mechanism changes behavior on a
fixture whose only correct path runs through that mechanism. Engineering
rounds run N=3 per arm and are iteration signal; declaration rounds (n>=50 per
arm, pre-registered, physically separated) are a separate authorized activity.
A family is only built when a mechanism issue gives it a hypothesis (#169).
See [capability README](capability/README.md) for the F1-launch-quota family.

## Historical decision replays

| Case | Decision to assess |
|---|---|
| July 8 baseline | A feasible reduced-width benchmark anchor, followed by a fair mixer comparison; no unsupported matched-baseline victory. |
| Aug 2 compiler ablation | Isolate dictionary versus routing while retaining q9/F9/K12 and the budget; a five-image development gain is exploratory. |
| Aug 30 GoPro command | Follow the explicit GoPro pivot, audit the official recipe and old result, then cost a matched comparison. |
| Sep 13 C7 | Distinguish tightening a safety margin from crossing actual row mass 1; use the pre-cutoff executed equation. |
| Sep 14 48h budget | Cancel/defer allocations before spending more, include evaluation overhead, and keep adaptive test reuse exploratory. |

## Prospective response evaluation

Give the proposer only one file from `prompts/`, the skill under evaluation,
and the relevant source-code snapshot. Keep `sealed/` and `rubric.json` away
from the proposer. A judge should first assess the response against the rubric
without seeing later outcomes; reveal outcomes only for retrospective analysis.
The files are separated by convention, not an access-control boundary.

Score each rubric item as 0 (violated/absent), 1 (partial), or 2 (satisfied),
and quote the response evidence. Record model/version, prompt hash, skill commit,
budget and judge. Compare paired cases at the same budget. Do not score matching
the eventual historical choice as inherently correct. These related cases have
been used during skill development and are not a new independent holdout.

The C7 prompt explicitly restores a then-known equation omitted from the earlier
blind packet. This is a source-aware replay, not a claim to reproduce its scores.
The September 14 packet restores the then-known 48h cap and 34.4h queue estimate.
No later 100k result appears in that prompt.

## Automated checks

Run `python -B benchmark/run.py`. This verifies packet integrity and replays
five associated executable guard behaviors in temporary project roots. Numeric
CSV examples are **synthetic scalar surrogates**; they are not historical PSNR,
PyTorch models, or reconstructed training jobs. The budget replay uses 1000 ms
per historical hour to exercise the same allocation arithmetic within the
reference runner's 60-second limit. Passing these tests does not establish that
an LLM will recommend a good scientific direction.

Run `python -B benchmark/performance.py` for bounded local performance samples.
It separates fresh-process formal admission, repeated checked-certificate gates,
advisor plan checks and in-process status reads. `--output timings.json` saves the
raw samples; `--repeats 5` is the default. Compare the same scalar source and
environment before attributing a speedup. These timings do not measure GPU
training, model quality or an autonomous research policy.

Run `python -B benchmark/dependency_performance.py --baseline <full-commit-sha>`
to compare full hypergraph analysis and Advisor selection against a **trusted local**
before-revision. It loads that commit's four Python modules, checks complete output
equality, alternates paired warmed samples, and reports raw milliseconds and source
hashes; `--output timings.json` retains the samples. Ordered/reverse synthetic chains
and the public goal map exercise different traversal costs. These measurements
include validation and hashing, but exclude source loading, model inference and
external computation; they are not universal speed or token-saving guarantees.

`python -B benchmark/redteam/runner.py` exercises four synthetic protocol
violations. The C7 scenario tests the mathematical boundary after removing the
self-signed flag; the hypothesis-drift scenario first executes a scalar
refutation. Its reported rates apply only to those four scenarios and do not
establish general resistance to adversarial proposals.

Run `python -B benchmark/formal_performance.py --repeats 7 --output timings.json`
for the declarative framework. It records fresh generation, independent proof
replay, WAL reads/writes and reads during an active uncommitted writer separately.
The default examples are small exact affine maps and theorem modules. A disk
cache can be slower than regenerating such cheap proofs; these samples establish
neither a worst-case latency bound nor GPU savings. Native Lean compilation, when
configured, is a separate operation and is not a millisecond database timing.

Run `python -B benchmark/affine_scale.py --dimension 64 --dimension 128` for the
opt-in dense exact-affine proof-chain profile. It times parsing, rational witness
solving, typed-plan construction, native proof generation, certificate replay and
full verification separately; it reports Python `tracemalloc` peak and certificate
bytes while keeping the 512-MiB Lean process cap visible. Larger cases are not part
of default CI. A 256D dense run did not complete within the 90-second local profile
window, so the current supported ceiling is 128D. These synthetic exact maps are a
software-path stress case, not a neural-network, GPU or training-readiness claim.

Regenerate packets from the private sibling corpus:

```powershell
python -B benchmark/extract.py --source ../research-direction-selector-eval
```

The manifest binds the source-card bytes and exported packets with SHA-256.
Sealed files retain original Obelisk message UUIDs for local evidence tracing.
They contain curated case text, never full session exports or credential messages.
