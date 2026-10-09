# Public command map

Problem-model exploration uses `structure request|propose|next|drive|advance|feedback|activate|rollback|list|recover`.
It requires an initialized project and current TMS, uses existing execution authority and budget,
and returns open Agent tasks or independently checked observations. See [problem structure](problem-structure.md).

Use this map when the short Skill route table does not cover the task. It covers all public leaf command paths in `scripts/rds_cli.py`'s current `parser()`, plus relevant Advisor options and documented helper entry points. Aliases do not add capabilities. This is a discovery map, not a requirement to invoke every function.

| Command | Behavior | Prerequisite | Guide |
| --- | --- | --- | --- |
| `structure request` | Return bounded open Agent tasks | Initialized project and saved dependencies | [Problem structure](problem-structure.md) |
| `structure propose` | Retain an experimental topology branch | Current request, sourced testable proposal and allowed manifests | [Problem structure](problem-structure.md) |
| `structure next` | Consume feedback and select a distinguishing test | Same ledger and remaining budget | [Problem structure](problem-structure.md) |
| `structure drive` | Bounded proposal/execution/feedback handoff | Existing admission and `--steps` in 1..8 | [Problem structure](problem-structure.md) |
| `structure advance` | Run or recover candidate/verifier once | Retained `--id` and existing project execution authority | [Problem structure](problem-structure.md) |
| `structure feedback` | Bind independent observations to originals | Actual receipts/results and matching scope/hash | [Problem structure](problem-structure.md) |
| `structure activate` | Publish experimental topology | Supporting independent observation, unchanged goal, idle runs | [Problem structure](problem-structure.md) |
| `structure rollback` | Restore original decomposition | Matching activation ancestry, retaining fresh owned facts | [Problem structure](problem-structure.md) |
| `structure list` | Inspect retained branches and costs | Existing project | [Problem structure](problem-structure.md) |
| `structure recover` | Reconcile interrupted controller cost once | Controller known dead; never steal live work | [Problem structure](problem-structure.md) |

```text
skill_dir := directory containing SKILL.md
CLI := python -B "<skill_dir>/scripts/rds_cli.py" --root "<user-project>"
REBIND printed scripts/rds_cli.py command prefixes TO CLI; preserve remaining arguments
SELECT one relevant entry; READ its linked guide and CLI <command path> --help
SUPPLY the actual project inputs, authorized budget, and required installed backend
KEEP records in user-project; never initialize a ledger in the installation/cache
IF unavailable or unsupported: report that boundary; do not substitute a claimed result
```

Global `--version` reports the installed version; `--help` lists the installed command tree. Help and version do not establish execution or scientific success. Help is also recorded by the optional local usage logger. Paths below are canonical command paths, followed by their arguments. Distinguish the scalar reference ledger (`state.sqlite3`) from the project ledger (`project.sqlite3`); they are not interchangeable.

## Research choice, evidence and dependencies

| Command | Purpose | Inputs and prerequisites | Guide |
| --- | --- | --- | --- |
| `advise` | Review evidence and propose a next decision | Policy-bound projects use the frozen policy and owned graph, without caller context/graph/choice overrides; legacy modes below accept sourced inputs | [Owned Advisor](program-owned-advisor.md), [Agent entry](agent-entry.md), [Advisor design](advisor-graph-design.md) |
| `artifacts import` | Import original records with field-level provenance | `--manifest` binding source files, hashes, identities and selectors; observations do not certify mechanisms | [Evidence import](development-loop.md) |
| `hypergraph` | Maintain and inspect bounded AND/OR dependencies | Initial `--input`, saved map, or bounded `--update`/`--declare`; retract/refute flags, `--trace-cone`, `--audit-files`, `--audit-receipts` are available | [TMS](truth-maintenance.md), [Evidence binding](hypergraph-evidence.md) |
| `reject` | Record a scoped rejection of the current route | Existing choice, `--reason`, `--evidence`; optional `--route` checks identity and `--domain` declares scope | [Rejection](agent-entry.md) |
| `guard` | Check a frozen comparable-metric or milestone policy | `--policy` and its bound records; evaluation does not replace the incumbent | [Guard implementation](../scripts/rds_guard.py), [Entry](agent-entry.md) |
| `checkpoint save` | Preserve a decision and state snapshot | `--id`; decision workflow uses `--decision`; `--kind` selects project/reference when needed | [Continuation](development-loop.md), [Local decisions](lightweight-workflow.md) |
| `checkpoint restore` | Reconcile a checkpoint with live state | Existing `--id`, correct ledger; restoration neither refunds spent budget nor relaunches work | [Continuation](development-loop.md) |
| `advancement score` | Score recorded intervention predictions | `--protocol`, `--trajectories`; evaluator-only `--confirmations` if available; unmeasured outcomes remain unmeasured | [Advancement protocol](advisor-advancement.md) |

