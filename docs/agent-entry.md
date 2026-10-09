# Low-friction research entry

Programmatic completion supplies file hashes, protocol identities, timestamps, receipts and previously recorded question/scope fields. It does not invent a research objective, budget, seed, hidden input, proof, causal explanation or scientific rejection. The normal frozen ProjectStore and append-only checkpoints remain authoritative.

In the examples below, `scripts/`, `references/` and `examples/` refer to resources beside the installed `SKILL.md`. Resolve the CLI path there, including in a plugin, and always pass `--root <user-project>` explicitly; user input paths and `--ledger` identify the user's project records. Do not initialize `.rds/` in the Skill installation or plugin cache.

Before every experiment, the Agent runs `project discover` from the requested
root. Discovery is read-only and checks that directory and its ancestors. Rebind
subsequent calls to the returned `project_root`, inspect its existing work and
remaining budget, and add the experiment as another run in the same project.
Do not make a new ledger for each experiment. A deliberately independent nested
project requires `project init --separate-project "<reason>"`; `exec` from a child
directory cannot bypass an existing parent project.

New `project init` defaults to FULL and requires a valid explicit Advisor policy
or `--recipe`. An intentionally limited non-policy contract uses `--mode quick`;
exact retries of old non-policy projects remain compatible. FULL means owned
collection and selection, with optional autonomy, confirmation and tools only
when their declarations exist. Read the stderr mode/advisor banner and returned
nonhashed `workflow` capabilities. Original signed receipt bodies stay unchanged.
Use [same-ledger activation](program-owned-advisor.md#enable-advisor-in-an-existing-quick-project)
to add owned Advisor to a legacy project without replacing its frozen history.

## Wrap a command

```powershell
python -B scripts/rds_cli.py --root <source-directory> exec --name probe-001 --timeout 30 -- python -B probe.py
```

One call freezes inputs, constructs the contract/protocol/manifest, reserves the explicit wall allowance, executes and records the real receipt. No handwritten protocol JSON is needed. The default cap is 60 seconds; supply the appropriate bounded cap. Jobs that must outlive the conversation use `--background`, the existing Windows Task Scheduler runner. Jobs longer than 3600 seconds use the regular project runner.

The command runs in `<source-directory>/.rds/exec/probe-001`, a frozen copy. Existing file arguments and static local Python imports are copied, including package initializers. Add implicit inputs with `--bind data=relative/file.json` (also `code`, `config`, `evaluator`). Dynamic imports, external packages, environment access and remote inputs are not completely discovered. Keep these limitations explicit; this trusted-code runner is not an OS sandbox. Non-Python/inline commands require an explicit code binding. Commands cannot invoke a shell.

Use `--output outputs/result.json` to require an output file in the job copy; its parent directories are created after runner validation. Declare each required file, not its directory. Read original stdout/stderr and declared outputs through the receipt's artifact paths. Omit `--name` to derive a stable name from the frozen request: an identical call reuses its receipt and changed inputs produce a new identity. Explicit names remain immutable. Failure/timeout logs and spent costs survive. Exit code 0 records process completion; operational success also requires valid outputs and unchanged bindings. Mathematical or policy success remains unknown.

For an existing local Python file, use `python -B scripts/rds_cli.py --root <source-directory> exec -t 10 probe.py`. The entry supplies the current Python executable and infers the child boundary. Once the child starts, all its arguments remain untouched, including `--help` and `--root`. Use explicit `--` for commands whose boundary is unclear.

## Select before executing

For the default FULL campaign, use the [frozen Advisor policy](program-owned-advisor.md). `advise` reads the complete registered inventory and current owned graph; `project advance` executes at most one program-selected route and automatically incorporates its receipt. Caller context, graph and choice overrides are refused in that workflow. The commands below describe the compatible caller-directed QUICK workflow for projects without `advisor_policy`.

Both workflows require complete computation over all declared active direction
graphs and the current persistent TMS before a route can be selected.
`analysis_coverage` retains graph identities, node/edge counts, completeness and
reasons. An incomplete report cannot authorize a choice, including an explicit
`--choose`; owned review returns `INCOMPLETE_ANALYSIS` without a selected run.
If dependency declarations have changed, update the saved map first rather than
passing a different private dependency map. Full computation does not establish
that unknown premises are true or that arbitrary project files were analyzed.

Maintain the semantic `context.json` and structured graph for the actual next decision. The tool supplies repetitive bookkeeping around these inputs; it cannot derive the right mathematical question from arbitrary prose.

Decision scopes are dictionaries with at most 16 fields and 2048 serialized UTF-8 bytes. Keys are nonempty strings of at most 512 characters; values are JSON atoms: strings, finite numbers, booleans or null. Keep arrays, tables and nested objects in explicit bound inputs or source records, and use scalar identifiers in the scope. Validation identifies the invalid field without rewriting its contents.

```powershell
python -B scripts/rds_cli.py --root <existing-ledger> advise --context context.json --graph graph.json --record next-choice --brief
python -B scripts/rds_cli.py --root <source-directory> exec --name probe-002 --timeout 30 --context context.json --graph graph.json --ledger <existing-ledger> -- python -B probe.py
```

If exactly one `READY` candidate remains, its identity is completed automatically. Otherwise supply `--choose <candidate-id>` from the advice. This records the caller's planned route, not scientific acceptance. The prospective `exec` reviews the current ledger, records the choice **before execution**, then records the execution identity afterward in that same ledger. Unchanged rejected routes are unavailable; changed relevant facts, premises or scope can reopen review. Unknown prerequisites, ambiguity or corrupt history cannot authorize a quick research run. An `exec` without context/ledger is explicitly an operational wrapper and does not claim direction selection.

If the selected action was discarded for an invalid declared protocol, the entry reports a bounded, identity-matched reason instead of only saying the candidate is missing. For example, an `OBLIGATION_CHECK` missing one of its three outcomes remains rejected and identifies the outcome requirement. Repair the original declaration; the entry does not invent an outcome, substitute another route or launch a job. Unrelated discarded alternatives do not prevent a uniquely available route from being selected.

Prospective child jobs consume a conservative wall allowance in that same parent ledger before launch; unspent allowance is not refunded. Changing a job name or creating a fresh child directory cannot replenish it. Quick execution requires a wall-only parent budget; multi-resource work needs an explicit project manifest. The original human deadline and authorization still apply.

## Theory and experiments

Use the same prospective choice, bounded runner, receipts and checkpoint ledger across theory, empirical, or mixed workflows. No external mathematics Skill is required. All modes share the unified budget ledger, but each enforces its own acceptance contract:

Set `research_mode` to `theory`, `empirical` or `mixed` in the existing decision context when using a declared domain workflow. Advisor then returns the configured frontier/actions, ledger review and optional resource plan without unrelated legacy ML branch hints or reference catalogs. Contexts without this field preserve their previous behavior. Mode names do not grant execution authority or change evidence standards.

| Work | Next decision | Necessary evidence |
|---|---|---|
| Pure theory | Close an obligation, validate a counterexample, or identify what remains open | Original objective, domain/quantifiers/premises, exact certificate and applicable checker |
| Pure experiments | Distinguish explanations or decide whether the task metric improves | Measured observables, comparable controls, valid uncertainty and declared scope |
| Mixed | Use a conditional theorem to justify a test, then check its application | Mathematical premises and their correspondence to the executed code/data; retain both verdicts |

For a single theoretical obligation, an executable graph action may use:

```json
{"id":"check-L1","kind":"OBLIGATION_CHECK","target":"L1",
 "claim":"For every x in the stated domain D, P(x) holds under premises H.",
 "description":"Produce and independently check the exact certificate for L1",
 "methods":{"purpose":"proof","arithmetic":"exact"},
 "required_observables":["certificate","checker result","unclosed premises"],
 "outcomes":[
   {"observation":"verified","next_decision":"review dependent obligation"},
   {"observation":"counterexample","next_decision":"revise the scoped claim"},
   {"observation":"unresolved","next_decision":"inspect remaining obligation"}]}
```

Attach this to an ordinary `executable` node with the actual decision ID and explicit `preconditions`, which may be empty. Preconditions such as an exhaustive domain reduction or sound pruning need source-backed evidence. Missing premises produce evidence requests before execution. Theory and experiment actions may coexist; obligation checks need no competing explanations and are not ranked against empirical probes by invented discrimination scores. Existing experiment actions keep their rival/outcome requirements.

`verified` requires checking the stated claim and premises at the reported strength; `counterexample` needs a checked witness in the same domain. A rejected proof, software error, timeout or unfinished enumeration belongs to `unresolved` and does not falsify the claim. Outcome declarations remain `INPUT_REPORTED`; Advisor readiness is not proof. The runner does not infer a scientific outcome from exit code 0. Retain the actual result and checker evidence in declared outputs:

```powershell
python -B scripts/rds_cli.py --root <source-directory> exec --timeout 60 --context context.json --graph graph.json --ledger <existing-ledger> --output outputs/certificate.json --output outputs/summary.json -- python -B prove_and_check.py
```

The program implements the domain-specific producer/checker; RDS supplies bindings and execution records. A cover check can prove a particular upper bound while leaving the universal matching lower bound open. Finite branch-and-bound proves a universal statement only with a justified complete search domain, sound exclusions and no unresolved branch. Avoid rigid manual prohibitions based on fixed branch counts or polynomial degrees; tool transitions depend on actual computational burden. Persist large proof trees, enumerations and configuration databases to disk (under declared outputs), loading only lightweight summary bounds, unresolved branches and certificate hashes into context to minimize token overhead.

Before a material computation, identify its mathematical structure, intended result and smallest sufficient implementation. Check only the selected backend's actual capability, estimate runtime/memory/expression growth and keep existing resource limits. Missing capabilities need an available fallback or concrete blocker. Distinguish numerical evidence, bounded checking, exact calculation, proof certificates and native formal verification. Arbitrary precision or CAS success alone does not raise evidence strength; another implementation is useful when it addresses a discrepancy or the required verification strength. Deliver the actual requested result when feasible, keeping large artifacts on disk with a compact summary.

### Review the research choice

Before a substantive choice, supply the serious directions or competing explanations that could change it, with their sources and a distinguishing observation, then consume the existing `search.selection_review`. Use its basis, flags and relevant `next_move` to explain why the proposed next step is worth taking. If `--brief` leaves that decision unresolved, read the relevant selection review from its saved `record`; a one-candidate result, successful checkpoint or budget admission cannot establish that alternatives were compared. Review the inputs before choosing rather than using a later call to endorse an already executed route.

When the review reports a single configured direction, missing/overlapping predictions, unresolved premises or truncated search, repair the consequential gap or state precisely what comparison remains open. Retain why the chosen observation can change the next decision. A fixed user-requested action and a single proof obligation need no artificial competing experiment; describe their actual scope. This review neither authorizes additional resources nor automatically launches an experiment. Existing saved dependency maps can be supplied with `--saved-dependencies` as described below; the review does not invent missing dependencies.

A ready rival test is named as a check of an UNKNOWN goal predicate only when its action directly targets that predicate or its existing `goal_contribution` names a valid declared path ending there. An invalid or UNKNOWN mapped path is not bypassed by the action label. Guidance names only the linked predicates and leaves other UNKNOWN obligations open; a declared path is not proof that an observation will resolve the goal. Unrelated exploratory candidates stay available and do not become evidence for application progress (#99).

Advisor returns `search.selection_review` alongside the existing readiness and cost ranking. It reports a single supplied graph direction, absent or overlapping rival predictions, unresolved prediction premises and search truncation. `READY` still means that the configured procedure's prerequisites are satisfied; it does not show that this is the best research direction. A single scoped proof obligation needs no invented rival experiment. Composed intervention plans remain a separate review scope.

For empirical comparisons, use the existing action `discrimination` fields: explicit rival IDs, same-scope predicted outcome labels, their source and any application conditions. Only supported conditional coverage at comparable sourced costs can establish the existing Pareto relation. Naming two rivals or giving both the same pass/fail predictions supplies no causal discrimination. When local manipulation repeatedly passes but task quality fails, investigate the missing local-to-task or source-to-target bridge rather than treating the proxy as sufficient or claiming the whole hypothesis family disproved.

Optionally add the project's **existing** task acceptance predicates to `decision.goal_conditions`; for example `[{"fact":"quality_gain","op":"gte","value":0.05}]`, where both the metric and threshold come from the actual project protocol. Advisor evaluates the sourced current facts separately from procedural readiness. Missing evidence stays `UNKNOWN`; `FALSE` identifies an open goal bridge, and even `TRUE` remains `INPUT_REPORTED`. When a goal predicate is `UNKNOWN` because the application/transfer measurement itself is missing while a supported discriminating rival test is ready, `next_move` is `DESIGN_DISCRIMINATOR` naming that observation — repeated local/procedural success (however many runs) is not application progress, replication and healthy local routes stay preserved, and this is route guidance, not a universal veto or a diagnosis of why the transfer gap exists. Otherwise the move is `RESOLVE_PREMISE` as before; a failed evidence adapter also stays `UNKNOWN` and becomes a `READ_ONLY_EVIDENCE_REQUEST`. This review supplies neither execution permission nor a scientific certificate and does not block an authorized attempt to improve a currently failing goal. `advise --record` retains the review with the choice; `--brief` exposes its basis and a few warning kinds while the full record stays in CAS. The synthetic progress-policy behavior is pinned by `tests/test_rds_progress_policy.py` (#43).

To hold a solution to a stricter standard, optionally add `decision.affirmations` with `portable` and/or `applicable` predicates from the project protocol, for example `{"portable":[{"fact":"clean_root_replay_error","op":"lte","value":0.1}],"applicable":[{"fact":"worst_declared_cohort_error","op":"lte","value":0.1}]}`. Advisor then reports `goal.triple_affirmative`: FOUND (the goal predicates), PORTABLE and APPLICABLE each affirm only on TRUE evidence read by `--artifacts` or derived by the program from inputs that are still importer-read, and only when no other affirmative reuses that evidence (the same file hash and locator under another fact name, or a derivation from it; re-serialized bytes are a different reading). A typed or configured value never affirms, though a failing one refutes just as it fails the goal review; a missing fact or an undeclared affirmative stays `UNKNOWN`. When the goal predicates are TRUE but the triple is not, `TRIPLE_AFFIRMATIVE_OPEN` is listed first and, if no other move applies, `next_move` asks to affirm or refute the open affirmatives (`RESOLVE_PREMISE`) while any is unresolved, and otherwise treats a failed one as a scoped gap (`REFORMULATE`). Obstructions apply only to goal predicates, not to affirmation facts, and `affirmations` is not part of the goal identity used by recorded goal history. `goal.status` itself is unchanged, and RDS does not define what portable or applicable means for a domain (#80).

Identical `goal_conditions` (in any order) and `goal_revision` identify the same goal across renamed questions in the same ledger. An unchanged rejected route under such a renamed question is blocked as `REPEAT_REJECTED_ROUTE` with its `recorded_question_id`; a changed scope or changed review facts reopen it as before. Whichever question recorded it, the latest applicable decision in checkpoint order wins, so returning to an older question neither bypasses a later rejection nor revives a superseded one. When a ready candidate changes only the parameters of a route whose latest same-goal choice in the current scope is a rejection recorded with the same review facts, `GOAL_ROUTES_REJECTED` reports the bounded count, and while the goal is `UNKNOWN` or `FALSE` `next_move` becomes `REFORMULATE`: compare one changed premise, representation or method with the smallest repair before adding more parameters. A first unmeasured attempt, a route with a changed operation or intervention, a scoped obligation check, overlapping rivals (still `DESIGN_DISCRIMINATOR`) and a supported rival test keep their previous review, and the variant itself is not blocked. A later recorded choice of the same route in that scope supersedes its rejection; a changed scope or changed relevant facts give no variant hint, because that scope or evidence has not failed. Recorded rejections are scoped choices, not a capacity bound or a guilty premise. Descriptions, rivals and outcomes are display fields for this family, as in route identity; an action without a structured target, intervention or operation has no parameter family. Another question's malformed record does not block this question: Advisor reports `GOAL_HISTORY_SKIPPED` and uses only the current question's records. Different or absent goal predicates or revisions share no history; declared parameter domains and A→B→A oscillation still use only the current question, and separate project roots still do not share one ledger.

When a goal obligation is blocked, the caller can declare the observed obstruction in `advisor_context.obstructions`. This takes 1 to 8 records, each with an `id`, the `obligation` (an existing `goal_conditions` fact or `completion_standard`), a `cause` and a `source`. The `source` is locator text, or an object with only `path`, `url`, `receipt_id` or `locator` text fields. A record may also carry `requirement: {input, operation, output}`, a `dependency` name (only with `DEPENDENCY_UNAVAILABLE`), theory-tool `signals` (only with `UNSUPPORTED_OPERATION`) and a `scope`. `selection_review.obstruction_review` maps each cause to a response:

- `MISSING_INPUT` gives `EVIDENCE_REPAIR`.
- `EXECUTION_CAP` gives `INCOMPLETE_COMPUTATION`, which is never a refutation or a capability gap.
- `DEPENDENCY_UNAVAILABLE` gives `DEPENDENCY_REPORT`. For a supported name, this includes the existing narrow `rds_capabilities.py` live check; nothing is installed.
- `ADAPTER_MISMATCH` gives `ADAPTER_REPAIR`, and the claim stays `UNKNOWN`.
- A sourced `UNSUPPORTED_OPERATION` with a requirement gives `CAPABILITY_REQUIRED`, carrying a source-bound `required_capability`. Its catalogue shortlist holds at most 3 theory-tool cards matched by the declared tags, and is labelled `BOUNDED_CATALOGUE_NOT_EXHAUSTIVE`, with prerequisites `NOT_ASSESSED`.
- An unsourced or undetermined cause, or an unsupported operation without a requirement, gives `DISCRIMINATING_CHECK` with the cause kept `UNKNOWN`.

When different causes remain declared for the same open obligation (each applicable or with unknown applicability; ruled-out records are ignored), every applicable record on it is held back to `DISCRIMINATING_CHECK` with the cause `UNKNOWN`. This includes unsourced records; records carry no time order, so a stale record is retired by removing it or by its scope. Records with the same cause do not conflict. A held-back record keeps its declared requirement, dependency name and live check as data for the check. Guidance text is fixed; record text stays in its own fields and is never spliced into instructions.

A record may also name its run's project-ledger receipt as `receipt: {project_root, sha256}`. This is the binding shape of dependency-map `evidence.receipt`, with `project_root` at most 512 characters; the entry echoes it with the sha256 lowercased. The receipt is read only with `advisor_context.audit_receipts: true`. Each distinct declared `(project_root, sha256)` is read once per advise call, read-only, and nothing is written; a dependency-map `evidence.receipt` naming the same pair in that call shares the read, and each consumer judges it by its own rule. Different `project_root` texts are never merged. Every entry with a receipt carries `receipt_audit`:

- `RECEIPT_FOUND` reports the `run_id`, the `run_status` and an `execution_cap`:
  - `TIMEOUT` when the receipt records `timeout: true`;
  - otherwise, when a `stop_reason` is recorded, that value (for example `CAMPAIGN_DEADLINE` or `PROGRESS_NO_GROWTH`), or `STOP_POLICY` when the value is not short text;
  - otherwise `null`.
- `RECEIPT_NOT_FOUND`, `RECEIPT_AMBIGUOUS` (more than one stored receipt carries the sha256), `RECEIPT_BODY_INVALID` (the stored body does not decode to a JSON object, or fails the ledger's own receipt check: a repeated key, another run, a sha256 that does not match the row or the recomputed digest, or a value with no canonical encoding; `reason` names the type or the failure), `RECEIPT_BODY_MISMATCH` (the found object does not carry the declared sha256; the shared ledger read already reports such a row as `RECEIPT_BODY_INVALID`) or `LEDGER_UNAVAILABLE` (`reason` names the exception type) fails closed: the record gives `DISCRIMINATING_CHECK` with the cause `UNKNOWN`. Nothing is read out of the body.
- `NOT_AUDITED` (without the `true` opt-in) keeps the binding as data. Nothing is read, and the response is unchanged.

A recorded execution cap counts as a declared `EXECUTION_CAP` on that obligation under the rule above. Any other applicable cause on it is therefore held back. For example, a timed-out run cannot back `CAPABILITY_REQUIRED` or `SPECIFY_CAPABILITY`. A receipt never fills in a cause, replaces `source`, or reads exit status or success as a reason. A succeeded receipt only records execution. Existing reasons (no source, undetermined cause, missing requirement or dependency) keep precedence.

A record is `NOT_APPLICABLE` when:

- its obligation is not a goal;
- its goal's current predicates are all `TRUE`; or
- its declared scope differs from the current scope.

Scope uses `decision.scope`, falling back to the context-level `scope`, and is compared as canonical JSON, as in the ledger. So changed evidence or scope reopens the route. A scoped record with no current scope stays `UNKNOWN`.

Applicable records are referenced from the existing `next_move`. A move is never created or removed, so a healthy route without a move stays unblocked. An applicable `CAPABILITY_REQUIRED` record changes the kind to `SPECIFY_CAPABILITY` only when the goal is not `TRUE`, every `UNKNOWN` goal predicate has such a record, and the move is `REFORMULATE`, `REVIEW_ALTERNATIVE` or the goal-evidence `RESOLVE_PREMISE`. The previous kind and reason are kept in `supersedes`, so recorded rejections stay visible. History-integrity, input, search-scope, goal-link and discriminator moves keep their kind and only reference the records. Advisor consumes the key once, after every existing context check, so existing errors keep their precedence. Only the Advisor entry consumes it; a frontier-only context validates it without effect. Malformed records are rejected with the field to repair. These are input-reported obstructions, not a diagnosis, and they authorize neither execution nor installation. `--brief` still shows only the move kind, which can now be `SPECIFY_CAPABILITY`; the full review stays in CAS. Contexts without the key are unchanged.

Large provenance maps belong in their original source manifest. Use its digest and locator in a scoped context rather than repeating the full file map in every fact. Oversized loop input retains the same byte cap and now reports the actual size plus a repair hint; no facts are silently dropped or compressed into stronger evidence.

For repeated declared file-hash maps, use the native compactor before submitting a new context:

```powershell
python -B scripts/rds_context_compact.py --input context.json --output-dir NEW_DIRECTORY
```

The output directory must be new. Only filename-to-SHA256 maps at `facts.*.binding.code_sha256` or `facts.*.bindings.code_sha256` are replaced by their canonical manifest digest; other fact data, sources and scopes remain intact. Identical maps share one complete `manifests/<digest>.json`. The directory retains the full original bytes in `source.json`, the new `context.json`, and `summary.json` with changed JSON pointers, manifest locators and measured facts bytes. For example, repeated 100-file maps can share one manifest rather than repeating all entries. Byte reduction is not a measured token saving.

Input remains capped at 2 MiB, and compacted facts must still fit the engine's unchanged 262144-byte cap; an unrelated oversized context needs a scoped redesign. This records declared identity and changes representation, without verifying actual files, strengthening evidence or granting permission. The new representation changes the request fingerprint: review and record it as a new request, preserving earlier frozen contexts and records. Read the current task's needed manifests on demand.

Before expensive execution, check the actual data/shape inventory and instrument the methods that really execute; inferred call counts remain derived counts. Require a nonvacuous wiring probe under its declared premises, rather than a universal nonzero-gradient assertion that can fail legitimately. On failure, preserve valid partial observations with their narrower scope and identify the first failed stage; launch success, terminal execution, evidence validity, manipulation and task acceptance are separate results.

Native selected-capability checks need no external Skill:

```powershell
python -B scripts/rds_cli.py exec --timeout 10 -- python -B scripts/rds_capabilities.py --capability sympy_exact
```

The helper checks one of `python_exact`, `sympy_exact`, `mpmath_iv` or `torch_cpu` in the running interpreter, recording its real module path/version and a small operation. A failed check remains a failure, with no silent fallback, package installation or scan of every backend. The frozen runner supplies the cap, source binding and original logs. Availability is a narrow live smoke check: it does not prove rigorous interval rounding, the requested algorithm, CUDA readiness or resource fit. Native Lean readiness already belongs to the formal entry. Short jobs need no additional probe when their actual primary check establishes the same capability. Do not introduce a persistent capability cache as mathematical evidence.

Switch from lengthy manual expansion based on the actual unresolved work, not an arbitrary polynomial degree or branch count. A direct proof may be shorter than a computational pipeline. If a bounded computational attempt cannot close the claim, preserve its remaining branches and actual blocker; do not repeat the same text or restart the same completed job merely to appear active.

The [native research](native-research.md) objective lock and exact assets supply continuity: retain the original statement and quantifiers, reopen affected downstream obligations when a premise changes, and preserve failed outputs with separate corrections. No external archive is initialized and self-review remains self-review. In mixed work, the existing theory-probe entry also checks `application_status`; a conditional theorem alone does not admit an empirical run.

## Clarify method limits

Treat candidate construction, verification of a fixed candidate, and proof of a universal claim as separate purposes. An algorithm name alone does not identify its purpose: branch and bound can search heuristically or exhaustively exclude a rigorously bounded domain. Floating point with proved enclosures can support a certificate; exact arithmetic can still be used for forbidden heuristic search. A coverage certificate proves a construction upper bound, not a matching unrestricted lower bound or global optimum.

Consider certifying computation during direction selection when it can close the next mathematical obligation within the authorized budget. Use the existing graph/Advisor fields: state the exact obligation and quantified domain; expose domain completeness and each pruning lemma as prerequisite facts with real evidence; require the bound certificate, remaining unresolved branches and independent replay as observables; let verified completion versus a counterexample or unresolved branch change the next decision. Missing prerequisites should produce a concrete bridge lemma or smaller valid sub-obligation. Record the prospective choice before execution and consume its actual result in the next selection, instead of wrapping a finished proof only for archival. A reusable domain adapter must provide these mathematical obligations and a real checker; the generic scope checker supplies no universal branch-and-bound solver.

When a user's instruction has materially different plausible scopes, ask one short question before the affected step. Keep that question pending and continue work that does not depend on the answer. Neither silence nor a quoted proposal resolves it. External contest rules can describe admissible evidence but do not override the user's method, CPU/GPU, budget or deadline restrictions. Do not extrapolate a small-case runtime into an unmeasured promise for larger cases.

Opt in through the **existing** Advisor context, without another database or command. Retain the original wording and its locator; after actual clarification, retain that wording and source as well. For example, after a human confirms that strict assisted proof is allowed and heuristic candidate search is forbidden:

```json
{
  "method_constraints": [{
    "id": "candidate-search",
    "quote": "不要数值搜索",
    "source": "user:original-message",
    "status": "CONFIRMED",
    "confirmation": {
      "quote": "允许严格辅助证明，禁止启发式数值找候选",
      "source": "user:clarification-message"
    },
    "when": {"purpose": "candidate_search"},
    "forbid": {"technique": "heuristic"}
  }]
}
```

This is a schema example, not an authorization. Each clause needs a unique `id`, original `quote`, `source` and `status` (`CONFIRMED` or `UNRESOLVED`). A confirmed clause has exactly one nonempty `forbid` or `require` predicate. `when` limits its scope; an omitted/empty `when` applies to all described steps. An unresolved clause may supply a short `question`. Unknown scope must remain unknown rather than being guessed from keywords. A clarification's `confirmation` records actual `quote` and `source`; use the latest genuine instruction while retaining the earlier wording.

Actions and templates declare `methods` as one flat string dictionary or a list of steps, for example `{"purpose":"proof","technique":"certifying_branch_bound","arithmetic":"certified","device":"cpu"}`. Include each stage in a mixed search/verification/proof workflow. Names are exact caller-defined values, not fuzzy aliases. Predicates are bounded to eight string fields; contexts contain at most 32 clauses and workflows at most 32 distinct method steps. If these fields are missing, describe the actual operation from its code/protocol; do not ask the human to fill a form.

Advisor reports compatible, conflicting or unknown scopes. Conflicts block that candidate; unresolved wording produces clarification questions; missing descriptions remain unknown. Other compatible candidates remain available. Constraint-aware experiment composition retains every declared stage and keeps distinct method descriptions separate even for otherwise identical intervention aliases. Quick prospective execution rechecks the supplied context before launch or parent-budget charge, then retains the clauses and review in its ordinary checkpoint; the frozen request binds the entire research context. A supplied `READY` or compatibility label cannot bypass that check. Old contexts without `method_constraints` retain their behavior.

This checks caller-reported declarations, not natural-language meaning, the authenticity of a source locator, or whether code implements its declared method. `INPUT_REPORTED` compatibility grants no execution authority, validates no theorem and replenishes no budget. At continuation boundaries, carry forward the recorded clauses and clarification; dropping them from a new context is not permission to lift a restriction. Read the actual program, authorization, receipt and appropriate proof evidence separately.

## Record a scoped rejection

```powershell
python -B scripts/rds_cli.py --root <existing-ledger> reject --reason "declared scoped counterexample" --evidence witness.json
```

The latest matching checkpoint supplies the question ID, goal revision, scope, candidate and contemporaneous facts. `--route` can verify the expected candidate. The tool retains the original witness bytes and hash, reason and previous checkpoint identity. Missing context produces an actionable error. It never infers a mathematical counterexample from a failed process or permanently bans a route outside its recorded scope.

Inside Python, the same entry is available from the skill's `scripts` directory:

```python
from rds_quick import record_falsification
record_falsification(ledger_root, witness=bad_example_dict, reason="why this scoped claim fails")
```

This records caller-declared falsification with `RECORDED_INPUT_NOT_SCIENTIFIC_VERIFICATION` assurance. Run the appropriate exact checker separately when required.

## Output and tolerant spelling

New `exec`/`reject` commands return a compact digest by default; `--json` returns their full report. `advise`, `project status`, `status`, `formal verify` and `formal check` support `--brief`/`--digest`. Full JSON is retained in `.rds/cas/<sha256>.json`; original execution logs remain intact in the runner's artifact directory. Counts are local ledger counts, not proof of scientific use or autonomy.

A refused request prints one `[RDS-REJECT] <message>` line on stderr: repair the request, file or declared input. `[RDS-ERROR] <ExceptionType>: <message>` means the `.rds/` state database (`sqlite3`), a dependency import or a subprocess call failed. Restore the state or environment before retrying. Rewriting the request does not fix these. Both tags exit 1. The tag changes only how the failure is reported, not admission, budget or recorded state. `[RDS-REJECT]` still covers some faults that are not refusals: an embedded NUL byte in a path, or a `KeyError`/`TypeError` raised by a code defect rather than by a malformed spec.

For agent use, prefer `advise --brief`, `status --brief` and `project status --brief`. Read a digest's `record` only when its missing detail can change the next decision; do not automatically reload the complete CAS record. Keep the current goal, next move, blocking reason and evidence locator, and report meaningful progress once rather than repeating unchanged status each call. A small digest is a projection, not permission to discard the original evidence or skip current execution checks.

`project status --brief` reports current `run_states` counts and the last finished native `latest_receipt`, selected by `ended_at` rather than run ID ordering. The receipt's `run_status` and `exit_code` describe that finished attempt; live `RUNNING`/`RESERVED` work remains visible in the counts and does not become complete. With no finished native receipt, `latest_receipt` is absent. A nonempty recorded `stderr.bin` adds `latest_receipt.stderr_path`, an absolute locator built from its receipt's `cwd` and artifact path. Read that binary log as text when appropriate; there are no `stdout_tail`/`stderr_tail` fields, and the digest does not read or print raw logs. `RECORDED` means the full status JSON was preserved, not that execution succeeded or scientific acceptance was established.

Nonempty receipt `errors` adds `latest_receipt.error_count` for the complete count and `latest_receipt.errors` containing only the first error, capped at 200 characters including a trailing `...` when truncated. These are recorded runner errors, not raw log text. An exit code of 0 can still yield `FAILED` when a declared output is missing or is a directory instead of a file. The full CAS record retains every original error. A receipt's `sha256` identifies that receipt, not the filename of the full status CAS JSON; use the digest's `record` path to locate the preserved full output.

Show one short factual line from observed events: `RDS | loaded, not invoked` when the Skill is actually loaded before the task's first CLI call, `RDS | advise: running` on dispatch, or `RDS | advise: returned` for a known zero exit. Report `error` for a host/tool error, `nonzero exit` for a known nonzero exit, and `result unknown` when the result is unavailable. A host session starting does not establish Skill loading; reading the Skill does not establish a CLI call. Retain the actual command's existing receipt or saved-record locator; hashes need not appear in the line. Omit unchanged or host-displayed duplicates and make no calls solely for display. Calling Advisor does not establish sufficient direction comparison; a returned call does not mean a proof, successful research execution or scientific acceptance. Actual backend, assurance and application scope remain distinct in the underlying evidence.

Exact commands take priority, followed by explicit aliases and unique command prefixes. Examples: `advisor`/`review` → `advise`, `proj` → `project`, `cp` → `checkpoint`, `execute` → `exec`, `deny` → `reject`, `calls` → `usage`; Chinese aliases include `审查`, `执行`, `否决`, `检查点`, `项目`, `调用`, `状态`. `--workspace` and `--project-root` alias `--root`, including after the subcommand; `--context` aliases `--research-context`. Ambiguous `adv` reports `advise, advancement`; a typo receives repair suggestions. Values, budgets, paths, witness contents and the wrapped argv after `--` are never fuzzy-normalized.

Additional aliases include `test`/`eval`/`start`/`测试` → `exec`, `suggest`/`route`/`规划` → `advise`, and `falsify`/`counterexample`/`证伪` → `reject`. `verify`/`prove`/`证明` expand to `formal verify`; `check`/`核查` to `formal check`; `snapshot`/`存档` to `checkpoint save`. Existing `run` and `plan` keep their original meanings. Root supports `-w`, `-d`, `--dir`; exec supports `-t`, `-o`, `-c`, `-l`; reject supports `-m`, `-e`. Suggestions do not authorize execution.

Use `hypergraph --input proof-graph.json` to import bounded AND/OR dependencies. It also accepts [compact declarations](truth-maintenance.md): the program generates, saves and maintains the internal table. Later calls use `hypergraph --update one-change.json`, `--declare '<small JSON>'`, `--retract-node <id>` or `--refute-node <id>` without a full map or previous-record argument. Default output gives a short observation and next step; immutable details stay in CAS. `--audit-files` checks bytes, and `--json` exposes details; neither reachability nor `SUPPORTED` proves mathematics. Exit 2 covers input ambiguity, truncation or competing updates, distinguished by `status`/`next_step`. See the [Codex/Pi loop design](agent-loop-tms.md).

## Goal-linked dependencies

Keep a small map of the original acceptance, necessary premises and serious alternatives. One hyperedge's premises are AND; separate edges to a conclusion are OR. Unknown evidence remains `UNKNOWN`, an unproved implication remains `PROPOSED`, and every node/rule has a source. Do not confuse a domain's enumerated hypergraph with RDS's research dependency map. The [synthetic example](../examples/goal-linked-hypergraph.json) deliberately leaves all original-goal obligations open despite two supported side results.

Use `advise --saved-dependencies` to inject the owning root's current map into the existing Advisor context, with no table in the model's context file. Prospective `exec --saved-dependencies` reloads it from `--ledger` before admission. Alternatively supply the existing schema, compact declarations or a program snapshot in `advisor_context.dependency_map`; do not use both routes. Advisor retains the actual map, original input SHA, normalized map SHA when repaired, blocker sets, ready obligations and truncation in `selection_review.dependency_review`. It does not infer implications from prose or auto-verify labels. Source files can separately be audited by the existing hypergraph CLI.

For an action whose actual `target` is `unrestricted_lower`, declare `goal_contribution` with three fields:

```json
{"target":"completion_standard",
 "path":["unrestricted_lower","completion_standard"],
 "source":"original-contract.json#/completion_standard"}
```

The contribution's target must name an existing `decision.goal_conditions` fact or the native bound `completion_standard`. In every mode, `action.target` must exactly match the first path entry; a missing or mismatched target leaves the contribution `UNKNOWN` without changing `READY`. Without a map this is a text declaration. With a map, path entries must be actual, distinct node IDs ending at a listed goal, and each step must follow a non-contradicted hyperedge. Actual node IDs take precedence over `rule:<id>` aliases; when no such node exists, start with `rule:<id>` followed by its conclusion to check that proposed bridge. A contributory path cannot bypass the edge's other AND premises or a proposed rule. Contradicted AND premises and ungrounded circular goals cannot supply a valid path. Preserve a transfer/reduction obligation before applying a small-domain certificate to a wider goal. Empirical maps similarly need application and manipulation bridges; a path does not identify a causal effect.

Set `require_goal_link: true` in the prospective research context when execution must be restricted to current original-goal obligations. Before recording/launching/charging, quick entry recomputes the actual map and path, requires complete blocker analysis, and requires the path's first node/rule to be a current ready obligation of an open goal. Every selected AND bridge needs a grounded route for all its premises; a healthy OR alternative cannot hide a cyclic branch. A text-only link, closed node/goal, contradicted bridge, missing map or forged advice label cannot satisfy this check. The guard always audits declared evidence: receipt-grounded bindings must resolve against their named ledger and audited source files must match their recorded sha256; a path relying on an ungrounded binding or a byte `MISMATCH` is refused with the exact repair token (`RECEIPT_REVALIDATION` / `EVIDENCE_REPAIR`) before dispatch. When the installed analyzer cannot audit receipts, declared bindings stay fail-closed instead of being silently trusted. The existing choice record retains the actual checked map/hash and contribution under `goal_guard`, alongside the earlier Advisor review. Keep this setting through continuation; existing contexts default to advisory behavior for compatibility. This is a structural guard on declared inputs, not a verifier of the graph's scientific soundness or a guarantee of research gain; it does not prevent executing unrelated commands outside this entry.

For native objectives, quick exec checks the current ledger binding before choice and fills it from that check when omitted from the raw context. A conflicting stale advice binding is rejected; direct `choice` calls cannot use advice to supply a missing current binding. A missing or mismatched action target also fails the strict guard before recording, launch or parent charge.

`REVIEW_GOAL_LINK` requests a missing/invalid link only when all available actions lack one; a healthy linked alternative remains available. These prompts change neither `READY`, budget nor scientific acceptance, and replication remains legitimate. Full paths and reasons stay in CAS/checkpoints; `--brief` retains at most three relevant warning kinds. A graph closed under reported labels remains reported closure, not original-goal completion.

An optional `exec --guard policy.json` checks comparable metrics or replays frozen milestones before allowing promotion. `reject --domain domain.json` records only explicitly justified parameter exclusions. Both use existing artifacts and checkpoints; see [regression guards](regression-guards.md).

## Performance and design provenance

Pure Frontier queries skip the unrelated judgment graph and ML recommendations. No persistent cached proof or scientific truth is introduced. Measure real local timings and output bytes before making speed/token claims; desktop/network reconnection time is outside this CLI measurement.

The interface follows independently implemented standard practices also observable in Claude Code: ranked command/alias suggestions, exact matches taking precedence, and definitions loaded when needed. The [official MCP guide](https://code.claude.com/docs/en/mcp#scale-with-mcp-tool-search) documents deferred tool discovery. The [official exact-match bug report](https://github.com/anthropics/claude-code/issues/19259) illustrates the need to separate fuzzy suggestions from execution. A bounded inspection of a third-party 2.1.88 source-map reconstruction informed the comparison; it is an old snapshot, not an authenticated current Anthropic source commit. No reconstructed code or dependency was copied into RDS.
