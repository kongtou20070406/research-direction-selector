# Architecture: measured dependency baseline

This is a source inventory, not a claim that the layered bus is finished.
No runtime module, ledger, admission rule, or recovery path is changed by B0.

## Current B1 extraction result

| Stage | Upward dependency pairs | Largest SCC | Mutual dependency pairs |
|---|---:|---:|---:|
| Original B0 | 66 | 52 | 54 |
| B0.5 labels only | 51 | 52 | 54 |
| B1a reference state/values | 44 | 45 | 50 |
| B1b guarded CAS | 34 | 46 | 48 |
| B1c QUICK allowances/recovery | 30 | 47 | 48 |
| B1d result recording/output values | **29** | **48** | **48** |

The first 15 upward-pair changes are classification only; B1 removes another
22 upward pairs through real import changes. The latest graph has 125 modules,
478 combined pairs, 726 sites, 4 cyclic SCCs, 8 unresolved dynamic sites and no
unclassified edges. Source-set SHA-256:
`5cdc3c99224e9a2bd9b78280d5d1273cf68d205694fe1506adf9ad90378cc556`
(committed LF bytes). The inventory hashes raw bytes: a CRLF checkout has a
different source fingerprint even if its dependency topology is identical.
New service modules increase total edge/module counts and join an existing
kernel SCC; do not interpret extraction as an acyclic architecture.

### Remaining entry-point edges

There are **no confirmed import/provider edges to `rds_cli`**. All remaining
confirmed edges to `rds_quick`, including same-layer edges, are:

| Caller | Symbols | Why retained in this behavior-preserving cut |
|---|---|---|
| `rds_owned_advisor` (L3) | `choice` | Uses scope/coverage, method review, dependency and goal-contribution policy; calling it SHARED/L1 would mislabel L3 behavior. |
| `rds_owned_history` (L1) | `choice` | `prepare_decision` combines policy selection with history capture. Must separate the selected evidence packet from the kernel checkpoint writer, not move Advisor policy below the kernel. |
| `rds_tools` (L1) | `execute` | QUICK owns nested source/parent admission closures capturing locks, contracts, prepared review and materialization state. Extracting only the function name leaves the same globals/callback dependency; splitting requires an explicit execution-service boundary and equivalence tests. |
| `rds_cli` (L4) | `record_choice`, `brief`, `execute`, `reject_route`, `compact_brief_planning`, `serialize_brief` | Same-layer compatibility entry dispatch, not an upward violation. Pure/value and recording names are retained as aliases. |

CLI executable paths, command suggestions and `engine_id` file bindings are
retained deliberately; they are file/process references, not in-process import
edges. Dynamically replaced providers and computed plugin modules remain unknown,
not certified absent. B1 mechanical extraction is implemented; complete central
policy/execution decoupling is **not** claimed. The next cut needs a lock-external
decision packet and lock-internal stale/binding checks before execution rewiring.

## B2: production readonly selectors

Workspace admission now opens its committed identity/progress reads through
`rds_sqlite.readonly_connection` rather than `ProjectStore._db`. Ancestor
discovery and paid QUICK scope checks use the same connection boundary; effective
child contracts and owner revision history retain the existing verified readers.
The platform helper uses `mode=ro`, `query_only` and the original timeout, closes
on failure, and does not select WAL, migrate, or change the writer connection.
Pending writer events must not hide a rolled-back committed ledger.

The legacy admission API still returns a `ProjectStore`, and CAS still invokes
both campaign and workspace guards. This is a read-path extraction, not complete
Store removal, lock-free publication, or a finished bus. Binding, mutation locks,
pointer anchoring, original event names and cumulative accounting are unchanged.

Campaign marker admission also uses leaf JSON/value helpers and the verified
`rds_contract_read` lineage reader directly. Its unbound and bound read paths do
not import Store, method-revision writers, Advisor, QUICK or CLI. This does not
remove Campaign's writer-side Store/revision dependencies: binding and recovery
retain their original implementations. Damaged genesis and missing identity
events still refuse admission without repairing or rewriting the ledger.

