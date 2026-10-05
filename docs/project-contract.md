# Minimal project contract template

An owned campaign can additionally freeze `advisor_policy.autonomy` for
[bounded research drive](autonomy-loop.md) and `advisor_policy.confirmation`
for [domain confirmation](domain-confirmation.md). Both consume the existing
routes and ledger; neither adds command, data or budget authority.

Load this template when starting a locked experiment campaign. `project init` validates relative binding paths, each file's hex SHA-256, all five binding roles, and `min_useful_delta` as an exact rational string. Optional fields (`objective_sha256`, `execution_policy`, `stop_policy`, `maintenance_allowance`) are documented in [stop policy](stop-policy.md) and [native research](native-research.md). For program-owned collection and route selection, also freeze `advisor_policy`; its full contract and executable CPU example are in [program-owned Advisor](program-owned-advisor.md). Existing contracts without that policy retain the workflow below.

Generate a real, immediately valid demonstration contract with `python -B "<skill-dir>/examples/project-runner/prepare.py" --root "<new-empty-user-directory>"`. `<skill-dir>` is the directory containing `SKILL.md`; the new directory is outside the Skill installation or plugin cache. The demonstration's metric and budget are examples, not defaults for the user's research.

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

With `python -B "<skill-dir>/scripts/rds_cli.py" --root "<user-project>"` as the command prefix, run `project init --contract <user-project>/contract.json`, register each arm with `project create --manifest <user-project>/<arm>.json`, and follow `project next`. Binding paths and allowed commands refer to the user project, not the Skill directory. See the [project tools](development-loop.md) for execution and recovery.
