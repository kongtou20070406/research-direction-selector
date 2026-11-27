# Multi-Agent Parallel Orchestration Playbook

Maximize throughput cleanly while keeping academic prose coherent and preventing merge conflicts. Evidence for each rule: [agent-workflow-evidence.md](agent-workflow-evidence.md).

## 0. Admission Rule (Fan Out Only Decomposable Work)

Dispatch parallel workers only when all three hold: (a) the pieces share no mutable state or intermediate result, (b) each piece has its own mechanical acceptance test, and (c) the lead can verify each piece without redoing it. Keep sequential chains (debugging, iterative derivation, a proof whose steps depend on each other) in one agent. Under matched budgets, multi-agent setups lost 39–70% on a sequential benchmark and gained 81% on a decomposable one (E1).

## 1. Strict Responsibility Boundary (Advisory vs. Closed Execution)

- **Main Agent Only (Never Delegate + Zero-Command Interface)**: Research narrative, core submission prose, art direction (figure story, palette, hierarchy), **final visual PNG sign-off**, open-ended research design, and final synthesis. Never ask the researcher to type CLI commands or flags; translate natural conversation into background tool runs and bring the researcher only the decisions that need them, each with a recommendation.
- **Main Agent Owns Evaluation Semantics & Raw Failure Probes**: The Lead writes and owns the `eval/` entry points (what the model or planner observes, which observation window and target slice, how wall-clock time is measured, and which baseline class each result is compared against) and inspects raw failure samples itself (worst trajectories, real-simulator vs. in-model error gaps, zero-action controls) before any number enters a table. Never delegate these to an engineering worker; a worker that only sees aggregated `RESULTS.json` cannot catch a mis-pointed slice or a drifted mask.
- **Lead Offload Matrix (Always Pass `--skill <name>` to `scripts/dispatch_gpt.py` so GPT receives the inlined skill contract and passes host-side post-gates)**:
  - *Figure coding & coordinate iteration* → `gpt-6.2-sol` / `gpt-6.2-astra` with `--skill paper-figure-craft --verify-cmd "...audit_figure_taste.py..."`; Lead inspects final PNGs.
  - *LaTeX layout, BibTeX/ref/table sync* → `gpt-6.2-astra` with `--skill latex-typeset` (auto-enforces `--snapshot-prose` / `--verify-prose` + `layout_audit.py`).
  - *Card/CI/test/repo/handoff plumbing* → `gpt-6.2-sol` with `--protect tests` + `skills/research-gate/scripts/accept_worker_deliverable.py`.
  - *Heavy codebase, ledger, or literature pre-digs* → read-only subagent or parallel `gpt-6.2-astra` returning `<=12`-line gists with file/citation pointers.
- **Parallel Verifiable Semi-Open Research & Red-Team (`gpt-6.2-astra`, `-s read-only`)**:
  - **Verifiable Semi-Open Problems (Zero GPU)**: Fan out 2–3 independent read-only `astra` sessions concurrently on non-experimental semi-open problems that carry a clear deterministic acceptance bar (Lean 4/SymPy proof patch or 10-line Python counterexample, minimal-operator derivation with spectral/gradient bounds + CPU test, competing root-cause CPU probes, cited cross-domain prior art with scope limits, or Top-3 reviewer attack maps). The Lead mechanically verifies every output and brings the researcher only the decisions that need them; `astra` never sets the North Star or writes final prose.
  - **Two-Point Open-Context Red Team (decoupled from routine card closure)**: Run open-context, full-repo red-team passes at exactly two points: project/direction start (before the first sizable GPU commitment) and before numbers enter a main table or a freeze. Give two independent sessions the repository, eval code and raw results but no conclusion hints, and ask what the result reduces to in plain words, the strongest unfair-baseline story, hidden theorem premises, and the cheapest kill experiment. Their findings are evidence the Lead weighs and may rebut with evidence, not a gate verdict. Use `astra` as a closed proof referee (symbol collisions, missing hypotheses, the 3-part theorem standard) at any time. Whenever empirical findings arbitrate a divergence, update the hypothesis directly; if a sub-branch conflicts with core constraints, quarantine it and keep advancing the clean line.
- **Editorial & Evidence-Consistency Advisor (`gpt-6.2-sol`, read-only)**: Before freezing a manuscript version, run a read-only `codex exec -m gpt-6.2-sol -s read-only` review to audit academic restraint, `§3 -> §4` causal alignment, cross-section symbol collisions, prose-vs-ledger numerical fidelity, and 3-tier external citation discipline. Stop it after its report. Never let it write manuscript prose.
- **Cross-Family Consultation (optional)**: For key positioning decisions and major-draft consultations, the Lead may bring in a cross-family model (e.g., Claude Opus) through `/swarm`, under the same rules: read-only unless given its own worktree, independent reports, raw evidence over summaries.
- **Direct Dispatch over Relay**: Call `scripts/dispatch_gpt.py` directly from the Lead rather than inserting intermediate relay hops; relays (when used in a native swarm) post file paths and raw output, never their own summaries.
- **Closed Implementation Tasks -> Contract + Parallel Workers (`gpt-6.2-sol`)**: Once the evaluation semantics, specification, math equations, file ownership, and acceptance-test SHAs are locked down, dispatch cleanly scoped, mutually exclusive mechanical deliverables (scripts, `tests/`, non-kernel pipeline/adapter plumbing, same-recipe baselines, formatters) concurrently.