`rds_project_read_services` (L1) borrows the original connection and selects
contract, state, checkpoint-history and controller records without opening a
second database or committing. Store run/status reads, owned Advisor state,
owned-history coverage, activation state, steering and controller event reads
use it. Store retains the original contract/run/receipt decoder entry points;
checkpoint, lineage, controller and completion validators are still enforced.

The existing `ProjectReadPort` now participates in real budget reads. These
compatibility reads compute scalar-size caps inside the caller's snapshot so
old contracts with more than 64 resource dimensions remain readable. Explicit
new bounded queries still use the port's caller-selected fixed limits. This
cut does not impose a new global legacy body limit or create another ledger.

Original completion events, exposures, cumulative spent/charged/reserved values,
method lineage and marked before/after checkpoints remain part of the same
state. The new service never repairs retained records or evaluates Advisor
policy. Lower-to-upper admission callbacks remain for the B3 cut.

The five metrics remain **29 upward pairs / largest SCC 48 / 48 mutual pairs /
8 unresolved dynamic sites / 0 unclassified edges**. Seven new downward/leaf
pairs adopt the read boundary; no upward pair is added or removed. No business
schema, events, migrations or budget resets are introduced. CLI engine identity
includes both read-service sources so changed code cannot reuse old identity.

## B1a: reference state no longer lives in the CLI

`rds_state` (L1) owns the existing `RDSState` implementation; its SQLite schema,
transaction boundaries, campaign/workspace checks and budget invariants are
unchanged. `rds_reference_values` (SHARED) owns the original strict JSON parser,
byte-or-JSON digest and reference-version/resource constants. It depends only on
the standard library and `rds_core`, not the CLI or ProjectStore.

The CLI re-exports the old public names. Its small `digest` compatibility wrapper
still uses the CLI `canonical` seam. The build-version literal is retained for
packaging, with a test requiring equality to the shared version. Engine identity
now includes both extracted files: old data stays readable, but changed code
does not reuse an old execution identity.

Seven lower consumers now import these services directly: `rds_adversary`,
`rds_campaign`, `rds_math`, `rds_project_lifecycle`, `rds_tools`,
`rds_tool_compare` and `rds_workspace`. There are no remaining confirmed
L0-L3 import/provider edges to `rds_cli`. CLI strings used as subprocess entry
points or diagnostic suggestions are not in-process upward imports.

| Measured stage | Upward pairs | Largest cyclic SCC | Mutual pairs |
|---|---:|---:|---:|
| Original B0 labels | 66 | 52 | 54 |
| B0.5 responsibility labels | 51 | 52 | 54 |
| B1a reference state/value extraction | 44 | 45 | 50 |

B1a has 121 modules and 454 combined dependency pairs. This is extraction,
not a claim that the whole graph becomes smaller: new downward service edges
replace old entry-point edges. QUICK dependencies remain for the next cut.
No business schema or event changes, data migration, or accounting reset.

## B1b: guarded CAS publication

`rds_cas` (L1) now owns `cas_bytes` and `cas_json`; QUICK re-exports both.
Thirteen consumers use the kernel service directly. Mutation locking, campaign
and workspace admission, byte limits, content hashes and existing-file integrity
checks are preserved. This is a guarded publisher, not an unprivileged SHARED
helper. It has no CLI/QUICK import, and engine identity covers the new file.

With the same classification, upward pairs are **34**, mutual pairs **48**,
and the largest SCC **46** (122 modules, 463 combined pairs). The SCC grew by
one versus B1a because the new CAS module still participates in the existing
ProjectStore/campaign/workspace component; extraction is not cycle elimination.
Eight lower modules still reach QUICK: campaign, workspace, project and hooks
for allowance/recovery; owned_advisor and owned_history for choice validation;
tools for execution/allowance; tms_store for persisted brief output.

No schema or events change. Tests that inject CAS failures/results must patch
the new service lookup site; public old function names are retained, but their
module-global namespace is not a mutable proxy to every consumer.

