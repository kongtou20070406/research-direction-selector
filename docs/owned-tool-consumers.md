# Local tools consumed by an owned research decision

This optional bridge takes a locally qualified Python function through the
existing project ledger: **extract → validate → register → prepare application →
freeze → next/advance → original result → decision consumer**. It connects local
RSI assets to a declared current obligation; discovery or registration alone
does not establish applicability, execution or scientific benefit.

## Run the public finite example

From the source checkout, choose a fresh empty sibling directory:

```powershell
python -B examples/owned-tool-consumers/run.py --workspace ../owned-tool-consumers-demo
```

The script declares two independent oracle values, 14 for `[1, 2, 3]` and 18 for
`[-3, 3]`, before executing `sum_squares`. It first retains one failed local
candidate, then qualifies and registers the correct function. The frozen
application calls that exported function on exactly those two inputs. Its JSON
`/status` observation feeds the unchanged goal predicate `task.status == PASS`.
The oracle is frozen as evaluator bytes; these are reused development cases,
not unused confirmation or a general proof about the function.

The workspace contains `cli-transcript.json` with exact commands, exit codes,
stdout and stderr; full stage reports in `reports/`; the original application
output in `outputs/application.json`; and `summary.json`. The existing `.rds/`
ledger, artifacts and native qualification jobs retain the underlying receipts.
`reports/checkpoints.json` displays the existing before/after records, while
`reports/preparation-costs.json` displays qualification accounting. Existing
evidence is never overwritten by a rerun of the example.

## Prepare and freeze the actual consumer

The script uses these real CLI entries. Each RSI entry needs `--json` when a
caller needs the full machine-readable record rather than its normal digest:

```powershell
python -B scripts/rds_cli.py --root ../owned-tool-consumers-demo rsi extract --source ../owned-tool-consumers-demo/source.py --entry sum_squares --name squares --json
python -B scripts/rds_cli.py --root ../owned-tool-consumers-demo rsi validate --name squares --cases ../owned-tool-consumers-demo/cases.json --timeout 3 --json
python -B scripts/rds_cli.py --root ../owned-tool-consumers-demo rsi register --name squares --validation <returned-validation-id> --json
python -B scripts/rds_cli.py --root ../owned-tool-consumers-demo rsi prepare-application --name squares --inputs inputs.json --cases cases.json --code-path qualified.py --driver apply.py --request request.json --output outputs/application.json --decision ../owned-tool-consumers-demo/decision.json --action-file ../owned-tool-consumers-demo/action.json --candidate apply --run-id apply --obligation task.status --observation-fact task.status --json
```

These commands explain the script's construction; do not replay them against
its initialized workspace. Preparation returns `binding`, `required_bindings`
and exact `argv`, with `execution_started=false` and authorization unchanged.
The returned binding belongs in `advisor_policy.tool_bindings` before project
init. The contract must also bind the exported code, fixed program driver,
request, task inputs, evaluator cases and route protocol; authorize that exact
argv; declare the output observation; and include the matching action/route.
The script constructs the complete contract, including the combined code hash.
Validation exits 0 for `LOCAL_CASES_PASSED`, 1 for `FAILED` and 2 for `UNKNOWN`;
the example expects and retains the wrong candidate's exit 1 before continuing.

```powershell
python -B scripts/rds_cli.py --root ../owned-tool-consumers-demo project init --contract ../owned-tool-consumers-demo/contract.json
python -B scripts/rds_cli.py --root ../owned-tool-consumers-demo project next
python -B scripts/rds_cli.py --root ../owned-tool-consumers-demo project advance
python -B scripts/rds_cli.py --root ../owned-tool-consumers-demo project next
```

`next` rechecks tool records, refutations, native validation originals, current
task bytes, goal/action identity and the fixed driver/request/argv. A qualified
candidate still has to pass ordinary dependencies, method, history and budget
gates. `advance` selects at most one authorized route, rechecks admission,
settles its original receipt and collects the result. The exported driver is
ordinary trusted project code; this mechanism does not create an OS sandbox.

