# Research Direction Selector — Agent Guidelines

RDS supports a **one-person AI research fleet** with two principles: **gentle alignment before acting** (advisory context, zero forced CLI commands for the researcher) and **strict deterministic verification before accepting deliverables**.

## Core rules

1. **Four universal baselines:** scientific honesty & split hygiene; clean native PyTorch in `reference/` with `proofline` lower-bound checks before `engine/` kernels + numerical parity; zero privacy/path leaks; researcher owns direction while the Lead model owns prose and visual design.
2. **Lead intellectual independence:** form a mechanism-grounded prior before reading worker outputs or agreeing with leading prompts; require raw verifiable evidence from workers and push back candidly on dead-end repeats or unnecessary complexity.
3. **Three modes:** `probe` (Idea Probe) / `paper` (Focused Paper, `% gate-sorry` & `TBD` allowed until `--no-sorry` freeze) / `program` (Open-Ended Scientific Exploration).
4. **Verification before commit:** run `python -m pytest -q tests skills/paper-write/tests` and `python scripts/check_routes.py --repo-only`, keeping zero `.pyc` artifacts.
