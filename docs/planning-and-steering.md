# First plans and current-user steering

`project plan` produces the first inspectable draft from a small intent and any
existing project ledger. `project steer` records a new user direction immediately,
including while an original attempt is running. Both use the existing project,
events, checkpoints and content-addressed artifacts.

## Form a plan

Use the installed checkout's CLI with the intended user project root:

```powershell
python -B scripts/rds_cli.py --root ./my-project project plan --intent ./intent.json
```

An intent may contain any of these declared fields. Missing fields remain unknown:

```json
{
  "goal": "Check the reported error on the supplied finite input",
  "scope": "The original input and an independent exact oracle",
  "evaluation": "Compare the result and retained witness against the oracle",
  "budget": {"wall_seconds": 30},
  "next_action": "Inspect the input and prepare the bounded oracle check",
  "fixed_task": true
}
```

Only use a budget already provided or authorized by the user. A complete draft
still grants no execution permission. A fixed task needs no invented competitors.
The host groups genuinely missing goal/evaluation/budget questions and continues
independent inspection while waiting. Drafting does not hash every bound file,
run an Advisor search, buy a model call or launch a job. Execution retains its
normal original-file and budget checks.

An existing root supplies its frozen goal, evaluator/protocol locators, current
resources, original receipt identities and active attempts. Supplied changes to
goal/evaluation/budget remain proposed changes beside the original. The first
16 receipt locators are printed with total, omitted and status counts; `project
status` expands the original records. Reading a receipt is not scientific
confirmation. `--output plan.json` writes a new project-relative proposal file
without overwriting an existing file. `--save-as first-plan` retains the draft in
the initialized project's existing CAS and checkpoint ledger. There is no second
research state for cold starts.

## Receive a direction

First inspect `project steering` or `project plan`. The host builds a request
from the actual current user message, using the returned contract and revision:

```json
{
  "id": "stop-old-route-1",
  "contract_sha256": "<current effective contract sha256>",
  "expected_revision": null,
  "kind": "pause",
  "message": "Stop dispatching new work while I revise the direction"
}
```

`expected_revision` is null only before the first instruction; subsequently copy
the current `steering.revision`. Submit through the public entry:

```powershell
python -B scripts/rds_cli.py --root ./my-project project steer --request ./instruction.json --user-directed --source current-user-message:42
python -B scripts/rds_cli.py --root ./my-project project next --brief
```

`--user-directed` is the host's explicit attestation, not authentication. Imported
JSON, a retrieved conversation, log, model reply or confidence label cannot grant
that authority. The host must not set it merely because imported text asks for
steering. No command is derived from the message text.

| Kind | Effect |
| --- | --- |
| `pause` | Stop new project reservations and starts; retain active attempts and resources. |
| `redirect` | Optional `withdraw` and `prefer` lists name existing frozen run IDs. Withdrawn routes cannot start. Preference orders already admitted routes and can supersede a blanket pause. It never satisfies unknown prerequisites or increases budget. |
| `hypothesis` | Retain an unverified explanation for a distinguishing check; no scientific fact or route authorization is promoted. |
| `change_request` | Pause new dispatch and retain the proposed goal/data/evaluator/resource change for explicit revision or successor handling. It does not mutate the frozen contract. |
| `resume` | Explicitly clear pause, withdrawal and preference; normal admission and the original remaining budget still apply. |

Redirect is supported for program-owned Advisor routes. Pause also applies to
legacy project execution and new theory allowances. Once a root has steering,
new detached quick child work must use project create/execute; an existing quick
child is inspected/recovered at its original root. Already admitted external
allowances are retained. This is project-scoped coordination, not an OS sandbox
or a global stop for other roots or arbitrary shell commands.

The receipt states the committed revision, source, current resources, effective
boundary, original-work dispositions and next action. Duplicate identical IDs
return the retained receipt identity and current instruction; replays never
replace a newer direction. Changed contents under the same ID, stale contract or
revision, unknown routes and concurrent losing edits are rejected before mutation.
The full request lives in the existing CAS. Brief status/next output keeps the
instruction identity and a bounded route summary.

