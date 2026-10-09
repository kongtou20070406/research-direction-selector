# Native research records and local tool reuse

RDS does not require MRS. Native records use the existing `.rds/project.sqlite3` and content-addressed `.rds/cas/`; no additional database, container, service or model endpoint is introduced. These are research assets, not another operational state machine or a conversation mirror.

## Bind the original objective

Save the original declaration without silently editing its mathematical statement:

```json
{
  "schema": "rds-objective-v1",
  "question_id": "exact-cover",
  "goal_revision": "1",
  "scope": {"domain": "unit-disk"},
  "statement": "Determine the globally optimal covering radius",
  "domain": "all allowed disk centers",
  "quantifier_order": ["for every allowed configuration"],
  "assumptions": [],
  "evidence_standard": "exact proof",
  "completion_standard": "matching global lower and upper bounds"
}
```

```text
python -B scripts/rds_cli.py --root <project> math bind --objective objective.json
python -B scripts/rds_cli.py --root <project> exec --objective objective.json -- probe.py
```

Binding freezes the original bytes, including formatting, and the six mathematical fields. Rebinding an identical declaration is idempotent. Changed objectives require an explicit new project; keep the old record and user-authorized change reference. Bind before native assets, rather than relabeling older evidence under a new objective.

`exec` reuses an existing native objective automatically, copies its original declaration into the frozen job, and binds its hash to the request and operational contract. Admission, completion and continuation verify that binding. Advisor checks explicit parent-objective bindings and conflicts with its fixed scope; a local lemma/question may have its own decision ID and narrower scope. The original parent objective remains fixed. Ordinary projects with no native mathematical objective keep their existing behavior. An existing operational contract cannot be retroactively relabeled with a new objective. A storage-only native project is not an execution contract and grants no command or budget authority.

A hash prevents unnoticed byte/identity changes in these checked paths; it cannot determine whether arbitrary prose or a program proves the original theorem. Native records are trusted local bookkeeping, not protection against an actor who rewrites the entire database and all hashes.

## Preserve assets and review dependencies

```text
python -B scripts/rds_cli.py --root <project> math add --id L3 --kind lemma --file lemma.md
python -B scripts/rds_cli.py --root <project> math add --id T1 --kind lemma --file theorem.md --depends L3
python -B scripts/rds_cli.py --root <project> math refute --id L3 --reason "missing hypothesis" --evidence witness.json
python -B scripts/rds_cli.py --root <project> math affected --id L3
python -B scripts/rds_cli.py --root <project> math get --id T1 --json
```

Kinds include lemma, algebraic-root, geometry and note. Polynomial coefficients, rational isolating intervals, coordinates and proofs remain in their original files; recording does not recompute a Sturm sequence or certify geometry. Each named record binds the current objective, exact asset bytes and explicit parent-record hashes. Changed assets use new identities. Records and refutations are append-only; source files may subsequently change without changing their saved originals.

`math refute` records a declared refutation and reports the transitive dependency lineage needing review. It preserves every original. `REVIEW_REQUIRED` does not mean that all downstream mathematics is false: an AND/OR proof may have another valid support. The existing `hypergraph` command analyzes a supplied proof graph; lineage recording does not infer that graph from Markdown. Route rejection through `reject` remains distinct from refuting a mathematical asset.

Assets are bounded to 8 MiB each, objectives to 64 KiB, dependencies to 64 names and native records to 2048 entries. Exact retrieval checks record and CAS integrity. Status and catalog reads do not hash every historical asset. Default CLI output is a small digest pointing to the full record; `--json` exposes full metadata.

## Discover and apply a capability

At a consequential blocker, describe the required input, operation and output and the original-goal obligation it would address. Inspect the reported reason before calling it a capability gap: missing data, unavailable backends, implementation errors and exhausted search bounds need different next steps. Repeated UNKNOWN alone does not diagnose a missing ability.

Find accumulated local functions before rebuilding them:

```text
python -B scripts/rds_cli.py --root <project> rsi list
python -B scripts/rds_cli.py --root <project> rsi list --name <exact-tool-name>
```

The default digest shows at most three names and function entries, sorted by name, with `tool_count`, `omitted_tools` and the full record locator. This order is not a relevance ranking. `--name` filters exactly, including the full `--json` output; an unknown name returns an empty catalogue. `recorded_registration` reports a historical local adoption, while `reuse_checked=false` means this lookup has not revalidated it. Function identifiers longer than 128 characters have `entry=null` and `entry_omitted=true`; retrieve the exact identifier from the record rather than calling a truncated name. Listing neither executes the function nor rehashes every source asset.

