# One project, many experiments

An RDS project is a research campaign with a shared goal, evidence history and
budget. Add an experiment as a run in that project. A run, failed attempt, quick
check or method revision does not by itself justify a new project.

## Bind a known campaign workspace

Campaign binding is **opt-in**. Existing projects are **UNBOUND** by default.
When the user has identified the campaign's actual working directory and existing
canonical project, bind that workspace once before continuing experiments:

```powershell
python -B scripts/rds_cli.py --root <canonical-project> project bind-workspace --workspace-root <existing-campaign-workspace>
$env:RDS_CAMPAIGN_BINDING = '<absolute-campaign-workspace>/.rds-campaign.json'
python -B scripts/rds_cli.py --root <working-directory> project discover
```

Use the returned absolute binding path and retain it in the AI's command
environment, including subprocesses. The workspace must already exist and
contain the canonical project. Do not invent a workspace, goal or canonical
ledger merely to satisfy this entry. Binding retains the existing contract,
failed attempts, receipts, checkpoints, exposures and charged budget; it creates
no new execution allowance.

Within that bound campaign, discovery prioritizes the canonical project even
from a sibling directory. Rebind commands to that returned root. A different
root, `--separate-project`, `--supersedes`, a fresh copy of the same contract,
the legacy reference runner and public QUICK `exec` cannot start another
experiment or replace the canonical ledger. A missing or invalid binding/ledger
is an error to repair, not permission to initialize again. Continue declared work
with canonical `project create`, `project execute` or owned `project advance`;
their original command, budget, evidence and recovery gates still apply.

Binding waits for the canonical ledger's active reservations and retained QUICK
jobs to settle. QUICK admission checks the original native binding intent while
holding the parent transaction, including the marker publication window. An
already admitted sibling attempt can still record progress and settle its exact
original receipt and costs. `project recover --id <original-run>` remains
available there without rerunning it or creating a brief artifact. This narrow
settlement path cannot initialize, register, dispatch, change policy or grant a
new budget; uncertain active attempts retain their reservations for recovery.

Previously registered tools retain their existing read-only qualification and
receipt checks. Public `rsi validate` and comparison cannot create new
qualification preparation or charge another experiment in a bound campaign,
including a request that appears reusable. Use an already approved canonical
project route for declared work; binding adds no tool-execution privilege.

The frozen copied autonomy worker receives the kernel's runtime directory only
after the existing owned admission accepts its native model request. It imports
the same campaign guard before writing a dispatch intent or starting a provider.
This runtime bootstrap adds no command authority or execution allowance; a
manual worker without the required runtime fails rather than bypassing the guard.

This guards cooperating RDS entry points and inherited campaign context. It is
not an OS sandbox, a filesystem lockdown, user authentication or protection
against arbitrary host code or deliberately bypassing the inherited context.

The AI should first locate the project from the working directory:

```powershell
python -B scripts/rds_cli.py --root <working-directory> project discover
```

For an unbound workspace, discovery inspects that directory and its ancestors without creating a research
ledger. Reuse the returned `project_root` and `next_action`. It does not scan
siblings or infer that two unrelated research topics are identical. Nested new
projects are refused by the public entry points unless initialization explicitly
supplies `--separate-project "<independent research scope>"`; that declaration is
recorded with the original new contract. A declared successor uses the existing `--supersedes`
lineage. These are cooperative CLI guards, not an OS sandbox or a global project
registry.

In unbound workspaces, managed tool-qualification workspaces remain internal jobs
of their original project. Their internal caller verifies the original
preparation ledger and workspace identity. Campaign binding does not give these
children a general exception to canonical project ownership.

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

Advisor reviews the entire active direction graph, the current dependency map
and any supplied frontier graph, including disconnected components, all nodes
and all edges. Saved project
dependencies are loaded automatically. A caller cannot replace them with a
smaller map for a single review; update the declared current map first.
The public Advisor API also rejects replacing an owned project's frozen direction
graph and collects the project's actual evidence. Frontier analysis retains its
explicit date cutoff; excluded future records are visible in the report.

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
