# Agent loop with the RDS tools

An agent can run its own research loop without a Compass campaign: measure a
point, rank the next one, synthesize candidates, replay them, and check a claim
on points that did not shape it. The four primitives are CLI calls that read and
write JSON; none of them calls a model.

```bash
python -B scripts/rds_cli.py --root <project> compass tool synthesize --request synth.json
python -B scripts/rds_cli.py --root <project> compass tool rank       --request rank.json
python -B scripts/rds_cli.py --root <project> compass tool replay     --request replay.json
python -B scripts/rds_cli.py --root <project> compass tool holdout    --request holdout.json
```

## Loop

```text
observe    -> rows: [{id, inputs, outputs}]                 (the measurements you have)
hypothesize-> a few candidate laws as expressions
rank       -> the input with the highest EIG per cost       (compass tool rank)
measure    -> run the experiment for that input, append a row
replay     -> drop every candidate that the new row refutes (compass tool replay)
holdout    -> check the survivor on reserved, receipted points (compass tool holdout)
```

Stop when one candidate survives and the holdout agrees; otherwise state the
unknown. A synthesis that returns no fits says nothing about the world — only
that this grammar and bound did not reach a fit.

## Request shapes

`synthesize`

```json
{"inputs": ["x"], "observable": "y", "tolerances": {"y": {"abs": 0, "rel": 0}},
 "rows": [{"id": "x1", "inputs": {"x": "1"}, "outputs": {"y": "2"}}],
 "grammar": {"operators": ["add", "mul"], "constants": ["1", "2"]},
 "max_nodes": 6, "max_candidates": 20000}
```

`rank`

```json
{"inputs": ["x"], "observable": "y", "tolerances": {"y": {"abs": 0, "rel": 0}},
 "rows": [], "rivals": [{"id": "a", "predict": "x + 1"}],
 "candidates": [{"id": "x2", "inputs": {"x": "2"}, "cost": "1"}], "limit": 3}
```

`replay`

```json
{"inputs": ["x"], "observable": "y", "tolerances": {"y": {"abs": 0, "rel": 0}},
 "law": "x + 1", "rows": [{"id": "x1", "inputs": {"x": "1"}, "outputs": {"y": "2"}}]}
```

`holdout` (reads the project ledger)

```json
{"root": "<project>", "hypothesis_id": "<id in the ledger>",
 "measurement_ids": ["<candidate id>"], "observable": "y",
 "tolerances": {"y": {"abs": 0, "rel": 0}}}
```

## Evidence rules

- `synthesize`, `rank`, `replay` are pure computation over the rows you pass.
  They do not check that a row came from a receipt; use `holdout` when a claim
  must rest on receipted measurements.
- `holdout` re-checks receipt bindings and returns `STALE_EVIDENCE` or
  `UNKNOWN_MEASUREMENT` instead of a downgraded row replay.
- A `CONSISTENT` replay is not `SUPPORT`. It means the candidate agrees with the
  rows supplied, within the stated tolerances.
- An EIG ordering is advisory: zero EIG means no rival separates at any
  remaining input; do not buy a point anyway.

## Worked example

[`benchmark/newtonbench/agent_tool_demo.py`](../benchmark/newtonbench/agent_tool_demo.py)
runs `rank -> measure -> replay -> filter` through these CLI calls and leaves a
single correct law after three measurements.