### 1.1 Reference vs. Per-Device Engine Layer (Silent Closed Loop)

1. **Reference (`<pkg>/reference/`)**: Lead writes clean, device-agnostic native PyTorch (`<=4` core forward equations), locks `recipe.lock.json`, and writes `tests/test_reference_engine_parity.py` (forward output + backward gradient `allclose`, shape/mask/blindness invariants, and fallback behavior).
2. **Bottleneck Check**: Run `perf-ruler` on the target GPU. For small quick screens or compute-saturated runs where `reference/` is already near the floor, skip hand-written kernels and run `reference/` directly.
3. **Per-Device Operator Optimization (`<pkg>/engine/`)**: When memory-bandwidth or launch-bound, the **Lead model (Gemini)** directly authors and tunes the per-device fused kernels (`Triton`/`CUDA`/`torch.compile`) targeting the specific GPU architecture (e.g., Colab T4/L4, A100/H100, RTX 5090) with graceful fallback to `reference/` on unsupported devices, while **`gpt-6.2-astra` (`-s read-only`)** acts strictly as a fault-finding auditor (hunting numerical/gradient/boundary/aliasing/race/benchmark-fairness flaws). **Scope limit**: this Lead-authoring + `astra`-red-team split applies **only** to operator/kernel performance optimization; non-kernel closed engineering (data/checkpoint plumbing, Colab batch runs, same-recipe baselines, scripts/tests) still goes to `gpt-6.2-sol` via `scripts/dispatch_gpt.py`.
4. **Parity & Gain Gate**: Host runs the locked parity test plus wall-clock/VRAM check; reject or silently fall back to `reference/` if mathematical semantics shift or speedup is `<1.15x`, and report one summary line (speedup + max numerical diff).

## 2. Explicit Process, Deliverable Contract & Automated Acceptance Gate

