"""Real CLI fixture waits; no changes to logging or runtime policy."""
from contextlib import closing, contextmanager
import json
import hashlib
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import time

# One invocation can wait ten seconds at both start and exit logging, in
# addition to interpreter startup. This bounds the fixture, not product time.
CLI_WATCHDOG_SECONDS = 60


def ledger_snapshot(path):
    try:
        with closing(sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True, timeout=.1)) as db:
            return {"status": "READ", "calls": [
                dict(zip(("id", "started", "day", "command", "mode", "version", "exit_code", "elapsed_ms"), row))
                for row in db.execute("SELECT id,started,day,command,mode,version,exit_code,elapsed_ms FROM calls ORDER BY id")]}
    except (OSError, sqlite3.Error, ValueError) as exc:
        return {"status": "UNKNOWN", "error": str(exc)}


def run_cli(command, folder, ledger, *, watchdog=CLI_WATCHDOG_SECONDS, ready=None):
    began = time.monotonic()
    started_at = time.time()
    child = subprocess.Popen(command, cwd=folder, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True, encoding="utf-8")
    try:
        if ready is not None:
            ready(child)
        try:
            stdout, stderr = child.communicate(timeout=watchdog)
        except subprocess.TimeoutExpired as exc:
            child.kill()
            stdout, stderr = child.communicate(timeout=5)
            exc.output, exc.stderr = stdout, stderr
            exc.add_note(json.dumps({
                "fixture_watchdog": True, "pid": child.pid, "returncode": child.returncode,
                "elapsed": time.monotonic() - began, "phase": "UNKNOWN",
                "stdout": stdout, "stderr": stderr, "ledger": ledger_snapshot(ledger),
            }, ensure_ascii=False))
            raise
        result = subprocess.CompletedProcess(command, child.returncode, stdout, stderr)
        result.fixture_timing = {"pid": child.pid, "started_at": started_at, "finished_at": time.time(),
                                 "elapsed_seconds": time.monotonic() - began}
        return result
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
        # communicate closes these normally; readiness failures need closure too.
        for stream in (child.stdout, child.stderr):
            stream.close()


def count_diagnostics(ledger, results, report=None):
    """Original synthetic child outputs and row dates expose loss/unfinished/filter failures."""
    return json.dumps({"report": report, "ledger": ledger_snapshot(ledger), "children": [
        {**getattr(result, "fixture_timing", {}), "returncode": result.returncode,
         "stdout": result.stdout, "stderr": result.stderr} for result in results]},
        ensure_ascii=True, sort_keys=True)