## B1c: QUICK allowance and recovery service

`rds_quick_ledger` (L1) contains the original eleven allowance/checkpoint/pending
materialization/completion helpers. Function bodies, signatures and decorators
are unchanged. Campaign, workspace and tools import their helpers directly.
The default QUICK lifecycle provider now loads/registers this service, not the
entry module: ProjectStore recovery no longer imports QUICK or selects a route.
All old QUICK helper names remain re-exported.

The service still uses the existing parent transaction, original request/CAS
bindings, cumulative budget and checkpoints. It neither creates another ledger
nor refunds an allowance during retry. Runtime-injected provider identities
remain unknown to static analysis; this cut changes the default provider only.

Current measurement: **30 upward pairs, largest SCC 47, 48 mutual pairs**;
123 modules, 473 combined pairs. Four lower QUICK edges remain: owned_advisor
and owned_history use `choice`, tools uses `execute`, and tms_store uses `brief`.
The new kernel service is inside the old kernel SCC, so SCC size is reported
honestly rather than treated as a monotone extraction score. No schemas/events
change. The recovery/allowance/admission suite covers retained choices, parent
locks, original receipts, interrupted materialization and no duplicate charging.

## B1d: record publication and pure output helpers

`rds_result_record` (L1) owns the original `brief` function: publish the complete
CAS record, then read ledger/checkpoint information for its bounded digest.
`rds_output_values` (SHARED) owns `_brief_move`, `serialize_brief` and
`compact_brief_planning`; these have standard-library-only imports and perform
no ledger reads or writes. QUICK re-exports all four unchanged implementations,
and TMS imports the recording service rather than the entry point. Both new
files participate in engine identity. No schema/event or presentation contract
changes are introduced by the extraction.

## Reproduce

```sh
python -B tools/import_graph.py --root . > import-graph.json
python -B -m unittest discover -s tests -p test_import_graph.py -v
```

The stdlib-only tool parses `scripts/**/*.py` without importing those modules.
It emits versioned JSON to stdout, including recognized local edge sites, source hash,
responsibility label, unresolved loader, SCC, and mutual pair. Keep generated
reports outside tracked source. A failed parse or empty tree exits 2, not success.

## Original B0 Snapshot

- Inspected source commit: `64daa9d0607bbad7ed37817740a225e6625d48fb`.
- Source-set SHA-256: `3e7e4b5b9bef00a16d1c9b84a2a99d7ad0c232b3394ef7f0cf6a2f7bdf6d7bdb`.
- 119 Python modules; 431 distinct static local edges.
- 445 combined local edges at 704 sites, including declared dynamic targets and provider calls.
- 54 mutual pairs, 3 cyclic SCCs, 66 classified upward pairs.
- 8 unresolved dynamic sites; 0 unclassified edge sites in this snapshot.

Do not compare these counts with the old 106-module / 50-pair inventory as if
they used the same source or graph definition. A mutual pair is not an SCC.

## B0.5 Responsibility Audit

Only `GROUPS` labels and this document change in this step. All 119 runtime
source hashes, 445 dependency pairs, 704 sites and unresolved targets are
identical to B0. Being widely imported is not itself a reason to lower a module;
the functions below establish its actual responsibility.

