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

Binding requires all native ledgers in the explicit workspace, including sibling
projects, reference plans, preparation intents and recursively retained QUICK
jobs, to settle first. An incomplete or over-limit inventory refuses publication.
Native ledger, CAS, tool qualification and owned-dispatch writers share a
cross-process mutation lock with binding publication,
even before a marker exists. The lock covers short writes and admission; it is
released during experiment execution. Finish or recover pending original work,
then retry the same binding command. An older binding may already coexist with
an admitted sibling attempt: its narrow progress/receipt/cost settlement and
`project recover --id <original-run>` remain available without new admission,
budget, policy changes or brief artifacts.

If the marker was deleted while `RDS_CAMPAIGN_BINDING` still requires it, only
the explicit `project bind-workspace` command can restore it. The requested
workspace must match that exact required path and the original ledger's unique
verified binding event. Restoration republishes the original nonce; a missing
native event, conflicting marker or changed identity remains an error.

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
lineage. A nested successor may omit the separation declaration only when its
predecessor resolves to the discovered ancestor; an unrelated predecessor does
not authorize splitting the ancestor's research history. These are cooperative
CLI guards, not an OS sandbox or a global project registry. Local native state
without a project contract does not hide an ancestor's project contract.

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

Add work to an initialized QUICK project with `project create` and `project
execute`. A plain QUICK contract cannot use `exec` to open an uncharged child
ledger. A declared execution-policy owner can retain its existing charged QUICK
path; an alternative `--ledger` cannot replace the source contract's authority.

The CLI prints `mode` and actual Advisor ownership on stderr. Unhashed operation
responses include `workflow`; brief responses include a compact mode/Advisor/
next-command summary. Original hashed receipts remain unchanged. Full responses
also list applicable existing capabilities. FULL means program-owned collection
and route selection; it does not enable optional autonomy, tools, confirmation,
formal solvers or model execution without their declarations. `exec` is visibly
QUICK. Existing exact initialization retries keep working.
The legacy top-level `init` creates a reference-state ledger and does not label
that ledger as a QUICK project.

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
This includes retained QUICK children: activation checks their original ledger
state and receipts, and refuses missing, incomplete or still-active child work.
QUICK decision checkpoints also verify the contract they were prepared against
inside their write transaction. If activation wins that race, the stale
checkpoint is rejected; an already completed child's receipt and charged budget
remain available for inspection without rerunning the experiment.
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
