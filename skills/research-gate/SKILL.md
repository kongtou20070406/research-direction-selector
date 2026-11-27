---
name: research-gate
description: Vet ML/AI research proposals, model architectures, loss functions, training recipes, evaluation metrics, and formal verification plans BEFORE coding or burning GPU, and pack research handoffs or stage archives. 立项评审、架构设计、损失函数、训练配方或正式实验前的反自嗨与极简门禁，以及科研交接与阶段归档。Do NOT use while drafting or polishing paper prose (-> paper-write), searching papers (-> paper_search), computing FLOPs/latency (-> perf-ruler), or choosing the next route for a stalled research line (-> research-direction-selector).
---

# Pre-code research gate

This workflow empowers a **one-person AI research fleet** to pursue hard open scientific problems through clean mechanisms, crisp theory, and high-information experiments. It does two jobs: **Inspire & Align** before acting (never ask the researcher to type CLI commands; translate conversation into background tools) and **Verify** strictly before accepting deliverables. Co-create the hypothesis, baseline, and falsification threshold first — no blind trial-and-error bypassing. Domain-specific bans belong in each project's `GOAL.md` or `.agents/brief.json`.

## Project modes (`scripts/bootstrap.py --init-project`)

- **M0 Idea Probe (`probe`)**: Fast, minimal native-PyTorch validation of a crisp scientific intuition after checking prior insights.
- **M1 Focused Paper (`paper`)**: Turn a clear discovery into a crisp, reproducible manuscript (`.agents/brief.json` + `paper_gate.json`, zero glue; daily arXiv PDFs tracking pending numbers with `% gate-sorry`/`TBD` and clearing them at `--no-sorry` freeze).
- **M2 Open-Ended Scientific Exploration (`program`)**: Sustained inquiry into a hard open scientific problem with `program.json` proposition tree, high-information experiments, shared knowledge base, and nightly sync — papers emerge naturally as milestones along the quest.

## Four universal hard baselines

1. **Scientific honesty & no leakage**: disjoint train/val/holdout splits, untouched tests, traceable `run_meta`/seed/precision, report only observed runs.
2. **Code purity & parity**: `reference/` holds clean, minimal native PyTorch (passing `--code-quality`); before optimizing, check `proofline` (`perf-ruler`) zero-GPU formal latency lower bounds (`compute`/`memory`/`serial` + Z3/Lean 4). Only when `engine/` has clear headroom does the **Lead model** author per-device operator optimizations (`Triton`/`CUDA`/`torch.compile`) in `engine/` while a read-only `independent reviewer` audits strictly for bugs and edge cases, passing forward+backward parity + `>=1.15x` speedup (else keep `reference/`). This split applies **only** to operator/kernel performance optimization; all other closed engineering tasks still go to an `engineering worker` via `scripts/dispatch_gpt.py` with `--protect reference tests`. Never delete handwritten drafts or hijack an occupied local GPU.
3. **Zero privacy leaks**: keep local paths and credentials out of shared/public repos.
4. **Human & lead-model sovereignty**: researcher owns the north star, direction and final verdicts; Lead owns core prose voice, art direction, and final visual PNG sign-off, offloading mechanical chores (plot/TikZ coding, LaTeX typesetting/BibTeX/ref sync, card/CI/test plumbing, heavy pre-digs) to workers via `scripts/dispatch_gpt.py --skill <name>` with mandatory skill-contract injection and post-gates. Never poll background tasks/subagents with `schedule`/`sleep` loops—rely on reactive wakeup plus a single stall timeout.

## Core scientific discipline

1. Search prior art and the knowledge registry (`scripts/prior_art_check.py`, `scripts/knowledge.py`) before designing a run; deposit kept primitives and ruled-out approaches with explicit `scope`, promote generalizable entries to `跨项目.jsonl`, and do a jump review after two non-passing main-line runs.
2. Lock `<=3` falsifiable What-claims, scope limits, primary metric, kill threshold and matched-recipe standard baseline before spending GPU; tune on `validation-only` with a single-holdout, screen with 1 seed, and confirm main tables with multi-seed matched-budget runs (report all 3 north-star rulers: delta, 95% CI, cost).
3. Keep models and proofs minimal and understandable; verify bottom-up (`pytest`/Hypothesis → `perf-ruler` → SymPy/Z3 → Lean 4), run `polish_check` / `accept_worker_deliverable.py --code-quality`, and run read-only `independent reviewer` audits (evidence only, no verdict) before freezing main claims.
4. Fan out **verifiable, non-experimental semi-open problems** (Lean/SymPy proofs or Python counterexamples, minimal-operator derivations, competing root-cause CPU probes, cited prior art, reviewer stress-tests) to 2–3 parallel `independent reviewer` sessions; Lead verifies mechanically and distills `<=2` options.
5. Keep each collaborator surface in its own lightweight shared repo (`scripts/publish_shared.py`, `scripts/contrib_proposal.py`); pair `program_board.py --knowledge` with `跨项目.jsonl` to surface ready primitives, use `scripts/sessions.py` to coordinate parallel conversations and Lead-owned swarm workers (`--owner` / `--cascade`, auto-surfaced by `experience_brief.py`; see [Parallel playbook](references/multi-agent-parallel-playbook.md) §§7–8), and use `obelisk` to recover cross-session/post-compaction original evidence.
6. **Lead intellectual independence**: form a mechanism-grounded prior before reading worker outputs or agreeing with leading prompts; demand raw verifiable evidence (not verdicts) from workers, and push back candidly with a counterexample or alternative when an idea clashes with known dead ends or simplicity (logging informed overrides in `DECISIONS.md`).

## Read on demand

- [Research program](references/research-program.md) · [Workflow modification rules](references/workflow-evolution-rules.md) · [Handoff and archive](references/handoff-archive.md)
- [Reviewer precheck](references/reviewer-precheck-gate.md) · [Verification levels](references/formal-verification-pyramid.md) · [Parallel playbook](references/multi-agent-parallel-playbook.md) · [Evidence ledger](references/agent-workflow-evidence.md)
