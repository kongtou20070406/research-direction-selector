# One project, many experiments

An RDS project is a research campaign with a shared goal, evidence history and
budget. Add an experiment as a run in that project. A run, failed attempt, quick
check or method revision does not by itself justify a new project.

The AI should first locate the project from the working directory:

```powershell
python -B scripts/rds_cli.py --root <working-directory> project discover
```

Discovery inspects that directory and its ancestors without creating a research
ledger. Reuse the returned `project_root` and `next_action`. It does not scan
siblings or infer that two unrelated research topics are identical. Nested new
projects are refused by the public entry points unless initialization explicitly
supplies `--separate-project "<independent research scope>"`; that declaration is
recorded in the new ledger. A declared successor uses the existing `--supersedes`
lineage. These are cooperative CLI guards, not an OS sandbox or a global project
registry.

## FULL and QUICK

New public `project init` defaults to **FULL**, with program-owned Advisor:

```powershell
python -B scripts/rds_cli.py --root <project> project init --recipe <recipe.json>
python -B scripts/rds_cli.py --root <project> project next --brief
python -B scripts/rds_cli.py --root <project> project advance --brief
```

A full contract containing a valid `advisor_policy` can replace `--recipe` with
`--contract`. The AI must derive the goal, allowed routes, result readers and
budget from the user's actual request. An incomplete declaration is rejected;
RDS does not invent these inputs to satisfy the default.

An intentionally limited execution workflow is explicit:

```powershell
python -B scripts/rds_cli.py --root <project> project init --mode quick --contract <contract.json>
```

The CLI prints `mode` and actual Advisor ownership on stderr. Unhashed operation
responses include `workflow`; brief responses include a compact mode/Advisor/
next-command summary. Original hashed receipts remain unchanged. Full responses
also list applicable existing capabilities. FULL means program-owned collection
and route selection; it does not enable optional autonomy, tools, confirmation,
formal solvers or model execution without their declarations. `exec` is visibly
QUICK. Existing exact initialization retries keep working.

## Enable Advisor without starting over

Prepare a basic policy using the same bindings, allowed commands, output scope
and budget. A route for an existing run must keep that run's exact manifest.
Preview it against the current project:

```powershell
python -B scripts/rds_cli.py --root <project> project enable-advisor --policy <policy.json>
```

Inspect the proposed contract/policy identities, original run and receipt counts,
budget and observations. Apply that exact policy and preview snapshot:

```powershell
python -B scripts/rds_cli.py --root <project> project enable-advisor --policy <policy.json> --apply --expected-snapshot <snapshot_sha256>
python -B scripts/rds_cli.py --root <project> project next --brief
```

Apply appends one verified contract-lineage event in the existing ledger. It
preserves genesis, original receipts, checkpoints, attempts, exposure and spent
budget. It launches no command. A changed policy or project state requires a
fresh preview; an exact apply retry is idempotent. Reserved/running work,
outstanding reservations or a prepared method revision must be reconciled first.
Activation accepts only the basic `schema/context/graph/routes/observations`
policy; it cannot add command authority, budget or optional controller privileges.
Missing old outputs remain UNKNOWN when Advisor collects evidence.

## Complete analysis before any selection

Advisor reviews the entire active direction graph and the current dependency
map, including disconnected components, all nodes and all edges. Saved project
dependencies are loaded automatically. A caller cannot replace them with a
smaller map for a single review; update the declared current map first.

`analysis_coverage` reports FULL or INCOMPLETE, input identities, node/edge counts
and reasons. Detailed reports retain node conditions and edge dispositions.
Dependency analysis computes blocker alternatives for all nodes while preserving
the originally declared goals. Descriptive relations are explicitly reported as
descriptive; they cannot silently become executable inferences. UNKNOWN evidence
can occur in a fully completed analysis and remains UNKNOWN.

Node/depth/candidate/composition limits, absent endpoints or incomplete dependency
analysis prevent selection and dispatch in both FULL and QUICK. An otherwise
complete local goal or active reservation does not override this gate. Owned
review returns `INCOMPLETE_ANALYSIS` and no selected run; the CLI exits with code
2. Read-only diagnostics and reconciliation remain available. Resolve the input
or computation limit while retaining the whole active project graph. Brief output
may omit detail from display; its saved report preserves the full analysis.

Coverage is bounded program analysis of registered active inputs, not an LLM
reasoning transcript or scientific proof. Unregistered files elsewhere on disk
and historical superseded snapshots are not silently treated as active graphs.
