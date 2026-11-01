#!/usr/bin/env python3
"""Shared paid-query entry point for the skill A/B launch-decision protocol.

Both arms execute this identical file. It owns the task's credit ledger and query
log at ``$RDS_TRIAL_ROOT/scripts/``, so an RDS ``exec`` frozen copy still charges
and logs into the trial workspace instead of into its job copy.

Per call: read ledger -> check quota -> atomically charge the ledger -> sleep ->
query -> append the query log. A supervisor may kill the process after the charge
and before the log; that is the intended "interrupted paid query" gap.

The default backend is a deterministic local fake (no network, no model call, no
data.db). ``--backend sqlite`` reads the F1 fixture's data.db when a real trial
needs it; the dry-run uses the fake backend.

Single-writer assumption: the trial runs one paid query at a time. The ledger is
written atomically with os.replace; there is no cross-process lock.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

DEFAULT_QUOTA = 6
DEFAULT_LATENCY = 20.0
DEFAULT_N = 2365
BASELINE = 47.7
EFFECT = {1: 9.8, 2: -4.84, 3: -2.18, 4: -0.85}


def _env_int(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_float(name, default):
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def resolve_root(value):
    return Path(value or os.environ.get("RDS_TRIAL_ROOT") or os.getcwd()).resolve()


def load_ledger(path):
    if not path.exists():
        return {"used": 0}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    used = payload.get("used")
    if type(used) is not int or used < 0:
        return None
    return {"used": used}


def save_ledger(path, used):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp-{os.getpid()}")
    tmp.write_text(json.dumps({"used": used}) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_log(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def fake_query(arm, batch):
    mean = BASELINE + (EFFECT[batch] if arm == "B" else 0.0)
    return round(mean, 6), DEFAULT_N


def sqlite_query(path, arm, batch):
    if not path.exists():
        raise FileNotFoundError(str(path))
    connection = sqlite3.connect(str(path))
    try:
        row = connection.execute(
            "SELECT AVG(score), COUNT(*) FROM scores WHERE arm=? AND batch=?",
            (arm, batch),
        ).fetchone()
    finally:
        connection.close()
    if row is None or not row[1]:
        raise LookupError(f"no rows for arm={arm} batch={batch}")
    return round(float(row[0]), 6), int(row[1])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arm", choices=("A", "B"), required=True)
    parser.add_argument("--batch", type=int, choices=(1, 2, 3, 4), required=True)
    parser.add_argument("--root", default=None,
                        help="trial workspace; defaults to RDS_TRIAL_ROOT or cwd")
    parser.add_argument("--quota", type=int,
                        default=_env_int("RDS_QUERY_QUOTA", DEFAULT_QUOTA))
    parser.add_argument("--latency", type=float,
                        default=_env_float("RDS_QUERY_LATENCY", DEFAULT_LATENCY))
    parser.add_argument("--backend", choices=("fake", "sqlite"), default="fake")
    parser.add_argument("--data", default="data.db",
                        help="sqlite backend input, relative to the current directory")
    args = parser.parse_args(argv)

    if args.quota <= 0:
        print(json.dumps({"error": "quota must be positive", "quota": args.quota}))
        return 2

    root = resolve_root(args.root)
    scripts = root / "scripts"
    ledger_path = scripts / "credits.json"
    log_path = scripts / "query_log.jsonl"

    if args.backend == "sqlite" and not Path(args.data).exists():
        print(json.dumps({"error": "data not found", "path": str(Path(args.data).resolve())}))
        return 5

    ledger = load_ledger(ledger_path)
    if ledger is None:
        print(json.dumps({"error": "unreadable ledger", "path": str(ledger_path)}))
        return 4
    used = ledger["used"]
    if used >= args.quota:
        print(json.dumps({"error": "credit exhausted", "used": used, "quota": args.quota}))
        return 2

    # Charge before the slow query. A kill after this line and before the log
    # append is the deliberate interruption signal the protocol counts as a gap.
    save_ledger(ledger_path, used + 1)
    if args.latency > 0:
        time.sleep(args.latency)

    try:
        if args.backend == "sqlite":
            mean, n = sqlite_query(Path(args.data), args.arm, args.batch)
        else:
            mean, n = fake_query(args.arm, args.batch)
    except (FileNotFoundError, LookupError, OSError, sqlite3.Error) as error:
        print(json.dumps({"error": "query failed", "detail": str(error),
                          "credits_used": used + 1}))
        return 5

    record = {
        "arm": args.arm,
        "batch": args.batch,
        "ts": round(time.time(), 3),
        "mean": mean,
        "n": n,
        "credits_remaining": args.quota - used - 1,
    }
    try:
        append_log(log_path, record)
    except OSError as error:
        print(json.dumps({"error": "log append failed", "detail": str(error),
                          "credits_used": used + 1}))
        return 6

    print(json.dumps({"arm": args.arm, "batch": args.batch, "mean": mean, "n": n,
                      "credits_used": used + 1,
                      "credits_remaining": args.quota - used - 1}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
