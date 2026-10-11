# Host command hook and coverage

The 5.8 vision (lines 60-62) requires two enforcement layers: the native RDS
controller owns admitted processes and budget reservations, and a host command
hook restricts research computation to that controller by checking a **bound
request/admission identity** — not whether a command string contains `rds`.
This slice ships the hook, its coverage report and its bypass tests for the
first supported host (the Windows Task Scheduler registration used by
`project execute --background`). It is not an OS sandbox and requires no
Docker, VM or container.

For an opt-in structured stdio entry bound to one original campaign ledger, see
the [fixed-campaign host broker](host-broker.md). Its auxiliary Codex filter has
partial coverage; neither entry implements OS isolation or same-user protection.

## Commands

```
rds_cli.py host-hook install                      # strict coverage (default)
rds_cli.py host-hook install --permissive         # advisory record, same refusals
rds_cli.py host-hook coverage                     # report covered/uncovered surfaces
rds_cli.py host-hook validate --request req.json  # check one dispatch request
```

`install` requires an existing project ledger (`.rds/project.sqlite3`) and
writes `.rds/host-guard.json` with the supported host list and a digest over
the guard record plus the ledger file. `coverage` without an installed guard
reports `HOST_GUARD_MISSING` (exit code 2) and claims nothing.

## Admission identity

A dispatch request must carry the ledger-issued identity of the attempt it
wants to run:

```json
{"host": "windows-task-scheduler", "run_id": "r1",
 "attempt_id": "<controller-issued attempt id>",
 "argv": ["<python>", "-B", "code.py", "ok", "outputs/r1.json"],
 "executor_sha256": "<frozen executor digest>"}
```

`validate` refuses the request before dispatch when the guard is missing, the
host is not covered, the run is unknown, the attempt id does not match the
ledger-issued admission, the admission is terminal (`COMPLETED`/`FAILED` runs
are never rerun), the argv differs from the admitted manifest, or the executor
digest differs from the frozen one. A forged request with the right argv but a
guessed attempt id is refused — the check is identity binding, not string
matching.

## What the hook claims and does not claim

- Covered: a Task Scheduler worker that calls `validate` (or the controller
  path that issues the identity) runs only ledger-admitted attempts.
- Explicitly uncovered, reported by `coverage` under `uncovered`: direct shell
  execution, other host mechanisms, and processes started outside this project
  root. `claims_protection_against_direct_shell` is always `false`; Skill
  instructions alone cannot supply that guarantee.

## Bypass evidence

`tests/test_rds_host_hook.py` drives the real CLI entry point
(`python -B scripts/rds_cli.py host-hook ...`): forged attempt id, mismatched
argv, wrong executor digest, uncovered host, unknown run, missing field,
missing guard and terminal admission are all refused with `Host hook refusal`
and exit code 1; the genuine bound request is admitted. A controller-only test
would report narrower coverage; the coverage report states that difference
explicitly instead of implying host-wide protection.

## Regression coverage

- `tests/test_rds_host_hook.py` — coverage-missing claim discipline, install
  preconditions, real-request admission, six refusal cases and terminal-admission
  refusal, all through the public CLI.
