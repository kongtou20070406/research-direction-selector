"""Append-only research checkpoints; recovery compares live state, never replaces it."""
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path

SCHEMA = "rds-checkpoint-v1"
MAX_BYTES = 4_000_000


def _raw(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _sha(value):
    return hashlib.sha256(_raw(value).encode("utf-8")).hexdigest()


def _database(root, kind):
    if kind not in {"project", "reference"}:
        raise ValueError("Checkpoint kind must be project or reference")
    path = Path(root).resolve() / ".rds" / ("project.sqlite3" if kind == "project" else "state.sqlite3")
    if not path.is_file():
        raise ValueError("Initialize the matching project before saving or restoring a checkpoint")
    return path


def save_checkpoint(root, checkpoint_id, snapshot, *, kind, decision=None):
    if not isinstance(checkpoint_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", checkpoint_id):
        raise ValueError("Checkpoint identity must be 1–64 safe identifier characters")
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("contract"), dict):
        raise ValueError("Checkpoint needs a live operational snapshot and contract")
    if decision is not None and not isinstance(decision, dict):
        raise ValueError("Decision context must be an object")
    if decision:
        # Checkpoints are append-only: a route record review would refuse must never be written.
        from rds_advisor import validate_checkpoint_decision
        validate_checkpoint_decision(decision, checkpoint_id, Path(root).resolve() / ".rds")
    record = {"schema": SCHEMA, "id": checkpoint_id, "kind": kind,
              "created_ns": time.time_ns(), "contract_sha256": _sha(snapshot["contract"]),
              "snapshot": snapshot, "decision": decision or {},
              "decision_assurance": "RECORDED_INPUT_NOT_SCIENTIFIC_VERIFICATION"}
    raw = _raw(record)
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise ValueError("Checkpoint exceeds bounded record size")
    from rds_artifacts import strict_json
    try:  # Loop-history review parses every record this way; one it cannot read would block all questions.
        strict_json(raw)
    except ValueError as exc:
        raise ValueError("Checkpoint record would be unreadable by loop-history review: " + str(exc)) from exc
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    db = sqlite3.connect(_database(root, kind), timeout=15, isolation_level=None)
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY, sha TEXT NOT NULL, body TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS checkpoint_no_update BEFORE UPDATE ON checkpoints
            BEGIN SELECT RAISE(ABORT, 'checkpoints are append-only'); END;
            CREATE TRIGGER IF NOT EXISTS checkpoint_no_delete BEFORE DELETE ON checkpoints
            BEGIN SELECT RAISE(ABORT, 'checkpoints are append-only'); END;
        """)
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO checkpoints VALUES (?,?,?)", (checkpoint_id, sha, raw))
        db.commit()
    except sqlite3.IntegrityError as exc:
        db.rollback()
        raise ValueError("Checkpoint identity already exists; use a new identity") from exc
    finally:
        db.close()
    return {"schema": SCHEMA, "status": "SAVED", "id": checkpoint_id, "kind": kind,
            "sha256": sha, "contract_sha256": record["contract_sha256"]}


def _runs(snapshot):
    if "runs" not in snapshot:
        # Reference plans carry a separate execution identity minted at reservation.
        plans = snapshot.get("plans", {})
        return {plan.get("run_id") or "plan:" + plan_id:
                {**plan, "run_id": plan.get("run_id"), "plan_id": plan_id}
                for plan_id, plan in plans.items() if isinstance(plan, dict)} if isinstance(plans, dict) else {}
    runs = snapshot["runs"]
    if isinstance(runs, list):
        return {r.get("run_id", r.get("id")): r for r in runs if isinstance(r, dict)}
    return runs if isinstance(runs, dict) else {}


def restore_checkpoint(root, checkpoint_id, live_snapshot, *, kind):
    """Return a resumable handoff with current budgets, exposure and run identities."""
    path = _database(root, kind)
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=15)
    try:
        row = db.execute("SELECT sha,body FROM checkpoints WHERE id=?", (checkpoint_id,)).fetchone()
    except sqlite3.OperationalError as exc:
        raise ValueError("No checkpoints are recorded for this project") from exc
    finally:
        db.close()
    if row is None:
        raise ValueError("Unknown checkpoint identity")
    raw = row[1]
    if len(raw.encode("utf-8")) > MAX_BYTES or hashlib.sha256(raw.encode("utf-8")).hexdigest() != row[0]:
        raise ValueError("Checkpoint integrity failure")
    record = json.loads(raw)
    if record.get("schema") != SCHEMA or record.get("kind") != kind:
        raise ValueError("Incompatible checkpoint schema or project kind")
    if not isinstance(live_snapshot, dict) or not isinstance(live_snapshot.get("contract"), dict):
        raise ValueError("Read live operational state before recovery")
    old = record["snapshot"]
    conflicts = []
    if _sha(live_snapshot["contract"]) != record["contract_sha256"]:
        ancestor = False
        if kind == 'project':
            from rds_project import ProjectStore
            from rds_method_revision import contract_history, pending_revision
            store = ProjectStore(root)
            try:
                with store._db(True) as ledger:
                    ledger.execute('BEGIN')
                    lineage = contract_history(ledger)
                    ancestor = (pending_revision(ledger) is None
                                and _sha(live_snapshot['contract']) == lineage[-1]['sha256']
                                and any(h['sha256'] == record['contract_sha256']
                                        and h['contract'] == old.get('contract') for h in lineage))
            except (ValueError, OSError, sqlite3.Error):
                ancestor = False
        if not ancestor:
            conflicts.append({"field": "contract", "reason": "Live contract differs; do not resume under the old binding"})
    for reason in live_snapshot.get("binding_check", {}).get("errors", []):
        conflicts.append({"field": "bindings", "reason": reason})
    updates = []
    if _sha(live_snapshot['contract']) != record['contract_sha256'] and not any(
            c['field'] == 'contract' for c in conflicts):
        updates.append({'field': 'contract', 'reason': 'Verified method ancestor retained as history; current method and authorization remain live'})
    if live_snapshot.get('method_revision_pending'):
        conflicts.append({'field': 'method_revision', 'reason': 'Resume the durable prepared method revision before execution'})
    for field in ("budget", "exposures", "active_branch", "hypotheses", "final_plan"):
        if old.get(field) != live_snapshot.get(field):
            updates.append({"field": field, "reason": "Live state is authoritative; checkpoint state is retained only as history"})
    old_runs, live_runs = _runs(old), _runs(live_snapshot)
    for run_id in old_runs.keys() - live_runs.keys():
        conflicts.append({"field": "runs", "run_id": run_id, "reason": "Previously recorded run is missing from live state"})
    ongoing, completed, interrupted = [], [], []
    for run_id, run in live_runs.items():
        identity = {"run_id": run.get("run_id", run_id)}
        if "plan_id" in run:
            identity["plan_id"] = run["plan_id"]
        status = run.get("run_status", run.get("status", "UNKNOWN"))
        if status in {"RESERVED", "RUNNING"}:
            ongoing.append({**identity, "status": status, "next_action": "Inspect or reconcile this existing run; do not launch a duplicate"})
        elif status in {"COMPLETED", "SUCCEEDED"}:
            completed.append({**identity, "status": status, "assessment": run.get("assessment"),
                              "next_action": "Read the existing receipt and assess its evidence; do not repeat the execution"})
        elif status in {"FAILED", "INTERRUPTED", "TIMED_OUT", "RECOVERY_REQUIRED"}:
            interrupted.append({**identity, "status": status, "next_action": "Diagnose the preserved failure before creating any new attempt"})
    return {"schema": SCHEMA, "status": "CONFLICT" if conflicts else "RESUMABLE_HANDOFF", "id": checkpoint_id,
            "kind": kind, "checkpoint_sha256": row[0], "decision": record["decision"],
            "decision_assurance": record["decision_assurance"], "conflicts": conflicts, "updates": updates,
            "live_budget": live_snapshot.get("budget"), "live_exposures": live_snapshot.get("exposures", []),
            "ongoing_runs": ongoing, "completed_runs": completed, "interrupted_runs": interrupted,
            "pending_evidence": record["decision"].get("pending_evidence", []),
            "authorization": "UNCHANGED", "state_replaced": False, "execution_started": False,
            "history": {"next_action": "If needed, retrieve relevant original evidence with Obelisk in this exact project scope",
                        "project_path": str(Path(root).resolve()), "creates_authorization": False}}
