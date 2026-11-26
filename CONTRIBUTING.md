# Contributing to Research Direction Selector

Thank you for contributing to RDS v7.

## Development setup

```bash
python -m pytest -q tests skills/paper-write/tests
python scripts/check_routes.py --repo-only
```

## Pull request checklist

1. Keep `scripts/` and `skills/` cross-platform (Python 3.11+, no hardcoded local machine paths).
2. Keep `AGENTS.md <= 60` lines and top-level `SKILL.md` files concise (`<= 80` lines), preserving existing frontmatter `description` fields unless intentionally updating triggers.
3. Add or update unit tests in `tests/` for any behavioral, routing, or README/doc-shape change.
4. Keep hooks advisory and opt-in by default.