Every worker dispatch MUST define:
1. Exact **disjoint write scope** (one file or one directory per worker).
2. Explicit **deliverable contract & invariants** (exact symbols, zero cache artifacts, expected test pass).
3. **Mandatory Automated Acceptance Gate** (never accept by reading prose summary alone; a worker's PASS is a claim, not an observation, E6): snapshot before dispatch with `scripts/accept_worker_deliverable.py --snapshot-out <scratch>/base.json --protect tests`, then accept with `--baseline <scratch>/base.json` (or project-level `scripts/accept_gpt_deliverable.py --record`). The gate verifies:
   - Scope: only paths changed since the snapshot count, so a dirty tree works; any change outside `--allow` rejects (strict by default; `--lenient-scope` only reports);
   - Protected paths: any add, edit or delete under `--protect`, including a new `conftest.py`, is `test_tampering` (E5);
   - AST syntax parse + `pytest -p no:cacheprovider` pass, re-run by the gate itself; optional `--holdout-tests` the worker never saw catch special-cased fixes;
   - Zero `.pyc`, `__pycache__/`, `.hypothesis/`, or `.pytest_cache/` residue;
   - Append one JSON row to `ledger/gpt_experience.jsonl` with `failure_modes` derived from the failed checks, `worker_claim`, `lesson` and `evidence`. A claimed PASS that the gate rejects is logged as `false_claim`.

## 3. Non-Blocking Concurrent Launch Recipe

1. Write separate task contracts into an out-of-repo scratch directory and take one snapshot (`--snapshot-out`, `--protect`) before any worker starts.
2. Launch disjoint workers concurrently with short async wait (`WaitMsBeforeAsync=500`) while the lead agent writes prose or analysis directly. **Never poll background commands or subagents with `schedule` timers or shell `sleep` loops**: the host wakes the Lead automatically on completion; use only a single generous `NotificationTimeoutSeconds` (300–600s) as a stall guard. Repeated 25s–60s polling timers re-read the entire context for zero progress.
3. Run the automated acceptance gate on every finished worker output against the shared snapshot, passing the other workers' scopes as `--exclude`, and log the distilled lesson before integrating downstream work.

## 4. Review Aggregation (Independent Votes, Not Debate)

- Give each reviewer the artifact plus raw evidence (diff, logs, ledger rows, `run_meta.json`). Withhold the producer's verdict and summary; auditors adopt a stated conclusion even when it is wrong (E7).
- Two-phase blind review: the reviewer writes its own reading of what the evidence supports, then opens `claim.sealed` and reports the gap.
- Reviewers run in parallel and never see each other's reports. Do not run debate rounds; voting carries most of the gain and debate can suppress a correct minority (E8).
- The aggregator copies every blocking finding verbatim. One blocking finding that survives a mechanical recheck stops the freeze, whatever the other reviewers say.
- The model family that wrote an artifact never approves it (E9).

## 5. From Lessons to Rules

- A lesson becomes a candidate rule only with an id, a `failure_mode`, an evidence pointer and a count of recurrences. Track how often each rule helped or misfired (E4).
- Agents propose rule deltas; the researcher approves the merge into a SKILL.md. Agents never rewrite a whole SKILL.md, and self-written skills are not trusted without that review (E3).
- Retire a rule when newer evidence contradicts it or it has not fired across recent cards; record the retirement next to the rule's evidence.
- Before a SKILL.md change ships, run a small smoke comparison: same model and budget, one fresh context per task, old versus new skill files. A script checks literal anchors (numbers, citation keys, terms); a reader who sees anonymized outputs judges meaning. Claim no gain when the reader cannot tell the versions apart. A character count is not a token measurement. (Adapted from CCFA-Skills `evaluation/behavioral`, whose own smoke found no writing difference.)

## 6. Resuming After Compaction or Handoff

- Separate proposed, executed and verified. A plan is not a change, and a finished tool call is not a valid deliverable; check the artifact before trusting a completion statement (E6).
- A summary or task-state file is an index, not evidence. When it conflicts with the current instruction or a primary source, suspend that conclusion and the decisions built on it, and re-derive the facts from the source.
- Before repeating a side-effecting step (a training run, push, message or submission), check whether its result already exists. Resuming does not authorize a new external action.
- Match a late worker deliverable to the current goal and file versions. Reuse what still applies; refresh the rest instead of applying stale edits.

Sections 5–6 adapt CCFA-Skills (`ccf-common/references/continuity.md`, MIT) to this workflow.

## 7. Parallel Conversations on One Large Project

When multiple independent conversations work on the same large project simultaneously (for example, one running training cards while another drafts the manuscript or refactors an engine), keep them from colliding with five rules:

1. **Claim disjoint scope & hardware silently (`scripts/sessions.py`)**: Register each active conversation's write scope and hardware (`sessions.py claim <id> --task "..." --scope <paths> --resource <gpu:0>`) and release it when done. `experience_brief.py` automatically surfaces active sibling sessions and `⚠ CONFLICT` warnings every turn at 0 LLM tokens.
2. **Use a sibling `git worktree` for parallel code edits**: If two conversations edit code in the same repo, put the second conversation in a sibling `git worktree` instead of sharing one working tree; share one working tree only when write scopes are strictly disjoint (e.g., `latex/` vs. `src/`). Linked worktrees automatically share the main checkout's `.agents/sessions.json` board.
3. **Scoped commits only**: In a shared checkout, stage only your claimed paths (`git add <claimed_paths>`)—never `git add -A` or `git commit -a`. Keep shared logs (`STATUS.md`, `DECISIONS.md`, `ledger/*.jsonl`) append-only or single-owner.
4. **GPU mutual exclusion**: Before touching a local GPU, check `sessions.py check --resource gpu:0` alongside actual device occupancy; route secondary conversations to CPU or cloud GPUs when the local GPU is claimed.
5. **Cross-conversation memory**: Never assume another conversation's chat history. Pass facts through project files, `handoff.py` bundles, or `obelisk` search.

## 8. Lead-Managed Swarm Discipline

When a Lead conversation spins up a swarm of workers on a large task, prevent runaway churn and context pollution with six rules:

1. **Wave cap & admission (`<=3–4` active workers)**: Use a swarm only for coverage, independent verification, or scale beyond one context; keep sequential reasoning, core narrative, and visual design inline in the Lead.
2. **Context firewall (structured gist back, raw logs on disk)**: Workers write full logs, diffs, and tables to disk and return only a compact structured gist (`<=12` lines: artifact paths, exit status, exact metrics/hashes, blockers). Never dump raw worker chatter to the researcher.
3. **Bounded briefs & pipeline harvesting**: Every worker brief sets an explicit stop budget (max tool/retry rounds; stop after 2 failed attempts instead of looping). Harvest and verify workers as each completes instead of blocking on one straggler; inspect age on `sessions.py` and intervene or cancel if stalled.
4. **Isolated worktrees & owner-scoped claims**: Authoring workers edit inside isolated `workspace:"share"` / `git worktree`s and register with `sessions.py claim <worker> --owner <lead> --scope <slice> --worktree <path>`, blocking sibling-worker and cross-conversation collisions while allowing the Lead to slice its own scope. Readers and verifiers stay read-only.
5. **Verify and land before cleanup**: Run deterministic verification (`accept_worker_deliverable.py` / tests / parity) and land each finished worker's branch one at a time BEFORE killing its worktree; release finished workers (or `sessions.py release <lead> --cascade` at wave end) so zombie workers and stale claims never accumulate.
6. **Independent blind votes, Lead sovereignty**: Parallel reviewers never see each other's reports and never run debate rounds; the Lead verifies blocking findings mechanically, keeps its own judgment, and brings the researcher only the decisions that need them, each with a recommendation.
