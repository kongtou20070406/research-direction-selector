# Frozen receipt wheel

This example advances a pending factor queue using receipts from the existing
`rds_cli.py project` executor. It is a deterministic, synthetic control-flow
fixture. It does not establish scientific accuracy or research-policy benefit.

From the repository checkout, use a **new, empty sibling directory**:

```powershell
python -B examples/wheel/run.py --root ../wheel-demo
python -B -m unittest discover -s tests -p test_rds_wheel.py -v
```

The fixture freezes `contract.json`, `control.json`, `treatment.json` and the
three lines in `factors.txt` before its first receipt. `prepare.py` provisions
the trusted evaluator/data, computes their exact hashes, registers the slice
`wheel-<first 16 hex of sha256(contract_id)>`, and freezes every permitted
command in the original project contract. The static contract hashes describe
the distributed evaluator/data; provisioning computes hashes for the exact
prepared bytes before initialization, including explicit synthetic test variants.

The two bootstrap executions are control and treatment. The wheel then executes
`flat`, `benefit`, and `plateau`; `dead.txt` grows to `flat` and `plateau`, and
`pause.json` is terminal. The report asserts three post-bootstrap executions and
three changed cells. This equality belongs to this fixture: SCREEN executions
are separately budgeted probes and do not require a preceding changed cell.
The report retains original project receipt hashes and `project costs` output.

## Individual ticks

```powershell
python -B examples/wheel/prepare.py --root ../wheel-manual --bootstrap
python -B scripts/rds_wheel.py --root ../wheel-manual
```

Invoke the second command again to advance one tick. `run.py` is a bounded demo
driver; it delegates every experiment to the existing project executor. The
only project actions used are `init`, `create`, `execute`, `status`, and `costs`.
For an independently prepared frozen project contract, initialize with
`scripts/rds_wheel.py --root <root> --initialize <project-contract.json>`.

The frozen config binding has schema `rds-wheel-setup-v1`: the wheel contract,
evaluator/data paths, all agent-writable roots, initial factors, control/initial
templates, and per-factor `main`/`screen` templates. Each template declares its
ID, factor, positive `reserve_ms`, and exact allowed argv. Commands must be
identical to control except the single `--factor` value and output path. Main
IDs equal their factors. The adapter currently requires a wall-only resource
budget and one JSON metric output per receipt. `prepare.py` is the complete
reference mapping. Missing mappings return 2; proposed text never becomes code.

The evaluator must be outside `inbox`, wheel state, output roots, and every
declared agent-writable tree. The trusted host must enforce those capability
boundaries when connecting an agent. RDS trusted command execution is not an OS
sandbox. In particular, do not give a model unrestricted filesystem/terminal
access and then treat this path check as process isolation.

## Cell and queue semantics

Initialization writes the complete wheel into a private staging directory under
`.rds`, then publishes it with one rename after the native project accepts the
same contract. A stable OS lock serializes initializers. A failed write or exit
before publication leaves no final wheel; retrying the same contract preserves
the native ledger and budget. A different contract is rejected. Stale staging
directories are never trusted as initialized state. This is process-interruption
recovery, not a cross-storage power-loss transaction.

Metric direction and useful-delta threshold come from the frozen project
contract; the wheel contract must agree. Receipt bindings, output hashes and
the pre-registered slice are checked. Exit code does not determine the cell.
Both the evaluator and declared data slice must be outside every declared
agent-writable root and project output root before initialization.
Either absent/nonfinite metric yields UNKNOWN; threshold equality qualifies.
`cell changed` compares the new TRUE/FALSE with the preceding processed cell;
the first known cell changes from the initial unset state.

A zero delta appends the factor to `dead.txt` and exits without execution in that
tick. The following tick consumes this result and advances. `factors.txt` is a
pending queue: dispatch consumes its selected live line. Receipt identity,
processed transitions and pending execution are retained separately. A nonzero,
unchanged cell with live factors idles. With no live factors, the wheel tries
SCREEN before pausing for lack of eligible proposals. It pauses before exceeding
the next reservation, and never widens `band.json` from `{"width":"narrow"}`.

If a process stops after journaling dispatch but before a native attempt, the
next tick retains that same manifest and completes its remaining registration
and execution. It requires the exact frozen manifest and native contract, and
only executes an unstarted RESERVED run with no attempt or worker identity.
An existing attempt, terminal run without a receipt, mismatched manifest or
unknown native state requires inspection; a tick never redispatches it.

SCREEN selects the oldest unprocessed eligible inbox row, with default quota 1
keyed by proposer ID. Its budget is `min(screen_wall_ms, remaining_wall // 10)`.
An unchanged screen halves quota with integer division; a changed screen adds
one, capped at 2, and admits its factor. A zero-delta factor remains dead. Rows
remain in the append-only inbox, including rejected/processed rows. Each proposal
is screened once; repeat attempts need a distinct, preauthorized execution
mapping. A previously registered SCREEN run or retained manifest consumes that
mapping for every proposer. Duplicate rows remain in the inbox without charging
their proposer; selection continues to the next eligible distinct mapping, or
pauses when none remains. Known ID/file collisions are checked before committing
pending intent. Screening never directly admits an unmeasured inbox proposal.

`state.json` journals quota, queue, dead and transition updates so interruption
does not repeat a quota update. A pending attempt without a receipt returns 4
and retains its original registration for operator inspection; it is never
automatically resent. Only `rds_wheel.py` writes `.rds/wheel/`. `pause.json` is
written once; every later tick returns 0 without calling the project CLI.

## Optional external model handoff

The tick never calls an LLM. After at least two ticks, with no live factors,
no pending execution and no pause, a trusted caller may request:

```text
python -B scripts/rds_wheel.py --root <root> --prompt
python -B scripts/rds_wheel.py --root <root> --propose <proposer_id>
```

`--prompt` emits exactly the bytes of `dead.txt` followed by the last cell JSON.
Pass those bytes unchanged as model stdin; provide no additional instructions or
filesystem write capability. Feed stdout to `--propose` on stdin. Only one token
matching `^[a-z][a-z0-9_]{0,31}$` is accepted (one terminal newline is allowed),
after rechecking the evaluator hash. The wheel alone appends the resulting
proposal to `inbox.jsonl`. Invalid/dead tokens are discarded. A token still needs
a frozen execution mapping before it can be screened. Collect proposals before
the next tick would find no eligible work and write terminal pause.

Proposer IDs are limited to 256 UTF-8 bytes, each proposal row to 4096 bytes,
and the append-only inbox to 1024 rows and 1 MiB. A full inbox rejects new rows
without changing retained bytes or quota. Consumption uses a bounded read and
rejects an oversized inbox; malformed rows remain retained and cannot prevent
selection of a later valid row within the limits.

## Exit codes and evidence limits

| Code | Meaning |
| --- | --- |
| 0 | Idle, paused, dispatched, or a receipt was consumed |
| 2 | UNKNOWN metric/missing mapping, invalid input or unresolved operation error |
| 3 | Frozen input/evaluator hash or retained evidence mismatch |
| 4 | Missing tick-0 pair or missing receipt for a pending execution |

The RSI helper reports eligibility only when violations are zero, spin count
does not increase, and synthetic budget-to-TRUE improves by the requested delta.
It neither adopts nor edits rules. This change implements no paired rule
evaluation, so `regret` and scientific accuracy gain remain `UNKNOWN`.