Open only the selected tool's source/validation details as needed. If no local function fits, use the bounded [theory-tool lookup](theory-reformulation.md#按需查预载工具与可运行算子脚手架) for a relevant method and check its prerequisites. A catalogue miss means no matching recorded tool, not that no method exists. Reuse or compose a compatible implementation; when necessary, propose a parameterized adaptation with its expected output, checker and bounded cost. A new operator must serve the original obligation rather than manufacture a different goal.

Qualification has three distinct uses:

| Evidence | What to do with it |
| --- | --- |
| Known valid, discriminating negative and applicable boundary cases | Check the stated operation against expected answers grounded independently of its implementation. Keep failed cases; a process failure is not a scientific counterexample. |
| Actual goal-bound input and an appropriate checker/measurement | Run through the existing authorized execution path, then use the result in the next selection. A fixed demonstration or backend smoke does not substitute for this application. |
| A later invocation with a justified changed input or scope check | Reuse the retained function and original limits. Demonstrated reuse is scoped; outside-domain input should stay unsupported rather than inherit a previous PASS. |

Record only the requirement, selected tool/version, evidence locator and changed next decision in the existing context/checkpoint. Keep full code, cases and raw results on disk. If the function is a genuinely reusable addition, use the existing extraction and local qualification flow below. Do not create another capability registry or reload the whole library on each turn. Registration does not add a trusted proof rule or mark a scientific hypergraph node true.

