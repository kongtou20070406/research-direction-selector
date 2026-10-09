"""Append-only research checkpoints; recovery compares live state, never replaces it."""
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from rds_mutation import mutation

SCHEMA = "rds-checkpoint-v1"
MAX_BYTES = 4_000_000
_DEPENDENCY_UNCHECKED = object()


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


def _checkpoint_record(root, checkpoint_id, snapshot, *, kind, decision=None):
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
    return record, raw


def read_checkpoint(db, checkpoint_id, *, root):
    """Verify a retained row using the caller's coherent transaction."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone():
        return None
    row = db.execute('SELECT sha,body FROM checkpoints WHERE id=?', (checkpoint_id,)).fetchone()
    if row is None:
        return None
    sha, raw = row
    if not isinstance(raw, str) or len(raw.encode('utf-8')) > MAX_BYTES or hashlib.sha256(raw.encode('utf-8')).hexdigest() != sha:
        raise ValueError('Checkpoint integrity failure: ' + checkpoint_id)
    from rds_artifacts import strict_json
    record = strict_json(raw)
    if (not isinstance(record, dict) or record.get('schema') != SCHEMA or record.get('id') != checkpoint_id
            or record.get('kind') not in {'project', 'reference'}):
        raise ValueError('Checkpoint identity mismatch: ' + checkpoint_id)
    if (not isinstance(record.get('snapshot'), dict)
            or not isinstance(record['snapshot'].get('contract'), dict)
            or _sha(record['snapshot']['contract']) != record.get('contract_sha256')):
        raise ValueError('Checkpoint contract mismatch: ' + checkpoint_id)
    from rds_advisor import validate_checkpoint_decision
    validate_checkpoint_decision(record.get('decision', {}), checkpoint_id, Path(root).resolve() / '.rds')
    return {'record': record, 'sha256': sha}


def append_checkpoint(db, root, checkpoint_id, snapshot, *, kind, decision=None, idempotent=False,
                      _owned_run_id=None, _settled_attempt=None):
    """Append within an existing transaction; never commit the caller's work."""
    from rds_campaign import binding, enforce
    scope = binding(root)
    completion = (scope is not None and Path(root).resolve() != Path(scope['project_root'])
                  and _settled_attempt is not None and kind == 'project' and _owned_run_id is not None)
    if not completion:
        enforce(root, kind=kind)
    if not db.in_transaction:
        raise ValueError('Checkpoint append requires the owning transaction')
    filename = db.execute('PRAGMA database_list').fetchone()[2]
    if not filename or Path(filename).resolve() != _database(root, kind):
        raise ValueError('Checkpoint transaction belongs to a different ledger')
    if kind == 'project' and decision:
        from rds_method_revision import contract_history
        initialized = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone()
        contract = contract_history(db)[-1]['contract'] if initialized else snapshot.get('contract', {})
        note_only = (set(decision) == {'plan_draft', 'source_kind'}
                     and decision['source_kind'] == 'UNVERIFIED_PLAN_PROPOSAL')
        if 'advisor_policy' in contract and not note_only:
            from rds_project import ProjectStore, require
            from rds_owned_history import checkpoint_id as owned_checkpoint_id, snapshot as owned_snapshot, _check_choice
            require(_owned_run_id is not None, 'Program-owned Advisor owns decision checkpoints; use project next/advance')
            store = ProjectStore(root)
            run = store._run(db, _owned_run_id)
            if _settled_attempt is None:
                require(checkpoint_id == owned_checkpoint_id('before', run)
                        and run['status'] == 'RESERVED' and run['attempt_id'] is None
                        and snapshot == owned_snapshot(store, db),
                        'Owned decision checkpoint requires its native reservation and live snapshot')
                _check_choice(store, db, run, decision, snapshot)
    if completion or _settled_attempt is not None:
        # Only the receipt-bound completion checkpoint of an existing admitted
        # attempt can accompany settlement after a workspace binding changes.
        from rds_project import ProjectStore, TERMINAL, require
        from rds_owned_history import checkpoint_id as owned_checkpoint_id, snapshot as owned_snapshot
        run = ProjectStore._run(db, _owned_run_id)
        row = db.execute('SELECT run_id,sha256,body FROM receipts WHERE run_id=?', (_owned_run_id,)).fetchone()
        receipt = ProjectStore._receipt(row) if row is not None else None
        require(run['attempt_id'] == _settled_attempt and run['status'] in TERMINAL
                and run.get('owned_history_recorded') is True and receipt is not None
                and receipt['attempt_id'] == _settled_attempt
                and receipt['process_status'] == run['status']
                and receipt['manifest_sha256'] == run['manifest_sha256']
                and checkpoint_id == owned_checkpoint_id('after', run),
                'Settlement checkpoint requires the original completed owned attempt')
        require(isinstance(decision, dict) and decision.get('execution') == {
                    'run_id': run['id'], 'attempt_id': run['attempt_id'],
                    'receipt_sha256': receipt['sha256'], 'run_status': receipt['run_status']}
                and snapshot == owned_snapshot(ProjectStore(root), db),
                'Settlement checkpoint must retain its original receipt and live accounting')
    record, raw = _checkpoint_record(root, checkpoint_id, snapshot, kind=kind, decision=decision)
    existing = read_checkpoint(db, checkpoint_id, root=root)
    if existing is not None:
        comparable = lambda item: {k: v for k, v in item.items() if k != 'created_ns'}
        if not idempotent or comparable(existing['record']) != comparable(record):
            raise ValueError('Checkpoint identity already exists with conflicting contents')
        return {'schema': SCHEMA, 'status': 'ALREADY_SAVED', 'id': checkpoint_id, 'kind': kind,
                'sha256': existing['sha256'], 'contract_sha256': record['contract_sha256']}
    if kind == 'project':
        from rds_owned_history import check_append_slots
        check_append_slots(root, db, checkpoint_id, owned_run_id=_owned_run_id)
    # execute(), not executescript(): DDL must not implicitly commit admission.
    db.execute('CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY, sha TEXT NOT NULL, body TEXT NOT NULL)')
    for operation in ('UPDATE', 'DELETE'):
        db.execute(f"CREATE TRIGGER IF NOT EXISTS checkpoint_no_{operation.lower()} BEFORE {operation} ON checkpoints "
                   "BEGIN SELECT RAISE(ABORT, 'checkpoints are append-only'); END")
    sha = hashlib.sha256(raw.encode('utf-8')).hexdigest()
    db.execute('INSERT INTO checkpoints VALUES (?,?,?)', (checkpoint_id, sha, raw))
    return {'schema': SCHEMA, 'status': 'SAVED', 'id': checkpoint_id, 'kind': kind,
            'sha256': sha, 'contract_sha256': record['contract_sha256']}


