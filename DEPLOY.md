# Deployment & Setup Guide

RDS v7 works standalone out of the box with zero mandatory background hooks. Use this guide when setting up a new machine, onboarding a project, or enabling optional advisory hooks.

---

## 1. Prerequisites

| Component | Requirement | Purpose |
| :--- | :--- | :--- |
| Python | 3.11+ | Core scripts, gates, and `pytest` suite |
| Git & GitHub CLI (`gh`) | Recent release | Version control, shared repos, and CI checks |
| TeX Live / XeLaTeX (optional) | `xelatex`, `latexmk`, `pdftoppm` | `paper_gate.py` PDF builds and page rendering |
| Companion tools (optional) | [Proofline](https://github.com/kongtou20070406/proofline), [Obelisk](https://github.com/tommy0103/obelisk) | Zero-GPU Roofline+Z3+Lean4 bounds and cross-session recall |

---

## 2. Quick Setup (New Machine)

```bash
git clone https://github.com/kongtou20070406/research-direction-selector.git <path/to/rds>
cd <path/to/rds>
python -m pytest -q tests skills/paper-write/tests
python scripts/check_routes.py --repo-only
```

To link skills into existing local agent directories and install repo git hooks:

```bash
python scripts/bootstrap.py --apply
```

*(Run without `--apply` first for a read-only dry run.)*

---

## 3. Onboarding a Research Project

```bash
python <path/to/rds>/scripts/bootstrap.py --init-project <path/to/project> --name my_study --mode program --vault <path/to/notes>
```

Edit `<path/to/project>/.agents/brief.json` to set:
- `"mode"`: `"probe"`, `"paper"`, or `"program"`.
- `"focus"` (optional): a short string or `{"program": "program.json"}` to surface the active focal note at the top of project briefs.

Validate anytime with:

```bash
python <path/to/rds>/scripts/experience_brief.py --check <path/to/project>
```

---

## 4. Optional Advisory Hooks

RDS never forces background hooks. If you want lightweight, advisory skill hints (`[RDS Context Hint]`) and project briefs (`[Project Context Brief]`) injected on turn start, point your agent's `UserPromptSubmit` / `PreInvocation` hook to:
- `python <path/to/rds>/scripts/skill_router.py claude` (or `agy`)
- `python <path/to/rds>/scripts/experience_brief.py --cwd <project>`

Both exit silently with empty output when no trigger or `.agents/brief.json` matches.
