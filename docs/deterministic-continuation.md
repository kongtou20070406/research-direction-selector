# Continue ordinary work to a judgment boundary

An owned project already freezes its goal, routes, original result readers and
budget. Use one bounded foreground pass to run its program-selected steps:

```powershell
python -B scripts/rds_cli.py --root <project> project drive --until-judgment --controller-wall-seconds 2 --max-steps 8
```

For an ordinary `advisor_policy`, explicitly choose a positive control allowance
of at most 120 seconds from the **existing wall budget**. The allowance is held
during the pass, so leave enough unreserved budget for the frozen workers. It is
not additional budget. A project with `advisor_policy.autonomy` uses its frozen
controller allowance; omit `--controller-wall-seconds` for that project. An
override is rejected. The default `project drive` still requires autonomy and
retains its existing authorized model-repair behavior.

The opted-in pass uses the existing Advisor, graph/rank/tool preferences,
admission transaction and execution receipts. It respects human steering,
per-pass executions, any frozen cumulative autonomy cap, campaign deadline,
resource reservations and method constraints. It reconciles an original paid
result before continuing, but does not request or start **new model reasoning**.
An existing prepared repair request returns `MODEL_REQUEST_READY` with its
original input reference; an uncertain paid delivery requires reconciliation.
This stop applies to the controller's declared repair slots. Frozen project
commands remain trusted code and must be scoped when the project is initialized.

## Read the stop before acting

The returned `status` retains the precise controller outcome. Its `handoff`
contains the original goal's contract locator, a response class, original
evidence references and the remaining resources. Common response classes are:

| Response | Meaning |
| --- | --- |
| `BOUNDED_CONTINUATION` | This pass reached its step/transition limit. A later bounded pass rechecks original admission. |
| `WAIT_FOR_ORIGINAL_ATTEMPT` | Observe/recover the original worker; do not start a replacement. |
| `REPAIR_ORIGINAL_EVIDENCE` | Repair collection from original artifacts, then inspect `project next`. Completed work remains paid and is not repeated. |
| `RECONCILE_ORIGINAL_DELIVERY` | A provider result is uncertain. Retain the original call, receipt and cost. |
| `RESEARCH_JUDGMENT` | No further deterministic route was selected, or a prepared model request needs judgment. Inspect prerequisites, alternatives and budget before proposing a new method. |
| `HUMAN_STEERING` | A retained user instruction prevents dispatch or adoption. Inspect `project steering`. |
| `INDEPENDENT_CONFIRMATION` | Goal predicates or execution alone do not establish the declared domain result. |
| `INSPECT_ADMISSION_OR_RECOVERY` | Resolve the original admission/recovery failure; expanding resources or scope requires authorization. |

The evidence view reuses the owned [working set](advisor-working-set.md): exact
goal predicates, unresolved dependencies/hypotheses, scoped feedback and separate
operational outcomes. Each section shows at most four entries, with 2 KiB detail
limits and complete originals in the existing CAS. Read omitted originals before
a consequential choice. A refuted observation, failed process, unknown result
and false goal remain different observations. No handoff grants execution or
promotes a scientific claim.

`VERIFIED_STOP_SNAPSHOT` describes the checked evidence **before controller
settlement**. The controller's release transaction supplies `resources` and
`resource_cut` separately; its cost changes the live fingerprint. This view is
not a current admission token. If the report is absent, stale, failed or cannot
be projected within the control allowance, `evidence_status` is `UNAVAILABLE`
and its goal status is `UNKNOWN`. Inspect `project next` to recollect before
making a consequential choice. Recovery never changes an original receipt.

## Costs and finite comparison

Control work, including the handoff projection, is measured and charged using
the existing drive reservation/release events. Original worker receipt wall
time is subtracted once from elapsed controller time; control overrun is charged
and no later worker is admitted after the allowance is exhausted. CLI startup,
caller/agent work and analysis outside the controller are separate costs. A
complete research comparison must include those costs and unknown measurements.

The [finite example](../examples/deterministic-continuation/README.md) compares
efficient ordinary `advance` orchestration with one `drive --until-judgment` call on the
unchanged six-row public workload, recording original attempts, receipts,
goal outcome and elapsed time. It invokes no model. Reduced caller turns in
that fixture do not measure token savings or scientific acceleration.