@mutation()
def save_checkpoint(root, checkpoint_id, snapshot, *, kind, decision=None,
                    _expected_contract_sha256=None,
                    _expected_dependency_snapshot_sha256=_DEPENDENCY_UNCHECKED):
    from rds_campaign import enforce
    enforce(root, kind=kind)
    # Validate before opening a writer, preserving the public save boundary.
    _checkpoint_record(root, checkpoint_id, snapshot, kind=kind, decision=decision)
    if _expected_contract_sha256 is not None:
        if kind != 'project' or not isinstance(_expected_contract_sha256, str) or not re.fullmatch(
                '[0-9a-f]{64}', _expected_contract_sha256):
            raise ValueError('Expected checkpoint contract must be a project SHA256 identity')
    if _expected_dependency_snapshot_sha256 is not _DEPENDENCY_UNCHECKED:
        if kind != 'project' or (_expected_dependency_snapshot_sha256 is not None and
                (not isinstance(_expected_dependency_snapshot_sha256, str) or not re.fullmatch(
                    '[0-9a-f]{64}', _expected_dependency_snapshot_sha256))):
            raise ValueError('Expected checkpoint dependency must be a project SHA256 identity or None')
    db = sqlite3.connect(_database(root, kind).as_uri() + '?mode=rw', uri=True,
                         timeout=15, isolation_level=None)
    db.row_factory = sqlite3.Row
    try:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        if _expected_contract_sha256 is not None:
            from rds_method_revision import contract_history
            if (contract_history(db)[-1]['sha256'] != _expected_contract_sha256
                    or _sha(snapshot['contract']) != _expected_contract_sha256):
                raise ValueError('Quick parent contract changed before checkpoint publication; '
                                 'inspect the retained job and any original receipt; do not rerun')
        if _expected_dependency_snapshot_sha256 is not _DEPENDENCY_UNCHECKED:
            head = None
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dependency_snapshots'").fetchone():
                head = db.execute('SELECT sha256 FROM dependency_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()
            if (head['sha256'] if head is not None else None) != _expected_dependency_snapshot_sha256:
                raise ValueError('Dependency snapshot changed before checkpoint publication; '
                                 'reanalyze the current map before recording a choice')
        result = append_checkpoint(db, root, checkpoint_id, snapshot, kind=kind, decision=decision)
        db.commit()
    except sqlite3.IntegrityError as exc:
        db.rollback()
        raise ValueError("Checkpoint identity already exists; use a new identity") from exc
    finally:
        db.close()
    return result


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
