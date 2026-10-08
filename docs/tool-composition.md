# Discover and compose local result tools

`rsi discover` discloses selected local function signatures and existing frozen
tool routes. `project compose-tools` runs a bounded declarative list through the
original project admission, budget, receipt and Advisor collection machinery.
Both use the existing research ledger and CAS; there is no second execution log.

This first engineering slice covers locally extracted Python tools already
prepared for an owned project. It does not discover remote services, install
dependencies, supply arbitrary shell/code evaluation, or infer authority from a
tool description. No provider trial or scientific effectiveness is established.

## Discover only the needed capability

```console
python -B scripts/rds_cli.py --root ../campaign rsi discover --name extract
python -B scripts/rds_cli.py --root ../campaign rsi discover --obligation extract.value --limit 4
```

Filters are exact local names and exact current policy obligations. Without a
filter, at most eight local tools are returned (configurable from 1 to 32).
Each includes its immutable record/code hash, parameter names, calling kinds,
required flags and up to eight frozen routes with original qualification refs,
resource estimates, timeout and observed run status. Large details carry a CAS
locator and an explicit omission indicator. Additional route identities remain
in `routes_original`; omitted tools require a more specific name/obligation.
Parameter types and external availability are not inferred from signatures.

`CAPABILITY_GAP` means no matching local record was found, not that the operation
is impossible. `NOT_CHECKED` applicability and `NOT_PROVED` purity are deliberate:
discovery validates record/blob integrity, but eligibility is recomputed from
original evidence at collection and execution. Discovery never starts a tool.
Use existing `rsi extract`, `validate`, `register` and `prepare-application` when
no qualified route exists; see [owned consumers](owned-tool-consumers.md).

## Submit a bounded request

Copy the current `contract_sha256` from discovery or `project status`. Save this
JSON **inside the campaign root** as `composition.json`:

```json
{
  "schema": "rds-tool-composition-v1",
  "contract_sha256": "<current 64-character contract SHA256>",
  "max_wall_seconds": 60,
  "steps": [
    {"op": "status"},
    {"op": "collect"},
    {"op": "execute_tool", "run_id": "extract"},
    {"op": "collect"},
    {"op": "costs"}
  ]
}
```

```console
python -B scripts/rds_cli.py --root ../campaign project compose-tools --request composition.json
```

There are at most 16 steps and eight distinct execution identities. Unknown
operations, extra fields, duplicate execution identities, unfrozen runs and
contract mismatches reject the **entire** request before any execution. The four
operations are:

| Operation | Existing operation and boundary |
| --- | --- |
| `status` | Read a coherent project snapshot; return budget and run counts plus the original snapshot locator. |
| `collect` | Run owned Advisor collection against original sources, qualifications, receipts and dependency state; retain adverse evidence, coverage and next action in the original report. |
| `execute_tool` | Accept only a frozen tool-bound run that is the current admitted selection. Register/execute through the existing kernel and collect afterward. |
| `costs` | Summarize existing receipts, preserving missing quantities and identity conflicts; no missing quantity becomes zero. |

`max_wall_seconds` is a prospective **scheduling window**, between 0 and 300
seconds exclusive of zero. Each new job's original timeout must fit the remaining
window, including at the transactional admission check. Collection and reporting
can finish after the window; this is not a hard process deadline. Original job
timeouts, budget charges/reservations, qualifications, steering and admission
remain authoritative. A later step receives no speculative authorization from
an earlier selection. A changed selection, exhausted budget, stale input,
collection failure or unresolved scientific decision causes a handoff.

## Originals, failures and recovery

Each compact step has an original CAS locator. Executions retain the original
attempt and receipt hash, bounded error details, tool use/consumption information
and the post-execution collection report. The overall CAS report preserves the
request digest and completed/remaining step counts. Read the referenced originals
when a field is omitted or evidence conflicts; compactness is not acceptance.

`COMPLETED` means all requested operations returned, not that the scientific goal
passed. `scientific_support` stays `UNKNOWN`. `HANDOFF` stops the list and returns
exit code 2; a malformed request rejects with exit code 1. Partial cost knowledge
is visible and may coexist with completion; cost identity conflicts stop the list.

Repeating a request observes/reconciles a dispatched run via `project recover`;
it never launches that identity again. A possibly active controller keeps its
reservation; a lost controller produces the kernel's original interrupted
receipt. If a tool finished but collection failed, repair collection or missing
originals instead of rerunning the tool. An undispatched reservation can still be
admitted later after its current evidence and budget checks pass. Inspect status
and receipts when the CLI response itself was lost.

## Same-work comparison

The [result-tool example](../examples/result-tools/run.py) runs the same frozen
synthetic extraction, matched metric comparison and adverse-result inventory:

```console
python -B examples/result-tools/run.py --workspace ../result-tools-existing
python -B examples/result-tools/run.py --workspace ../result-tools-composed --compose
```

Both invocations are already programs batching host calls. Compare their three
original application outputs, consumed results, checkpoints, receipts, preparation
charges and recovery stability. `cli-transcript.json` retains every actual CLI
call. The existing consumer sequence uses five calls; composition uses one plus
a separate verification call, with discovery counted separately. Internal
admission/collection work is retained, and composition can perform more internal
collections. This does **not** demonstrate a reduction over ordinary host batching
in model round trips, latency or token cost. Real provider calls are `NOT_RUN`,
token savings are `NOT_MEASURED`, and scientific gain remains `UNKNOWN`.

The three-tool example reserves a 120-second scheduling window for repeated
original evidence collection and waits up to 180 seconds for each composite CLI
response. Its existing project execution budget remains 60 seconds, and each
tool keeps its 10-second timeout. `--recipe --compose` also exercises the same
consumer through the recipe initializer. A scheduling or host timeout is a
retained failure/handoff, never permission to overwrite evidence or repeat a
completed attempt.
