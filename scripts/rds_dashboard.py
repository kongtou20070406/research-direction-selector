#!/usr/bin/env python3
"""Export an RDS ledger to a standalone, offline, read-only research dashboard."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import sys


def _asset_text(relative):
    return (Path(__file__).resolve().parent / "assets" / relative).read_bytes().decode("utf-8")


def read_json(raw):
    def reject_constant(value):
        raise ValueError("Non-finite JSON value: " + value)
    return json.loads(raw, parse_constant=reject_constant)


def read_snapshot(root):
    """Read the existing project or reference ledger without checking input files."""
    root = Path(root).resolve()
    project_path = root / ".rds" / "project.sqlite3"
    db_path = root / ".rds" / "state.sqlite3"
    snapshot = {"root": str(root), "database": str(db_path), "demo": False,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "available": False, "ledger_type": "NONE", "state": {}, "receipts": [],
                "receipt_sources": {}, "events": [], "event_count": 0}
    if project_path.is_file():
        from rds_project import ProjectStore
        snapshot["database"] = str(project_path)
        snapshot["ledger_type"] = "PROJECT"
        snapshot["state"] = ProjectStore(root).snapshot(check_bindings=False)
        snapshot["receipts"] = snapshot["state"]["receipts"]
        # The project API owns the state schema. Recent events are display-only
        # metadata and need no file-hash checks or second state representation.
        with closing(sqlite3.connect(project_path.as_uri() + "?mode=ro", uri=True,
                                     timeout=3, isolation_level=None)) as db:
            db.execute("PRAGMA query_only=ON")
            db.execute("BEGIN")
            try:
                snapshot["event_count"] = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                snapshot["events"] = [read_json(row[0]) for row in
                                      db.execute("SELECT body FROM events ORDER BY id DESC LIMIT 100")]
            finally:
                db.rollback()
        snapshot["available"] = True
        snapshot["receipt_sources"] = {receipt["run_id"]: {
            "database": str(project_path), "table": "receipts", "run_id": receipt["run_id"]}
            for receipt in snapshot["receipts"]}
        return snapshot
    if not db_path.is_file():
        return snapshot
    # Do not use RDSState.transaction: its status path can migrate and rewrite state.
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True,
                                 timeout=3, isolation_level=None)) as db:
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        try:
            row = db.execute("SELECT body FROM state WHERE id=1").fetchone()
            snapshot["state"] = read_json(row[0]) if row else {}
            snapshot["receipts"] = [read_json(row[0]) for row in
                                    db.execute("SELECT body FROM receipts ORDER BY run_id")]
            snapshot["event_count"] = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            snapshot["events"] = [read_json(row[0]) for row in
                                  db.execute("SELECT body FROM events ORDER BY seq DESC LIMIT 100")]
        finally:
            db.rollback()
    if not isinstance(snapshot["state"], dict) or any(
            not isinstance(item, dict) for item in snapshot["receipts"] + snapshot["events"]):
        raise ValueError("RDS state, receipts and events must be JSON objects")
    snapshot["available"] = bool(snapshot["state"])
    snapshot["ledger_type"] = "REFERENCE"
    snapshot["receipt_sources"] = {receipt["run_id"]: {
        "database": str(db_path), "table": "receipts", "run_id": receipt["run_id"]}
        for receipt in snapshot["receipts"]}
    return snapshot


def demo_snapshot(root):
    """A labelled orientation example; it contains no invented execution results."""
    snapshot = {"root": str(Path(root).resolve()), "database": None, "demo": True,
                "generated_at": datetime.now(timezone.utc).isoformat(), "available": True,
                "ledger_type": "DEMO", "receipts": [], "receipt_sources": {}, "events": [], "event_count": 0}
    snapshot["state"] = {
        "contract": {"project_id": "RDS · orientation example",
                     "claim": "比较一个明确干预与公平对照，判断它是否改变下一步科研选择。 / Compare a defined intervention with a fair control to inform the next research decision.",
                     "next_decision": "判断所提出的机制是否值得继续投入。 / Decide whether the proposed mechanism merits further investigation.",
                     "primary_metric": {"name": "Not specified in this example"},
                     "evaluation_scope": "Example only — no experiment has been run"},
        "active_branch": "main",
        "branches": {"main": {"id": "main", "orthogonal_dimension": "initial question",
                               "rationale": "先把问题、竞争解释和可证伪条件说清楚。 / State the question, rival explanation and falsifier first.",
                               "status": "EXAMPLE", "hypotheses": ["example-H1"]}},
        "hypotheses": {"example-H1": {
            "spec": {"id": "example-H1", "type": "mechanism",
                     "proposition": "目标改进来自所提出的机制，而不是额外计算。 / The proposed mechanism, rather than extra compute, explains the improvement.",
                     "falsifier": "等预算对照产生同样的观测。 / An equal-budget control produces the same observation."},
            "task_gain": "UNTESTED", "mechanism": "UNTESTED", "search_policy": "UNTESTED"}},
        "plans": {}}
    return snapshot


def validate_advisor_candidates(advisor):
    """Reject malformed native candidate records before writing unusable HTML."""
    records = [("advisor", advisor)] if not isinstance(advisor, list) else [
        (f"advisor[{i}]", item) for i, item in enumerate(advisor)]
    for path, record in list(records):
        if isinstance(record, dict) and isinstance(record.get("recommendations"), list):
            records.extend((f"{path}.recommendations[{i}]", item)
                           for i, item in enumerate(record["recommendations"]))
    for path, record in records:
        search = record.get("search") if isinstance(record, dict) else None
        if not isinstance(search, dict):
            continue
        groups = [(path + ".search", search)]
        if isinstance(search.get("experiment_composition"), dict):
            groups.append((path + ".search.experiment_composition", search["experiment_composition"]))
        for group_path, group in groups:
            for key in ("candidates", "blocked_candidates"):
                if key not in group:
                    continue
                candidates = group[key]
                if not isinstance(candidates, list):
                    raise ValueError(f"{group_path}.{key} must be an array")
                for i, candidate in enumerate(candidates):
                    locator = f"{group_path}.{key}[{i}]"
                    if not isinstance(candidate, dict):
                        raise ValueError(f"{locator} must be an object")
                    for field in ("steps", "derivation"):
                        if field in candidate and (not isinstance(candidate[field], list)
                                                   or any(not isinstance(item, dict) for item in candidate[field])):
                            raise ValueError(f"{locator}.{field} must be an array of objects")


def render_html(snapshot, advisor=None):
    validate_advisor_candidates(advisor)
    payload = {**snapshot, "advisor": advisor}
    # JSON lives in a script element: HTML parsers recognize </script> even inside JSON strings.
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).replace("&", "\\u0026")
    encoded = encoded.replace("<", "\\u003c").replace(">", "\\u003e")
    encoded = encoded.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    return HTML.replace("__RDS_SNAPSHOT__", encoded)


HTML = _asset_text("dashboard/index.html")


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Project containing .rds/project.sqlite3 or .rds/state.sqlite3")
    parser.add_argument("--output", default="rds-dashboard.html", help="Standalone HTML destination")
    parser.add_argument("--advisor", help="Optional structured advisor JSON file; imported as advice only")
    parser.add_argument("--demo", action="store_true", help="Show a labelled example without run results")
    args = parser.parse_args(argv)
    root, output = Path(args.root).resolve(), Path(args.output).resolve()
    try:
        if output.suffix.lower() != ".html" or output.is_relative_to(root / ".rds"):
            raise ValueError("Output must be an .html file outside the .rds ledger")
        advisor = read_json(Path(args.advisor).read_text(encoding="utf-8-sig")) if args.advisor else None
        if advisor is not None and not isinstance(advisor, (dict, list)):
            raise ValueError("Advisor JSON must be an object or array")
        snapshot = demo_snapshot(root) if args.demo else read_snapshot(root)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(render_html(snapshot, advisor), encoding="utf-8")
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(output), "source": snapshot["database"],
                      "available": snapshot["available"], "demo": snapshot["demo"],
                      "ledger_type": snapshot["ledger_type"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
