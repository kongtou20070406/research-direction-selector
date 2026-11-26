# Vendored subset of academic-research-skills

This folder keeps 93 reference files from [academic-research-skills](https://github.com/Imbad0202/academic-research-skills) v3.21.0 (released 2026-08-18) by Cheng-I Wu, licensed CC BY-NC 4.0 (see [LICENSE](LICENSE) and [NOTICE.md](NOTICE.md)). On 2026-11-23 the installed copy was pruned from the full 2,252-file upstream tree to the files `paper-write` reads.

Not vendored: the upstream `scripts/`, `shared/` contracts and schemas, `evals/`, `tests/`, `docs/`, `deep-research/`, `academic-pipeline/` and slash commands. When a document here says to run `scripts/check_*.py`, load a `shared/` contract, or hand off to an upstream pipeline agent, skip that step and use the `paper-write` gates instead:

- Tier 1: `verify_manuscript_formal.py` and `check_sentence_clarity.py` in `paper-write/scripts/`.
- Tier 2: the hash-bound `independent-reviewer` and `editorial-reviewer` reviews described in `paper-write/SKILL.md`.

To restore an upstream file, fetch it from the v3.21.0 release instead of re-vendoring the whole tree.
