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

Python generator commands must freeze effective pre-main `-S` and `-E` (or `-I`)
options. The example declares `-BES` before freezing the contract: `-S` disables
automatic site startup, and `-E` ignores mutable Python environment settings while
retaining ordinary local worker/helper imports. `-I` also removes the local script
search path, so use it only when the frozen worker explicitly supports that mode.
Consumed option values and post-script arguments do not supply isolation. Debug
`-X presite` module startup is unsupported. An old unisolated plan fails closed;
its historical argv and receipts are retained. Execution never adds flags to an
already admitted command. This boundary does not infer arbitrary import closure.

## Drive the full loop in one ledger

With both a frozen `jump-generation.json` and an autonomy policy, `project drive`
prepares the generator when the owned Advisor selects its first route. The same
drive executes each selected route, collects its original receipt, admits the
generated hypothesis against fresh owned evidence and places the jump packet in
the next actual model request. The model may adapt the proposed method; its
returned code still passes ordinary method revision and independent execution.

```text
python -B examples/jump-loop/run.py --workspace <new-absolute-sibling-workspace>
python -B examples/jump-loop/run.py --workspace <another-new-workspace> --native <absolute-codex-executable>
```

The default is an explicitly scripted engineering fixture. It computes the first
implementation from the received candidate expression, deliberately introduces
an offset, receives an independent `FAIL` and then returns a corrected method.
The second request must contain both the original generated jump and the exact
first counterexample. This exercises ten real project routes and two revisions.
The native option uses the existing frozen `codex_exec` adapter, Sol/high/default,
at most two provider calls and the same independent exact finite checker. It may
succeed earlier or stop without a valid method. Neither option measures scientific
gain; the public three-point polynomial is an engineering acceptance case.

After a compatible method revision, the packet validates the original generation
against verified contract ancestry. It carries origin/current contract hashes,
method adoptions and actual receipt/output locators. Its `CURRENT` status means
the projection was checked against the current ledger; `evidence_scope: HISTORICAL`
and `admission_authorized: false` preserve the status of the old hypothesis. Past
feedback cannot acquire current-scope support. Missing originals, incompatible
generator bindings or an unfinished generation crossing a revision stop visibly.

Nested structure control uses the drive's explicit controller reservation and is
charged once. Standalone structure calls retain their own bounded metering.
Direct `structure jump` reserves the next stage's controller work before its
worker is admitted: two seconds for probe/refresh consumption and fourteen for
the largest synthesis adoption (consume, fresh request, up to four proposals,
and finish). These are reservations within the original project budget, not new
budget or provider authority. Controller phases borrow the parent reservation;
only the outer controller scope charges elapsed control. Only the freshly
claimed worker interval settled by this invocation's original receipt is
excluded from controller time. Admission, observation, recovery and final
collection stay billed; an old receipt cannot offset current control overhead.
An interrupted direct reservation requires `structure recover`; it conservatively
charges the parent cap once with UNKNOWN final control cost. An active competing
controller returns `JUMP_BUSY`. Registration/execution races reconcile only an
exact owned manifest and its existing attempt; active observations require
recovery and never authorize another process.
Repeated drive calls recover original attempts and do not repurchase a completed
model request. `STEP_LIMIT` permits another bounded pass on the same ledger;
uncertain provider delivery still requires reconciliation. The example retains
each CLI response in `out/trajectory.json`, original provider traces and receipts,
and a summary showing actual status and unmeasured scientific gain.

## Return the jump to the AI

A new frozen plan can declare `generator_code_paths`, a nonempty bounded list
of frozen code bindings used by all three generator stages, including helpers.
Entrypoint paths are resolved before matching, including `./file.py`, a project
absolute path, and an explicit `-key=file.py` or `--key=file.py` argument. After a method revision these declared code
bytes must remain identical. A legacy plan without this field conservatively
requires all original code bindings to remain identical. The declaration does
not prove arbitrary dynamic import closure. The owning drive prepares only the
exact next-stage manifest; selecting a later stage or changing its manifest
returns `JUMP_WAITING_ADMISSION` before any initial generation request or run.

Generation completes by returning `agent_context`, a hash-bound
`rds-jump-packet-v1` containing the actual new premises, representation changes,
rival predictions, discriminating experiment, original source locators and current
independent feedback. The same projection is available as
`advise --working-set` -> `working_set.jump_packet`. A continuing Agent reads it
before choosing the next explanation or experiment, and records its decision in
the existing checkpoint. `NO_CANDIDATE` still returns the measured discrepancies,
retrieved inputs and search limits, giving the AI concrete material for another
approach without inventing an explanation.

The program-owned autonomy adapter also embeds this packet in the frozen model
request and the actual provider stdin prompt. When present, the reply requires
`jump_use_json`, a JSON string with the following shape:

```json
{
  "schema": 1,
  "packet_sha256": "<exact delivered packet digest>",
  "decisions": [
    {"id": "<delivered item id>", "disposition": "adapt",
     "reason": "The interaction explains the residual under these assumptions.",
     "next_step": "Implement that interaction and test its separating prediction."}
  ]
}
```

Every item needs exactly one `adopt`, `adapt`, `reject` or `defer` decision, a
reason and a concrete next step. The response validator checks the digest,
inventory and bounded nonempty decision fields, rejecting simple ACK-only replies.
It cannot establish the semantic quality or causal role of that explanation.
Ordinary method validation and independent execution still determine whether the
returned code can be adopted and what its result supports.

The existing ledger records `AUTONOMY_JUMP_REFERENCED` with the exact request,
original response receipt, decisions and returned source/policy hashes. Provider
dispatch also retains the prompt digest. Packet availability, attempted provider
delivery, a response that references the packet, method adoption and experimental
support remain separate observations. A dispatch intent alone does not prove
receipt by a provider; neither a citation nor adoption proves scientific benefit.

The packet is at most 32 KiB. Oversized content is retained whole in the existing
CAS with `NEEDS_ORIGINAL` and explicit omissions; altered or unavailable originals
yield `UNAVAILABLE`. An autonomous model request waits for complete current
evidence in either case. The Agent can inspect the original and continue through
the existing authorized workflow. Repeated packet reads do not start another
generator or model call. Tests exercise original jump projection separately from
real local fixture-provider stdin/response/adoption; no paid model or model-level
scientific improvement is measured by those tests.

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
Project-bound executables that start with a shebang are refused as direct Jump
commands. Call their interpreter explicitly with the required startup flags,
so admission can check the effective interpreter and its environment before
the frozen script. Binding script bytes alone does not freeze shebang startup.
