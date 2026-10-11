# Quick start: begin in plain language

You can ask an Agent to use RDS in ordinary language. You do not need to learn its internal terms or prepare JSON just to discuss a research question. Start with the goal, point to the relevant files, and say whether you want analysis, a proposed plan, or authorized execution.

## Copy a prompt that fits

### Understand a result

> Use RDS to inspect the results in `[path to logs or report]`. Separate what was measured from possible explanations. Suggest the smallest next check that could distinguish the leading explanations. Do not run an experiment or edit files.

### Compare two experiment ideas

> Use RDS to compare `[idea A]` and `[idea B]` for `[research goal]`. Use the metric and resource limit already stated in this project. Explain what each outcome would tell us and recommend one route. If a decision-changing detail is missing, ask me; do not invent it or start a run.

### Continue existing work

> Use RDS to continue this project. First inspect the recorded goal, completed runs, current evidence, and remaining budget. Summarize what is known and propose one next step. Do not reinitialize the project or repeat a run.

### Check a mathematical statement

> Use RDS to check this claim: `[claim]`. Keep its domain, assumptions, and quantifiers explicit. Tell me which parts a supported checker can establish and which remain open. Do not treat numerical examples as a proof.

Replace the bracketed text with your actual question or file path. If the project has no metric, evaluation rule, or resource limit, RDS should say so and ask only for information that could change the plan. These prompts request analysis; they do not authorize unmentioned experiments or spending.

## Try the CLI without writing a contract by hand

The Agent performs the backend preparation. Before every experiment it runs
`project discover` with the requested `--root`, then reuses the returned
`project_root` and its cumulative ledger, completed runs and remaining budget.
Discovery reads the requested directory and its ancestors; it does not search
sibling projects or create state. Add successive experiments as runs in the
same project. Nested initialization is rejected unless a deliberately independent
project supplies `--separate-project "<explicit reason>"`; a child `exec` cannot
bypass its parent project.

For a first plan, the Agent can use `project plan` before project initialization.
It returns the known goal, evaluation, resources and next action, grouping missing
information without inventing an execution budget. A small `--intent` file can
carry the user's declared inputs; an existing project supplies its original
contract and receipt locators. The draft starts no experiment. See
[planning and steering](planning-and-steering.md) for examples and for pausing or
redirecting an existing project from the current user request.

Requires Python 3.11 or later. The normal new project defaults to **FULL**, with
a valid explicit `advisor_policy` or an explicit finite [recipe](project-assembly.md).
The preparation script below creates a complete synthetic owned-policy example
in a new empty directory. The Agent must discover the intended location before
creating a project and reuse an existing project when one is found.

```powershell
python -B scripts/rds_cli.py --root . project discover
python -B examples/owned-advisor/prepare.py --root ./my-project
python -B scripts/rds_cli.py --root ./my-project project discover
python -B scripts/rds_cli.py --root ./my-project project init --contract ./my-project/contract.json
python -B scripts/rds_cli.py --root ./my-project project next --brief
```

These commands prepare and inspect a local project; they do not execute its
experiments. Use `project advance --brief` for one selected authorized step.
The [owned Advisor example](program-owned-advisor.md) uses synthetic data and
does not establish scientific confirmation or generalization. FULL supplies the
owned workflow; optional autonomy, domain confirmation and tool integrations
remain disabled unless explicitly declared. The stderr
`[RDS] mode=FULL|QUICK advisor=...` banner and nonhashed `workflow` metadata show the current mode and
supported capabilities without changing the original receipt body.

QUICK is an explicit limited route: an intentionally non-policy contract uses
`project init --mode quick --contract <contract.json>`. Existing exact retries of
legacy projects retain their original QUICK behavior. To enable owned Advisor
later, use `project enable-advisor --policy <policy.json>` to preview, then apply
that same policy with `--apply --expected-snapshot <snapshot_sha256>`. Activation
retains the genesis contract, history and budget in the same ledger; it does not
grant new commands or resources. See [project lifecycle](project-lifecycle.md).

In both modes, direction selection analyzes all declared active direction graphs
and the current saved TMS. `analysis_coverage` records the scope, identities,
counts and completeness; incomplete computation blocks selection. This covers
registered project graphs, not arbitrary files, and does not turn unknown
evidence into a fact.

### Explicit QUICK contract template

The limited template below has no Advisor policy and therefore requires
`project init --mode quick --contract <contract.json>`. For the default FULL
workflow use the complete example above or a [recipe](project-assembly.md).
Each `sha256` must be the real file hash, paths are relative to the project root,
and `min_useful_delta` is an exact rational string. Optional fields are documented
in [stop policy](stop-policy.md) and [native research](native-research.md).
Guessed hashes are rejected.

```json
{
  "schema": 1,
  "description": "CPU demonstration, not scientific confirmation",
  "bindings": [
    {"role": "code",      "path": "experiment.py", "sha256": "<sha256>"},
    {"role": "config",    "path": "config.json",   "sha256": "<sha256>"},
    {"role": "data",      "path": "data.csv",      "sha256": "<sha256>"},
    {"role": "evaluator", "path": "evaluate.py",   "sha256": "<sha256>"},
    {"role": "protocol",  "path": "protocol.json", "sha256": "<sha256>"}
  ],
  "allowed_commands": [
    ["python", "-B", "experiment.py", "--arm", "control",   "--output", "outputs/control.json"],
    ["python", "-B", "experiment.py", "--arm", "treatment", "--output", "outputs/treatment.json"]
  ],
  "output_roots": ["outputs"],
  "budget": {"wall_seconds": 20},
  "primary_metric": {"name": "mse", "direction": "min", "min_useful_delta": "1/100"}
}
```

## If you see `[RDS-REJECT]`

It means this command did not meet a prerequisite; by itself, it says nothing about whether your research idea is good or bad. Do not retry with guessed fields or reinitialize the project. Give the Agent the exact command, project directory, and complete output, then ask:

> Explain this exact RDS rejection in plain language. Name the missing or invalid input and the smallest safe correction. Check the current project state first. Do not edit files, reinitialize, run an experiment, or spend resources.

Some commands initialize different RDS ledgers. Follow the command for your current workflow and the actual rejection message; do not substitute `init` for `project init` (or the reverse) by guesswork. If the proposed fix changes the research goal, method, budget, data access, or starts a run, decide that change explicitly before proceeding.

## Read more only when needed

- [Agent entry: shorter commands and output](agent-entry.md)
- [Project and development loop](development-loop.md)
- [Research workflow](research-workflow.md)
- [Formal verification](formal-verification.md)
