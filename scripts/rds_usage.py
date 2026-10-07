"""Local, process-safe CLI invocation counts; no prompts or argument payloads."""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
import errno
import os
from pathlib import Path
import sqlite3
import sys
import time

COMMANDS = {"init", "hypothesis", "gate", "plan", "run", "data", "decide", "status",
            "project", "checkpoint", "artifacts", "formal", "meta", "history", "advise",
            "advancement", "branch", "usage", "exec", "reject", "guard", "hypergraph", "math", "rsi", "host-hook", "structure"}
ROOT_OPTIONS = {"--root", "--workspace", "--project-root", "-w", "-d", "--dir"}
COMMAND_MACROS = {"verify": ("formal", "verify"), "prove": ("formal", "verify"), "证明": ("formal", "verify"),
                  "check": ("formal", "check"), "核查": ("formal", "check"),
                  "snapshot": ("checkpoint", "save"), "存档": ("checkpoint", "save")}
COMMAND_ALIASES = {"exec": {"execute", "test", "eval", "start", "执行", "运行", "跑", "测试"},
                   "advise": {"advisor", "review", "suggest", "route", "审查", "建议", "路线", "规划"},
                   "reject": {"deny", "falsify", "counterexample", "drop", "否决", "反例", "证伪", "拒绝"},
                   "checkpoint": {"cp", "ledger", "检查点", "快照", "账本"},
                   "project": {"proj", "项目"}, "usage": {"calls", "调用", "调用次数"},
                   "status": {"stat", "info", "show", "summary", "状态", "进度", "总览"},
                   "formal": {"proof", "theorem", "形式化", "验证"},
                   "guard": {"regression", "regressions", "回退检查"},
                   "hypergraph": {"graph", "deps", "blockers", "and-or", "超图", "依赖"},
                   "math": {"assets", "数学", "资产"}, "rsi": {"tools", "evolve", "演化", "工具"},
                   "execute": {"exec", "执行"}, "save": {"record", "保存"}, "restore": {"resume", "恢复"}}
_last_error = None
_active_path = ContextVar('rds_usage_path', default=None)


def log_path():
    active = _active_path.get()
    if active is not None:
        return active
    override = os.environ.get("RDS_USAGE_DB")
    if override:
        return Path(override).expanduser().resolve()
    local = os.environ.get("LOCALAPPDATA")
    base = Path(local) if local else Path.home() / ".local" / "state"
    return base / "ResearchDirectionSelector" / "cli-usage.sqlite3"


def _unwritable(exc):
    # Only storage access failures justify changing the automatic location.
    # Busy writers, damaged databases and rejected inserts remain visible.
    if isinstance(exc, OSError):
        return isinstance(exc, PermissionError) or exc.errno in {errno.EACCES, errno.EPERM, errno.EROFS}
    code = getattr(exc, 'sqlite_errorcode', None)
    return isinstance(code, int) and code & 255 in {sqlite3.SQLITE_READONLY, sqlite3.SQLITE_PERM, sqlite3.SQLITE_CANTOPEN}


@contextmanager
def _database(*, write=True):
    path = log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Brief contention must not drop invocations. SQLite bounds this wait;
    # persistent storage failure still leaves the original command unaffected.
    connection = sqlite3.connect(path, timeout=10, isolation_level=None)
    deadline = time.monotonic() + 10

    def remaining_wait():
        # All schema/transaction waits share the original phase budget. A
        # paused process does not get a fresh ten seconds at every statement.
        milliseconds = max(0, int((deadline - time.monotonic()) * 1000))
        connection.execute(f"PRAGMA busy_timeout={milliseconds}")

    try:
        # Read the current database, not a process cache or file-existence
        # guess. Established logs need no DDL, including read-only queries.
        remaining_wait()
        objects = set(connection.execute(
            "SELECT type,name FROM sqlite_master WHERE name IN ('tracking','calls','calls_day')"
        ).fetchall())
        cold = not {('table', 'tracking'), ('table', 'calls'), ('index', 'calls_day')} <= objects
        if cold:
            # A cold/partial schema is one short transaction, rather than
            # three serial autocommit flushes competing with incoming calls.
            # IF NOT EXISTS rechecks safely after another initializer wins.
            remaining_wait()
            connection.execute('BEGIN IMMEDIATE')
            for statement in (
                "CREATE TABLE IF NOT EXISTS tracking (id INTEGER PRIMARY KEY CHECK(id=1), started REAL NOT NULL, day TEXT NOT NULL)",
                "CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, started REAL NOT NULL, day TEXT NOT NULL, command TEXT NOT NULL, mode TEXT NOT NULL, version TEXT NOT NULL, exit_code INTEGER, elapsed_ms REAL)",
                "CREATE INDEX IF NOT EXISTS calls_day ON calls(day)",
            ):
                remaining_wait()
                connection.execute(statement)
            if not write:
                remaining_wait()
                connection.commit()
        # Cold writes already own a transaction. Record the start before its
        # first commit: releasing/reacquiring a writer here can spend the
        # remaining phase budget after schema succeeded but before recording.
        # Cold readers commit schema before opening their consistent snapshot.
        # Start's tracking+call remain atomic; readers get one consistent
        # snapshot without reserving a writer or upgrading a read transaction.
        if not (cold and write):
            remaining_wait()
            connection.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        yield connection
        remaining_wait()
        connection.commit()
    finally:
        connection.close()


