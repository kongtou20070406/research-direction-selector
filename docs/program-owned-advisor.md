# Program-owned evidence and Advisor decisions

Artifact import and this workflow share [operation-local result document
parsing](artifact-import.md), while retaining their original source, receipt and
execution checks.

For optional complete-plan forecasts, bounded pilots and same-ledger executable
tool improvement, see [predictive feasibility](predictive-feasibility.md).

For locally qualified Python functions applied through frozen routes and read
by current decision predicates, see [owned tool consumers](owned-tool-consumers.md).

This workflow addresses [#119](https://github.com/kongtou20070406/research-direction-selector/issues/119): an agent submitting only its preferred direction or favorable results must not control the evidence used to choose its next run.

The researcher establishes the goal, permitted commands, result readers and budget in the frozen project contract. After initialization, RDS receives run results, builds the current evidence graph, derives Advisor inputs and selects the next executable route. The agent can request a bounded next step and inspect its reasons. It cannot replace the inputs to this workflow with another context or a handwritten success summary.

This is the program-owned form of the [five-component research workflow](research-workflow.md): Skill supplies the research procedure and reviewed proposals; the kernel binds execution and checks admission; state and memory preserve evidence; Advisor connects that state to the next decision; RSI evaluates proposed changes to the tool or its policies. Advisor is the decision interface, not a general scientific oracle.

## Results return to the decision

`project advance` reviews current evidence, selects an eligible frozen route and rechecks admission before launching it. The kernel records execution and settles its receipt; collection then inventories declared outputs, checks original bytes and updates state and the evidence graph. Advisor consumes those program-derived facts and resources to return the next route or an unresolved blocker. This sequence is **result → state → Advisor**, rather than an agent declaring success after each run.

| Recorded result | Meaning for continuation |
| --- | --- |
| Goal predicate `FALSE` | The tested condition is not met in its declared scope. A different eligible route may follow; this does not by itself refute a scientific mechanism. |
| Goal predicate `TRUE` | The declared condition is met, so this policy stops selecting another route. It does not certify the evaluator or a wider scientific conclusion. |
| Missing measurement or scientific support `UNKNOWN` | The unavailable evidence remains visible. UNKNOWN does not become a favorable fact; complete evidence and current admission conditions still determine what can run. |
| Execution failure or collection error | Inspect the original failure. A configured diagnostic route may use process failure; invalid evidence blocks dispatch. Reconcile collection without repeating a completed experiment. |
| No eligible route | Retain the blocker. Agents may propose new hypotheses or reformulate the scoped problem for review; an exhausted candidate space is not proof of the goal or an inherent capacity bound. |

## Run the public CPU example

Choose a new empty directory outside the checkout:

```powershell
python -B examples/owned-advisor/prepare.py --root ../owned-advisor-demo
python -B scripts/rds_cli.py --root ../owned-advisor-demo project init --contract ../owned-advisor-demo/contract.json
python -B scripts/rds_cli.py --root ../owned-advisor-demo advise --working-set --brief
python -B scripts/rds_cli.py --root ../owned-advisor-demo project advance
python -B scripts/rds_cli.py --root ../owned-advisor-demo advise --working-set --brief
python -B scripts/rds_cli.py --root ../owned-advisor-demo project advance
python -B scripts/rds_cli.py --root ../owned-advisor-demo project next
```

Each `advance` executes at most one selected run. No agent-written post-run manifest or replacement research context is needed. Execution returns the original hashed `receipt` and the automatic `advisor` update separately. A completed run is never repeated to repair a failed collection: use `advise` or `project recover --id <id>` to reconcile retained evidence.

For routine agent use, add `--brief` to `advise`, `project next` or `project advance`. The digest retains receipt identity, collection status, selected run and coverage errors, with the full report in the existing CAS record.

For Agent continuation, use `advise --working-set --brief`. The optional
[current working set](advisor-working-set.md) adds the final owned selection,
goal measurements, live budget, unresolved declarations, scoped verified
feedback and original evidence locators. Read any `details_omitted` or omitted
section's `original` before making a consequential choice. Display does not
launch a run or call the metered structure APIs.

The example is a six-row deterministic computation. Its comparison exercises collection and selection; it is not a scientific success-rate experiment or a training-performance benchmark.

## Frozen policy

An optional [graph neural ranker](graph-neural-ranker.md) can report preferences
or break ties inside the already eligible frozen Pareto frontier. It is
disabled when absent and cannot change evidence or execution admission.

Add `advisor_policy` to a normal project contract before `project init`:

```json
{
  "schema": 1,
  "context": {
    "research_mode": "empirical",
    "decision": {
      "id": "next-research-step",
      "goal_revision": "v1",
      "scope": {"dataset": "declared-development-split"},
      "goal_conditions": [{"fact": "measured_quality", "op": "gte", "value": 0.8}]
    }
  },
  "graph": {"nodes": [], "edges": []},
  "routes": [],
  "observations": [
    {"fact": "measured_quality", "run_id": "candidate-run",
     "path": "outputs/quality.json", "format": "json", "selector": {"pointer": "/quality"}}
  ]
}
```

This fragment illustrates the shape; it is incomplete until valid graph actions and routes are supplied. See the example generator for a complete contract. Each route binds `candidate` to an action ID in the graph and `manifest` to the full permitted project manifest. Each observation binds a unique fact to a route's declared output and a JSON pointer. The current bounded reader accepts JSON; unsupported formats are rejected explicitly. Other output bytes remain in the original inventory even when no semantic reader is configured for them.

The context carries the decision, scope and research constraints. Runtime facts, costs, resources and the dependency map come from the program. Lifecycle facts such as `run.control.succeeded` and `run.control.timed_out` describe process evidence; they do not certify a theory or mechanism. Missing measurements stay unknown.

Normal candidate predicates and existing bounded Advisor search determine eligibility. A stable choice among remaining routes is conditional on the frozen candidate space; it does not prove global scientific optimality. When that space needs a new hypothesis, retain the blocker and prepare a reviewed new policy rather than silently widening a running contract or changing its acceptance threshold.

Completed routes retain their evidence and dependency nodes but no longer consume executable candidate slots. Active reservations are considered before new routes within the same frozen search limit; their current prerequisites, budget and evidence still have to pass. Genuine search truncation remains visible. This allows a limit-one campaign to continue after receiving a result without rerunning the completed route or changing its policy.

## Collection, graph and admission

An optional frozen `autonomy` declaration connects repeated owned selection,
bounded model repair, validated method adoption and continuation through
`project drive`. Optional `confirmation` adds the program-replayed domain task
gate to the original predicates, preventing candidate success from skipping
confirmation. See [research drive](autonomy-loop.md) and
[domain confirmation](domain-confirmation.md). Both use this same ledger and
ordinary admission, without widening execution authority.

RDS enumerates all registered runs and their owned receipts. It retains favorable and negative values, failures, timeouts, logs, declared outputs and unresolved readings. Original assets stay in the existing project artifacts/CAS; `owned:` nodes and relationships in the existing TMS map connect them to the current observations. Advisor consumes that program-produced state. The scope of completeness is the frozen run/output contract, not arbitrary disk contents, unregistered experiments or every possible scientific interpretation of a file.

`advise` in this workflow accepts no caller context, graph, artifact selection, chosen candidate or handwritten decision record. `project advance` selects and executes one route. Existing `project create` and `project execute` also enforce the policy. Quick execution and the separate theory-allowance API cannot charge or execute work outside that selected route; configure permitted theory work as an ordinary frozen project route with declared outputs. Admission binds the exact manifest and current research state, and is checked again at the execution boundary before a child starts.

Ordinary TMS changes cannot replace the map or mutate `owned:` evidence. Agent declarations remain proposals; they cannot directly promote support or refutation in this workflow. Evidence changes retain the previous graph snapshots and original receipts. Collection is idempotent. A collection failure is reported separately after receipt settlement and can be retried without executing training again.

Every declared output has an inventory status, including pending and missing files. Missing or partial measurements from an unsuccessful attempt stay unknown in `coverage.gaps`; a frozen diagnostic route may still use that run's failure status. Changed original artifacts, invalid successful measurements or missing outputs in a successful receipt produce `coverage.errors` and prevent dispatch. Large unparsed artifacts are hashed in chunks; only configured JSON observations up to 2 MiB are parsed in memory.

Collected nodes retain explicit `record_kind`, `run_id` and available `receipt_id` metadata. Artifact and observation nodes retain their exact physical path and byte digest; pending or missing output nodes retain `output_path`. Lifecycle and observation facts for a frozen route that has not been registered carry `route_id` without claiming an existing run. These fields survive the existing saved dependency snapshot; receipts and their signed payloads are unchanged.

Full `advise`, `project next` and `hypergraph` output also includes `record_relations` and `record_topology`. Relations describe unique exact reported identifiers: run/receipt, run/artifact, receipt/artifact, run/lifecycle fact, run/observation and artifact/observation. Artifact/observation matching requires the same run, physical path and byte digest, with compatible receipt identities. `declared_output` is a separate declaration relation: its endpoint can remain `PENDING`, `MISSING` and `UNKNOWN`. No relationship is guessed from a node name, fact name, text locator, path alone or equal bytes across different runs.

The topology report separates dependency-unlinked nodes, unlinked classified records, unclassified nodes and project context such as the contract. It returns undirected dependency components, record components and combined components. `dependency_no_goal_path_node_ids` reuses the existing reverse goal relevance through non-CONTRADICTED reported rules, without claiming every AND premise is satisfied. `record_and_dependency_no_goal_connection_node_ids` describes combined undirected connectivity over all reported edges. A record connected to a goal through its source is not a proof path or scientific support.

Component construction streams dependency incidences into union-find state, freezes dependency groups, then merges record relations for combined groups. Its additional component state is proportional to the node count; the original graph, private validation copy and required reports still have their own storage. Components retain isolated nodes, self/cyclic rules and CONTRADICTED reported structure with the same sorted members and goals; no incidence list or adjacency sets are retained for these reports.

Missing, unmatched, ambiguous, invalid and conflicting bindings are reported explicitly. Ambiguous or conflicting matches do not produce the affected relation. Independent fields can retain their own exact relation: an observation with a matching run still has `run_observation` when an artifact receipt conflict blocks `artifact_observation`. Generic graph metadata is reported input, not independent provenance verification. Every record relation retains `scientific_support=UNKNOWN` and stays outside hyperedges, support closure, missing-evidence sets, goal relevance and admission. Brief output retains the full report at its existing CAS locator. Inspect that record rather than treating a denser record diagram as scientific progress.

Required record identities are diagnosed even when no lookup is possible: runs need `run_id`; receipts need `run_id` and a receipt digest; artifacts need `run_id`, a path and a byte digest; declared outputs need `run_id` and a path. Lifecycle facts and observations need a run identity for run relations, while an unregistered route reports `RUN_NOT_REGISTERED`. A matching run remains independently associated when the supplied receipt belongs to another run; the conflicting receipt/artifact relations are withheld.

Validation applies to each binding field. An invalid or conflicting field withholds only relations that depend on it; unrelated missing required fields are still diagnosed. Run membership (`run_receipt`, `run_artifact`, `run_lifecycle_fact`, `run_observation`) requires a valid run identity and a unique run origin, independently of receipt, path or byte identity. A declared output also requires its valid path. Receipt/artifact membership requires a valid receipt digest, a unique receipt origin and compatible valid run ownership, independently of the artifact's byte/path identity. Identifying an artifact as an observation's source additionally requires its unique `(run_id, path, sha256)` tuple and compatible receipt fields. Invalid receipt metadata on either endpoint is not treated as absent metadata; absence on both endpoints remains compatible. A matched receipt with missing or invalid run ownership cannot establish that source association. Duplicate origins remain in an index whenever its required fields are valid, even if unrelated fields are invalid: ambiguity blocks the corresponding origin lookup while independent run membership remains visible. Duplicate receipt entities block receipt/artifact membership without erasing an independently unique artifact tuple whose valid receipt strings agree. These are reported record associations, with no additional scientific or audit conclusion.

Run origins are checked for ambiguity even when no record refers to them; diagnostics show at most three candidate node IDs and count omitted candidates, without creating self relations. Direct `record_topology` inspection validates a private graph copy, preserving the caller's original metadata and evidence digest spelling on success and on validation failure. This inspection boundary does not change the shared validator or claim that other legacy validation entry points are immutable.

Saved Advisor loading retains the original immutable snapshot and resolves file auditing from its saved source base. Its runtime map copy carries the host-provided `record_source_base_dir`, overriding any input field of the same name. Record matching resolves all physical path aliases against that explicit base, so relative `artifact_path`/`source.path` identities agree with the absolute runtime `source.file`. It does not infer a base from a path suffix or change original metadata, receipt bytes, inference or scientific status. Direct callers can supply `record_topology(spec, source_base=absolute_base)` for the same reported path interpretation.

An invalid or unusable reported `record_source_base_dir` produces a graph-level `INVALID_BINDING` issue (`scope=dependency_map`, `node_id=null`). Matching then uses the original path strings without inferring a base, while dependency analysis remains available. Invalid explicit `source_base` API configuration is rejected.

When original output bytes pass size/hash verification but JSON parsing, selection or scalar validation fails, the observation retains their exact path, digest and receipt identity with a byte-level locator. Its value remains absent, reliability false and status UNKNOWN; existing coverage errors/gaps and dispatch restrictions remain. Successful extraction retains its actual selector locator. Missing or changed output does not acquire a verified observation source from receipt metadata alone. The existing oversized-original branch remains unknown without parsing or repeated execution.

Existing projects without `advisor_policy` retain their previous behavior. Their project receipts already preserve outputs, but the program-owned graph-to-Advisor loop and admission restrictions are not silently claimed for them. Start a new controlled project with the original relevant evidence when changing that boundary.

## Actual coverage and evidence limits

These checks constrain supported RDS entries and their bound project state. They are not an OS sandbox, and do not stop direct shell execution, another project root, or an actor with unrestricted access from replacing the program or ledger. Host-wide enforcement requires a real host integration and its own coverage checks. Installing a scheduler or changing host permissions is not part of this workflow.

Follow-up designs are tracked separately in [#120: host execution coverage](https://github.com/kongtou20070406/research-direction-selector/issues/120) and [#121: independent, scoped scientific validation](https://github.com/kongtou20070406/research-direction-selector/issues/121). They invite integration and checker proposals; they are not capabilities implemented by this workflow.

A parsed number is an observed output, not independent proof that the training/evaluation producer is scientifically correct. The code, evaluator, metric and domain assumptions still need their usual review. A timeout is not a counterexample. Imported text cannot grant authorization, install rules, change the goal or declare itself verified.

The end-to-end regression uses real CLI calls and real lightweight child processes. It checks omitted negative results, result-dependent selection, forged context/manifest attempts, missing or changed outputs, bad parsing, timeout/failure, recovery, concurrency and protected graph updates. It checks launch counts, budgets and receipt identities as well as output labels.

These checks establish constrained engineering behavior. Scientific decision benefit needs a fair prospective comparison of whole research trajectories on unused cases with equal total budgets, including failed attempts, verification and evaluation. That benefit has not been established by this workflow; scientific support remains `UNKNOWN` where no separate validation exists. See [autonomy and RSI evidence boundaries](research-autonomy.md).