## Frozen project execution and costs

| Command | Purpose | Inputs and prerequisites | Guide |
| --- | --- | --- | --- |
| `exec` | Freeze and run one authorized tool job | Explicit command after `--`, bounded `--timeout`, inputs and expected `--output`; prospective selection pairs `--context` with `--ledger` | [Command wrapper](agent-entry.md), [Execution policy](execution-policy.md) |
| `project init` | Lock a project execution contract | `--contract` with real bindings, commands, outputs and authorized budget | [Contract](project-contract.md), [Project runner](development-loop.md) |
| `project plan` | Form an inspectable draft with grouped missing inputs | Optional `--intent`; `--output` never overwrites; `--save-as` uses an initialized project's checkpoints | [Planning and steering](planning-and-steering.md) |
| `project steering` | Inspect current direction, resources and active-work dispositions | Initialized project | [Planning and steering](planning-and-steering.md) |
| `project steer` | Receive current-user pause, route priority, hypothesis or material-change request | Exact current contract/revision in `--request`; host attestation `--user-directed --source` | [Planning and steering](planning-and-steering.md) |
| `project improve` | Prepare diagnostics, editable tool code and a revision proposal | Frozen `method_evolution`, authorized `--code-path` and bounded `--id`; no adoption or child launch | [Tool improvement](predictive-feasibility.md) |
| `project revise` | Adopt or resume a bounded executable method change in the same ledger | `--proposal`, unchanged goals/evaluator/budget/commands, idle attempts and exact parent identity | [Tool improvement](predictive-feasibility.md) |
| `project create` | Register an execution and reserve resources | Initialized project and `--manifest` matching its contract | [Project runner](development-loop.md) |
| `project execute` | Execute a registered project run | Existing `--id`, valid bindings and resources; `--background` is the authorized Windows scheduler path | [Project runner](development-loop.md) |
| `project recover` | Reconcile an existing interrupted attempt | Existing `--id`; inspect retained state and artifacts rather than duplicating the attempt | [Project runner](development-loop.md) |
| `project next` | Derive the next campaign step | Initialized project ledger; resolve actual inputs in any emitted command | [Project next implementation](../scripts/rds_project.py), [Development loop](development-loop.md) |
| `project advance` | Execute one program-selected route and receive its results | Frozen `advisor_policy`; optional `--brief` or authorized `--background`; collection failures require recovery, not rerunning completed work | [Owned Advisor](program-owned-advisor.md) |
| `project drive` | Recover and drive a bounded research loop; request, verify and adopt a different method/tool on obstacles | Frozen `advisor_policy.autonomy`; pass limit plus original cumulative steps/budget/deadline | [Research drive](autonomy-loop.md), [domain confirmation](domain-confirmation.md) |
| `project compare` | Compare recorded control/treatment arms | Eligible completed arms and precommitted primary metric/useful-delta declaration | [Project comparison](../tests/test_rds_project_next.py) |
| `project status` | Inspect current runs, budget and receipts | Existing project; `--brief` gives bounded state plus a saved-record locator | [Output](agent-entry.md) |
| `project costs` | Inspect measured costs and charged estimates | Existing project records; failed attempts and unknown resource values remain visible | [Cost accounting](development-loop.md) |
| `project control-check` | Check whether a completed control is reusable | `--candidate` receipt and `--current` expected operation/protocol; source and output identity must match | [Control reuse](../examples/project-runner/README.md) |

## Native mathematical assets and local tools

For repeated JSON/metric/failure reading, [result tools](result-tools.md) provide
three data-only functions using the existing extraction and owned-consumer commands below.