def _label(argv):
    argv = argv[:argv.index('--')] if '--' in argv else argv
    command = "other"
    skip = False
    end = len(argv)
    for index, token in enumerate(argv):
        if skip:
            skip = False
        elif token in ROOT_OPTIONS:
            skip = True
        elif token == "--":
            break
        elif not token.startswith('-'):
            exact = next((c for c in COMMANDS if c.casefold() == token.casefold()), None)
            if exact is None and token.casefold() in COMMAND_MACROS:
                command = COMMAND_MACROS[token.casefold()][0]
                break
            aliases = [c for c in COMMANDS if token.casefold() in COMMAND_ALIASES.get(c, set())]
            prefixes = [c for c in COMMANDS if c.startswith(token.casefold())]
            matches = [exact] if exact else aliases or prefixes
            command = matches[0] if len(matches) == 1 else 'other'
            if command == 'exec':
                values = ROOT_OPTIONS | {'--name', '--id', '--timeout', '-t', '--time', '--timeout-seconds',
                    '--bind', '--output', '-o', '--out', '--guard', '--context', '--research-context', '-c',
                    '--ctx', '--graph', '--choose', '--ledger', '-l', '--db', '--objective'}
                i = index + 1
                while i < len(argv):
                    if not argv[i].startswith('-'):
                        end = i
                        break
                    i += 2 if argv[i].split('=', 1)[0] in values and '=' not in argv[i] else 1
            break  # Never label a wrapped command or argument as an RDS invocation.
    argv = argv[:end]
    mode = "help" if "--help" in argv or "-h" in argv else "version" if "--version" in argv else "command"
    return command if command != "other" else mode if mode != "command" else command, mode


def _start(argv, version):
    timestamp = time.time()
    day = datetime.fromtimestamp(timestamp).astimezone().date().isoformat()
    command, mode = _label(argv)
    with _database() as connection:
        connection.execute("INSERT OR IGNORE INTO tracking VALUES (1,?,?)", (timestamp, day))
        cursor = connection.execute("INSERT INTO calls (started,day,command,mode,version) VALUES (?,?,?,?,?)",
                                    (timestamp, day, command, mode, version))
        return cursor.lastrowid


def _log_failure(phase, exc):
    """Expose incomplete recording without printing paths, payloads or changing the command."""
    global _last_error
    _last_error = str(exc)
    code = getattr(exc, "sqlite_errorname", None)
    kind = type(exc).__name__ + ("/" + code if isinstance(code, str) else "")
    outcome = "start record not confirmed" if phase == "start" else "exit record not confirmed"
    try:
        print(f"[RDS-USAGE-DEGRADED] {phase} logging failed ({kind}); {outcome}; "
              "original command result is preserved", file=sys.stderr)
    except Exception:
        # Even an unavailable diagnostic stream cannot replace the command's result/exception.
        pass


def run_logged(function, argv, version, *, root=None):
    """Pin one database per invocation, including reports and exceptional exits."""
    context = _active_path.set(None)
    try:
        return _run_logged(function, argv, version, root=root)
    finally:
        _active_path.reset(context)


