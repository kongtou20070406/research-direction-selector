# Finite result tools with real decision consumers

Run from the repository in a fresh empty sibling workspace:

```powershell
python -B examples/result-tools/run.py --workspace ../RDS-result-tools-example
```

This public synthetic example qualifies JSON scalar extraction, compatible
metric comparison and failure inventory through the existing native RSI route.
All three returned values feed original owned goal predicates, with bound
source files, inputs, cases, execution receipts and preparation costs. The
failure fixture deliberately contains 64 failures and one skip; reading it does
not turn those failures into passing tests.

Inspect `summary.json` for actual consumption and material bytes,
`cli-transcript.json` for every original command/output, and `reports/` for
budget, recovery and checkpoint evidence. The complete original source JSON
remains in `sources/`; long traces are represented by pointers and omission
flags in the compact result. Nothing is overwritten on rerun.

The three finite cases are reused for qualification and application. Model
round trips, token savings and scientific improvement are not measured. Read
the [API, bounds and evidence limits](../../docs/result-tools.md) before reuse.
