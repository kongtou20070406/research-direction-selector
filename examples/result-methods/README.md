# Protocol-bound finite methods in an owned decision

From the checkout, choose a fresh sibling workspace and save the independently
declared fixture outside it:

```powershell
python -B examples/result-methods/specs.py | Out-File -Encoding utf8 ../method-specs.json
python -B examples/result-methods/run.py --workspace ../owned-methods-example --specs ../method-specs.json
```

The public fixture has three explicit oracles: paired loss improvement `2.0`,
zero residual against two independently specified finite values, and a two-row
gate with one beneficial acceptance and zero harmful acceptances out of one
harmful example. These are development cases, not safety or research-gain evidence.
`specs.py` never calls the candidate to construct an expected result.

Each specification names its method, exact args, full independent expected
return, original sources and result predicates. Source paths are confined to
`sources/`, hashes are checked against bytes, and actual method inputs must match
the original values/IDs or diagnostic text. Data/evaluator and upstream parent
sources must be present. These checks do not infer that caller scientific
semantics, reference independence or original parent claims are true.

The existing native entries extract, validate, register and prepare each method;
`project init --recipe` compiles the existing owned contract. A 90-second wall
budget includes native preparation and the three 10-second application
reservations. Goal predicates consume the returned improvement, tolerance boolean
and `/heads/gate/acceptance/harm/rate`; they do not use qualification PASS as the
useful observation. Repeated advance preserves original runs and spent budget.

`cli-transcript.json`, `reports/`, `outputs/`, source bytes, summary and the
original SQLite ledger retain admission, applicability, actual use, consumption,
cost and recovery evidence. The fixture reuses exact qualification cases:
unseen-task applicability, model token savings and scientific gain are unproved.
The driver is ordinary trusted project code, not an OS sandbox.