# Appended to the original slow-schema hook. Buffer in memory while holding
# SQLite locks; write once after native close so trace IO does not extend a
# writer interval. This only instruments the synthetic database in the hook.
PHASE_HOOK = r'''
_slow_connection = Connection
_slow_connect = sqlite3.connect
_phase_number = 0
class PhaseConnection(_slow_connection):
    def context(self):
        # Read only the known usage frame's timing fields; never argv/SQL
        # parameters. No native query or trace IO while a lock is held.
        import sys
        frame = sys._getframe(1)
        while frame:
            if (frame.f_globals.get("__name__") == "rds_usage"
                    and frame.f_code.co_name == "_database"):
                deadline = frame.f_locals.get("deadline")
                return {"deadline": deadline,
                    "remaining_seconds": deadline - time.monotonic() if deadline is not None else None,
                    "write": frame.f_locals.get("write")}
            frame = frame.f_back
        return {"deadline": None, "remaining_seconds": None, "write": None}
    def record(self, sql, stage, began=None, error=None, transaction=None):
        value = {"pid": os.getpid(), "connection": self.phase_number,
            "operation": sql, "stage": stage, "monotonic": time.monotonic(),
            "in_transaction": self.in_transaction if transaction is None else transaction,
            "busy_timeout_ms": getattr(self, "phase_busy_timeout", None),
            "transaction_work": sorted(getattr(self, "phase_work", set())), **self.context()}
        if began is not None:
            value["seconds"] = time.monotonic() - began
        if error is not None:
            value.update(error=type(error).__name__,
                sqlite_errorname=getattr(error, "sqlite_errorname", None),
                sqlite_errorcode=getattr(error, "sqlite_errorcode", None))
        self.phase_events.append(value)
    def perform(self, operation, function):
        began = time.monotonic()
        self.record(operation, "before")
        try:
            result = function()
        except BaseException as error:
            self.record(operation, "error", began, error)
            raise
        self.record(operation, "after", began)
        return result
    def execute(self, sql, *args, **kwargs):
        if sql.startswith("BEGIN"):
            self.phase_work = set()
        if sql.startswith("CREATE "):
            self.phase_work.add("schema")
        if sql.startswith(("INSERT ", "UPDATE ")):
            self.phase_work.add("caller")
        result = self.perform(sql, lambda: super(PhaseConnection, self).execute(sql, *args, **kwargs))
        if sql.startswith("PRAGMA busy_timeout="):
            self.phase_busy_timeout = int(sql.split("=", 1)[1])
        return result
    def commit(self):
        return self.perform("COMMIT", lambda: super(PhaseConnection, self).commit())
    def close(self):
        self.record("CLOSE", "before")
        result = super().close()
        self.record("CLOSE", "after", transaction=False)
        try:
            with (_work / "phases" / (str(os.getpid()) + ".jsonl")).open("a", encoding="utf-8") as stream:
                stream.write("".join(json.dumps(event) + "\n" for event in self.phase_events))
        except OSError:
            # Missing trace is UNKNOWN at the reader; never replace a native
            # SQLite result/exception with an instrumentation write error.
            pass
        return result
Connection = PhaseConnection
def phase_connect(path, *args, **kwargs):
    global _phase_number
    connection = _slow_connect(path, *args, **kwargs)
    if isinstance(connection, PhaseConnection):
        _phase_number += 1
        connection.phase_number = _phase_number
        connection.phase_events = []
        connection.phase_work = set()
        connection.record("CONNECT", "after")
    return connection
sqlite3.connect = phase_connect
'''


def phase_diagnostics(work, ledger, *, expected_pids=()):
    """Best-effort projection; corrupt evidence must not mask a count failure."""
    work, ledger = Path(work), Path(ledger)
    events, read_errors = [], []

    def issue(path, error, line=None):
        read_errors.append({"path": str(path), "line": line,
                            "error": type(error).__name__, "reason": str(error)})

    def number(value):
        return type(value) in (float, int) and math.isfinite(value)

    directory = work / "phases"
    try:
        paths = sorted(path for path in directory.iterdir() if path.suffix == ".jsonl")
    except OSError as exc:
        issue(directory, exc)
        paths = []
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            issue(path, exc)
            continue
        for line_number, line in enumerate(lines, 1):
            try:
                event = json.loads(line)
                if (not isinstance(event, dict)
                        or type(event.get("pid")) is not int or event["pid"] <= 0
                        or type(event.get("connection")) is not int or event["connection"] <= 0
                        or not isinstance(event.get("operation"), str)
                        or event.get("stage") not in ("before", "after", "error")
                        or not number(event.get("monotonic"))
                        or type(event.get("in_transaction")) is not bool
                        or (event["stage"] == "error" and
                            (not number(event.get("seconds")) or event["seconds"] < 0))):
                    raise ValueError("Incomplete or invalid phase record")
            except (ValueError, TypeError, OverflowError) as exc:
                issue(path, exc, line_number)
                continue
            events.append(event)
    events.sort(key=lambda event: event["monotonic"])
    connections = {}
    for event in events:
        connections.setdefault((event["pid"], event["connection"]), []).append(event)
    incomplete = []
    for identity, records in connections.items():
        pending = []
        complete = (records[0]["operation"] == "CONNECT" and records[0]["stage"] == "after"
                    and records[-1]["operation"] == "CLOSE" and records[-1]["stage"] == "after")
        for event in records:
            if event["stage"] == "before":
                pending.append(event["operation"])
            elif event["operation"] != "CONNECT":
                if not pending or pending.pop() != event["operation"]:
                    complete = False
        if pending or not complete:
            incomplete.append({"pid": identity[0], "connection": identity[1],
                               "reason": "missing connect/close or unmatched operation boundary"})
    acquired, writers = {}, []
    for event in events:
        identity = (event["pid"], event["connection"])
        if event["operation"] == "BEGIN IMMEDIATE" and event["stage"] == "after":
            acquired[identity] = event["monotonic"]
        if ((event["operation"] == "COMMIT" and event["stage"] == "after")
                or (event["operation"] == "CLOSE" and event["stage"] == "after")) and identity in acquired:
            writers.append({"pid": identity[0], "connection": identity[1],
                "acquired": acquired.pop(identity), "released": event["monotonic"],
                "release_operation": event["operation"]})
    errors = [event for event in events if event["stage"] == "error"]
    for error in errors:
        error["overlapping_observed_writers"] = [writer for writer in writers
            if writer["acquired"] < error["monotonic"]
            and writer["released"] > error["monotonic"] - error["seconds"]]
        error["observed_writers_at_error"] = [writer for writer in writers
            if writer["acquired"] <= error["monotonic"] <= writer["released"]] or "UNKNOWN"
    missing = sorted(set(expected_pids) - {event["pid"] for event in events})
    physical = {"path": str(ledger)}
    try:
        physical["path"] = str(ledger.resolve())
        content = ledger.read_bytes()
        physical.update(bytes=len(content), sha256=hashlib.sha256(content).hexdigest())
    except OSError as exc:
        physical.update(status="UNKNOWN", error=str(exc))
        issue(ledger, exc)
    return {"status": "OBSERVED" if events and not missing and not acquired and not read_errors and not incomplete else "UNKNOWN",
        "physical_database": physical, "missing_pids": missing,
        "read_errors": read_errors, "incomplete_connections": incomplete,
        "event_count": len(events), "errors": errors, "writer_intervals": writers,
        "transaction_events": [event for event in events if event["operation"].startswith(
            ("BEGIN", "COMMIT", "PRAGMA busy_timeout="))],
        "unclosed_writers": [{"pid": pid, "connection": number, "acquired": began}
                             for (pid, number), began in acquired.items()],
        "limit": "Buffered trace affects scheduling; native calls/errors preserved. Writer intervals are correlations, not proof of which lock blocked SQL; external/untraced holders and read locks UNKNOWN."}


