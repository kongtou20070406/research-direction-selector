#!/usr/bin/env python3
"""One-query dry run for the shared-ledger ``scripts/paid_query.py`` shim.

Arm A calls the shim from its trial workspace. Arm B calls the same bytes from a
simulated RDS ``exec`` job copy (``.rds/exec/<name>/scripts/``). Both must charge
and log into the trial workspace, not into the exec job copy.

Run: python -B benchmark/newtonbench/skill_ab_dryrun.py
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SHIM = REPO / "scripts" / "paid_query.py"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_shim(root, cwd, arm, batch):
    command = [
        sys.executable, "-B", "scripts/paid_query.py",
        "--arm", arm, "--batch", str(batch),
        "--backend", "fake", "--latency", "0",
    ]
    env = dict(os.environ)
    env["RDS_TRIAL_ROOT"] = str(root)
    return subprocess.run(
        command, cwd=str(cwd), env=env, capture_output=True,
        text=True, encoding="utf-8", timeout=60,
    )


def parse_result(label, proc):
    if proc.returncode != 0:
        return None, (f"{label} exit={proc.returncode} "
                      f"stdout={proc.stdout!r} stderr={proc.stderr!r}")
    try:
        return json.loads(proc.stdout), None
    except ValueError as error:
        return None, f"{label} stdout is not JSON: {error}: {proc.stdout!r}"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_log(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit one JSON object")
    args = parser.parse_args()

    if not SHIM.exists():
        print(f"missing shim: {SHIM}")
        return 1

    checks = []

    def check(name, passed, detail):
        checks.append((name, bool(passed), detail))

    with tempfile.TemporaryDirectory(prefix="skill-ab-dryrun-") as temp:
        base = Path(temp)
        ws_a = base / "ws-a"
        ws_b = base / "ws-b"
        for workspace in (ws_a, ws_b):
            (workspace / "scripts").mkdir(parents=True)
            shutil.copyfile(SHIM, workspace / "scripts" / "paid_query.py")
        job_b = ws_b / ".rds" / "exec" / "cell-2-B"
        (job_b / "scripts").mkdir(parents=True)
        shutil.copyfile(SHIM, job_b / "scripts" / "paid_query.py")

        proc_a = run_shim(ws_a, ws_a, "A", 2)
        proc_b = run_shim(ws_b, job_b, "B", 2)
        result_a, error_a = parse_result("A", proc_a)
        result_b, error_b = parse_result("B", proc_b)

        check("A exit 0", proc_a.returncode == 0, f"exit={proc_a.returncode}")
        check("B exit 0", proc_b.returncode == 0, f"exit={proc_b.returncode}")
        if error_a or error_b:
            print(error_a or error_b)
            return 1

        ledger_a_path = ws_a / "scripts" / "credits.json"
        ledger_b_path = ws_b / "scripts" / "credits.json"
        log_a_path = ws_a / "scripts" / "query_log.jsonl"
        log_b_path = ws_b / "scripts" / "query_log.jsonl"
        job_ledger = job_b / "scripts" / "credits.json"
        job_log = job_b / "scripts" / "query_log.jsonl"

        ledger_a = read_json(ledger_a_path)
        ledger_b = read_json(ledger_b_path)
        log_a = read_log(log_a_path)
        log_b = read_log(log_b_path)

        check("A workspace ledger=1", ledger_a.get("used") == 1, ledger_a)
        check("B workspace ledger=1", ledger_b.get("used") == 1, ledger_b)
        check("A workspace log has 1 line", len(log_a) == 1, len(log_a))
        check("B workspace log has 1 line", len(log_b) == 1, len(log_b))
        check("A log cell is A/2",
              bool(log_a) and (log_a[0].get("arm"), log_a[0].get("batch")) == ("A", 2),
              log_a[0] if log_a else None)
        check("B log cell is B/2",
              bool(log_b) and (log_b[0].get("arm"), log_b[0].get("batch")) == ("B", 2),
              log_b[0] if log_b else None)
        check("A credits_remaining=5", result_a.get("credits_remaining") == 5, result_a)
        check("B credits_remaining=5", result_b.get("credits_remaining") == 5, result_b)
        check("A fake mean=47.7", result_a.get("mean") == 47.7, result_a.get("mean"))
        check("B fake mean=42.86", result_b.get("mean") == 42.86, result_b.get("mean"))
        check("B exec copy has no ledger", not job_ledger.exists(), str(job_ledger))
        check("B exec copy has no log", not job_log.exists(), str(job_log))
        check("B workspace ledger exists", ledger_b_path.exists(), str(ledger_b_path))
        check("B workspace log exists", log_b_path.exists(), str(log_b_path))
        same_bytes = (sha256(SHIM) == sha256(ws_a / "scripts" / "paid_query.py")
                      == sha256(ws_b / "scripts" / "paid_query.py")
                      == sha256(job_b / "scripts" / "paid_query.py"))
        check("shim bytes identical in both arms", same_bytes, sha256(SHIM))

        rows = [
            ("A", "workspace", ledger_a["used"], len(log_a),
             result_a["credits_remaining"], "A/2", result_a["mean"]),
            ("B", "exec-copy", ledger_b["used"], len(log_b),
             result_b["credits_remaining"], "B/2", result_b["mean"]),
        ]
        shim_hash = sha256(SHIM)

    failed = [entry for entry in checks if not entry[1]]
    summary = {
        "dryrun": "PASS" if not failed else "FAIL",
        "arms": {
            row[0]: {"where": row[1], "ledger_used": row[2], "log_lines": row[3],
                     "credits_remaining": row[4], "cell": row[5], "mean": row[6]}
            for row in rows
        },
        "assertions": {"passed": len(checks) - len(failed), "total": len(checks),
                       "failed": [name for name, _, _ in failed]},
        "shim_sha256": shim_hash,
    }

    if args.json:
        print(json.dumps(summary, indent=2))
        return 0 if not failed else 1

    print("dry-run: shared-ledger paid_query.py (fake backend, latency 0)")
    print(f"{'arm':4} {'where':12} {'ledger_used':>11} {'log_lines':>9} "
          f"{'credits_left':>12} {'cell':6} {'mean':>8}")
    for arm, where, used, lines, remaining, cell, mean in rows:
        print(f"{arm:4} {where:12} {used:>11} {lines:>9} {remaining:>12} "
              f"{cell:6} {mean:>8}")
    print(f"shim sha256 (same in both arms): {shim_hash}")
    print("B exec copy ledger/log: absent")
    for name, passed, detail in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}"
              + ("" if passed else f"  actual={detail!r}"))
    print(f"assertions: {len(checks) - len(failed)}/{len(checks)} PASS")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