| Command | Purpose | Inputs and prerequisites | Guide |
| --- | --- | --- | --- |
| `math bind` | Freeze the original mathematical objective | `--objective` with original statement, quantifiers, assumptions and completion standard | [Native research](native-research.md) |
| `math add` | Retain a scoped research asset | Bound objective, `--id`, `--file`; optional `--kind` and `--depends` | [Native research](native-research.md) |
| `math get` | Retrieve an exact retained asset | Existing asset `--id`; retrieval checks stored identities | [Native research](native-research.md) |
| `math affected` | Inspect downstream dependency lineage | Existing asset `--id`; affected lineage is not automatic mathematical refutation | [Native research](native-research.md) |
| `math refute` | Retain a declared refutation and affected lineage | Existing `--id`, original `--reason` and `--evidence`; preserve previous records | [Native research](native-research.md) |
| `math status` | Inspect native objective/assets | Native project records; storage does not establish proof | [Native research](native-research.md) |
| `rsi list` | Discover retained local functions | Native tool records; optional exact `--name`; listing is not a fresh reuse check | [Local tools](native-research.md) |
| `rsi extract` | Extract a bounded local function candidate | `--source`, `--entry`, `--name`; supported AST and dependency closure | [Local tools](native-research.md) |
| `rsi validate` | Execute finite local qualification cases | Extracted `--name`, `--cases`, bounded timeout; supply `--ledger` when charging an existing research budget | [Local tools](native-research.md) |
| `rsi register` | Register an eligible validated local function | `--name`; `--validation` may be omitted only for one passing validation; rechecks bindings and receipt | [Local tools](native-research.md) |
| `rsi use` | Verify and optionally export a retained function | Registered `--name`; optional project `--output`; changed/refuted evidence blocks reuse | [Local tools](native-research.md) |
| `rsi prepare-application` | Prepare a qualified finite function for a current owned obligation | Before project init: exact tool, inputs/cases, goal/action, exported code, fixed driver/request, output and observation; returns bindings and argv without starting work | [Owned tool consumers](owned-tool-consumers.md) |
| `rsi compare` | Compare a tool revision on identical fixed-precision oracle cases | Extracted `--baseline`/`--candidate`; `--cases`, `--precision-key`, `--precision`; prospective `--min-speedup`; optional `--ledger` charges both checks; single measured walls only | [Fixed-precision comparison](native-research.md#compare-a-tool-revision-at-fixed-precision) |

## Mathematical verification

| Command | Purpose | Inputs and prerequisites | Guide |
| --- | --- | --- | --- |
| `formal rules` | List the installed trusted rule registry | No proof inputs; a methodology rule is not a trusted mathematical rule | [Formal framework](formal-verification.md) |
| `formal plan` | Inspect the trusted affine plan without generating a proof | `--spec` with direct `affine_fixed_point_synthesis`; optional `--output`, nonnegative `--max-work-units`; READY has assurance NONE and no certificate | [Formal framework](formal-verification.md) |
| `formal verify` | Generate and check a supported declaration | `--spec`; optional `--output`, `--no-cache`; `--max-work-units` only for direct affine synthesis, bypassing cache and incompatible with `--tactics`; budget excludes search/replay/time/memory; backend-specific dependencies apply | [Formal framework](formal-verification.md), [Native Lean](lean-native.md) |
| `formal check` | Replay an existing certificate | Matching `--spec` and `--certificate`; preserve PASS/FAIL/UNKNOWN and backend assurance separately | [Formal framework](formal-verification.md) |

The verifier guide maps declaration kinds to the installed scalar, matrix, neural/tensor, dynamics, geometric and native Lean/statistical adapters and their exact limits. Select the adapter from that guide; do not infer support from a roadmap or install all optional backends for an unrelated task.

## Scalar reference runner

These entries use the bounded reference contract and ledger, not a general project-training service.

| Command | Purpose | Inputs and prerequisites | Guide |
| --- | --- | --- | --- |
| `init` | Lock a scalar reference contract | `--contract` and its declared source/data/budget bindings | [Reference workflow](research-workflow.md), [Contract](../references/l3-state-machine.md) |
| `hypothesis add` | Register a scoped reference hypothesis | Initialized reference root and `--spec` | [Reference workflow](research-workflow.md) |
| `gate check` | Inspect reference-plan admission | `--plan` and matching current contract, hypothesis and evidence | [Reference workflow](research-workflow.md) |
| `plan create` | Recheck and reserve a reference plan | `--spec`; current admission, data-use and budget conditions | [Reference contract](../references/l3-state-machine.md) |
| `plan cancel` | Cancel an unstarted reference reservation | Existing `--id`; completed/running work is not refunded as unstarted work | [Reference workflow](research-workflow.md) |
| `run execute` | Execute an admitted reference plan | Existing `--id`; bound inputs and reserved allocation | [Reference workflow](research-workflow.md) |
| `run recover` | Reconcile an interrupted reference run | Existing `--id`; recovery does not silently start a replacement | [Reference workflow](research-workflow.md) |
| `data expose` | Record declared reference-data exposure | `--split`, `--purpose`, `--actor`, `--reason`; prior access remains relevant to independence | [Reference contract](../references/l3-state-machine.md) |
| `decide` | Assess an eligible recorded reference run | `--run`; retain separate execution, task-gain and mechanism judgments | [Reference workflow](research-workflow.md) |
| `status` | Inspect scalar reference state | Existing reference root; use `project status` for project execution records | [Reference workflow](research-workflow.md) |
| `branch status` | Inspect the active reference branch | Reference ledger; stagnation counters are scoped bookkeeping | [Branch implementation](../scripts/rds_cli.py) |
| `branch list` | List reference research branches | Reference ledger; these are not Git branches | [Branch implementation](../scripts/rds_cli.py) |
| `branch switch` | Select an existing reference branch | Existing `--id`; changes recorded active branch, not resource authority | [Branch implementation](../scripts/rds_cli.py) |
| `branch fork` | Record a new reference research branch | `--spec` with new ID, existing parent and rationale; preserve reference constraints | [Branch implementation](../scripts/rds_cli.py) |

## Rule proposals, evaluation and adoption

These operations act on research-method rules, not model weights. Use an explicitly chosen project graph for mutations; the default graph can point inside the installed Skill. Proposal, finite regression acceptance and independent research-policy improvement are separate.

| Command | Purpose | Inputs and prerequisites | Guide |
| --- | --- | --- | --- |
| `meta list-rules` | Inspect methodology nodes | Optional `--graph`; this is not the formal trusted-rule registry | [Rules](rule-obligations.md) |
| `meta validate-rule` | Validate a proposed rule structure | `--rule`; no self-signed scientific acceptance | [Rule acceptance](development-loop.md) |
| `meta evaluate-rule` | Replay original and proposed rules on cases | `--rule`, `--cases`, explicit `--graph`; valid partitions, protected assertions and bounded workload | [Rule acceptance](development-loop.md) |
| `meta apply-rule` | Adopt an eligible evaluated rule | `--rule`, explicit `--graph`, original `--cases`, bound `--evaluation`; `--force` does not bypass evidence; `--dry-run` available | [Rule acceptance](development-loop.md) |
| `meta rollback-rule` | Restore an eligible recorded graph adoption | `--record`, matching explicit `--graph`; `--dry-run` available | [Rule acceptance](development-loop.md) |
| `meta reflect` | Propose changes from recorded execution evidence | Project/reference ledger; optional output; `--terms` is a pending history request, not retrieved evidence | [Development loop](development-loop.md) |
| `meta fuzz` | Generate bounded adversarial plan variants | `--plan`; optional declared `--type`; generation does not execute those plans | [Adversary implementation](../scripts/rds_adversary.py) |
| `meta evaluate-alignment` | Run the existing rule-alignment diagnostic | `--rule`; structure-only lint is not measured policy improvement | [Rule acceptance](development-loop.md) |
| `meta auto-repair` | Run the legacy reference-ledger repair diagnostic | Reference ledger and explicit `--graph`; inspect `--dry-run`; it does not bypass rule-adoption requirements | [Development loop](development-loop.md), [Implementation](../scripts/rds_adversary.py) |

## History, usage and host admission

| Command | Purpose | Inputs and prerequisites | Guide |
| --- | --- | --- | --- |
| `history preflight` | Check optional Obelisk availability | Existing local Obelisk installation; no automatic installation | [History bridge](../references/obelisk.md) |
| `history prepare` | Prepare a scoped history evidence packet | `--output`; exact `--project-path`, useful `--terms`, bounded offsets or selected UUID when needed | [History bridge](../references/obelisk.md) |
| `history query` | Run the supported read-only history query | `--query` and the installed Obelisk bridge; keep explicit project scope | [History bridge](../references/obelisk.md) |
| `usage` | Inspect recorded daily invocation counts | Optional dates/`--days` and `--json`; missing logging is not zero usage or proof of absence | [Usage log](cli-usage.md) |
| `host-hook install` | Record supported admission-hook coverage | Existing project ledger; this is the scheduler admission hook, not plugin installation | [Admission hook](host-hook.md) |
| `host-hook coverage` | Report covered and uncovered dispatch surfaces | Project root; absent guard reports missing coverage | [Admission hook](host-hook.md) |
| `host-hook validate` | Validate a bound dispatch request | `--request` with supported host, live run/attempt identity, exact argv and executor hash | [Admission hook](host-hook.md) |

## Advisor modes within the existing command

These are options or context fields of `advise`, not additional top-level commands. Combine them only as supported by their guides.

In an `advisor_policy` project, `advise --brief` derives inputs from its ledger and current owned graph. The caller-directed modes below cannot replace those inputs or select another route. See [program-owned Advisor](program-owned-advisor.md) for coverage, result collection and recovery.

- **Selection and continuation:** `--context`, `--graph`, `--choose`, `--record`, `--brief`. Read `selection_review`; one ready candidate alone is not comparative evidence. [Entry and selection](agent-entry.md).
- **Saved dependency map:** `--saved-dependencies` with the owning project root. Explicit context maps are also supported; do not submit the same map by both routes. The program cannot guarantee completeness of omitted research information. [TMS](truth-maintenance.md).
- **Imported evidence:** `--artifacts <manifest>` rereads original bounded source records. [Evidence import](development-loop.md).
- **Experiment composition:** `--templates <pack>` plus research context enumerates finite compatible interventions. [Composition](development-loop.md).
- **Resource planning:** put sourced `resources` and costs in `--context`; the resulting `resource_plan` is a proposal, not a GPU launch or reservation. [Resource planning](resource-planning.md).
- **Frontier exploration/reformulation:** `--frontier <spec>` and optional `--frontier-proposals <proposals>` generate/review bounded tasks beyond the current graph. Novel labels and graph reachability do not certify science. [Frontier](advisor-frontier.md), [Theory reformulation](theory-reformulation.md).
- **Plan, trace and fit diagnostics:** `--plan`, `--telemetry`, `--doc`, `--train-loss`, `--val-loss`, `--baseline-loss`, `--fit-telemetry`; supply the matching recorded data instead of invented metrics. [Advisor implementation](../scripts/rds_advisor.py).
- **Scoped local literature:** `--literature` with optional `--topic` uses explicit local primary-source records. It is not a general web search. [Advisor implementation](../scripts/rds_advisor.py).
- **Retired option:** `--research-note` is retained to return migration guidance. Use `checkpoint save --decision` and scoped context instead; it is not a working alternative archive. [Local decision workflow](lightweight-workflow.md).

## Documented helper entry points

Run these as `python -B "<skill_dir>/scripts/<file>" ...`, checking their own `--help`; they are not `rds_cli.py` subcommands. Output paths belong to the user's project. Actual research computation still uses the authorized frozen execution path and its budget when applicable.

- `rds_theory_tools.py --signals <tags> --limit 3` or `--id <card-id>`: bounded theory-tool lookup. `--list-operators`, `--test-operator <id>`, and `--scaffold <id> --out <new-file>` expose the implemented operator demonstrations and exportable scaffolds. Self-tests are finite development evidence. [Theory tools and operators](theory-reformulation.md).
- `rds_capabilities.py --capability <supported-name>`: a narrow live backend smoke check, not installation or a proof of resource fitness. [Capability scope](agent-entry.md).
- `rds_dynamics_probe.py --input <snapshot> --output <report>`: bounded diagnostics on exported matrices/refinement data; optional NumPy-dependent results remain unavailable when missing. [Dynamics snapshots](theory-reformulation.md).
- `rds_context_compact.py --input <context> --output-dir <new-directory>`: compact repeated declared hash maps while retaining originals and manifests. [Context compaction](agent-entry.md).
- `rds_dashboard.py --root <user-project> --output <html>`: export a read-only offline view; optional `--advisor` or labelled `--demo`. [Dashboard](dashboard.md).
- `rds_hypergraph_view.py --root <user-project> --output <html>`: export the real saved hypergraph without changing its ledger. `--export-agent-input` prints separate raw graph and readable template; `--readable-json <file>` consumes snapshot-bound agent titles and purpose descriptions. Open the returned output with host preview/browser tools and provide its actual file/URL to the user; do not invent a localhost address or substitute a demo for a missing graph. Existing pinned Replica/Pixi assets are optional. [Graph page and agent interface](dashboard.md#agent-可读信息接口).
- `rds_hypergraph.py --input <map>`: one-shot bounded analysis and optional evidence audits; use the public `hypergraph` command above for program-owned continuation. [Hypergraph evidence](hypergraph-evidence.md).
- `rds_disk_cover_verify.py --spec <spec> --output <result>` and `rds_rational_voronoi_verify.py --spec <spec> --output <result>`: adapter-specific geometric generation/replay options; prefer `formal verify`/`formal check` when the declared kind is supported there. Inspect each helper's certificate and resource options. [Formal adapters](formal-verification.md).
- `build_plugins.py --output <new-package-directory>`: build host packages when plugin installation/development is the task. [Host plugins](host-plugins.md).

Modules such as `rds_resources.py` and `rds_operators.py` supply implementations to these entries; their names do not imply callable CLI commands. Worker processes and internal solver cores are owned by their dispatchers. The historical `rds_probe.py` path is not the current formal dispatcher and cannot inherit its assurance. Discover supported mathematical declarations through the verifier guide rather than invoking internal modules as new public capabilities.