def attach_usage_diagnostics(error, ledger, children, work):
    """Retain the original exception even if the observer itself fails."""
    try:
        evidence = (count_diagnostics(ledger, children) + " phases="
                    + json.dumps(phase_diagnostics(work, ledger,
                        expected_pids=[child.fixture_timing["pid"] for child in children])))
    except Exception as exc:
        evidence = json.dumps({"status": "UNKNOWN", "diagnostic_error": type(exc).__name__,
                               "reason": str(exc)})
    try:
        error.add_note(evidence)
    except Exception:
        pass


@contextmanager
def slow_schema_commits(folder, ledger):
    """Synthetic slow schema storage, using real SQLite writer locks/commits.

    Each schema-changing transaction waits 3.6 seconds while holding its
    writer. Autocommit DDL is wrapped in its equivalent single-statement
    transaction so the delay occurs before commit, rather than after the
    writer is released. Explicit schema transactions retain their boundaries.
    No SQLite error, timeout value, data write or CLI result is replaced.
    """
    work = Path(folder) / "usage-slow-schema"
    work.mkdir()
    (work / "phases").mkdir()
    (work / "sitecustomize.py").write_text(r'''
import json, os, pathlib, sqlite3, time
_connect = sqlite3.connect
_work = pathlib.Path(os.environ["RDS_USAGE_SLOW_SCHEMA"])
class Connection(sqlite3.Connection):
    schema_changed = False
    def execute(self, sql, *args, **kwargs):
        if not sql.startswith("CREATE "):
            return super().execute(sql, *args, **kwargs)
        owned = not self.in_transaction
        if owned:
            # Dispatch through the tracing subclass too. BEGIN takes the
            # non-CREATE branch, so this preserves the one native acquisition.
            self.execute("BEGIN IMMEDIATE")
        before = super().execute("PRAGMA schema_version").fetchone()[0]
        result = super().execute(sql, *args, **kwargs)
        after = super().execute("PRAGMA schema_version").fetchone()[0]
        self.schema_changed = self.schema_changed or before != after
        if owned:
            self.commit()
        return result
    def commit(self):
        if self.schema_changed:
            began = time.monotonic()
            time.sleep(3.6)
            with (_work / (str(os.getpid()) + ".jsonl")).open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"writer_held": self.in_transaction,
                    "delay_seconds": time.monotonic() - began}) + "\n")
            self.schema_changed = False
        return super().commit()
def connect(path, *args, **kwargs):
    if pathlib.Path(path).resolve() == pathlib.Path(os.environ["RDS_USAGE_DB"]).resolve():
        kwargs["factory"] = Connection
    return _connect(path, *args, **kwargs)
sqlite3.connect = connect
''', encoding="utf-8")
    with (work / "sitecustomize.py").open("a", encoding="utf-8") as stream:
        stream.write(PHASE_HOOK)
    yield {"work": work, "environment": {
        "RDS_USAGE_SLOW_SCHEMA": str(work),
        "PYTHONPATH": str(work) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }}


