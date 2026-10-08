# Assemble a research project from one declaration

`project init --recipe recipe.json` accepts explicit research semantics and
assembles the existing owned contract. The LLM declares goal predicates,
actions, the independent evaluator, commands, observations and authorized
resources once. RDS fills file hashes, protocol references, allowed commands,
graph nodes and action/run/observation associations. The human can continue
talking to the AI; this reduces backend configuration the AI must assemble.

```powershell
python -B scripts/rds_cli.py --root <research-project> project init --recipe <recipe.json>
python -B scripts/rds_cli.py --root <research-project> project next
python -B scripts/rds_cli.py --root <research-project> project advance
```

Initialization prepares a protocol and freezes a contract; it launches no job.
Selection, recovery, receipts, resources and observations retain their original
behavior. `project status` exposes the compiled contract. Initialization adds
an `assembly` report with source recipe SHA256, generated protocol locator,
compiled contract SHA256 and input/route counts. This is not a second ledger.

## Declaration fields

The top-level schema is `1`. Required fields are `files`, `protocol`, `context`,
`routes`, `output_roots` and `budget`; optional fields are `description` and
`output_files`. Unsupported fields fail closed. Advanced policies (autonomy,
confirmation, method revision or successor links) continue to use the existing
[contract interface](project-contract.md).

- `files` maps `code`, `config`, `data` and `evaluator` to lists of actual
  project-relative files. All four roles must be present after adding prepared
  applications. The evaluator identifies the original independent oracle;
  registering a file does not establish its scientific validity.
- `protocol` contains `path` and `metadata`. The generated path must be inside
  the project and outside `.rds`. Metadata retains explicit `data_split`, `init`,
  `seed`, `checkpoint`, `schedule`, `sample_work` and `numeric_protocol`, together
  with additional original protocol metadata such as metric definitions. Omit
  `code_sha256`, `config_sha256`, `data_sha256`, `path` and `sha256` inside metadata:
  RDS computes these identities from the actual bound bytes.
- `context` is the original [owned Advisor context](program-owned-advisor.md),
  including decision ID, goal revision, scope and explicit goal predicates.
  A prose [plan draft](planning-and-steering.md) is not compiler input. The agent
  must resolve its semantics without inventing missing goals, rivals, thresholds
  or resource authorization.
- `routes` contains 1–64 objects. Each combines explicit `action`, `run`,
  `observations`, and optional `preconditions` (default `[]`). Actions retain
  original admission rules and competing explanations or proof obligations.
  Preconditions can name owned observations and declared run lifecycle facts.
- An ordinary `run` declares `id`, `arm`, `argv`, `outpaths`, `timeout_seconds`
  and `resource_estimates`. Optional `control_id` defaults to `null`;
  `description` is also optional. RDS adds `schema` and the shared `protocol`.
  A treatment still requires its original control ID. Estimates must match
  budget dimensions and cover the declared timeout.
- Each observation declares `fact`, `path`, `selector` and optional `format`
  (`json`). RDS inserts the enclosing run ID. Paths must name that run's output;
  selectors use the original bounded JSON pointer reader.

For example, an ordinary route has this shape. Its paths and scientific
semantics must match the actual project; these demonstration limits are not
defaults for research:

```json
{
  "action": {
    "id": "measure-control", "kind": "PAIRED_TEST",
    "description": "Measure the declared control on the frozen dataset",
    "competing_explanations": ["linear fit suffices", "representation needs revision"],
    "required_observables": ["control_mse"],
    "outcomes": [
      {"observation": "low residual", "next_decision": "retain scoped fit"},
      {"observation": "high residual", "next_decision": "review representation"}
    ]
  },
  "run": {
    "id": "control", "arm": "control",
    "argv": ["python", "-B", "experiment.py", "--arm", "control", "--output", "outputs/control.json"],
    "outpaths": ["outputs/control.json"], "timeout_seconds": 4,
    "resource_estimates": {"wall_seconds": 5, "cpu_seconds": 5}
  },
  "observations": [
    {"fact": "control_mse", "path": "outputs/control.json", "selector": {"pointer": "/mse"}}
  ]
}
```

## Reuse qualified preparation

After the original `rsi extract`, `validate`, `register` and
[`prepare-application`](owned-tool-consumers.md), save its preparation JSON within
the project. A recipe route can specify
`"prepared_application": "reports/squares-prepare.json"`. Keep explicit `action`
and observations; supply only `timeout_seconds`, `resource_estimates` and optional
`description` in `run`.

RDS imports prepared bindings and derives the exact run ID, tool arm, argv and
output. It checks the original tool, adoption, validation receipt, case/input
bytes, action/goal identities, fixed driver and request. A report's status string
is insufficient. Changed files, unqualified inputs, extra premises or incompatible
commands reject initialization. This does not qualify or invoke a tool. Native
preparation costs, including failed candidates, remain chargeable by the original
initializer. The adapter currently requires a wall-only budget.

```powershell
python -B examples/result-tools/run.py --workspace <fresh-sibling-directory> --recipe
```

This executes the same three public finite tools and original consumers as the
example's default contract entry. It still qualifies each tool explicitly.

## Identity, retries and measurement

The compiler hashes each unique declared file once while assembling bindings;
native qualification, initialization and later admission retain independent
identity checks. There is no cross-invocation admission cache. The protocol is
written exclusively as canonical JSON plus a newline. Identical existing bytes
are reusable; different bytes fail. Recipe/report paths and bound inputs cannot
be route outputs or exact `output_files`.

Retrying the same compiled contract preserves original runs and spent/reserved
resources. A changed compiled contract fails before preparing a new protocol;
`--recipe` cannot be combined with `--supersedes`. Existing initialized projects
retain the revision/successor workflow. If initialization fails after protocol
preparation (for example because native preparation exceeds its original budget),
the immutable protocol may remain. No job starts or previous contract changes.
Inspect the original error and retain evidence instead of overwriting that file
or resetting the ledger.

```powershell
python -B examples/project-assembly/run.py --workspace <fresh-sibling-directory>
```

This paired comparison preserves the original public dataset, oracle, predicates
and two runs, checks recovery without repetition, and reports canonical JSON
input bytes, SHA256 field occurrences and actual CLI invocations. Its fixed goal
is MSE ≤ 0.01; the original treatment remains approximately 0.02184, so the goal
remains unmet. The baseline includes contract and protocol input; recipe input
excludes the generated protocol. Existing scripts and shell batching are valid
baseline capabilities. Fewer emitted fields do not establish fewer model round
trips, lower token usage or scientific gain; these remain `NOT_RUN`,
`NOT_MEASURED` and `UNKNOWN` in the report.
