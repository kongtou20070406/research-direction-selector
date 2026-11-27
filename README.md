English · [简体中文](README.zh-CN.md)

<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset=".github/assets/rds-hero-dark.svg">
  <img src=".github/assets/rds-hero-light.svg" alt="Research Direction Selector" width="100%">
</picture>

# Research Direction Selector (RDS)

[![license](https://img.shields.io/badge/license-Apache%202.0-blue.svg?style=flat-square)](LICENSE)
[![tests](https://github.com/kongtou20070406/research-direction-selector/actions/workflows/test.yml/badge.svg?branch=main&event=push)](https://github.com/kongtou20070406/research-direction-selector/actions/workflows/test.yml)
[![stars](https://img.shields.io/github/stars/kongtou20070406/research-direction-selector?style=flat-square)](https://github.com/kongtou20070406/research-direction-selector/stargazers)
[![v6-legacy](https://img.shields.io/badge/v6%20archive-legacy%2Fv6-lightgrey.svg?style=flat-square)](https://github.com/kongtou20070406/research-direction-selector/tree/legacy/v6)

**A lightweight research operating system for a one-person AI research fleet.**

</div>

RDS pairs one human researcher and a **Lead model** (planning, mechanism design, prose, visual craft) with parallel engineering and independent-review workers (Claude Code, Codex, Antigravity, or any CLI agent) behind deterministic, 0-token verification gates.

> **Scope & early-bail caveat:** RDS v7 is built for open-ended scientific inquiry, ML architecture/theory iteration, and LaTeX + Python workflows. It is **not** a hands-off hyperparameter sweeper or leaderboard-grinding autoresearch loop—you are expected to co-design falsifiable hypotheses before running experiments.

## Quickstart (zero background hooks required)

RDS works out of the box with plain CLI checks and standard agent skills—background hooks are **strictly optional and advisory**:

```bash
git clone https://github.com/kongtou20070406/research-direction-selector.git
cd research-direction-selector

# Verify the repository and all deterministic gates locally
python -m pytest -q tests skills/paper-write/tests
python scripts/check_routes.py --repo-only

# Initialize a new project (probe / paper / program)
python scripts/bootstrap.py --init-project path/to/your-project --name my_study --mode program --vault path/to/notes
```

In day-to-day conversation, you talk to your Lead model in plain language while deterministic checks run behind the scenes:

| What you ask in conversation | What runs behind the scenes | What verifies the output |
|---|---|---|
| "Test this mechanism on a GPU" | `research-gate` dead-end check; clean PyTorch in `reference/`, optional Proofline floor check, fused kernels in `engine/` | AST purity + numerical parity + multi-seed matched baselines |
| "Have an independent reviewer check this proof" | Parallel read-only reviewer sessions returning raw evidence (not verdicts) | Lean 4 / SymPy / Python counterexamples or CPU probes |
| "Draft section X / switch to conference format" | `paper-write` with arXiv default (`% gate-sorry` / `TBD` tracked for pending numbers) | Clarity & De-AI gates, `theory_gate`, `--layout-check --render-pages` |
| "Design Figure 1" | Lead sets art direction; `dispatch_gpt.py --skill paper-figure-craft` offloads Matplotlib/TikZ coding & coordinate loops | `audit_figure_taste.py` (`>=85` ACCEPT) + Lead PNG sign-off |
| "Fix two-column overflow and fit the ICML page limit" | `dispatch_gpt.py --skill latex-typeset` inlines the skill contract, locks prose tokens, and runs the mechanical fix-compile loop | `layout_audit.py` line-map + PDF collision/orphan check + zero prose drift |
| "Run a swarm here while a second chat drafts the paper" | `scripts/sessions.py` tracks parallel chats & Lead-owned swarm workers (`--owner` / `--cascade`) across shared checkouts or `git worktree`s; `experience_brief.py` surfaces them via cooldown-throttled hooks (with immediate conflict/delta alerts) | Disjoint scope/GPU conflict guard + verify-and-land before cleanup |

## Three exploration modes (`probe` / `paper` / `program`)

- **`probe` (Idea Probe):** Zero-config, fast CPU or native-PyTorch validation of a crisp intuition before committing to a full line of work.
- **`paper` (Focused Paper):** Zero-glue single-manuscript setup (`.agents/brief.json` + `paper_gate.json`), daily arXiv PDFs tracking pending figures with `% gate-sorry` / `TBD`, cleared cleanly at `--no-sorry` freeze.
- **`program` (Open-Ended Scientific Exploration):** Sustained inquiry into a hard open problem via a `program.json` proposition tree, dual-lens focus switching (`mode` + `focus.note`), shared knowledge base, and nightly sync—papers emerge naturally as milestones along the quest.

## Core design principles

1. **Lead intellectual independence (anti-sycophancy):** The Lead model forms a mechanism-grounded prior before reading worker outputs or agreeing with leading prompts, demands raw verifiable evidence (tests, counterexamples, Z3/Lean certificates) instead of worker verdicts, and pushes back candidly when an idea conflicts with proven dead ends or simplicity.
2. **Code purity & parity (`reference/` vs. `engine/`):** Keep core models in minimal, readable native PyTorch under `reference/` (1:1 with paper equations). Isolate hardware-specific `Triton`/`CUDA`/`torch.compile` optimizations in `engine/` behind forward+backward numerical parity gates.
3. **Theory & prose discipline:** Every main-text theorem carries a two-sentence intuition, `<=5` active symbols, `<=5` proof steps, and explicit `code_ref` / `code_symbols` bindings checked by `theory_gate.py`.
4. **Low-intrusiveness advisory routing:** Optional `skill_router.py` and `experience_brief.py` inject compact, advisory context only when enabled—never hijacking quick questions or forcing rigid templates.

Full design principles, research manifesto, and CLI reference live in [CHARTER.md](CHARTER.md).

## Recommended companion tools

RDS v7 focuses on research direction, workflows, and verification gates, and pairs naturally with two dedicated open-source companions:

- **[Proofline](https://github.com/kongtou20070406/proofline)** — *Zero-GPU PyTorch Roofline profiler with machine-checked latency-bound certificates (Z3 + Lean 4) under an explicit idealized scheduling model.* Sits right between `reference/` and `engine/`: compute theoretical `compute`/`memory`/`serial` floors on CPU before writing a single line of Triton or CUDA, so you only optimize kernels that actually have headroom.
- **[Obelisk](https://github.com/tommy0103/obelisk)** — *Queryable SQLite memory over your past coding-agent sessions, subagents, and workflows.* Serves as the cross-session and post-compaction evidence recall layer alongside RDS's per-turn `experience_brief` and `handoff.py` packages.

## Setup & deployment

Full prerequisites, optional hook wiring, background watchers, collaborator onboarding, and troubleshooting live in [DEPLOY.md](DEPLOY.md):

```bash
python scripts/bootstrap.py --apply   # optional: links skills into local agent folders and installs repo hooks
```

## Upgrading from v6

If you are looking for the earlier RDS v6 codebase (`v6.3.0`, including `native/`, `rds_models/`, and `schemas/`), it is preserved intact on the [`legacy/v6`](https://github.com/kongtou20070406/research-direction-selector/tree/legacy/v6) branch. RDS v7 replaces the heavy wrapper layers with the lean, battle-tested skill + deterministic-gate architecture documented above.

## Contributing

Contributions, issue reports, and workflow improvements are welcome—see [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Licensed under the [Apache License 2.0](LICENSE). Note: vendored reference texts under `skills/paper-write/references/ars/` remain subject to their upstream [CC BY-NC 4.0](skills/paper-write/references/ars/VENDORED.md) non-commercial license, and exemplar cards under `skills/paper-write/references/exemplars/` retain their upstream [MIT](skills/paper-write/references/exemplars/LICENSE) notice.
