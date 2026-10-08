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

For a first plan, the Agent can use `project plan` before project initialization.
It returns the known goal, evaluation, resources and next action, grouping missing
information without inventing an execution budget. A small `--intent` file can
carry the user's declared inputs; an existing project supplies its original
contract and receipt locators. The draft starts no experiment. See
[planning and steering](planning-and-steering.md) for examples and for pausing or
redirecting an existing project from the current user request.

Requires Python 3.11 or later. From a repository checkout, choose a **new, empty** directory. The preparation script creates a small synthetic dataset and matching code, evaluator, protocol, and file hashes. It refuses to reuse a non-empty directory.

```powershell
python -B examples/project-runner/prepare.py --root ./my-project
python -B scripts/rds_cli.py --root ./my-project project init --contract ./my-project/contract.json
python -B scripts/rds_cli.py --root ./my-project project status --brief
```

These commands prepare and inspect a local project; they do not run the two experiments. To execute the complete CPU demonstration, use the [project runner example](../examples/project-runner/README.md) and the command sequence in the [repository quick-start](../README.md#deterministic-execution--acceptance-kernel). The demonstration uses synthetic data and does not establish scientific confirmation or generalization.

### Minimal contract template

If you cannot run `prepare.py`, `project init` accepts a hand-written contract with this exact shape: each `sha256` must be the real hex SHA-256 of the bound file, paths are relative to the project root, `min_useful_delta` is an exact rational string, and optional fields (`objective_sha256`, `execution_policy`, `stop_policy`, `maintenance_allowance`) are documented in [stop policy](stop-policy.md) and [native research](native-research.md). A contract with guessed hashes is rejected.

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