def _run_logged(function, argv, version, *, root):
    """Record starts and exits; log failures never change the command's result."""
    global _last_error
    token, code, started = None, None, time.perf_counter()
    _last_error = None
    try:
        _active_path.set(log_path())
        token = _start(argv, version)
    except (OSError, sqlite3.Error, ValueError) as exc:
        if (root is not None and _unwritable(exc)
                and not os.environ.get('RDS_USAGE_DB')):
            try:
                resolved = Path(root() if callable(root) else root).resolve()
                _active_path.set(resolved / '.rds' / 'usage' / 'cli-usage.sqlite3')
                token = _start(argv, version)
            except (OSError, sqlite3.Error, ValueError) as fallback_error:
                _log_failure('start', fallback_error)
        else:
            _log_failure("start", exc)
    try:
        result = function()
        code = result if type(result) is int else 0
        return result
    except SystemExit as exc:
        code = exc.code if type(exc.code) is int else 0 if exc.code is None else 1
        raise
    except KeyboardInterrupt:
        code = 130
        raise
    except BaseException:
        code = 1
        raise
    finally:
        if token is not None:
            try:
                with _database() as connection:
                    connection.execute("UPDATE calls SET exit_code=?,elapsed_ms=? WHERE id=?",
                                       (code, round((time.perf_counter() - started) * 1000, 3), token))
            except (OSError, sqlite3.Error, ValueError) as exc:
                _log_failure("finish", exc)


def summarize(*, days=14, since=None, until=None):
    end = date.fromisoformat(until) if until is not None else date.today()
    if since is not None:
        start = date.fromisoformat(since)
    else:
        if type(days) is not int or not 1 <= days <= 3660:
            raise ValueError("--days must be an integer in 1..3660")
        start = end - timedelta(days=days - 1)
    if not 0 <= (end - start).days < 3660:
        raise ValueError("Date range must be ordered and span at most 3660 days")
    with _database(write=False) as connection:
        tracking = connection.execute("SELECT started,day FROM tracking WHERE id=1").fetchone()
        rows = connection.execute("SELECT day,COUNT(*),SUM(exit_code=0),SUM(exit_code!=0),SUM(exit_code IS NULL) FROM calls WHERE day BETWEEN ? AND ? GROUP BY day", (start.isoformat(), end.isoformat())).fetchall()
        commands = dict(connection.execute("SELECT command,COUNT(*) FROM calls WHERE day BETWEEN ? AND ? GROUP BY command ORDER BY command", (start.isoformat(), end.isoformat())))
        modes = dict(connection.execute("SELECT mode,COUNT(*) FROM calls WHERE day BETWEEN ? AND ? GROUP BY mode", (start.isoformat(), end.isoformat())))
    by_day = {row[0]: row[1:] for row in rows}
    daily = []
    for offset in range((end - start).days + 1):
        day = (start + timedelta(days=offset)).isoformat()
        counts = by_day.get(day, (0, 0, 0, 0))
        tracked = bool(tracking and day >= tracking[1]) or day in by_day
        daily.append({"date": day, "calls": counts[0] if tracked else None,
                      "successful": counts[1] or 0, "failed": counts[2] or 0,
                      "unfinished": counts[3] or 0, "tracked": tracked})
    return {"logging": "DEGRADED" if _last_error else "ENABLED", "log_path": str(log_path()),
            "tracking_since": datetime.fromtimestamp(tracking[0], timezone.utc).astimezone().isoformat() if tracking else None,
            "day_basis": "local calendar date at invocation", "since": start.isoformat(), "until": end.isoformat(),
            "total_calls": sum(row[1] for row in rows), "commands": commands, "modes": modes,
            "daily": daily, "log_error": _last_error, "query_calls_are_counted": True}


def render(summary):
    lines = ["RDS CLI usage", "Logging: " + summary["logging"],
             f"Period: {summary['since']} .. {summary['until']}",
             f"Recorded calls: {summary['total_calls']}", "", "Date        Calls"]
    for row in summary["daily"]:
        count = str(row["calls"]) if row["tracked"] else "-"
        lines.append(f"{row['date']}  {count:>5}")
    lines.extend(["", "Commands: " + ", ".join(f"{name}={count}" for name, count in summary["commands"].items()),
                  "Tracking since: " + str(summary["tracking_since"]), "Log: " + summary["log_path"],
                  "- = not tracked yet; usage queries also count."])
    if summary["log_error"]:
        lines.append("Log error: " + summary["log_error"])
    return "\n".join(lines)
