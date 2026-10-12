# Work with an experimental assistant

Describe the research question, existing evidence and the resources you have
authorized. The assistant explains a useful next test, carries out admitted
work, interprets the actual result, and uses it to decide what to do next.
The question should determine the test: what observation would distinguish the
competing explanations, or close the specific obligation you asked to check?
Fixed tasks do not need invented alternatives.

The ordinary reply is connected prose, with a plot or original evidence link
when useful. You do not need to operate the CLI, choose from numbered menus or
decode status cards and hashes. Raw records remain available for inspection.
If there is no result yet, the assistant says what is missing. A failed process,
negative measurement and checked counterexample have different meanings; none
may be substituted for another. Scientific benefit is not established by a
successful command or a valid receipt.

You can say "continue", "try another explanation", "pause new work" or "just
look at the results". Within an identifiable task, continuation keeps the
original write scope, methods, budget, deadline and evidence standard. It adds
no authority. The assistant asks when the intended task is ambiguous or a
material goal, method or resource change needs your decision, then continues
the work already granted. A question or hypothesis does not clear a pause.
The existing `resume` instruction clears withdrawal and preference as well as
pause, so the assistant must establish that you intend those effects.
A human rejection retains its scope and reason; changed evidence or conditions
can justify reconsideration without turning that rejection into a theorem.

## Available routes

These are agent-facing entries in the current source, not commands you must
run. Placeholder files must contain the actual declarations and current
identities. Route availability does not grant admission or resources.

| Need | Existing entry | Boundary |
| --- | --- | --- |
| Find existing work | `project discover` | Reuse the returned canonical root and its history; discovery uses ordinary usage logging. |
| Explain a retained decision | `project plan --dialogue` | Reads saved advice and steering; stale or missing advice is not a fresh selection. |
| Start after discovery finds no project | `project init --recipe <recipe.json>` | Defaults to FULL; explicit semantics and authorized resources are required. |
| Add owned Advisor to existing QUICK | `project enable-advisor --policy <policy.json>` | Preview first; apply reviewed policy at the expected snapshot in the same ledger. |
| Inspect or advance owned work | `project next` / `project advance --brief` | Advance executes at most one program-selected authorized step. |
| Continue several owned steps | `project drive --until-judgment --controller-wall-seconds <seconds>` | Bound the controller by the existing budget or frozen autonomy allowance; honor its actual stop. |
| Inspect steering | `project steering` | Read current constraints before proposing a change; submission uses the existing steering route. |
| Run declared work across sessions on Windows | `project execute --id <run-id> --background` | Existing Task Scheduler runner, original admission and resource limits. |
| Recover original work | `project recover --id <run-id>` | Inspect original attempt identity and receipts; uncertainty does not authorize replacement execution. |

New-campaign setup and existing-QUICK activation are separate operations.
Neither a new name nor activation resets failed attempts, paid costs or
reservations. Workspace binding and campaign binding also have distinct
markers and checks; see the [preview binding contract](releases/6.0.0-rc.1-preview.md#workspace-ownership-and-prevention-of-resets).
For an actual change, use [planning and steering](planning-and-steering.md);
for owned execution and repair, see [owned Advisor](program-owned-advisor.md)
and [deterministic continuation](deterministic-continuation.md).
The [command map](command-map.md) covers other supported routes.

For developer-facing native boundaries and old-data compatibility, see the
[optional native transport](native-bus.md). It does not change the researcher's
ordinary conversation flow.

## Planned long-job events

Durable long-job event delivery and host wakeups are planned work, not an
available end-to-end service in this preview. Proposed event names are
`JOB_STARTED`, `CHECKPOINT_COMMITTED`, `RESULT_READY`, `JOB_FAILED`,
`RESOURCE_LIMIT` and `RESEARCH_JUDGMENT`; they are not CLI commands or delivered
receipts. The existing continuation response class `RESEARCH_JUDGMENT` alone
does not implement that event protocol.

The plan is to retain events in a durable outbox, deduplicate by job, attempt
and sequence, and track acknowledgements and retries. Completion, failure,
resource limits or a needed research judgment should wake the assistant;
routine loss updates should not require repeated model calls. Low-cost local
timers and occasional reconciliation would recover missed notifications.

Acceptance still needs real cross-session execution, checkpoint state sufficient
for the declared training workload, cancellation that cannot revive work, and
recovery without duplicate execution or charging. Existing background execution
does not establish those guarantees for the proposed event service. Until that
integration is verified, the assistant reports actual available status and
original outputs without promising automatic wakeup or measured performance.