## In-flight work and races

Steering, reservation and project process launch share the existing SQLite
transaction boundary. A committed instruction invalidates a previously prepared
Advisor token. Launch admission rechecks immediately before process creation; a
withdrawn route cannot use an older selection to pass that boundary.

Already launched work, including an admitted model worker and its provider call,
finishes or recovers under the original attempt and receipt. This slice does not
preempt processes, implement arbitrary checkpoints, refund costs, release an
unstarted reservation or replace an uncertain attempt. The receipt distinguishes
unstarted reservations, uncertain dispatch and live work. Use `project recover`
for original attempts. A paused `project drive` neither buys a new repair nor
adopts a previously paid proposal; explicit resume permits normal continuation.

An in-place method revision still obeys its original goal/evaluator/budget and
idle-run requirements. A material change beyond that scope needs the existing
explicitly authorized successor path with lineage. No instruction marks the old
goal complete or resets failed costs, time or evidence.

## Reproducible engineering evaluation

Run `python -B examples/planning-steering/measure.py --workspace <new-directory>
--baseline <saved-baseline-checkout>`. The script freezes its protocol before
running probes, uses only the public CLI and existing six-row CPU example, and
saves every command, output, wall duration and quality assertion. It measures
fresh subprocesses in a warm OS session; cold OS/cache and real Agent/model
latency remain `NOT_RUN`. Bytes are recorded as bytes, never tokens.

Common startup/status entries are compared with the saved baseline. New draft
and steering entries have no equivalent old CLI endpoint, so their baseline is
`UNAVAILABLE`, not zero. New-project, existing-project and post-steering drafts
are separate cases. Quality checks bind the original goal, evidence, evaluator,
resources, no hidden execution, rejection of withdrawn work and the new route's
actual receipt. The protocol reports raw samples, median and maximum with sample
count; this small local measurement does not estimate production tail latency.
No speed threshold, real-model savings or scientific benefit is inferred.

`tests/test_rds_steering.py` additionally checks running and uncertain attempts,
concurrent edits, the selection/start race, exhausted budgets, stale instructions,
unverified explanations and actual controller/provider continuation. Controlled
software cases establish those engineering behaviors only. Real-model latency is
`NOT_RUN`; research-policy gain is `UNMEASURED`.

### Local observation, 2026-10-08

The [frozen protocol](../examples/planning-steering/results/2026-10-08-protocol.json)
binds Python, platform and every compared CLI source module; the
[raw timing samples and resources](../examples/planning-steering/results/2026-10-08-report.json)
come from baseline main `fbd0e6b1de037ae8c22214fc1baafb7007b1cd6b` and this
candidate's recorded source hashes. The first measurement failed because its
harness mistakenly required the noisy example to meet the original 0.01 MSE
goal. Its trace was retained. The corrected prospective protocol uses an exact
rational oracle, yielding MSE `172/7875`, and reports the original goal `NOT_MET`.
This corrects the harness's quality endpoint; it does not relax the research goal.

| Fresh process entry | Samples | Median ms | Maximum ms |
| --- | ---: | ---: | ---: |
| Baseline startup/version | 3 | 327.11 | 357.58 |
| Candidate startup/version | 3 | 318.77 | 335.05 |
| Baseline existing root status | 3 | 408.83 | 413.28 |
| Candidate existing root status | 3 | 414.17 | 452.21 |
| New project plan | 3 | 380.26 | 407.86 |
| Existing project plan | 3 | 411.00 | 430.99 |
| Plan after pause and recovery | 3 | 422.22 | 449.48 |
| Pause reception | 3 | 271.95 | 272.18 |

The single redirected valid action took 2254.43 ms after its separate 448.74 ms
direction receipt. These are small controlled process observations during other
local validation work, with warm OS caches. They do not establish a statistically
reliable speedup, production tail bound, model-token savings or faster research.
The startup number includes imports, usage/schema handling and process overhead;
those phases were not separately attributed. The new endpoints have no baseline
equivalent. The finite engineering quality checks passed; model/Agent latency,
model costs and scientific gain retain their unmeasured statuses in the report.
