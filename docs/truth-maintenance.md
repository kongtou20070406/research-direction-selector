# Program-owned dependency maintenance

**English** · [简体中文](truth-maintenance.zh-CN.md)

Give the tool small declarations and changes. The program builds the internal AND/OR table, selects justification witnesses, checks the structure and recomputes the closure and goal blockers. An agent does not maintain a second support table or copy derived statuses back into its input.

The existing `hypergraph` command accepts its original schema and compact input:

```json
{
  "claims": {"premise": {"status": "supported", "source": "observation.json"}},
  "rules": [{"from": "premise", "to": "target", "status": "supported", "source": "implication.json"}],
  "goal": "target"
}
```

Here the program creates the `target` node as `UNKNOWN`, assigns the rule ID and derives the reported closure. The agent supplies the meaning of the support relation once. All premises of one rule are AND; separate rules concluding the same node are OR. The tool cannot discover a scientific implication from arbitrary prose.

## Input tolerance

Unambiguous formatting repairs are automatic and retained in `input_review`:

- A whole JSON code fence and trailing commas outside strings are accepted.
- `claims`/`nodes`, `rules`/`hyperedges`, `goal`/`goals`, `state`/`status`, and rule `from`/`premises`/`if`, `to`/`conclusion`/`then` are accepted.
- Node records can be keyed by ID; a single premise or goal can be a string. Identifier whitespace and status case are normalized, and repeated AND premises are deduplicated.
- Missing node status defaults to `UNKNOWN`; missing rule status defaults to `PROPOSED`. Referenced nodes and rule IDs are generated. Missing sources get input-declaration locators, and a source-free `SUPPORTED` assertion is withheld as `UNKNOWN` or `PROPOSED`.
- A supplied file/hash source gets a locator while retaining its original fields. Invalid hash pairs remain explicit input errors. Identical repeated changes reuse the snapshot; a new change source is retained in history.

Conflicting aliases, duplicate IDs, missing rule endpoints and unknown change targets are reported together. No change is applied while these ambiguities remain. Exit 2 means input clarification or a computation limit; an open scientific obligation is still usable and does not cause a formatting rejection. Duplicate JSON keys and non-finite numbers remain invalid. Imported code is data and is never executed.

## Submit changes in the agent loop

```powershell
python -B scripts/rds_cli.py --root ../tms-demo hypergraph -i examples/tms-input.json
```

The root must already exist. The program saves its map in the existing `.rds/project.sqlite3` and CAS. Subsequent calls automatically load that root's current state. Neither the whole table nor the previous `record` path is a required model argument:

```powershell
python -B scripts/rds_cli.py --root ../tms-demo hypergraph --retract-node premise --change-source later-observation.json
python -B scripts/rds_cli.py --root ../tms-demo hypergraph --update one-change.json
python -B scripts/rds_cli.py --root ../tms-demo hypergraph --trace-cone target
```

`one-change.json` can contain just `{"claims":{"premise":{"status":"supported","source":"new-observation.json"}}}`. `--declare '<small JSON>'` also accepts inline declarations without a file-writing round trip. At most eight fragments are merged per call; references do not overwrite existing observations, and explicit conflicting scope/revision or computation limits need a deliberate new-map import. The program supplies IDs, sources for declarations and derived status tables. It does not invent the meaning of a scientific relation.

Default output contains at most three goal statuses, bounded change counts/IDs, one next step and a CAS `record` locator for optional inspection. It combines compilation, revision, analysis and storage in one tool action. Retraction withdraws a node's reported support (`UNKNOWN`) or a rule's reported support (`PROPOSED`). `--refute-node` / `--refute-rule` instead record `CONTRADICTED`. Original evidence sources stay on the rows; the change source and previous status/source stay in append-only history. `--output <new-file>` and `--json` expose the full result when needed.

The ordinary closure, goal statuses, blocker sets and reported rows all refer to the revised map. Independent valid OR alternatives survive; an unanchored cycle cannot prove itself. Earlier snapshots remain immutable. Use `--input <earlier-record-path>` for an explicit restoration. Input errors leave the current map unchanged; a competing writer produces `CONFLICT` rather than overwriting newer state or returning stale supported goals. Exit 2 covers clarification (`UNKNOWN`), bounded incomplete computation (`INCOMPLETE`) and concurrency (`CONFLICT`); inspect `status`/`next_step`. Syntax, storage or integrity errors retain the existing exit 1 path.

Use `advise --saved-dependencies --context context.json --graph graph.json` to inject this root's current map into the existing Advisor context on the program side. Prospective `exec --saved-dependencies --context context.json --graph graph.json --ledger <owner>` also reloads from its owning ledger before admission. The context file needs no dependency table. Explicit `dependency_map` input remains supported, including compact declarations and full program-generated snapshots; internal canonical maps retain their strict validation. Existing receipt checks, goal guards, budgets and authorization remain in their current owners. Do not attach the same map through both routes.

For a custom tool, `rds_tms_store.tms_tool(root, declaration=..., retract_nodes=...)` exposes the same operation. The host binds `root` from local context; the model sees only the small arguments and brief result. Codex/Pi can already use the CLI through their shell tool. Native custom-tool wrappers are integration examples, not installed plugins. Do not require TMS on every model turn or use an always-continue hook: call it when dependencies or evidence change, or an explanation would change the next action. See the [Codex/Pi design evidence and byte comparison](agent-loop-tms.md).

## Evidence boundary

An operator caller can use `rds_hypergraph.apply_operator_verdict(map, "node:<id>" or "rule:<id>", result)` and retain the returned immutable snapshot and revision. A `PASS` with `evidence: {"receipt": {"project_root": ..., "sha256": ...}}` declares `SUPPORTED` only after the existing ledger audit grounds a succeeded, integrity-checked receipt. Missing, failed or corrupt receipts leave the declaration unchanged. Each distinct receipt binding is read once per operation; an execution receipt still does not verify the statement.

`CONTRADICTED` requires an operator name, a 64-character hexadecimal `input_sha256` and a bounded finite JSON witness. JSON objects require string keys and arrays require lists: key or container coercion is refused so the witness survives strict JSON storage unchanged. The revision retains the full previous source and archives the operator/hash/witness tuple as the change source; the original source on the row stays intact. The actual `BoundedFiniteModelOperator.search_counterexample` result uses `FAIL` with `COUNTEREXAMPLE_FOUND`: attach `operator: "bounded_finite_model"` and the exact input digest to adapt that result. Predicate errors and other `FAIL` results remain unproven, as do `UNKNOWN`, `ERROR` and bare `PASS`. Invalid or oversized verdict data raises `ValueError` before changing the map. The ordinary TMS recomputes closure and blockers while preserving independent OR alternatives.

The assurance remains `INPUT_REPORTED_DEPENDENCY_ANALYSIS_NOT_PROOF`. A source locator, reported `SUPPORTED` label, file hash, execution receipt or finite local cases does not establish a scientific claim. File auditing checks bytes only. The program manages dependency consequences and offers a next check; it grants no execution authorization and declares no universal capability mastery.

Reusable candidate execution continues through `rsi extract/validate/register/use`. Goal-bound application, independent expected answers and scoped reuse remain the work described in #43/#45. The operator bridge covers the declared TMS transition in #49; external operator execution and persistence remain with the caller. This interface introduces no capability registry or mandatory three-tier workflow.
