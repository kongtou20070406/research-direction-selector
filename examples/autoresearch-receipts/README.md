# CPU comparison, original receipts and recovery

From the repository checkout, with Python 3.11+ and a new empty sibling directory:

```powershell
python -B examples/autoresearch-receipts/run.py --workspace ../rds-autoresearch-demo
```

This reuses the [project-runner](../project-runner/README.md) fixture's six recorded points, constant control, line treatment and mean-squared-error calculation. There is no dataset download, model call or GPU work. The hypothesis is only that a line fits these points with lower recorded error than a constant; it is not a claim about unseen data.

For each arm, the script calls the existing `exec` CLI with a ten-second wall reservation, explicit data/configuration bindings and a required output. It calls the identical argv again and checks `EXISTING_JOB`, `execution_started=false`, original receipt equality and unchanged runs, receipts, exposures and budgets. Each arm must retain exactly one run and receipt. It adds no runner or execution semantics.

`summary.json` reports the two observed metrics and receipt/attempt identities. `records/` keeps each original CLI argv, return code, stdout and stderr; `.rds/exec/control` and `.rds/exec/treatment` contain the native frozen jobs, ledgers and artifacts. Do not modify or delete these records to obtain a fresh result. The script refuses a nonempty workspace. To observe an existing job yourself, execute the original argv in its saved `records/*-first.json`; keep all inputs and the explicit name unchanged. Changed inputs require a new reviewed identity rather than reusing a completed name.

The original process outputs are observations from a public engineering fixture. Successful execution, an improved fitted metric and receipt reuse are distinct from scientific validity. The report keeps `scientific_support=UNKNOWN`; [supported formal verification](../../docs/autoresearch.md#three-separate-kinds-of-evidence) is a separate statement/backend check. Failures retain their original records and costs; stop and inspect them rather than blindly rerunning a job.
