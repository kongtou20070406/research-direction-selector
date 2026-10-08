# Generate explanations from three sources

An opt-in project can produce a concrete new explanation when `structure drive`
has no proposal. It runs three frozen routes: **probe → refresh → synthesize**.
The resulting explanation has new premises, rival predictions and an independent
experiment. Existing `structure propose`, `next`, `advance` and `feedback` retain
the original goals and decide what the observed result supports.

```text
python -B examples/jump-generation/run.py --workspace <new-absolute-workspace>
python -B scripts/rds_cli.py --root <project> structure jump --steps 3
python -B scripts/rds_cli.py --root <project> structure drive
```

`jump` runs at most three generation routes per call; its default is one. `drive`
advances one generation route when it would otherwise request open exploration.
It returns the generated proposal before experimental execution; the next
`drive` call goes through the ordinary candidate selection and experiment path.
Projects without the binding keep the existing Agent handoff.

## What actually generates the explanation

The bundled, executable finite integer adapter demonstrates all three inputs:

| Input | Executed operation | Downstream use |
| --- | --- | --- |
| Diagnostic measurements | Query the frozen synthetic instrument at declared inputs, compute baseline residuals and counterexamples | Every observation constrains the expression search |
| Scoped sources and retained branches | Query a frozen local corpus, retain source text/locator/date/applicability and its file hash; read typed operator hints and branch ASTs | Hints select within the authorized grammar; branch ASTs seed the search |
| Program synthesis | Enumerate exact integer ASTs, apply safe identities, reject counterexamples and choose a separating declared probe | Generate a new model node, executable expression and two precommitted numeric predictions |

The default example searches for the interaction term from observed rows; the
answer expression is absent from its plan and corpus. An independent evaluator
then compares the generated predictions with a frozen finite table. Tests remove
the retrieved operator, change an acquired observation and change a retained
branch under a matched search cap. Each affects the actual generated result.

This adapter performs bounded symbolic fitting, not continuous symbolic
regression over arbitrary scientific data. It supports `add`, `sub`, `mul` and
`mod`, exact integers within ±10¹², at most 9 AST nodes, 10,000 construction
attempts, 128 observations/probes and 64 retained seeds. Duplicate constructions
and rewrites consume the attempt cap. Only syntax and supported exact identities
are deduplicated: equal observed values do not prove equivalent theories.
Undefined arithmetic cannot be rewritten away. Search limits, filtered counts,
selected seed, exhaustion and observational coincidence remain visible.

The demonstration's instrument, source note and retained branch are labelled
synthetic public fixtures. Its corpus retrieval is executable local retrieval;
it does not claim a live web search, general text-to-theory understanding, sealed
benchmark performance or scientific benefit. The generator does not read the
independent table, but trusted project commands are not a security sandbox.
Literal prose is never executed. A document without usable typed hints contributes
source material without pretending to generate a new domain premise.

## Bind a project's tools before execution

Bind `jump-generation.json` uniquely as `role: config` in the existing contract.
The schema is `{"schema":1,"stages":[...]}` with exactly three ordered entries:

```json
{
  "kind": "probe",
  "run": {
    "schema": 1,
    "id": "jump-probe",
    "arm": "tool",
    "argv": ["<frozen executable>", "<frozen tool>", "out/probe.json"],
    "protocol_path": "protocol.json",
    "outpaths": ["out/probe.json"],
    "resource_estimates": {"wall_seconds": 10},
    "timeout_seconds": 10
  },
  "output": "out/probe.json"
}
```

Follow with `refresh` and `synthesize` entries, each with unique run IDs and
outputs. `protocol_path` resolves its hash from the original frozen protocol
binding; embedding that hash in the config would create a config/protocol hash
cycle. Everything else uses the ordinary project manifest and resource rules.
All commands, executables, programs, data, source corpora and evaluators must
already be bound and authorized. Existing owned-Advisor admission also applies;
this adapter adds no bypass for an ineligible tool command.

A project-specific probe can measure a new sample or run a counterexample solver.
A project-specific refresh route can use an authorized literature or history
tool, retaining its actual bounded originals and source identities. This is the
extension point for live retrieval; credentials, network permission and provider
budget are supplied through the existing project setup. Failed or inapplicable
retrieval stays visible. Adding new executable code uses the existing method
revision path. The built-in worker needs no model or paid provider.

## Result and provenance contract

Every stage emits strict JSON, at most 128 KiB:

```json
{
  "schema": 1,
  "kind": "refresh",
  "request_sha256": "<original structure request digest>",
  "inputs": [
    {"run_id":"jump-probe", "receipt_sha256":"<receipt digest>",
     "path":"out/probe.json", "sha256":"<original output digest>"}
  ],
  "result": {}
}
```

The first stage has no preceding inputs. The second names the first, and the
third names both in order. The controller rereads original successful receipts
and output bytes, verifies these links and appends their locators to each
proposal's sources. Source identity proves which material was consumed; it does
not prove the source claim true or independently establish semantic grounding.
Domain adapters and perturbation checks must demonstrate how content affects
the generated expression. The final result contains `status: PROPOSED` with
1–4 ordinary structure `proposals`, or `status: NO_CANDIDATE` with an empty list
and a reason. All proposal validation and feedback-trigger requirements remain.
When adaptive search is bound, admission uses a fresh request that includes the
three generation receipts and claims an existing exploration slot. The original
generation request remains linked through the stage envelopes; no stale-evidence
guard is relaxed.

## Stops and recovery

The plan has one stable generation identity per frozen contract. Repeated calls
consume the same original request, attempts and output hashes. A reserved route
can proceed; a dispatched route is recovered before further work; failure or
uncertain delivery never silently starts a fresh attempt. Completed sources are
revalidated on reuse. Controller work is metered in the same wall budget.
Interruption of controller work uses `structure recover`; interruption of a
worker uses the original `project recover` path. No second ledger is created.

`NO_CANDIDATE`, source failure, changed output, budget refusal and stale scope
remain distinct. An additional generation round needs an explicit compatible
revision with new routes and retained past costs. No-candidate is only exhaustion
of this declared search, not evidence that no explanation exists. A generated
proposal remains `UNKNOWN` until the existing independent experiment provides
evidence. Even a successful finite test does not establish general abductive
ability or improved scientific outcomes.