## Applicability, use and consumption are separate

The full owned report includes `tool_utilization.tools`, `counts`,
`applicable_use_rate` and `applicable_consumption_rate`:

| Report | Meaning |
| --- | --- |
| `APPLICABLE` | Exact current task inputs/cases and original local qualification match the frozen application. |
| `INAPPLICABLE` | A checked task mismatch, refutation or false required premise blocks this binding. |
| `UNKNOWN` | Required qualification, premises or verified application path is missing; tags and catalogue matches do not fill that gap. |
| `used=true` | The original receipt and fixed-driver output establish actual finite calls with the bound tool/input/case identities. |
| `consumed=true` / `result_consumed=true` | A reliable original observation is read by a current goal predicate or actually evaluated action predicate; the report lists those consumers. |

Both rates use only the current `APPLICABLE` bindings as denominator, with
`applicable_used` or `applicable_consumed` as numerator. `INAPPLICABLE` and
`UNKNOWN` counts remain separate. A zero denominator yields `null` rates and
`zero_denominator=NOT_APPLICABLE`; it is not zero utilization or success.
The declared binding scope is the inventory boundary, not every tool on disk.
Exact Python call inputs retain JSON types and mapping order: `true`, `1` and
`1.0` are distinct, and changing kwargs or nested mapping order needs new
qualification. Only the outer `args`/`kwargs` record order is irrelevant.
A tool may be used while its output has no decision consumer: that is reported
as `RESULT_NOT_READ_BY_DECISION` and does not count as consumption. Consumption
can include an unfavorable result; it does not imply goal success.
An unevaluated graph declaration, disconnected node or predicate for another
decision does not count as a reader. Consumers come from actual search goal,
derivation and evaluated condition traces rather than every predicate on disk.

## Budget and existing history

This native qualifier measures wall time, so this bridge currently requires a
wall-only project budget. When `tool_bindings` is present at genesis, even as an
empty inventory, init charges existing native validations under this root,
including failed candidates and conservative unfinished allowances, once to
that frozen budget through `TOOL_PREPARATION_COST`. The example's 40
seconds includes preparation plus the 10-second application reservation;
preparation is not free. Repeating init/review does not charge it again.
Unresolved or unpublished native preparations block owned initialization until
the original job is reconciled; retry retains its original attempt and receipt.

Qualify and prepare before owned init. Native validation after initialization
with tool bindings is rejected so it cannot become work outside the frozen
campaign budget. New executable routes require the existing authorized method
revision procedure; preparation does not add commands or expand permissions.
Post-hoc bindings cannot make unaccounted qualification free: that qualification
remains `UNKNOWN` and blocks application until its cost is accounted under the
supported prospective workflow. An explicit binding inventory and matching
finite task domain define the coverage; a full catalogue is not silently used.

Owned reservation records the actual decision and decision-time evidence in an
existing checkpoint; completion records the original receipt against that
decision. Subsequent reviews verify and consume that history. Recovery retains
live budget, attempts and exposure and does not rerun a completed application.
History uses the actual bound execution identity and links each committed choice
to its terminal receipt. Failed or timed-out execution remains operational
evidence, with scientific support `UNKNOWN`. Capacity is checked before a new
worker starts, and manual notes cannot take the slots reserved for completion.
See [program-owned Advisor](program-owned-advisor.md) for recovery and the normal
evidence-to-selection loop.

## Current scope

There are at most 16 explicit tool bindings. Qualification is currently
`EXACT_TASK_CASES` for native exported Python functions, with 1–64 finite inputs
and the fixed driver/request. It does not cover broad typed solver contracts,
automatic cross-task qualification, arbitrary CAS/Lean applications or automatic
new-route authorization. The script uses the source checkout; it does not update
an installed plugin. Local cases, applicability and decision consumption leave
scientific gain **UNKNOWN**; claims about research benefit need an independent,
matched evaluation including qualification, failure and verification cost.