| Module | Before -> after | Reason in current code |
|---|---|---|
| `rds_artifacts` | L2 -> L1 | `ingest_manifest` bounds and verifies original artifacts; `ArtifactFact` carries importer provenance used by kernel checks, not research planning. |
| `rds_contract_read` | L2 -> L1 | `effective_contract` and `contract_history` validate ledger/CAS lineage, including activation transitions. This is the kernel's contract reader. |
| `rds_project_read_port` | L2 -> L1 | `ProjectReadPort` borrows a SQLite transaction and issues capped SELECTs for raw ledger fields; it is not yet a research-state service. |
| `rds_owned_history` | L2 -> L1 | `capture_choice` / `capture_completion` append checkpoints in the caller's transaction; this mixed module is not merely a read projection. |
| `rds_source_documents` | L2 -> L1 | `SourceDocument` parses and validates operational receipt/result fields shared by artifact admission; no research strategy or graph assembly. Kept with the L1 cost contract it consumes, not marked as a dependency-free leaf. |
| `rds_probe` | L2 -> L1 | `execute`, `formal_probe` and certificate checks form the bounded reference runner/verifier, not an Advisor suggestion. |
| `rds_tool_applicability` | L2 -> L1 | `validate_bindings` and `inspect` check exact-task qualification, original assets and retained receipts. Its Advisor callback is still an upward violation. |
| `rds_project_lifecycle` | L4 -> L1 | `initialize` and `enable_advisor` implement same-ledger policy activation, locks and event writes. Formatting helpers do not make this a CLI-only entrypoint. |
| `rds_hypergraph_archive` | L2 -> L4 | `build_archive_display` / `render_archive_html` build display-only archive relations; the module explicitly excludes inference and imports the UI renderer. It is not the research hypergraph projection. |

L2 retains `rds_hypergraph`, `rds_discrimination` and graph adapters: they derive
research relationships or observations. Their mixed I/O and upward calls remain
visible; relabelling them merely to reduce a count would conceal real work.
Likewise CLI/QUICK stay L4 until their reusable state/helpers are extracted.

| Metric | B0 labels | B0.5 labels | Runtime change |
|---|---:|---:|---|
| Upward module pairs | 66 | 51 | None: 17 false classifications removed, 2 newly exposed |
| Largest cyclic SCC | 52 | 52 | None |
| Mutual dependency pairs | 54 | 54 | None |

The newly exposed violations are `rds_project_lifecycle -> rds_cli` and
`rds_project_lifecycle -> rds_owned_advisor`. The first needs state/helper
extraction; the latter is a real policy-validation callback, not a label fix.
No dependency is removed and no runtime behavior, schema or event changes.

## Responsibility labels

Labels in `tools/import_graph.py:GROUPS` make the current mixed files auditable.
They describe responsibility, not proof that a module already obeys its layer.
For example, `rds_project` still owns platform process/SQLite code as well as
domain policy. `rds_cli` still contains lower-level helpers and `RDSState`.

| Label | Current inventory responsibility |
|---|---|
| L0 | Platform I/O primitives and cross-process mutation locking |
| L1 | Ledger reads/writes, admission, settlement, evidence/receipt checks and certificate kernels |
| L2 | Derived research relationships, state projections and diagnostic data |
| L3 | Advisor, proposals, search, predictions and research policies |
| L4 | CLI/QUICK/worker, presentation and existing assembly entrypoints |
| SHARED | Leaf value/decoding/arithmetic contracts; no platform ownership |

SHARED is a leaf contract label, not a sixth service layer or exemption:
its imports into numbered layers are violations too. Unmapped new modules
are UNKNOWN and appear in `unclassified_edges`, never silently treated as L0.

There is no unified application composition root yet. Existing
`rds_project_assembly` compiles declarations and delegates initialization;
`rds_verify` assembles verifier adapters. These are not interchangeable with
a bus that owns all entrypoints, persistence and recovery.

## All Classified Upward Pairs After B0.5

Each row lists every observed site for that pair. A static import inside a
function or conditional remains an edge; moving it there does not remove it.
The JSON `kind` distinguishes syntax, dynamic candidates and provider callbacks.