def wait_marker(path, *, child=None, stop=None):
    deadline = time.monotonic() + CLI_WATCHDOG_SECONDS
    while not path.exists():
        if stop is not None and stop.is_set():
            raise RuntimeError("Synthetic writer stopped")
        if child is not None and child.poll() is not None:
            raise RuntimeError("Child exited before its readiness marker")
        if time.monotonic() >= deadline:
            raise RuntimeError("Synthetic marker missing: " + path.name)
        time.sleep(.01)


@contextmanager
def cold_commit_handoff(folder, ledger):
    """Real writer handoff after cold commit, with the original 10s deadline.

    Deliberately spend 5.8s before schema inspection and the existing 3.6s
    schema-writer delay. A parent real writer then holds 1s after the first
    commit. No time/error/SQLite return is replaced. Separate schema/caller
    transactions lose the start in the remaining <1s acquisition window.
    """
    with slow_schema_commits(folder, ledger) as probe:
        with (probe["work"] / "sitecustomize.py").open("a", encoding="utf-8") as stream:
            stream.write(r'''
_traced_connection = Connection
class Connection(_traced_connection):
    inspected = False
    handed_off = False
    def execute(self, sql, *args, **kwargs):
        if self.phase_number == 1 and sql.startswith("SELECT type,name") and not self.inspected:
            self.inspected = True
            time.sleep(5.8)
        return super().execute(sql, *args, **kwargs)
    def commit(self):
        result = super().commit()
        if self.phase_number == 1 and not self.handed_off:
            self.handed_off = True
            (_work / "handoff-ready").touch()
            deadline = time.monotonic() + 60
            while not (_work / "handoff-release").exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("Synthetic writer handoff gate expired")
                time.sleep(.01)
        return result
''')
        try:
            yield probe
        finally:
            (probe["work"] / "handoff-release").touch()


@contextmanager
def caller_commit_reader(folder, ledger, *, expire=False):
    """Pause after the real start INSERT so a parent can retain a read lock.

    The expired control spends 10.1 real seconds inside that start transaction;
    production remaining_wait must set zero at COMMIT, with no renewed budget.
    """
    with slow_schema_commits(folder, ledger) as probe:
        with (probe["work"] / "sitecustomize.py").open("a", encoding="utf-8") as stream:
            stream.write(r'''
_traced_connection = Connection
class Connection(_traced_connection):
    paused = False
    def execute(self, sql, *args, **kwargs):
        result = super().execute(sql, *args, **kwargs)
        if self.phase_number == 1 and sql.startswith("INSERT INTO calls") and not self.paused:
            self.paused = True
            (_work / "caller-ready").touch()
            deadline = time.monotonic() + 60
            while not (_work / "caller-release").exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("Synthetic caller gate expired")
                time.sleep(.01)
            if os.environ.get("RDS_USAGE_EXPIRE_CALLER") == "1":
                time.sleep(10.1)
        return result
''')
        probe["environment"]["RDS_USAGE_EXPIRE_CALLER"] = "1" if expire else "0"
        try:
            yield probe
        finally:
            (probe["work"] / "caller-release").touch()


