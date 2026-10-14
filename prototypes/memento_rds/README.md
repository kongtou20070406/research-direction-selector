# Isolated reflective world-model prototype

This directory is deliberately absent from production routing, plugin exports
and admission. It imports no RDS modules and creates no database or scheduler.

`model.py` compiles an explicit natural-language rule plus declarative update
expressions into executable Python expression code. It returns content hashes,
complete replay predictions and original counterexamples. A revision requires
a semantic-review verdict bound to the exact rulebook/code artifact; failed or
UNKNOWN review retains the previous pair. Unknown actions reject prediction.

The expression dialect is intentionally small. It is an engineering prototype
of compilation/replay/revision, not ARC-AGI-3 reproduction, a learned research
predictor, a general Python sandbox, or scientific support. Tests supply fixture
review verdicts, not independent LLM semantic certification. A later domain
adapter must supply real retained receipts to the S1 contract before adoption.

Run: `uv run --with pytest --no-project python -m pytest prototypes/memento_rds/test_model.py -q`.
DeepSeek evaluates actual RDS agent use separately; these fixtures do not measure
RDS efficacy. See `docs/memento-prototype-plan.md` for paired testing and budgets.