| From | To | Source lines |
|---|---|---|
| `rds_adversary` (L3) | `rds_cli` (L4) | [125](../scripts/rds_adversary.py#L125) |
| `rds_advisor_search` (L3) | `rds_capabilities` (L4) | [763](../scripts/rds_advisor_search.py#L763) |
| `rds_advisor_workset` (L3) | `rds_quick` (L4) | [10](../scripts/rds_advisor_workset.py#L10) |
| `rds_autonomy` (L3) | `rds_autonomy_worker` (L4) | [78](../scripts/rds_autonomy.py#L78) |
| `rds_autonomy` (L3) | `rds_quick` (L4) | [318](../scripts/rds_autonomy.py#L318), [387](../scripts/rds_autonomy.py#L387) |
| `rds_campaign` (L1) | `rds_autonomy` (L3) | [455](../scripts/rds_campaign.py#L455) |
| `rds_campaign` (L1) | `rds_cli` (L4) | [659](../scripts/rds_campaign.py#L659) |
| `rds_campaign` (L1) | `rds_hypergraph` (L2) | [497](../scripts/rds_campaign.py#L497) |
| `rds_campaign` (L1) | `rds_owned_advisor` (L3) | [419](../scripts/rds_campaign.py#L419), [462](../scripts/rds_campaign.py#L462) |
| `rds_campaign` (L1) | `rds_quick` (L4) | [391](../scripts/rds_campaign.py#L391) |
| `rds_checkpoints` (L1) | `rds_advisor` (L3) | [41](../scripts/rds_checkpoints.py#L41), [77](../scripts/rds_checkpoints.py#L77) |
| `rds_discrimination` (L2) | `rds_advisor_search` (L3) | [13](../scripts/rds_discrimination.py#L13) |
| `rds_domain_confirmation` (L1) | `rds_owned_advisor` (L3) | [180](../scripts/rds_domain_confirmation.py#L180) |
| `rds_guard` (L1) | `rds_advisor` (L3) | [66](../scripts/rds_guard.py#L66), [144](../scripts/rds_guard.py#L144) |
| `rds_jump` (L3) | `rds_quick` (L4) | [84](../scripts/rds_jump.py#L84) |
| `rds_math` (L1) | `rds_cli` (L4) | [30](../scripts/rds_math.py#L30) |
| `rds_math` (L1) | `rds_quick` (L4) | [12](../scripts/rds_math.py#L12) |
| `rds_method_revision` (L1) | `rds_autonomy` (L3) | [69](../scripts/rds_method_revision.py#L69) |
| `rds_method_revision` (L1) | `rds_owned_advisor` (L3) | [106](../scripts/rds_method_revision.py#L106) |
| `rds_owned_advisor` (L3) | `rds_quick` (L4) | [507](../scripts/rds_owned_advisor.py#L507) |
| `rds_owned_history` (L1) | `rds_quick` (L4) | [96](../scripts/rds_owned_history.py#L96) |
| `rds_owned_tools` (L1) | `rds_owned_advisor` (L3) | [154](../scripts/rds_owned_tools.py#L154) |
| `rds_postcommit_confirmation` (L1) | `rds_quick` (L4) | [140](../scripts/rds_postcommit_confirmation.py#L140) |
| `rds_project` (L1) | `rds_advisor_search` (L3) | [543](../scripts/rds_project.py#L543) |
| `rds_project` (L1) | `rds_autonomy` (L3) | [492](../scripts/rds_project.py#L492), [1189](../scripts/rds_project.py#L1189) |
| `rds_project` (L1) | `rds_feasibility` (L3) | [495](../scripts/rds_project.py#L495), [1171](../scripts/rds_project.py#L1171) |
| `rds_project` (L1) | `rds_owned_advisor` (L3) | [395](../scripts/rds_project.py#L395), [412](../scripts/rds_project.py#L412), [426](../scripts/rds_project.py#L426), [957](../scripts/rds_project.py#L957), [1712](../scripts/rds_project.py#L1712) |
| `rds_project` (L1) | `rds_quick` (L4) | [1665](../scripts/rds_project.py#L1665), [1675](../scripts/rds_project.py#L1675) |
| `rds_project_hooks` (L1) | `rds_owned_advisor` (L3) | [20](../scripts/rds_project_hooks.py#L20) |
| `rds_project_hooks` (L1) | `rds_quick` (L4) | [20](../scripts/rds_project_hooks.py#L20) |
| `rds_project_lifecycle` (L1) | `rds_cli` (L4) | [67](../scripts/rds_project_lifecycle.py#L67) |
| `rds_project_lifecycle` (L1) | `rds_owned_advisor` (L3) | [194](../scripts/rds_project_lifecycle.py#L194), [352](../scripts/rds_project_lifecycle.py#L352) |
| `rds_rsi_confirmation` (L1) | `rds_meta` (L3) | [6](../scripts/rds_rsi_confirmation.py#L6) |
| `rds_steering` (L1) | `rds_dialogue` (L4) | [254](../scripts/rds_steering.py#L254) |
| `rds_steering` (L1) | `rds_quick` (L4) | [126](../scripts/rds_steering.py#L126), [260](../scripts/rds_steering.py#L260) |
| `rds_structure` (L3) | `rds_quick` (L4) | [19](../scripts/rds_structure.py#L19) |
| `rds_theory_progression` (L1) | `rds_operators` (L3) | [161](../scripts/rds_theory_progression.py#L161), [386](../scripts/rds_theory_progression.py#L386) |
| `rds_tms_store` (L1) | `rds_hypergraph` (L2) | [10](../scripts/rds_tms_store.py#L10) |
| `rds_tms_store` (L1) | `rds_quick` (L4) | [13](../scripts/rds_tms_store.py#L13) |
| `rds_tool_applicability` (L1) | `rds_advisor_search` (L3) | [147](../scripts/rds_tool_applicability.py#L147) |
| `rds_tool_applicability` (L1) | `rds_owned_advisor` (L3) | [146](../scripts/rds_tool_applicability.py#L146) |
| `rds_tool_calls` (L1) | `rds_owned_advisor` (L3) | [166](../scripts/rds_tool_calls.py#L166) |
| `rds_tool_calls` (L1) | `rds_quick` (L4) | [10](../scripts/rds_tool_calls.py#L10) |
| `rds_tool_compare` (L1) | `rds_cli` (L4) | [50](../scripts/rds_tool_compare.py#L50) |
| `rds_tool_compare` (L1) | `rds_quick` (L4) | [13](../scripts/rds_tool_compare.py#L13) |
| `rds_tool_workbench` (L1) | `rds_owned_advisor` (L3) | [121](../scripts/rds_tool_workbench.py#L121) |
| `rds_tool_workbench` (L1) | `rds_quick` (L4) | [122](../scripts/rds_tool_workbench.py#L122) |
| `rds_tools` (L1) | `rds_cli` (L4) | [123](../scripts/rds_tools.py#L123) |
| `rds_tools` (L1) | `rds_quick` (L4) | [19](../scripts/rds_tools.py#L19) |
| `rds_workspace` (L1) | `rds_cli` (L4) | [144](../scripts/rds_workspace.py#L144), [263](../scripts/rds_workspace.py#L263), [373](../scripts/rds_workspace.py#L373), [412](../scripts/rds_workspace.py#L412) |
| `rds_workspace` (L1) | `rds_quick` (L4) | [508](../scripts/rds_workspace.py#L508) |

## Important non-lexical coupling

These source-observed bindings supplement the generated import graph. They
are not claims that the corresponding paths executed in a live experiment.

| Binding | Concrete source evidence |
|---|---|
| Store -> Advisor provider | [alias](../scripts/rds_project.py#L28), [prepare](../scripts/rds_project.py#L395), [locked check](../scripts/rds_project.py#L412), [state](../scripts/rds_project.py#L1712); [default mapping](../scripts/rds_project_hooks.py#L7) and [registration](../scripts/rds_owned_advisor.py#L814) |
| Store -> QUICK provider | [recovery](../scripts/rds_project.py#L1665), [registration](../scripts/rds_quick.py#L1488); included as `provider_callback` candidate edges |
| Store -> injected QUICK callbacks | [binding](../scripts/rds_quick.py#L1180), [execution](../scripts/rds_project.py#L1273), [locked execution](../scripts/rds_project.py#L1287); not resolved by the generic import scanner |
| Store -> worker process entry | [worker filename/argv](../scripts/rds_project.py#L1898), [task action](../scripts/rds_project.py#L1903), [worker -> Store](../scripts/rds_project_worker.py#L16); process coupling, not a Python import |
| BuiltinVerifier -> injected ProofRule methods | [method delegation](../scripts/rds_builtin_verifiers.py#L38), [dynamic load](../scripts/rds_verify.py#L42), [installation](../scripts/rds_verify.py#L115); finite declarations are candidate edges, not a runtime registry census |
| Generated tool driver -> supplied source | [driver template](../scripts/rds_tool_application.py#L27), [exec](../scripts/rds_tool_application.py#L51); supplied source and its imports are outside this graph |

B2 adopts `ProjectReadPort` through `ProjectReadServices.budget` in Store and
owned Advisor reads. Store and Advisor retain their original full receipt/run
validators; a raw genesis or run projection is not substituted for them.

## Unresolved dynamic sites

| Source | Expression |
|---|---|
| [rds_capabilities:38](../scripts/rds_capabilities.py#L38) | `importlib.import_module(name)` |
| [rds_project_hooks:20](../scripts/rds_project_hooks.py#L20) | `import_module(_modules[name])` |
| [rds_verify:42](../scripts/rds_verify.py#L42) | `importlib.import_module(self.module)` |
| [rds_verify:48](../scripts/rds_verify.py#L48) | `importlib.import_module(self.module)` |
| [rds_verify:52](../scripts/rds_verify.py#L52) | `importlib.import_module(self.module)` |
| [rds_verify:324](../scripts/rds_verify.py#L324) | `importlib.import_module(step['rule'].module)` |
| [rds_verify:645](../scripts/rds_verify.py#L645) | `importlib.import_module(rule.module)` |
| [rds_verify:907](../scripts/rds_verify.py#L907) | `importlib.import_module(rule.module)` |

The hook dictionary supplies two default candidate targets, but `register()`
can replace providers. `ProofRule` declarations supply finite builtin targets;
runtime objects and extensions remain unresolved. Module-name strings alone
are recorded separately and never converted into import edges.

## Cyclic components

- `rds_adversary`, `rds_advisor`, `rds_advisor_coverage`, `rds_advisor_search`, `rds_advisor_workset`, `rds_autonomy`, `rds_autonomy_worker`, `rds_campaign`, `rds_checkpoints`, `rds_cli`, `rds_continuation`, `rds_dialogue`, `rds_discrimination`, `rds_domain_confirmation`, `rds_experiments`, `rds_feasibility`, `rds_frontier`, `rds_frontier_proposals`, `rds_global_planning`, `rds_graph_ranker`, `rds_guard`, `rds_host_hook`, `rds_hypergraph`, `rds_hypergraph_input`, `rds_jump`, `rds_math`, `rds_meta`, `rds_method_revision`, `rds_owned_advisor`, `rds_owned_history`, `rds_owned_tools`, `rds_postcommit_confirmation`, `rds_project`, `rds_project_assembly`, `rds_project_hooks`, `rds_project_lifecycle`, `rds_proof_cache`, `rds_quick`, `rds_rsi`, `rds_rsi_confirmation`, `rds_search_allocation`, `rds_search_neighborhood`, `rds_steering`, `rds_structure`, `rds_tms_store`, `rds_tool_applicability`, `rds_tool_application`, `rds_tool_calls`, `rds_tool_compare`, `rds_tool_workbench`, `rds_tools`, `rds_workspace`
- `rds_affine_toolchain`, `rds_probe`, `rds_scalar_verify`, `rds_theory_progression`, `rds_verify`
- `rds_hypergraph_archive`, `rds_hypergraph_view`

## Coverage limits

- This is a conservative syntax inventory, not whole-program call analysis.
- Import aliases respect lexical scopes, with ambiguous rebindings unresolved;
  comprehension targets stay local (except assignment expressions), while
  preceding unconditional imports in immediate module/class code resolve in
  source order. Deferred functions retain conservative global lookup. Other
  class-body shadowing conservatively retains outer loader alternatives.
  global/nonlocal mutation and arbitrary computed loaders need runtime analysis.
  Conditional edges may never run. Class bodies are not method closure scopes.
- Immediate module calls do not borrow a loader's first import from later code.
  Eager list/set/dict comprehensions retain this order; generator bodies and
  function bodies keep deferred lookup. Exception matching precedes its alias.
  Definition defaults/bases and the first loop iterable precede their own name
  bindings; repeated loop-body mutations remain conservative UNKNOWN.
  Comprehension filters run before their result expressions, independent of
  textual position. A loader name mutated within that comprehension remains
  UNKNOWN (including conditional mutations and repeated iterations); its first
  iterable still uses the preceding outer binding. Unrelated mutations do not
  invalidate a known loader. This is not a proof of which branch ran.
  Named loader re-exports through local module imports are explicitly UNKNOWN,
  not definite edges. Alias walks are capped at 64 expansions; cycles and limit
  exhaustion stay UNKNOWN. Relative aliases use the inspected package path.
  Arbitrary computed exports are not resolved.
- Generator-expression assignment targets affect an outer name only on iteration.
  This inventory does not prove whether a generator was consumed; possible loader
  mutation remains an explicit UNKNOWN site, not a definite rebinding or a claim
  that no dependency exists. Use runtime evidence to disambiguate these sites.
- Bare annotations preserve module/class values but still declare function
  locals. Completed module classes participate in ordered declaration lookup.
  For Python 3.11-3.13, postponed annotations and function-local variable
  annotations contribute string references, not executable loader sites.
  Without postponement, module/class attribute, subscript and parenthesized-name
  annotations are evaluated, although their values are not stored in
  `__annotations__` ([Python reference](https://docs.python.org/3.13/reference/simple_stmts.html#annotated-assignment-statements)).
  Eager signature annotations, defaults, assigned values and bodies remain scanned.
  Reflective annotation evaluation (for example, `get_type_hints`) is not modeled.
- Literal registry candidates require an unambiguous binding to that exact
  module assignment, not just a matching variable name. Local shadowing or
  ambiguous rebinding stays UNKNOWN; these candidates are not runtime values.
- External dependencies are not nodes in this repository-local graph. Imports
  in generated code, plugins or launched programs need separate inventories.
- Relative static package imports are resolved against the source tree; dynamic
  relative imports and opaque file loaders stay unresolved.
- Source-root-qualified `scripts.<local module>` imports map to the same names
  as direct imports, including the native graph fallback. An inspected local
  `scripts` package is not stripped, and missing root modules cannot become
  stdlib loader identities. Dotted imports include existing package initializer
  dependencies; namespace packages do not invent files. Prefixes are deduplicated
  per syntax site and do not introduce implicit self-imports. Runtime `sys.path`
  manipulation and module-cache replacement remain outside this static model.
- Literal `__import__` fromlist members matching local modules are candidates;
  computed lists and wildcard exports stay unresolved alongside the base edge.
- Literal `runpy.run_module` names are dynamic edges; computed names and
  `runpy.run_path` file execution stay unresolved. No inspected code is executed.
- A returned graph with unresolved sites is usable inventory, not a PASS for
  dependency direction. Zero discovered edges would not prove zero callbacks.
- B0 removes **zero** runtime edges. The table is the baseline for subsequent
  read-side/admission/composition changes, not evidence those changes landed.

## Production attempt notifications

`ProjectStore` now emits existing v1 outbox envelopes in the same database
transaction as the attempt transition or receipt settlement. `JOB_STARTED`
means the attempt entered RUNNING, not that child launch succeeded. Completion
emits `RESULT_READY`, `JOB_FAILED`, or an observed `RESOURCE_LIMIT`, bound to the
original receipt digest. Exit zero with missing outputs remains failure; result
availability never promotes scientific support. An event-write failure rolls
back that transaction. Recovery reuses existing receipts without a second event
or settlement. Read-only inspection does not create outbox tables or backfill
old receipts. Legacy non-UUID attempt identities settle unchanged, without a
v1 notification; their notification coverage is incomplete. This is a downward
L1 project-to-outbox dependency, not completion of the remaining Advisor/hook
cycle cuts or a guarantee of host-visible exactly-once notification.