@contextmanager
def paused_schema(folder, ledger):
    """Pause one real CLI after schema preparation, with bounded parent cleanup."""
    work = Path(folder) / "usage-schema-pause"
    work.mkdir()
    (work / "sitecustomize.py").write_text(r'''
import json, os, pathlib, sqlite3, time
_connect = sqlite3.connect
_work = pathlib.Path(os.environ["RDS_USAGE_SCHEMA_PAUSE"])
class Connection(sqlite3.Connection):
    schema_pause_ready = False
    def pause(self):
        (_work / "ready").write_text(json.dumps({"in_transaction": self.in_transaction}))
        deadline = time.monotonic() + 60
        while not (_work / "release").exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("Synthetic schema-pause gate expired")
            time.sleep(.01)
    def execute(self, sql, *args, **kwargs):
        result = super().execute(sql, *args, **kwargs)
        if sql.startswith("CREATE INDEX") and not (_work / "ready").exists():
            if self.in_transaction:
                self.schema_pause_ready = True
            else:
                self.pause()
        return result
    def commit(self):
        result = super().commit()
        if self.schema_pause_ready:
            self.schema_pause_ready = False
            self.pause()
        return result
def connect(path, *args, **kwargs):
    if pathlib.Path(path).resolve() == pathlib.Path(os.environ["RDS_USAGE_DB"]).resolve():
        kwargs["factory"] = Connection
    return _connect(path, *args, **kwargs)
sqlite3.connect = connect
''', encoding="utf-8")
    try:
        yield {"work": work, "environment": {
            "RDS_USAGE_SCHEMA_PAUSE": str(work),
            "PYTHONPATH": str(work) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        }}
    finally:
        (work / "release").touch()


# Only the declared synthetic database is instrumented. Real connect/BEGIN/
# commit/close calls run unchanged; gates let the parent hold two real writers.
HOOK = r'''
import os, pathlib, sqlite3, time
_connect = sqlite3.connect
_number = 0
_work = pathlib.Path(os.environ["RDS_USAGE_FIXTURE_SCOPE"])
class Connection(sqlite3.Connection):
    def execute(self, sql, *args, **kwargs):
        if sql == "BEGIN IMMEDIATE":
            (_work / (str(self.number) + "-waiting")).touch()
        return super().execute(sql, *args, **kwargs)
    def close(self):
        result = super().close()
        if self.number == 1:
            (_work / "start-closed").touch()
            deadline = time.monotonic() + 60
            while not (_work / "exit-writer-ready").exists():
                if time.monotonic() >= deadline:
                    raise RuntimeError("Synthetic exit-writer gate expired")
                time.sleep(.01)
        return result
def connect(path, *args, **kwargs):
    global _number
    if pathlib.Path(path).resolve() != pathlib.Path(os.environ["RDS_USAGE_DB"]).resolve():
        return _connect(path, *args, **kwargs)
    _number += 1
    kwargs["factory"] = Connection
    connection = _connect(path, *args, **kwargs)
    connection.number = _number
    return connection
sqlite3.connect = connect
'''


@contextmanager
def dual_sqlite_wait(folder, ledger):
    work = Path(folder) / "usage-lock-fixture"
    work.mkdir()
    (work / "sitecustomize.py").write_text(HOOK, encoding="utf-8")
    locked, stop = threading.Event(), threading.Event()
    intervals, errors = [], []

    def hold():
        began = time.monotonic()
        while (remaining := 8.2 - (time.monotonic() - began)) > 0:
            if stop.wait(remaining):
                raise RuntimeError("Synthetic writer stopped")
        intervals.append(time.monotonic() - began)

    def writer():
        try:
            with closing(sqlite3.connect(ledger, timeout=10)) as db:
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    locked.set()
                    wait_marker(work / "1-waiting", stop=stop)
                    hold()
                wait_marker(work / "start-closed", stop=stop)
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    (work / "exit-writer-ready").touch()
                    wait_marker(work / "2-waiting", stop=stop)
                    hold()
        except BaseException as exc:
            if not stop.is_set():
                errors.append(exc)
            locked.set()
            (work / "exit-writer-ready").touch()

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        if not locked.wait(CLI_WATCHDOG_SECONDS):
            raise RuntimeError("Synthetic first writer did not acquire its lock")
        if errors:
            raise errors[0]
        yield {"environment": {
            "RDS_USAGE_FIXTURE_SCOPE": str(work),
            "PYTHONPATH": str(work) + os.pathsep + os.environ.get("PYTHONPATH", ""),
        }, "intervals": intervals}
    finally:
        stop.set()
        thread.join(timeout=12)
        if thread.is_alive():
            raise RuntimeError("Synthetic writer did not stop")
        if errors:
            raise errors[0]
