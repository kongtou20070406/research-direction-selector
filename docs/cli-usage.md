# Local CLI usage log

Every `rds_cli.py` invocation attempts to record its start and exit in a local SQLite log,
automatically. Updated checkouts share the current user's default log when that
location is writable. Calls include help, version, invalid arguments, failed
commands and statistics queries.

SQLite lock waits share a ten-second budget per logging phase for temporary contention;
persistent failures leave the command's original result unchanged. Existing journal
modes are retained, and fresh logs use SQLite's default mode without a first-use
existence/PRAGMA race. Cold or partial-schema writes initialize the schema and
write tracking and the start row atomically in one transaction before its first
commit. Cold readers commit schema preparation before opening their consistent
read snapshot; warm reads use the existing schema without reserving a writer.
A writer stalled inside a data transaction can still exhaust another
call's bounded wait; complete recording is not guaranteed during persistent contention.
The invocation reports a logging failure to stderr as
[RDS-USAGE-DEGRADED], naming the start/finish phase, exception type and available
SQLite error code without printing paths or argument payloads. Its original result
or exception remains unchanged even if the diagnostic stream is unavailable.
An unconfirmed start can be absent from counts; an unconfirmed finish can leave
an unfinished entry. The current process's logging=ENABLED is not proof that
earlier invocations were completely recorded. Missing history is not reconstructed.
Counts describe recorded CLI invocations; API imports and loading Skill instructions
have their own evidence. No prompt, full argv, input path or credential is stored.
The command labels cover the current public top-level CLI commands, including
`host-hook`. Older rows that labelled its calls `other` or generic `help` are
retained as recorded; their original command cannot be recovered from those rows.

Show recent daily counts (14 days by default):

```text
python -B scripts/rds_cli.py usage --days 7
python -B scripts/rds_cli.py usage --since 2026-09-01 --until 2026-10-01
python -B scripts/rds_cli.py usage --days 30 --json
```

Dates are inclusive and use the local calendar date recorded at invocation.
The output shows total recorded calls, daily counts, command/mode counts and
the tracking start. Dates before logging started show `-` in the table and
`calls=null, tracked=false` in JSON; they are not silently reported as zero.
Existing historical calls are not reconstructed from guesses. A query counts
itself, and its start is visible before its exit is written. JSON also reports
successful, failed and unfinished entries; an unfinished entry alone does not
establish that a process is still running.

On Windows the log is `%LOCALAPPDATA%/ResearchDirectionSelector/cli-usage.sqlite3`.
Other platforms use `~/.local/state/ResearchDirectionSelector/cli-usage.sqlite3`.
`RDS_USAGE_DB` can select a different file, including an isolated test log. The
log is independent of project budget/evidence ledgers and is never uploaded.

In a workspace-write sandbox, the default user-state directory may be outside
the agent's writable roots. If that automatic location cannot be opened or
written because of permissions or read-only storage, the CLI falls back to
`<root>/.rds/usage/cli-usage.sqlite3`, including for `--help`. The existing CLI
resolves the root, using the current directory when omitted; options belonging
to a wrapped child command do not select it. Start, report and finish use the
same file. `usage --json` reports the selected `log_path`. Fallback counts
describe that project-local file only and are not merged with the user-wide log.

An explicit `RDS_USAGE_DB` remains authoritative, even if that selected file is
unavailable: it does not trigger automatic fallback. The current CLI does not
recognize `XDG_STATE_HOME`. Busy writers (`SQLITE_BUSY`) and damaged databases
do not cause fallback. If the automatic location and fallback both fail, the
`RDS-USAGE-DEGRADED` diagnostic and original command-result preservation still
apply. Help may create the usage directory, but does not initialize a research
project or its budget/evidence ledger. Pass the intended `--root`; a successful
automatic fallback needs no environment-variable workaround.

SQLite transactions support concurrent CLI processes. Storage failures preserve
the original command's return/exception; usage queries expose unavailable or
degraded logging. Usage reports can run without initializing project budget or
evidence state.