This is an agent workflow over existing entries. Automated gap diagnosis and goal-bound application consumers are tracked in [#43](https://github.com/kongtou20070406/research-direction-selector/issues/43) and [#45](https://github.com/kongtou20070406/research-direction-selector/issues/45); they are not delivered by the catalogue fix. No model-weight learning or general mastery is claimed.

## Extract, test, register and actually reuse

```text
python -B scripts/rds_cli.py --root <project> rsi extract --source probe.py --entry decide --name decide-v1
python -B scripts/rds_cli.py --root <project> rsi validate --name decide-v1 --cases cases.json -t 10
python -B scripts/rds_cli.py --root <project> rsi register --name decide-v1
python -B scripts/rds_cli.py --root <project> rsi use --name decide-v1 --output reusable_decide.py
```

Registration completes the validation ID only when exactly one passing local validation exists; otherwise pass the exact returned ID with `--validation`. Cases are explicit development material, for example:

```json
[
  {"args": [1, 3], "expected": true},
  {"args": [2, 3], "expected": false},
  {"args": [1, 0], "expected_error": "ValueError"}
]
```

Extraction parses at most 512 KiB/30000 AST nodes without importing or executing source. It keeps up to 64 bindings in the entry's local function dependency closure, literal immutable constants, explicit math/fractions imports and the narrow `from json import loads` import (including aliases), discarding unrelated top-level code. Other JSON APIs and `import json` remain unsupported. Decorators, dynamic/global-state constructs, private attributes, unsupported imports and unresolved globals require manual extraction. An unsupported candidate is rejected with a concrete reason; it is not silently modified to return a desired answer. AST admission does not prove purity or execution safety.

The [finite result tools](result-tools.md) provide strict JSON scalar extraction, compatible metric comparison and original failure inventory through this same route. Their [public example](../examples/result-tools/README.md) carries all three results into owned decision predicates, retaining exact finite qualification, original sources, costs and receipts.

Validation runs a frozen candidate and fixed case evaluator through the existing native runner. It preserves both successful and failed output, source bindings and receipts. Repeating the same frozen candidate/cases/controller request reads its existing job rather than rerunning. Validation accepts 1–64 JSON cases and a wall cap of at most 60 seconds. During budgeted research, pass `--ledger <existing-wall-budget-project>` to charge the allowance before dispatch; otherwise only a per-check limit is known. Do not treat missing total-budget data as unlimited authorized research.

Registration rechecks the actual evaluator/candidate/case bindings, successful receipt and untouched result artifacts. It creates `.rds/tools/<name>.py` without overwriting different content. `rsi use --output` exports the same module into an ordinary project file, enabling `from reusable_decide import decide` and normal `exec` input freezing. Later refutation or source/receipt changes block reuse pending review. Existing registered bytes remain intact.

`LOCAL_CASES_ONLY` is finite local qualification, not mathematical soundness, fresh confirmation, policy improvement or main-repository adoption. No property expectations, scientific thresholds or policy-gain percentages are invented. The next useful research step must actually consume the tool; successful registration alone is not a research benefit.

## Compare a tool revision at fixed precision

After a failure suggests a tool change, check the replacement on the same declared work before using its speed as a selection reason:

```text
python -B scripts/rds_cli.py --root <project> rsi compare --baseline <old-name> --candidate <new-name> --cases cases.json --precision-key precision --precision 16000 -t 10 --min-speedup 1.1 --ledger <existing-wall-budget-project>
```

Both names must identify extracted local functions with different source bytes. Every oracle case must explicitly pass the same positive integer precision keyword. The command preserves the original case bytes in CAS and appends a dependency-bound comparison plan as an ordinary native `note` **before either validation starts**. The plan fixes source identities, cases, precision, wall cap, runtime context, order and the descriptive ratio threshold. Its validation identities are scoped to that plan: a previous standalone `rsi validate` cannot become a retrospectively predeclared comparison. Both checks use the existing evaluator and native execution receipts; neither is auto-registered or adopted.

The comparison rechecks the actual frozen sources, evaluator, cases, receipts and output artifacts. An incorrect case result returns `correctness=FAIL` and has no speedup verdict. A process failure without a case verdict, timeout or unresolved execution remains UNKNOWN. Both checks charge the optional parent wall ledger before dispatch. If the second allowance cannot fit, it is not launched: the plan, completed first check and original charge survive. Repeating the unchanged request reads retained native jobs without a new attempt or charge. A charge interrupted before the native job was saved requires inspection and grants no replacement launch. Parent budgets with program-owned Advisor, execution or stop/maintenance policies continue to require their existing project execution paths.

A retained RESERVED/RUNNING job without a receipt returns UNKNOWN with its job locator. It does not create an immutable terminal validation/result, and an unresolved baseline prevents candidate dispatch. Inspect or recover that original job through its native execution path, then read the same comparison again; the allowance and attempt identity remain retained.

Only positive, explicitly measured receipt `wall_seconds` in seconds can form the ratio `baseline / candidate`; reservations, charged estimates and missing costs never become measured values. Missing or differing host/Python/executable context yields UNKNOWN. The default digest includes correctness, fixed precision, case count, measured walls, ratio, reasons and a full record locator. `--json` exposes original receipt/validation locators. Exit codes are 0 for a completed descriptive comparison, 1 for incorrect local output or an RDS admission rejection, and 2 for unknown performance.

`OBSERVED_SPEEDUP` means that this **single** wall observation per tool meets the prospectively supplied ratio; `NO_OBSERVED_SPEEDUP` records the opposite observation. The fixed order is baseline then candidate. Wall cost includes child startup, imports, case evaluation and result serialization; controller preparation and comparison bookkeeping are outside that receipt measurement. System load, variability, CPU time and peak memory are not measured, and a later invocation reuses these historical observations. This is not a statistical improvement gate. To claim stable acceleration, use a separately authorized repeated/interleaved protocol. The explicit precision parameter checks workload identity; it does not prove arbitrary source code honors precision internally or solves the original research obligation.

The [public integer-root example](../examples/tool-comparison/README.md) uses the same 16000 decimal scale in Newton and standard-library implementations with exact, independently specified answers. It is a synthetic software comparison, not the disk-covering polynomial problem. General external CAS executables are outside the conservative local-function subset. Whole research trajectories, solver failures and exact mathematical certificates still require their original execution and verification paths.

## Optional cooperation and upstream contributions

When MRS is installed and its record workflow is authorized, use its original records/exports as explicit sources and retain their identities. Do not automatically scan another Skill directory, initialize an MRS library, translate its review status into local independent review or copy its implementation. RDS keeps the objective, asset provenance and dependency obligations needed by its own workflow; MRS can supply richer archival/exchange behavior when requested.

Local tool/lemma/rule acceptance and public PR promotion are separate. Upstream contributions need a scoped general capability, protected acceptance criteria, appropriate independent checks and reviewer approval. Lean contributions must include the relevant imports and audit coverage and preserve the intended theorem statement. Whole same-budget trajectories and unused confirmation are required for research-policy claims. This delivery does not automatically extract Lean lemmas, generate strategy patches, create PRs or merge them.
