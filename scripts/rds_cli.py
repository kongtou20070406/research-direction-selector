#!/usr/bin/env python3
"""RDS L3 local reference kernel; this is NOT an OS security boundary.

SQLite transactions arbitrate admission, resource allocations, and receipts.
The fixed runner executes rational ASTs with a paired MSE evaluator. External
JSON cannot certify manipulation, metric gain, final authorization or success.
"""
import argparse
import ast
import difflib
from contextlib import contextmanager
from copy import deepcopy
from fractions import Fraction
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import uuid

from rds_probe import parse_source, rational, read_rows, formal_requirement
from rds_formal_kernel import bounded

VERSION = "5.9.0-rc.1"
RESOURCES = {"runtime_ms", "runs"}
SELF_SIGNED = {"manipulation_verified", "falsifier_triggered", "primary_metric_gain",
               "final_run_authorized", "matched_recipe", "matched_compute"}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def digest(value):
    raw = value if isinstance(value, bytes) else canonical(value).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "Duplicate JSON key: " + key)
            result[key] = value
        return result

    def bad_constant(value):
        raise ValueError("Non-finite JSON value: " + value)

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=bad_constant)
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds the parser limit") from exc


def load_spec(path):
    return strict_json(Path(path).read_text(encoding="utf-8-sig"))


def read_bounded(path, cap):
    with open(path, "rb") as handle:
        raw = handle.read(cap + 1)
    require(len(raw) <= cap, "Artifact exceeds the reference runner limit")
    return raw


def reject_self_signatures(obj):
    if isinstance(obj, dict):
        require(not SELF_SIGNED.intersection(obj), "Self-signed verification fields are forbidden")
        for value in obj.values():
            reject_self_signatures(value)
    elif isinstance(obj, list):
        for value in obj:
            reject_self_signatures(value)


def identity(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value),
            "Invalid identity (up to 80 ASCII letters, numbers, _, . or -)")
    return value


def json_object(value, label):
    require(isinstance(value, dict), label + " must be a JSON object")
    return value


def required_field(record, key, label):
    require(key in record, "Missing " + label + " field: " + key)
    return record[key]


def path_field(value, label):
    require(isinstance(value, str) and value, label + " must be a non-empty path string")
    return value


def known_plan(state, plan_id):
    require(plan_id in state["plans"], "Unknown plan ID: " + str(plan_id))
    return state["plans"][plan_id]


def resource_vector(value, positive=False):
    require(isinstance(value, dict) and set(value) == RESOURCES,
            "Budget must explicitly contain runtime_ms and runs")
    require(all(type(v) is int and 0 <= v <= 10**12 for v in value.values()),
            "Resource amounts must be nonnegative bounded integers, never bool/float")
    if positive:
        require(value["runtime_ms"] > 0 and value["runs"] == 1,
                "A plan reserves positive runtime_ms and exactly one run")
    return value


def engine_id():
    # Ordinary AST admission/execution must work without importing a solver.
    try:
        sympy_version = importlib.metadata.version("sympy")
    except importlib.metadata.PackageNotFoundError:
        sympy_version = None
    here = Path(__file__).resolve().parent
    from rds_verify import verifier_id
    return digest({"cli": digest((here / "rds_cli.py").read_bytes()),
                   "quick": digest((here / "rds_quick.py").read_bytes()),
                   "methods": digest((here / "rds_methods.py").read_bytes()),
                   "direction_search": digest((here / "rds_advisor_search.py").read_bytes()),
                   "direction_advisor": digest((here / "rds_advisor.py").read_bytes()),
                   "usage": digest((here / "rds_usage.py").read_bytes()),
                   "guard": digest((here / "rds_guard.py").read_bytes()),
                   "probe": digest((here / "rds_probe.py").read_bytes()),
                   "formal_kernel": digest((here / "rds_formal_kernel.py").read_bytes()),
                   "declarative_verifier": verifier_id(),
                   "python": sys.version, "sympy": sympy_version})


def formal_gate(hypothesis, source):
    if formal_requirement(hypothesis) is None:
        return {"status": "PASS", "assurance": "AST_ONLY", "backend": "ast"}
    payload = {"operation": "admission", "hypothesis": hypothesis, "source": source}
    try:
        completed = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).with_name("rds_probe.py"))],
            input=canonical(payload).encode("utf-8"), capture_output=True, timeout=15)
    except subprocess.TimeoutExpired:
        raise ValueError("Formal gate UNKNOWN: verifier exceeded 15 seconds") from None
    require(completed.returncode == 0, "Formal gate failed: " + completed.stdout.decode("utf-8", errors="replace"))
    probe = strict_json(completed.stdout.decode("utf-8"))
    require(probe.get("status") == "PASS", "Formal gate " + probe.get("status", "UNKNOWN") + ": " + probe.get("reason", "no boundary crossing"))
    require(probe.get("application_status", "PASS") == "PASS", "Formal application assumptions remain UNKNOWN")
    return probe


def cached_admission(state, binding, hypothesis, source):
    """Existing admitted certificates are an optional, independently checked cache."""
    from rds_probe import declarative_probe, formal_probe

    formal = formal_requirement(hypothesis)
    if formal is None:
        return None
    identity_keys = ("source_sha256", "hypothesis_sha256", "engine_sha256")
    for plan in state["plans"].values():
        old = plan["binding"]
        if not all(old.get(key) == binding[key] for key in identity_keys):
            continue
        probe = old.get("admission_probe", {})
        certificate = probe.get("certificate")
        if probe.get("status") == "PASS" and isinstance(certificate, dict) and certificate.get("verdict") == "PASS":
            if formal["kind"] == "declarative":
                checked = declarative_probe(formal, probe)
            else:
                checked = formal_probe(parse_source(source), formal, probe)
            if checked.get("status") == "PASS" and checked.get("application_status", "PASS") == "PASS":
                return checked
    return None


class RDSState:
    def __init__(self, root_dir):
        self.root = Path(root_dir).resolve()
        self.directory = self.root / ".rds"
        self.db_path = self.directory / "state.sqlite3"

    def connect(self, create=False, readonly=False):
        if create:
            require(not (self.directory / "contract.json").exists(),
                    "Legacy v5 JSON state found; preserve it and initialize a new root")
            self.directory.mkdir(parents=True, exist_ok=True)
        require(create or self.db_path.exists(), "RDS is not initialized")
        target = self.db_path.as_uri() + "?mode=ro" if readonly else self.db_path
        db = sqlite3.connect(target, uri=readonly, timeout=15, isolation_level=None)
        db.execute("PRAGMA foreign_keys=ON")
        if not readonly:
            db.execute("PRAGMA synchronous=FULL")
        if create:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS artifacts (sha TEXT PRIMARY KEY, raw BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts (run_id TEXT PRIMARY KEY, sha TEXT NOT NULL, body TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS receipt_no_update BEFORE UPDATE ON receipts
                BEGIN SELECT RAISE(ABORT, 'receipts are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS receipt_no_delete BEFORE DELETE ON receipts
                BEGIN SELECT RAISE(ABORT, 'receipts are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS event_no_update BEFORE UPDATE ON events
                BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS event_no_delete BEFORE DELETE ON events
                BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS artifact_no_update BEFORE UPDATE ON artifacts
                BEGIN SELECT RAISE(ABORT, 'artifacts are append-only'); END;
                CREATE TRIGGER IF NOT EXISTS artifact_no_delete BEFORE DELETE ON artifacts
                BEGIN SELECT RAISE(ABORT, 'artifacts are append-only'); END;
            """)
        return db

    @staticmethod
    def read_state(db):
        row = db.execute("SELECT body FROM state WHERE id=1").fetchone()
        state = strict_json(row[0]) if row else {}
        if state:
            require(state["version"] in {VERSION, "5.7.0", "5.1.0", "5.2.0", "5.3.0", "5.4.0", "5.5.0-rc.1", "5.5.0-rc.2", "5.6.0-rc.1", "5.6.0-rc.2"},
                    "Incompatible state version")
            require(digest(state["contract"]) == state["contract_sha256"], "Contract integrity failure")
            if "branches" not in state:
                state["branches"] = {
                    "main": {
                        "id": "main", "parent_id": None, "orthogonal_dimension": "baseline",
                        "rationale": "Initial primary exploration branch", "status": "ACTIVE",
                        "stagnation_count": 0, "created_ns": 0,
                        "hypotheses": list(state.get("hypotheses", {}).keys())
                    }
                }
            state.setdefault("active_branch", "main")
            state.setdefault("baseline_cache", {})
        return state

    @contextmanager
    def snapshot(self):
        """Read a consistent state without taking a writer lock or rewriting it."""
        db = self.connect(readonly=True)
        try:
            db.execute("BEGIN")
            yield db, self.read_state(db)
        finally:
            db.close()

    @contextmanager
    def transaction(self, create=False):
        db = self.connect(create)
        try:
            db.execute("BEGIN IMMEDIATE")
            state = self.read_state(db)
            yield db, state
            if state:
                self.invariants(state)
                db.execute("INSERT INTO state VALUES (1,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                           (canonical(state),))
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def invariants(state):
        budget = state["budget"]
        for key in RESOURCES:
            require(budget["spent"][key] >= 0 and budget["reserved"][key] >= 0, "Negative ledger balance")
            require(budget["spent"][key] + budget["reserved"][key] <= budget["limits"][key],
                    "Budget invariant violated")
            live = sum(p["spec"]["resources"][key] for p in state["plans"].values()
                       if p["run_status"] == "RESERVED")
            require(live == budget["reserved"][key], "Reservation ledger does not match plans")

    @staticmethod
    def event(db, kind, **fields):
        body = {"kind": kind, "time_ns": time.time_ns(), **fields}
        cur = db.execute("INSERT INTO events(body) VALUES (?)", (canonical(body),))
        return cur.lastrowid

    @staticmethod
    def artifact(db, raw):
        sha = digest(raw)
        db.execute("INSERT OR IGNORE INTO artifacts VALUES (?,?)", (sha, raw))
        return sha


def cmd_init(args, rds):
    contract = load_spec(args.contract)
    reject_self_signatures(contract)
    # Scalars cannot be searched for fields; lists and strings keep their missing-field message.
    require(isinstance(contract, (dict, list, str)), "Contract must be a JSON object")
    # Batch every missing required field into one rejection so a caller repairs
    # all of them in a single round trip instead of one field per attempt (#72).
    missing = [key for key in ("project_id", "claim", "primary_metric", "budget", "splits", "baseline_source")
               if key not in contract]
    require(not missing, "Missing contract fields: " + ", ".join(missing))
    json_object(contract, "Contract")
    identity(contract["project_id"])
    metric = json_object(contract["primary_metric"], "Contract primary_metric")
    require(metric.get("name") == "mse" and metric.get("direction") == "min",
            "The reference runner supports only paired MSE minimization")
    delta = required_field(metric, "min_useful_delta", "contract primary_metric")
    require(rational(delta) > 0, "Precommit a positive minimum useful gain")
    require(contract.get("evaluation_scope") in {"finite_locked_dataset", "population"},
            "Declare evaluation_scope: finite_locked_dataset or population")
    budget = json_object(contract["budget"], "Contract budget")
    limits = resource_vector(required_field(budget, "limits", "contract budget"))
    floor = resource_vector(required_field(budget, "confirmation_floor", "contract budget"))
    require(all(floor[k] <= limits[k] for k in RESOURCES), "Confirmation reserve exceeds cap")
    require(contract["splits"], "Register at least one data partition")
    json_object(contract["splits"], "Contract splits")
    registry = {}
    for sid, split in contract["splits"].items():
        identity(sid)
        json_object(split, "Contract split " + sid)
        role = required_field(split, "role", "contract split " + sid)
        require(isinstance(role, str) and role in {"train", "development", "confirmation"}, "Unknown split role")
        identity(required_field(split, "cohort", "contract split " + sid))
        path = Path(path_field(required_field(split, "path", "contract split " + sid),
                               "Contract split " + sid + " path"))
        path = (rds.root / path).resolve() if not path.is_absolute() else path.resolve()
        raw = read_bounded(path, 2_000_000)
        rows = read_rows(raw.decode("utf-8-sig"))
        require(split.get("prior_exposure") in ("none_declared", "unknown", "exposed"),
                "Custodian must declare prior_exposure; unknown is ineligible for confirmation")
        registry[sid] = {**split, "path": str(path), "sha256": digest(raw),
                         "sample_ids": [digest(r[0]) for r in rows]}
    contract["splits"] = registry
    baseline = Path(path_field(contract["baseline_source"], "Contract baseline_source"))
    baseline = (rds.root / baseline).resolve() if not baseline.is_absolute() else baseline.resolve()
    baseline_raw = read_bounded(baseline, 8192)
    baseline_ast = parse_source(baseline_raw.decode("utf-8-sig"))["control"]
    contract["baseline_control_ast"] = ast.dump(baseline_ast)
    contract["baseline_source_sha256"] = digest(baseline_raw)
    with rds.transaction(create=True) as (db, state):
        require(not state, "Already initialized; init never erases history")
        state.update(version=VERSION, contract=contract, contract_sha256=digest(contract),
                     engine_sha256=engine_id(), hypotheses={}, plans={}, exposures=[], final_plan=None,
                     active_branch="main",
                     branches={
                         "main": {
                             "id": "main", "parent_id": None, "orthogonal_dimension": "baseline",
                             "rationale": "Initial primary exploration branch", "status": "ACTIVE",
                             "stagnation_count": 0, "created_ns": time.time_ns(),
                             "hypotheses": []
                         }
                     },
                     baseline_cache={},
                     budget={"limits": limits, "confirmation_floor": floor,
                             "spent": {k: 0 for k in RESOURCES}, "reserved": {k: 0 for k in RESOURCES}})
        rds.event(db, "CONTRACT_LOCKED", contract_sha256=state["contract_sha256"])
    return {"version": VERSION, "contract_sha256": digest(contract)}


def cmd_hypothesis(args, rds):
    spec = load_spec(args.spec)
    reject_self_signatures(spec)
    json_object(spec, "Hypothesis spec")
    hid = identity(required_field(spec, "id", "hypothesis"))
    require(spec.get("proposition") and spec.get("falsifier"), "Precommit proposition and falsifier")
    require(spec.get("type") in {"task_gain", "mechanism", "search_policy"}, "Unknown hypothesis type")
    formal_requirement(spec)
    with rds.transaction() as (db, state):
        require(hid not in state["hypotheses"], "Hypothesis already locked; create a revision ID")
        state["hypotheses"][hid] = {"spec": spec, "sha256": digest(spec), "task_gain": "UNTESTED",
                                   "mechanism": "UNTESTED", "search_policy": "UNTESTED", "assessments": []}
        active_b = state.get("active_branch", "main")
        if active_b in state.get("branches", {}):
            if hid not in state["branches"][active_b]["hypotheses"]:
                state["branches"][active_b]["hypotheses"].append(hid)
        rds.event(db, "HYPOTHESIS_LOCKED", hypothesis_id=hid, sha256=digest(spec), branch_id=active_b)
    return {"hypothesis_id": hid, "branch_id": active_b}


def related_splits(contract, split_id):
    """Transitive alias/overlap closure. Cohorts mean shared information lineage."""
    registry, related = contract["splits"], {split_id}
    while True:
        additions = {sid for sid, candidate in registry.items() for old in related
                     if candidate["cohort"] == registry[old]["cohort"]
                     or candidate["sha256"] == registry[old]["sha256"]
                     or set(candidate["sample_ids"]).intersection(registry[old]["sample_ids"])}
        if additions <= related:
            return related
        related |= additions


def clean_confirmation(state, split_id, own_run=None):
    related = related_splits(state["contract"], split_id)
    if any(state["contract"]["splits"][sid]["role"] != "confirmation"
           or state["contract"]["splits"][sid]["prior_exposure"] != "none_declared" for sid in related):
        return False
    return not any(e["split_id"] in related and not
                   (e["purpose"] == "final_evaluate" and e.get("run_id") == own_run)
                   for e in state["exposures"])


def validate_plan(plan, state, rds, admission=None):
    reject_self_signatures(plan)
    require(set(plan) == {"id", "hypothesis_id", "split_id", "purpose", "source", "resources"},
            "Plan fields: id, hypothesis_id, split_id, purpose, source, resources; no imported permits")
    identity(plan["id"])
    require(isinstance(plan["hypothesis_id"], str) and plan["hypothesis_id"] in state["hypotheses"],
            "Unknown hypothesis")
    require(isinstance(plan["split_id"], str) and plan["split_id"] in state["contract"]["splits"], "Unknown split")
    require(isinstance(plan["purpose"], str) and plan["purpose"] in {"explore", "confirm"}, "Unknown purpose")
    resources = resource_vector(plan["resources"], positive=True)
    require(resources["runtime_ms"] <= 60000, "Reference runner has a 60-second maximum allocation")
    require(state["final_plan"] is None, "Selection is frozen by a final plan")
    split = state["contract"]["splits"][plan["split_id"]]
    if plan["purpose"] == "confirm":
        require(split["role"] == "confirmation", "Confirmation requires a confirmation partition")
        require(clean_confirmation(state, plan["split_id"]), "Confirmation lineage already exposed")
        require(not any(p["run_status"] in {"RESERVED", "RUNNING"} for p in state["plans"].values()),
                "Finish or cancel active exploration before freezing final selection")
    else:
        require(split["role"] == "development", "Exploration requires development data")
    budget = state["budget"]
    for key in RESOURCES:
        protected = budget["confirmation_floor"][key] if plan["purpose"] == "explore" else 0
        require(budget["spent"][key] + budget["reserved"][key] + resources[key] + protected
                <= budget["limits"][key], "Budget unavailable or protected for confirmation: " + key)
    path = Path(path_field(plan["source"], "Plan source"))
    path = (rds.root / path).resolve() if not path.is_absolute() else path.resolve()
    source = read_bounded(path, 8192)
    functions = parse_source(source.decode("utf-8-sig"))
    require(ast.dump(functions["control"]) == state["contract"]["baseline_control_ast"],
            "Baseline computation changed from the locked contract")
    require(engine_id() == state["engine_sha256"], "Verifier version changed; a new contract is required")
    binding = {"source_path": str(path), "source_sha256": digest(source), "source": source.decode("utf-8-sig"),
               "engine_sha256": state["engine_sha256"], "contract_sha256": state["contract_sha256"],
               "hypothesis_sha256": state["hypotheses"][plan["hypothesis_id"]]["sha256"],
               "dataset_sha256": split["sha256"], "plan_sha256": digest(plan)}
    if admission is not None:
        require(all(admission.get(key) == value for key, value in binding.items()),
                "Admission binding changed while formal verification was running")
        probe = admission["admission_probe"]
    else:
        hypothesis = state["hypotheses"][plan["hypothesis_id"]]["spec"]
        probe = cached_admission(state, binding, hypothesis, binding["source"])
        if probe is None:
            probe = formal_gate(hypothesis, binding["source"])
    require(probe.get("status") == "PASS", "Formal gate " + probe.get("status", "UNKNOWN"))
    require(probe.get("application_status", "PASS") == "PASS", "Formal application assumptions remain UNKNOWN")
    return {**binding, "admission_probe": probe}


def cmd_plan(args, rds, advisory=False):
    plan = json_object(load_spec(args.plan if advisory else args.spec), "Plan spec")
    # Only a string ID can name a reserved plan; anything else falls through to validate_plan.
    plan_id = plan.get("id") if isinstance(plan.get("id"), str) else None
    # Solver work is deliberately outside the reservation transaction. The
    # committing transaction rechecks every resource and source binding.
    with rds.snapshot() as (_, state):
        existing = state["plans"].get(plan_id)
        if existing:
            require(digest(plan) == existing["binding"]["plan_sha256"], "Plan ID reused with changed contents")
            return {"plan_id": plan["id"], "run_status": existing["run_status"], "idempotent": True}
    binding = validate_plan(plan, state, rds)
    if advisory:
        return {"status": "ADMISSIBLE_NOW", "reserved": False,
                "probe": binding["admission_probe"],
                "note": "Admission is rechecked atomically by plan create"}
    with rds.transaction() as (db, state):
        existing = state["plans"].get(plan_id)
        if existing:
            require(digest(plan) == existing["binding"]["plan_sha256"], "Plan ID reused with changed contents")
            return {"plan_id": plan["id"], "run_status": existing["run_status"], "idempotent": True}
        binding = validate_plan(plan, state, rds, admission=binding)
        branch_id = state.get("active_branch", "main")
        require(branch_id in state["branches"], "Unknown planning branch")
        state["plans"][plan["id"]] = {"spec": plan, "binding": binding, "run_status": "RESERVED",
                                       "run_id": "RUN-" + uuid.uuid4().hex, "assessment": None,
                                       "branch_id": branch_id}
        for key, value in plan["resources"].items():
            state["budget"]["reserved"][key] += value
        if plan["purpose"] == "confirm":
            state["final_plan"] = plan["id"]
        rds.artifact(db, binding["source"].encode("utf-8"))
        rds.event(db, "PLAN_RESERVED", plan_id=plan["id"], branch_id=branch_id,
                  binding=binding, resources=plan["resources"])
    return {"plan_id": plan["id"], "run_status": "RESERVED", "binding": binding}


def cmd_cancel(args, rds):
    with rds.transaction() as (db, state):
        plan = known_plan(state, args.id)
        if plan["run_status"] == "CANCELLED":
            return {"run_status": "CANCELLED", "idempotent": True}
        require(plan["run_status"] == "RESERVED", "Only an unstarted reservation can be released")
        for key, value in plan["spec"]["resources"].items():
            state["budget"]["reserved"][key] -= value
        plan["run_status"] = "CANCELLED"
        if state["final_plan"] == args.id:
            state["final_plan"] = None
        rds.event(db, "PLAN_CANCELLED", plan_id=args.id)
    return {"run_status": "CANCELLED"}


def cmd_expose(args, rds):
    with rds.transaction() as (db, state):
        require(args.split in state["contract"]["splits"], "Unknown split")
        exposure = {"split_id": args.split, "purpose": args.purpose, "actor": args.actor,
                    "reason": args.reason, "time_ns": time.time_ns()}
        exposure["seq"] = rds.event(db, "DATA_EXPOSURE", **exposure)
        state["exposures"].append(exposure)
        affected = related_splits(state["contract"], args.split)
        for plan in state["plans"].values():
            if plan["spec"]["purpose"] == "confirm" and plan["spec"]["split_id"] in affected:
                if plan["assessment"] and plan["assessment"]["task_gain"] in {"CONFIRMED", "REFUTED"}:
                    state["hypotheses"][plan["spec"]["hypothesis_id"]]["task_gain"] = "NEEDS_REVIEW"
                    rds.event(db, "ASSESSMENT_INVALIDATED", run_id=plan["run_id"], cause=exposure["seq"])
    return {"exposure": exposure}


def cmd_run(args, rds):
    # Slow proof replay runs outside either SQLite transaction. The charging
    # transaction rechecks liveness and the exact binding it was replayed for.
    with rds.snapshot() as (_, snapshot):
        candidate = known_plan(snapshot, args.id)
        if candidate["run_status"] != "RESERVED":
            return {"run_id": candidate["run_id"], "run_status": candidate["run_status"], "idempotent": True}
        expected_binding = candidate["binding"]
        expected_hypothesis = snapshot["hypotheses"][candidate["spec"]["hypothesis_id"]]
    hypothesis = expected_hypothesis["spec"]
    formal = formal_requirement(hypothesis)
    probe = expected_binding["admission_probe"]
    if formal and formal["kind"] == "declarative":
        from rds_probe import declarative_probe
        probe = declarative_probe(formal, probe)
    with rds.transaction() as (db, state):
        plan = state["plans"][args.id]
        if plan["run_status"] != "RESERVED":
            return {"run_id": plan["run_id"], "run_status": plan["run_status"], "idempotent": True}
        binding, spec = plan["binding"], plan["spec"]
        require(binding == expected_binding, "Admission binding changed while formal verification was running")
        require(state["hypotheses"][spec["hypothesis_id"]] == expected_hypothesis,
                "Hypothesis binding changed while formal verification was running")
        require(engine_id() == binding["engine_sha256"], "Verifier changed after admission")
        require(digest(read_bounded(binding["source_path"], 8192)) == binding["source_sha256"],
                "Source changed after plan lock")
        require(digest(spec) == binding["plan_sha256"], "Plan integrity failure")
        require(state["hypotheses"][spec["hypothesis_id"]]["sha256"] == binding["hypothesis_sha256"],
                "Hypothesis binding failure")
        if spec["purpose"] == "confirm":
            require(clean_confirmation(state, spec["split_id"]), "Confirmation contaminated after admission")
        binding = {**binding, "admission_probe": probe}
        require(probe.get("status") == "PASS", "Formal gate " + probe.get("status", "UNKNOWN"))
        require(probe.get("application_status", "PASS") == "PASS", "Formal application assumptions remain UNKNOWN")
        # Charge once before execution. No refund after start or a lost runner.
        for key, value in spec["resources"].items():
            state["budget"]["reserved"][key] -= value
            state["budget"]["spent"][key] += value
        plan["run_status"] = "RUNNING"
        plan["started_ns"] = time.time_ns()
        plan["lease_expires_ns"] = plan["started_ns"] + (spec["resources"]["runtime_ms"] + 5000) * 1_000_000
        exposure = {"split_id": spec["split_id"], "purpose": "final_evaluate" if spec["purpose"] == "confirm"
                    else "development_evaluate", "run_id": plan["run_id"], "actor": "reference_runner",
                    "time_ns": time.time_ns()}
        exposure["seq"] = rds.event(db, "DATA_ACCESS_COMMITTED", **exposure)
        state["exposures"].append(exposure)
        dataset_path = state["contract"]["splits"][spec["split_id"]]["path"]
        run_id = plan["run_id"]
        baseline_key = digest({
            "control_ast": state["contract"]["baseline_control_ast"],
            "dataset_sha256": binding["dataset_sha256"]
        })
        cached_control = state.get("baseline_cache", {}).get(baseline_key, {}).get("observations")
    started = time.monotonic_ns()
    data_raw, stdout, stderr, result = b"", b"", b"", None
    returncode, status = None, "FAILED"
    try:
        data_raw = read_bounded(dataset_path, 2_000_000)
        require(digest(data_raw) == binding["dataset_sha256"], "Dataset changed after contract lock")
        payload = {"source": binding["source"], "data": data_raw.decode("utf-8-sig"), "hypothesis": hypothesis,
                   "admission_probe": binding["admission_probe"]}
        if cached_control:
            payload["cached_control"] = cached_control
        worker = Path(__file__).resolve().with_name("rds_probe.py")
        completed = subprocess.run([sys.executable, "-I", str(worker)],
                                   input=canonical(payload).encode("utf-8"), capture_output=True,
                                   timeout=spec["resources"]["runtime_ms"] / 1000, shell=False)
        stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
        if returncode == 0:
            result = strict_json(stdout.decode("utf-8"))
            status = "SUCCEEDED"
    except subprocess.TimeoutExpired as exc:
        status, stdout, stderr = "TIMED_OUT", exc.stdout or b"", exc.stderr or b""
    except (OSError, ValueError, UnicodeError) as exc:
        stderr = str(exc).encode("utf-8")
    receipt = {"run_id": run_id, "plan_id": spec["id"], "binding": binding, "run_status": status,
               "returncode": returncode, "elapsed_ms": (time.monotonic_ns() - started) // 1_000_000,
               "charged_allocation": spec["resources"], "result": result,
               "baseline_key": baseline_key, "control_reused": bool(cached_control)}
    with rds.transaction() as (db, state):
        live = state["plans"][args.id]
        if live["run_status"] != "RUNNING":
            return {"run_status": live["run_status"], "late_result_discarded": True}
        receipt["artifacts"] = {"data": rds.artifact(db, data_raw), "stdout": rds.artifact(db, stdout),
                                "stderr": rds.artifact(db, stderr)}
        if status == "SUCCEEDED" and result and not cached_control:
            if "baseline_cache" not in state:
                state["baseline_cache"] = {}
            control_map = {r["sample_id"]: {"control": r["control"], "control_loss": r["control_loss"]}
                           for r in result.get("observations", [])}
            state["baseline_cache"][baseline_key] = {
                "control_mean": result.get("control_mean"),
                "observations": control_map,
                "first_run_id": run_id
            }
            rds.event(db, "BASELINE_CACHED", baseline_key=baseline_key, run_id=run_id)
        receipt["sha256"] = digest(receipt)
        db.execute("INSERT INTO receipts VALUES (?,?,?)", (run_id, receipt["sha256"], canonical(receipt)))
        live["run_status"] = status
        rds.event(db, "RUN_FINISHED", run_id=run_id, run_status=status, receipt_sha256=receipt["sha256"])
    return {"run_id": run_id, "run_status": status, "receipt_sha256": receipt["sha256"],
            "control_reused": bool(cached_control)}


def cmd_recover(args, rds):
    with rds.transaction() as (db, state):
        plan = known_plan(state, args.id)
        require(plan["run_status"] == "RUNNING", "Only RUNNING plans require reconciliation")
        require(time.time_ns() > plan["lease_expires_ns"], "Execution lease has not expired")
        plan["run_status"] = "RECOVERY_REQUIRED"
        rds.event(db, "RECOVERY_REQUIRED", run_id=plan["run_id"],
                  reason="No terminal receipt; allocation remains charged, no automatic rerun")
    return {"run_status": "RECOVERY_REQUIRED", "budget_refunded": False}


def merge_axis(old, new):
    if old in {"NEEDS_REVIEW", "DISPUTED"}:
        return old
    if {old, new} in ({"CONFIRMED", "REFUTED"}, {"SUPPORTED", "REFUTED"}):
        return "DISPUTED"
    rank = {"UNTESTED": 0, "NOT_TESTED": 1, "INCONCLUSIVE": 2, "EXPLORATORY": 3,
            "SUPPORTED": 4, "CONFIRMED": 4, "REFUTED": 4}
    return new if rank[new] >= rank[old] else old


def assess(result, contract, purpose, clean, formal):
    gain = bounded(Fraction(result["gain"]))
    useful = gain > rational(contract["primary_metric"]["min_useful_delta"])
    final = purpose == "confirm" and clean and contract["evaluation_scope"] == "finite_locked_dataset"
    task = ("CONFIRMED" if useful else "REFUTED") if final else ("EXPLORATORY" if useful else "INCONCLUSIVE")
    probe, mechanism = result["probe"], "UNTESTED"
    side_condition = formal and formal.get("kind") == "declarative"
    if side_condition:
        mechanism = "NOT_TESTED"
    elif formal:
        mechanism = "NOT_TESTED" if probe["status"] == "FAIL" else "INCONCLUSIVE"
        # Passing manipulation never supports causality. This adapter can refute
        # ONLY its typed necessity claim with an executed exact counterexample.
        necessity = formal_requirement({"formal": formal}).get("statement") == "threshold_necessity"
        if necessity and probe["status"] == "PASS" and probe.get("necessity_counterexamples"):
            mechanism = "REFUTED"
    outcome = {"task_gain": task, "mechanism": mechanism, "search_policy": "UNTESTED",
            "scope": contract["evaluation_scope"], "gain": result["gain"],
            "manipulation": "NOT_APPLICABLE" if side_condition else probe["status"],
            "note": "Finite benchmark comparison only; no population inference or causal support"}
    if side_condition:
        outcome.update(formal_status=probe["status"], formal_assurance=probe.get("assurance", "NONE"),
                       claim_relation="declared_side_condition_only")
    return outcome


def plan_branch(state, plan):
    """Use locked ownership; older plans need a unique recorded hypothesis owner."""
    if "branch_id" in plan:
        return plan["branch_id"]
    owners = [bid for bid, branch in state.get("branches", {}).items()
              if plan["spec"]["hypothesis_id"] in branch.get("hypotheses", [])]
    return owners[0] if len(owners) == 1 else None


def cmd_decide(args, rds):
    with rds.transaction() as (db, state):
        require(engine_id() == state["engine_sha256"], "Verifier changed; old receipts require versioned review")
        matches = [p for p in state["plans"].values() if p["run_id"] == args.run]
        require(len(matches) == 1, "Unknown run receipt")
        plan = matches[0]
        row = db.execute("SELECT body FROM receipts WHERE run_id=?", (args.run,)).fetchone()
        require(row is not None, "A runner-generated receipt is required")
        receipt = strict_json(row[0])
        sha = receipt.pop("sha256")
        require(digest(receipt) == sha, "Receipt integrity failure")
        require(receipt["binding"] == plan["binding"], "Receipt/plan binding mismatch")
        require(receipt["run_id"] == args.run and receipt["plan_id"] == plan["spec"]["id"],
                "Receipt identity mismatch")
        for artifact_sha in receipt["artifacts"].values():
            artifact = db.execute("SELECT raw FROM artifacts WHERE sha=?", (artifact_sha,)).fetchone()
            require(artifact is not None and digest(artifact[0]) == artifact_sha, "Raw artifact integrity failure")
        require(receipt["run_status"] == "SUCCEEDED" and plan["run_status"] == "SUCCEEDED",
                "Execution failure is not scientific refutation")
        if plan["assessment"] is not None:
            active_b = plan_branch(state, plan)
            branch_info = state.get("branches", {}).get(active_b)
            stagnation_report = {
                "branch_id": active_b,
                "stagnation_count": branch_info.get("stagnation_count", 0),
                "stagnated": branch_info.get("stagnation_count", 0) >= 3,
                "threshold": 3,
                "recommendation": "IDEMPOTENT_DECISION"
            } if branch_info is not None else None
            return {"assessment": plan["assessment"], "idempotent": True,
                    "stagnation": stagnation_report,
                    "current_task_gain": state["hypotheses"][plan["spec"]["hypothesis_id"]]["task_gain"]}
        spec = plan["spec"]
        node = state["hypotheses"][spec["hypothesis_id"]]
        clean = clean_confirmation(state, spec["split_id"], args.run)
        outcome = assess(receipt["result"], state["contract"], spec["purpose"], clean, node["spec"].get("formal"))
        plan["assessment"] = {**outcome, "receipt_sha256": sha, "run_id": args.run,
                              "hypothesis_sha256": node["sha256"], "clean_confirmation": clean}
        node["assessments"].append(plan["assessment"])
        for axis in ("task_gain", "mechanism"):
            node[axis] = merge_axis(node[axis], outcome[axis])
        rds.event(db, "ASSESSMENT_RECORDED", **plan["assessment"])

        # Policy RSI: Stagnation tracking (FML-Bench v2)
        active_b = plan_branch(state, plan)
        branch_info = state.get("branches", {}).get(active_b)
        stagnation_report = None
        if branch_info is not None:
            gain_val = bounded(Fraction(receipt["result"]["gain"]))
            min_delta = rational(state["contract"]["primary_metric"]["min_useful_delta"])
            useful = gain_val > min_delta
            if useful and outcome["task_gain"] in {"CONFIRMED", "EXPLORATORY"}:
                branch_info["stagnation_count"] = 0
                branch_info["status"] = "ACTIVE"
            else:
                branch_info["stagnation_count"] += 1
                if branch_info["stagnation_count"] >= 3:
                    branch_info["status"] = "STAGNATING"
                    rds.event(db, "STAGNATION_DETECTED", branch_id=active_b,
                              stagnation_count=branch_info["stagnation_count"])
            stagnated = branch_info["stagnation_count"] >= 3
            stagnation_report = {
                "branch_id": active_b,
                "stagnation_count": branch_info["stagnation_count"],
                "stagnated": stagnated,
                "threshold": 3,
                "recommendation": (
                    "STAGNATION_DETECTED: Consecutive 3 runs without useful gain. "
                    "Switch from greedy parameter search to orthogonal branching (FML-Bench v2) via 'branch fork'."
                    if stagnated else "CONTINUE_GREEDY"
                )
            }
    return {"assessment": plan["assessment"], "stagnation": stagnation_report}


def cmd_status(args, rds):
    with rds.snapshot() as (db, state):
        result = dict(state)
        result["receipts"] = [strict_json(r[0]) for r in db.execute("SELECT body FROM receipts ORDER BY run_id")]
        result["event_count"] = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        return result


def cmd_branch(args, rds):
    with rds.transaction() as (db, state):
        branches = state.get("branches", {})
        active = state.get("active_branch", "main")
        if args.action == "status":
            info = branches.get(active, {})
            stagnated = info.get("stagnation_count", 0) >= 3
            return {
                "active_branch": active,
                "status": info.get("status", "ACTIVE"),
                "stagnation_count": info.get("stagnation_count", 0),
                "stagnated": stagnated,
                "recommendation": (
                    "STAGNATION_DETECTED: Consecutive 3 runs without useful gain. "
                    "Consider 'branch fork' to open an orthogonal research direction (FML-Bench v2)."
                    if stagnated else "ACTIVE: Continue current branch exploration."
                ),
                "branch_details": info
            }
        elif args.action == "list":
            return {"active_branch": active, "branches": branches}
        elif args.action == "switch":
            bid = identity(args.id)
            require(bid in branches, f"Unknown branch ID '{bid}'")
            state["active_branch"] = bid
            rds.event(db, "BRANCH_SWITCHED", branch_id=bid)
            return {"active_branch": bid, "status": branches[bid].get("status", "ACTIVE")}
        elif args.action == "fork":
            spec = load_spec(args.spec)
            reject_self_signatures(spec)
            json_object(spec, "Branch spec")
            bid = identity(required_field(spec, "id", "branch"))
            parent = identity(spec.get("parent_id", active))
            require(bid not in branches, f"Branch ID '{bid}' already exists")
            require(parent in branches, f"Parent branch '{parent}' does not exist")
            require(spec.get("orthogonal_dimension"), "Must declare orthogonal_dimension (e.g. representation, mechanism, loss)")
            require(spec.get("rationale"), "Must declare rationale for branching")

            if "budget_split" in spec:
                split = resource_vector(spec["budget_split"])
                budget = state["budget"]
                for k in RESOURCES:
                    available = budget["limits"][k] - (budget["spent"][k] + budget["reserved"][k])
                    require(split[k] <= available,
                            f"Branch budget split exceeds available unspent {k}: {split[k]} > {available}")

            branches[bid] = {
                "id": bid,
                "parent_id": parent,
                "orthogonal_dimension": spec["orthogonal_dimension"],
                "rationale": spec["rationale"],
                "status": "ACTIVE",
                "stagnation_count": 0,
                "created_ns": time.time_ns(),
                "budget_split": spec.get("budget_split"),
                "hypotheses": []
            }
            state["active_branch"] = bid
            rds.event(db, "BRANCH_FORKED", branch_id=bid, parent_id=parent,
                      orthogonal_dimension=spec["orthogonal_dimension"])
            return {"forked_branch": bid, "parent_id": parent, "active_branch": bid}
        else:
            raise ValueError(f"Unknown branch action: {args.action}")


def cmd_meta(args, rds):
    from rds_meta import (
        validate_rule, load_judgment_graph, apply_rule, reflect_from_state
    )
    if args.action == "list-rules":
        _, graph = load_judgment_graph(getattr(args, "graph", None))
        return {"schema": graph.get("schema", 1), "total_nodes": len(graph.get("nodes", [])),
                "nodes": graph.get("nodes", [])}
    elif args.action == "validate-rule":
        spec = load_spec(args.rule)
        reject_self_signatures(spec)
        validate_rule(spec)
        return {"status": "VALID", "rule_id": spec["id"], "sha256": digest(spec)}
    elif args.action == "apply-rule":
        spec = load_spec(args.rule)
        reject_self_signatures(spec)
        res = apply_rule(spec, graph_path=getattr(args, "graph", None),
                         force=getattr(args, "force", False),
                         dry_run=getattr(args, "dry_run", False),
                         evaluation=getattr(args, "evaluation", None),
                         cases=getattr(args, "cases", None),
                         record_dir=rds.directory / "rsi")
        # The adoption record is owned by RSI. An optional L3 event must not
        # create an empty reference ledger inside a project-only workspace.
        if rds.db_path.is_file():
            try:
                with rds.transaction() as (db, state):
                    rds.event(db, "RULE_APPLIED", **res)
            except Exception:
                pass
        return res
    elif args.action == "evaluate-rule":
        from rds_rsi import evaluate_candidate
        spec = load_spec(args.rule)
        reject_self_signatures(spec)
        _, graph = load_judgment_graph(args.graph)
        result = evaluate_candidate(spec, graph, load_spec(args.cases), confirmation_dir=rds.directory / "rsi")
        if args.output:
            Path(args.output).write_text(canonical(result), encoding="utf-8")
        return result
    elif args.action == "rollback-rule":
        from rds_meta import rollback_rule
        return rollback_rule(args.record, graph_path=args.graph, dry_run=args.dry_run)
    elif args.action == "reflect":
        if _has_project_contract(args.root):
            from rds_project import ProjectStore
            state = ProjectStore(args.root).snapshot()
            receipts = state["receipts"]
        else:
            with rds.snapshot() as (db, state):
                receipts = [strict_json(r[0]) for r in db.execute("SELECT body FROM receipts ORDER BY run_id")]
        # Search terms are a retrieval request, not retrieved Obelisk evidence.
        proposals = reflect_from_state(state, receipts)
        if getattr(args, "output", None):
            Path(args.output).write_text(json.dumps(proposals, indent=2, ensure_ascii=False), encoding="utf-8")
        result = {"proposed_rules_count": len(proposals), "proposals": proposals, "auto_apply": False}
        if getattr(args, "terms", None):
            result["history_request"] = {"terms": args.terms, "project_path": str(Path(args.root).resolve()),
                                         "evidence_status": "NOT_RETRIEVED"}
        return result
    elif args.action == "fuzz":
        from rds_adversary import AdversarialMutator
        plan = json_object(load_spec(args.plan), "Plan spec")
        m_type = getattr(args, "type", "all")
        mutants = AdversarialMutator.mutate_plan(plan, mutation_type=m_type)
        return {"original_plan_id": plan.get("id"), "mutants_count": len(mutants), "mutants": mutants}
    elif args.action == "evaluate-alignment":
        from rds_adversary import AlignmentEvaluator
        rule = load_spec(args.rule)
        evaluator = AlignmentEvaluator(Path(args.root))
        return evaluator.evaluate_rule(rule)
    elif args.action == "auto-repair":
        from rds_adversary import AutoRepairEngine
        graph_path = Path(getattr(args, "graph", None) or (Path(__file__).resolve().parents[1] / "references/judgment-graph.yaml"))
        engine = AutoRepairEngine(Path(args.root), graph_path)
        dry = getattr(args, "dry_run", False)
        return engine.run_self_repair(dry_run=dry)
    else:
        raise ValueError(f"Unknown meta action: {args.action}")


def cmd_advise(args, rds):
    from rds_advisor import RDSAdvisor
    owned = _owned_project(args.root)
    if owned is not None:
        overrides = ("research_context", "graph", "artifacts", "templates", "choose", "record",
                     "frontier", "frontier_proposals", "saved_dependencies", "plan", "telemetry",
                     "doc", "topic", "train_loss", "val_loss", "baseline_loss", "fit_telemetry",
                     "literature", "research_note")
        require(not any(getattr(args, name, None) is not None and getattr(args, name) is not False
                        for name in overrides),
                'Program-owned Advisor reads the frozen policy and complete run ledger; '
                'caller context, graph, facts and choice overrides are not accepted')
        from rds_owned_advisor import review
        result = review(owned)
        if getattr(args, 'working_set', False):
            from rds_advisor_workset import build
            result['working_set'] = build(owned, result)
        return result
    require(not getattr(args, 'working_set', False), '--working-set requires a program-owned advisor_policy')
    require(not getattr(args, "research_note", None),
            "--research-note is retired; use checkpoint save --decision and a scoped --research-context")
    has_train = getattr(args, "train_loss", None) is not None
    has_val = getattr(args, "val_loss", None) is not None
    require(has_train == has_val, "Provide both --train-loss and --val-loss for fit diagnosis")
    require(not (getattr(args, "fit_telemetry", None) or getattr(args, "baseline_loss", None) is not None)
            or (has_train and has_val), "--fit-telemetry and --baseline-loss require --train-loss and --val-loss")
    modes = [bool(getattr(args, name, None)) for name in ("literature", "telemetry", "doc", "plan")]
    require(sum(modes) + int(has_train) <= 1, "Choose one Advisor mode per call")
    has_search_context = bool(getattr(args, "research_context", None) or getattr(args, "artifacts", None))
    has_context = bool(has_search_context or getattr(args, "frontier", None) or getattr(args, "frontier_proposals", None))
    require(not getattr(args, "templates", None) or has_search_context,
            "--templates requires --research-context or --artifacts with a decision")
    require(not getattr(args, 'saved_dependencies', False) or has_search_context,
            '--saved-dependencies requires a research context or artifact decision')
    require(not (any(modes) or has_train) or not (has_context or getattr(args, "templates", None) or getattr(args, "graph", None)),
            "Research context, artifacts, templates, frontier and graph require the direction-search mode")
    require(not getattr(args, "topic", None) or getattr(args, "doc", None), "--topic requires --doc")
    advisor = RDSAdvisor(Path(args.root))

    if getattr(args, "literature", None):
        principles = advisor.query_literature_principles(args.literature)
        return {"advisor_type": "LITERATURE_PRINCIPLES_SURVEY", "query": args.literature,
                "matches_count": len(principles), "principles": principles,
                "status": "UNAVAILABLE" if advisor.literature_load_errors else "LOADED",
                "literature_load_errors": advisor.literature_load_errors,
                "assurance": "HEURISTIC_ONLY"}
    
    # These branches do not read research state. In particular, a solver must
    # not inherit an outer SQLite reader that would block another writer's commit.
    if getattr(args, "telemetry", None):
        return advisor.advise_on_loss_dynamics(load_spec(args.telemetry))
    if getattr(args, "train_loss", None) is not None and getattr(args, "val_loss", None) is not None:
        b_loss = float(args.baseline_loss) if getattr(args, "baseline_loss", None) is not None else None
        fit_telemetry = load_spec(args.fit_telemetry) if getattr(args, "fit_telemetry", None) else None
        return advisor.diagnose_fit_status(float(args.train_loss), float(args.val_loss), b_loss,
                                          telemetry=fit_telemetry)
    if getattr(args, "doc", None):
        return advisor.ingest_document(Path(args.doc), topic=getattr(args, "topic", None))
    if getattr(args, "plan", None):
        # A non-object plan is malformed input, not a compliance finding to advise on.
        plan = json_object(load_spec(args.plan), "Plan spec")
        try:
            cmd_plan(argparse.Namespace(plan=args.plan, action="check"), rds, advisory=True)
        except Exception as exc:
            return advisor.advise_on_rejection(str(exc), plan)
        return {
            "advisor_type": "PLAN_COMPLIANCE_PASS",
            "status": "APPROVED",
            "actionable_suggestion": "方案通过门禁安全检查，可提交计划；正式提交时将重新核验预算和数据暴露。"
        }
    if _has_project_contract(args.root):
        from rds_project import ProjectStore
        state = ProjectStore(args.root).snapshot()
    elif rds.db_path.exists():
        with rds.snapshot() as (_, snapshot):
            state = snapshot
    else:
        require(has_context, "RDS is not initialized")
        state = {}
    if getattr(args, "research_context", None):
        state["advisor_context"] = load_spec(args.research_context)
    if getattr(args, 'saved_dependencies', False):
        from rds_tms_store import with_saved_dependencies
        state['advisor_context'] = with_saved_dependencies(args.root, state.get('advisor_context', {}))
    from rds_advisor_coverage import project_context
    state['advisor_context'] = project_context(args.root, state.get('advisor_context', {}))
    if getattr(args, "frontier", None):
        context = state.setdefault("advisor_context", {})
        require(isinstance(context, dict), "Research context must be an object")
        require("frontier" not in context, "Supply frontier in --research-context or --frontier, not both")
        context["frontier"] = strict_json(read_bounded(args.frontier, 2 * 1024 * 1024).decode("utf-8-sig"))
    if getattr(args, "frontier_proposals", None):
        context = state.setdefault("advisor_context", {})
        require(isinstance(context, dict) and "frontier" in context, "--frontier-proposals requires frontier input")
        require("frontier_proposals" not in context, "Supply one frontier proposal pack")
        context["frontier_proposals"] = strict_json(read_bounded(args.frontier_proposals, 128 * 1024).decode("utf-8-sig"))
    context = state.get("advisor_context", {})
    require(not isinstance(context, dict) or "frontier_proposals" not in context or "frontier" in context,
            "Frontier proposals require frontier input")
    imported = None
    if getattr(args, "artifacts", None):
        from rds_artifacts import BINDING_FIELDS, _unknown, ingest_manifest
        imported = ingest_manifest(args.artifacts, root=args.root, receipts=state.get("receipts"))
        context = dict(imported["context"])
        manual = state.get("advisor_context", {})
        require(isinstance(manual, dict) and isinstance(manual.get("facts", {}), dict),
                "Research context and facts must be objects")
        require(isinstance(manual.get("costs", {}), dict), "Research context costs must be an object")
        for key in ("decision", "targets", "budget", "max_depth", "max_candidates", "target_types", "templates", "frontier", "frontier_proposals", "resources",
                    "dependency_map", "objective_binding", "method_constraints", "research_mode", "require_goal_link", "scope",
                    "audit_receipts", "audit_files",
                    "obstructions"):
            if key in manual:
                context[key] = manual[key]
        facts = dict(context.get("facts", {}))
        for name, record in manual.get("facts", {}).items():
            require(isinstance(record, dict), "Each manual fact must be an object")
            binding = record.get("binding", {})
            require(isinstance(binding, dict), "Each manual fact binding must be an object")
            original = facts.get(name)
            fields = ([key for key in BINDING_FIELDS if key in binding and key in original.get("binding", {})
                       and binding[key] != original["binding"][key]] if original is not None else [])
            if original is not None and (original.get("value") != record.get("value") or
                    isinstance(original.get("value"), bool) != isinstance(record.get("value"), bool) or fields):
                imported["conflicts"].append({"fact_id": name, "fields": fields,
                    "reason": "Manual declaration conflicts with imported record"})
                facts[name] = deepcopy(original)
                _unknown(facts[name], "Manual declaration conflicts with imported record")
            elif name in facts and (record.get("reliable") is False or record.get("reliability") in {"UNRELIABLE", "UNKNOWN"}):
                imported["conflicts"].append({"fact_id": name, "field": "reliability", "reason": "Manual context explicitly disputes this record's reliability"})
                facts[name] = deepcopy(original)
                _unknown(facts[name], "Manual context explicitly disputes this record's reliability")
            elif name not in facts:
                facts[name] = record
        context["facts"] = facts
        costs = dict(context.get("costs", {}))
        for name, record in manual.get("costs", {}).items():
            require(isinstance(record, dict), "Each manual cost must be an object")
            binding = record.get("binding", {})
            require(isinstance(binding, dict), "Each manual cost binding must be an object")
            if name not in costs:
                costs[name] = record
                continue
            original = costs[name]
            fields = [key for key in ("value", "resource", "unit", "comparison_group")
                      if original.get(key) != record.get(key)]
            fields.extend("binding." + key for key in BINDING_FIELDS
                          if key in binding and key in original.get("binding", {})
                          and binding[key] != original["binding"][key])
            if isinstance(original.get("value"), bool) != isinstance(record.get("value"), bool):
                fields.append("value")
            if record.get("reliable") is False or record.get("reliability") in {"UNRELIABLE", "UNKNOWN"}:
                fields.append("reliability")
            if fields:
                reason = "Manual declaration conflicts with imported cost"
                imported["conflicts"].append({"cost_id": name, "fields": sorted(set(fields)), "reason": reason})
                costs[name] = deepcopy(original)
                _unknown(costs[name], reason)
        context["costs"] = costs
        if imported["conflicts"]:
            imported["status"] = "CONFLICT"
        state["advisor_context"] = context
    if getattr(args, "templates", None):
        context = state.get("advisor_context", {})
        decision = context.get("decision") if isinstance(context, dict) else None
        decision_id = decision.get("id") if isinstance(decision, dict) else decision
        require(not isinstance(context, dict) or "frontier" not in context
                or isinstance(decision_id, str) and bool(decision_id.strip()),
                "--templates with frontier input requires an explicit direction-search decision")
        state["advisor_templates"] = load_spec(args.templates)
    frontier_only = isinstance(state.get("advisor_context"), dict) and "frontier" in state["advisor_context"] and "decision" not in state["advisor_context"]
    require(not frontier_only or not getattr(args, "graph", None), "--graph needs an explicit direction-search decision")
    if frontier_only:
        graph = {}
    else:
        from rds_meta import load_judgment_graph
        _, graph = load_judgment_graph(getattr(args, "graph", None))
    from rds_math import check_context
    binding = check_context(args.root, state.get('advisor_context', {}))
    if binding is not None:
        # Keep the original goal visible while reviewing a narrower local action.
        context = deepcopy(state.get('advisor_context', {}))
        context['objective_binding'] = binding
        state = {**state, 'advisor_context': context}
    recommendations = advisor.recommend_next_directions(state, graph)
    result = {
        "advisor_type": "STRATEGIC_RESEARCH_ADVICE",
        "active_branch": state.get("active_branch", "main"),
        "recommendations_count": len(recommendations),
        "recommendations": recommendations
    }
    if binding is not None:
        result['objective_binding'] = binding
    if imported is not None:
        result["artifact_import"] = imported
    if getattr(args, "record", None) or getattr(args, "choose", None):
        require(getattr(args, "record", None), "--choose needs --record; omit --choose only when one READY candidate remains")
        from rds_quick import record_choice
        result["checkpoint"] = record_choice(args.root, result, state.get("advisor_context", {}), args.choose, args.record)
    return result


def cmd_project(args):
    """Run a locked external project without claiming task or mechanism gains."""
    from rds_project import ProjectStore
    from rds_project_lifecycle import check_root, discover, enable_advisor, initialize
    store = ProjectStore(args.root)
    if args.action == 'discover':
        return discover(args.root)
    if args.action != 'init':
        check_root(args.root)
    if args.action == 'enable-advisor':
        return enable_advisor(store, load_spec(args.policy), apply=args.apply,
                              expected_snapshot=args.expected_snapshot)
    if args.action == 'plan':
        from rds_steering import plan
        return plan(store, load_spec(args.intent) if args.intent else None,
                    output=args.output, save_as=args.save_as)
    if args.action == 'steering':
        from rds_steering import status
        return status(store)
    if args.action == 'steer':
        from rds_steering import submit
        return submit(store, load_spec(args.request), user_directed=args.user_directed, source=args.source)
    if args.action == "init":
        if args.recipe:
            require(args.supersedes is None, 'Recipe initialization cannot supersede an existing project')
            require(args.mode != 'quick', 'A recipe creates a FULL project with program-owned Advisor')
            check_root(args.root, separate_reason=args.separate_project)
            from rds_project_assembly import initialize as assemble
            return assemble(store, args.recipe, separate_reason=args.separate_project)
        return initialize(store, load_spec(args.contract), mode=args.mode, supersedes=args.supersedes,
                          separate_reason=args.separate_project)
    if args.action == "revise":
        from rds_method_revision import apply
        return apply(store, load_spec(args.proposal))
    if args.action == "improve":
        from rds_tool_workbench import prepare
        return prepare(store, args.code_path, args.id)
    if args.action == "create":
        return store.register(load_spec(args.manifest))
    if args.action == "execute":
        return _project_result(store, store.execute(args.id, background=args.background))
    if args.action == "recover":
        return _project_result(store, store.recover(args.id))
    if args.action == "drive":
        from rds_autonomy import drive
        return drive(store, args.max_steps, prepare_only=args.prepare_only)
    if args.action == "advance":
        require(_owned_project(args.root) is not None,
                'project advance requires a frozen advisor_policy; legacy projects use project next')
        from rds_owned_advisor import review
        advice = review(store)
        selected = advice.get('selected_manifest')
        if selected is None:
            return advice
        snapshot = store.snapshot()
        if not any(run['id'] == selected['id'] for run in snapshot['runs']):
            store.register(selected)
        return _project_result(store, store.execute(selected['id'], background=args.background))
    if args.action == "next":
        if _owned_project(args.root) is not None:
            from rds_owned_advisor import review
            return review(store)
        return store.next_move()
    if args.action == "compare":
        return store.compare()
    if args.action == "costs":
        from rds_costs import summarize_costs
        return summarize_costs(store.snapshot().get("receipts", []))
    if args.action == "control-check":
        from rds_costs import check_control_reuse
        return check_control_reuse(load_spec(args.candidate), load_spec(args.current), root=args.root)
    return store.snapshot()


def _project_result(store, receipt):
    """Keep the hashed receipt intact while exposing automatic result reception."""
    advice = getattr(store, 'last_advisor_review', None)
    if advice is None:
        return receipt
    result = {'receipt': receipt, 'advisor': advice}
    observation = getattr(store, 'last_advisor_observation', None)
    if observation is not None:
        result['observation'] = observation
    return result


def _owned_project(root):
    if not _has_project_contract(root):
        return None
    from rds_project import ProjectStore
    store = ProjectStore(root)
    with store._db(True) as db:
        return store if 'advisor_policy' in store._contract(db) else None


def _has_project_contract(root):
    from rds_project import ProjectStore
    store = ProjectStore(root)
    if not store.path.is_file():
        return False
    with store._db(True) as db:
        table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone()
        return bool(table and db.execute('SELECT 1 FROM contract WHERE id=1').fetchone())


def _reference_binding_check(rds, snapshot):
    """Check continuation bindings, reading each named input once at this boundary."""
    files, errors, contents, hashes = [], [], {}, {}
    def read(path, limit):
        path = Path(path)
        path = (rds.root / path).resolve() if not path.is_absolute() else path.resolve()
        if path not in contents:
            try:
                contents[path] = read_bounded(path, limit)
            except (ValueError, OSError) as exc:
                contents[path] = exc
        if isinstance(contents[path], Exception):
            raise contents[path]
        require(len(contents[path]) <= limit, "Bound input exceeds byte limit")
        return path, contents[path]

    def compare(path, expected, role, limit):
        try:
            path, raw = read(path, limit)
            if path not in hashes:
                hashes[path] = digest(raw)
            require(hashes[path] == expected, f"Binding changed: {role}: {path}")
            files.append({"path": str(path), "role": role, "status": "UNCHANGED"})
        except (ValueError, KeyError, OSError, UnicodeError) as exc:
            errors.append(f"{role}: {exc}")

    contract = snapshot["contract"]
    try:
        path, raw = read(contract["baseline_source"], 8192)
        control = ast.dump(parse_source(raw.decode("utf-8-sig"))["control"])
        require(control == contract["baseline_control_ast"], f"Baseline computation changed: {path}")
        files.append({"path": str(path), "role": "baseline_control", "status": "UNCHANGED"})
    except (ValueError, KeyError, OSError, UnicodeError, SyntaxError) as exc:
        errors.append(f"baseline_control: {exc}")
    for split_id, split in contract["splits"].items():
        compare(split["path"], split["sha256"], "split:" + split_id, 2_000_000)
    for plan_id, plan in snapshot.get("plans", {}).items():
        if plan.get("run_status") in {"RESERVED", "RUNNING", "RECOVERY_REQUIRED"}:
            binding = plan.get("binding", {})
            if "source_path" not in binding or "source_sha256" not in binding:
                errors.append("Missing source binding for pending plan " + plan_id)
            else:
                compare(binding["source_path"], binding["source_sha256"], "pending_source:" + plan_id, 8192)
    current_engine = engine_id()
    if current_engine != snapshot.get("engine_sha256"):
        errors.append("Reference engine changed; initialize a new contract before execution")
    return {"files": files, "engine_sha256": current_engine, "errors": errors}


def cmd_checkpoint(args, rds):
    from rds_checkpoints import restore_checkpoint, save_checkpoint
    kind = args.kind
    if kind == "auto":
        kind = "project" if _has_project_contract(args.root) else "reference"
    if kind == "project":
        from rds_project import ProjectStore
        snapshot = ProjectStore(args.root).snapshot(check_bindings=args.action == "restore")
    else:
        with rds.snapshot() as (_, snapshot):
            snapshot = dict(snapshot)
        if args.action == "restore":
            snapshot["binding_check"] = _reference_binding_check(rds, snapshot)
    if args.action == "save":
        return save_checkpoint(args.root, args.id, snapshot, kind=kind,
                               decision=load_spec(args.decision) if args.decision else None)
    return restore_checkpoint(args.root, args.id, snapshot, kind=kind)


class FriendlyParser(argparse.ArgumentParser):
    """Exact commands first, documented aliases second, unique prefixes last.

    Never approximate paths/values or the command after --. Typos receive a
    repair hint rather than triggering a different operation.
    """
    def parse_args(self, args=None, namespace=None):
        return super().parse_args(self.normalize_args(args), namespace)

    def normalize_args(self, args=None, *, quiet=False):
        """Resolve our options once; quiet inspection performs no CLI action."""
        def fail(message):
            if quiet:
                raise ValueError(message)
            self.error(message)
        tokens = list(sys.argv[1:] if args is None else args)
        from rds_usage import COMMAND_ALIASES as aliases, COMMAND_MACROS, ROOT_OPTIONS
        current, result, globals_, i = self, [], [], 0
        wrapped = False
        while i < len(tokens):
            token = tokens[i]
            if token == '--':
                result.extend(tokens[i:])
                break
            key = token.split('=', 1)[0]
            root_value = token.split('=', 1)[1] if '=' in token else None
            if key not in ROOT_OPTIONS and current is self and token.startswith('-') and len(token) > 2:
                # Match the top-level spellings argparse already accepts, including
                # unique long prefixes and attached short-option values. Tuple
                # layouts differ across Python versions; the explicit value is last.
                matches = self._get_option_tuples(token)
                if len(matches) == 1 and matches[0][0] is self._option_string_actions.get('--root'):
                    key, root_value = matches[0][1], matches[0][-1]
            if key in ROOT_OPTIONS:
                if globals_:
                    fail('Supply one project root')
                if root_value is not None:
                    globals_ = ['--root', root_value]
                else:
                    if i + 1 >= len(tokens):
                        fail(key + ' requires a path')
                    globals_ = ['--root', tokens[i + 1]]
                    i += 1
                i += 1
                continue
            if token.startswith("-"):
                key = token.split("=", 1)[0]
                action = current._option_string_actions.get(key)
                if action is None:
                    opts = [v for v in current._option_string_actions if v.startswith(key)]
                    action = current._option_string_actions[opts[0]] if len(opts) == 1 else None
                result.append(token)
                if action is not None and action.nargs != 0 and "=" not in token:
                    count = action.nargs if isinstance(action.nargs, int) else 1
                    result.extend(tokens[i + 1:i + 1 + count])
                    i += count
            else:
                sub = next((a for a in current._actions if isinstance(a, argparse._SubParsersAction)), None)
                if sub is None and wrapped:
                    # Once the child starts, neither root flags nor its options are ours.
                    if not quiet:
                        print('[RDS-RESOLVE] implicit child boundary before ' + token, file=sys.stderr)
                    result.extend(['--'] + tokens[i:])
                    break
                if sub is not None:
                    choices = sub.choices
                    exact = next((v for v in choices if v.casefold() == token.casefold()), None)
                    macro = COMMAND_MACROS.get(token.casefold()) if current is self and exact is None else None
                    if macro:
                        result.extend(macro)
                        if not quiet:
                            print('[RDS-RESOLVE] ' + token + ' -> ' + ' '.join(macro), file=sys.stderr)
                        current = choices[macro[0]]
                        for nested in macro[1:]:
                            children = next(a for a in current._actions if isinstance(a, argparse._SubParsersAction))
                            current = children.choices[nested]
                        i += 1
                        continue
                    matches = ([exact] if exact else [v for v in choices if token.casefold() in aliases.get(v, set())])
                    if not matches:
                        matches = [v for v in choices if v.casefold().startswith(token.casefold())]
                    if len(matches) != 1:
                        suggestions = matches or difflib.get_close_matches(token, choices, n=3, cutoff=0.5)
                        fail("Ambiguous or unknown command " + token + "; candidates: " + ", ".join(suggestions))
                    resolved = matches[0]
                    if resolved != token and not quiet:
                        print("[RDS-RESOLVE] " + token + " -> " + resolved, file=sys.stderr)
                    token = resolved
                    current = choices[resolved]
                    wrapped = wrapped or (sub is next(a for a in self._actions if isinstance(a, argparse._SubParsersAction)) and resolved == 'exec')
                result.append(token)
            i += 1
        return globals_ + result

    def error(self, message):
        options = list(self._option_string_actions)
        unknown = next((token for token in message.split() if token.startswith('--')), None)
        suggestions = difflib.get_close_matches(unknown or '', options, n=2, cutoff=0.5)
        if suggestions:
            message += " (closest: " + ", ".join(suggestions) + ")"
        # The hint must run as printed: keep the failing subcommand path and ask for help (#72).
        path = ' '.join(self.prog.split()[1:])
        super().error(message + '\n[RDS-HINT] python scripts/rds_cli.py ' + (path + ' ' if path else '') + '--help')


def parser():
    p = FriendlyParser(description=__doc__)
    p.add_argument("--root", "--workspace", "--project-root", "-w", "-d", "--dir", default=".")
    p.add_argument("--version", action="version", version=VERSION)
    commands = p.add_subparsers(dest="command", required=True)
    quick = commands.add_parser("exec", help="Freeze, bind and execute a local tool command without contract JSON")
    quick.add_argument("--name", "--id", help="Optional stable identity; defaults to frozen-request hash")
    quick.add_argument("--timeout", "-t", "--time", "--timeout-seconds", type=float, default=60, help="Wall cap in seconds (default 60, max 3600)")
    quick.add_argument("--bind", action="append", default=[], help="Additional input: code|config|data|evaluator=relative/path")
    quick.add_argument("--output", "-o", "--out", action="append", default=[], help="Required output file in the frozen job workspace; directories are not accepted")
    quick.add_argument("--guard", help="Frozen metric/milestone policy; FAIL/UNKNOWN blocks promotion, retains the run")
    quick.add_argument('--objective', help='Bind original rds-objective-v1 JSON; reuse a bound native objective by default')
    quick.add_argument("--background", action="store_true", help="Use the existing Windows Task Scheduler runner")
    quick.add_argument("--context", "--research-context", "-c", "--ctx", dest="research_context")
    quick.add_argument("--graph")
    quick.add_argument('--saved-dependencies', action='store_true', help='Read current dependencies from --ledger before advice and admission')
    quick.add_argument("--choose", help="Exact candidate ID from the direction review")
    quick.add_argument("--ledger", "-l", "--db", help="Existing project ledger for prospective choice and execution feedback")
    quick.add_argument("--json", action="store_true", help="Return the full operational receipt")
    quick.add_argument("argv", nargs=argparse.REMAINDER)
    reject = commands.add_parser("reject", help="Record a scoped rejection using the current choice, without decision JSON")
    reject.add_argument("--route", help="Current candidate ID; default the single recorded route")
    reject.add_argument("--reason", "-m", "--why", "--message", required=True)
    reject.add_argument("--evidence", "-e", "--witness", required=True)
    reject.add_argument("--domain", help="Explicit min/max/eq parameter predicates and universal-range justification")
    reject.add_argument("--id")
    reject.add_argument("--json", action="store_true")
    guard = commands.add_parser('guard', help='Check a frozen comparable-metric/milestone policy without changing the incumbent')
    guard.add_argument('--policy', required=True)
    guard.add_argument('--json', action='store_true')
    structure = commands.add_parser('structure', help='Bounded problem-model branches, experiments and TMS rollback')
    structure_actions = structure.add_subparsers(dest='action', required=True)
    structure_actions.add_parser('request', help='Return a few open exploration tasks').add_argument('--limit', type=int, default=3)
    structure_actions.add_parser('propose', help='Retain an experimentally testable candidate topology').add_argument('--proposal', required=True)
    for name in ('advance', 'feedback', 'activate', 'rollback'):
        structure_actions.add_parser(name).add_argument('--id', required=True)
    structure_actions.add_parser('next')
    structure_actions.add_parser('list')
    structure_actions.add_parser('recover')
    structure_actions.add_parser('drive', help='Consume proposals and feedback until a bounded stop or open Agent task').add_argument('--steps', type=int, default=1)
    hypergraph = commands.add_parser('hypergraph', help='Bounded AND/OR proof dependency analysis, not proof certification')
    hypergraph.add_argument('--input', '-i', help='Import or restore a map; omitted inputs reuse this root\'s saved map')
    hypergraph.add_argument('--output', '-o')
    hypergraph.add_argument('--audit-files', action='store_true')
    hypergraph.add_argument('--update', '-u', action='append', default=[], help='Merge a small declaration fragment into the saved map')
    hypergraph.add_argument('--declare', action='append', default=[], help='Small JSON declaration, without writing an input file')
    for flag in ('retract-node', 'retract-rule', 'refute-node', 'refute-rule'):
        hypergraph.add_argument('--' + flag, action='append', default=[])
    hypergraph.add_argument('--change-source', help='Locator for the declared change; creates no scientific verdict')
    hypergraph.add_argument('--trace-cone', help='Explain the selected support for one claim')
    hypergraph.add_argument('--audit-receipts', action='store_true',
                            help='verify receipt-bound evidence against project ledgers')
    hypergraph.add_argument('--json', action='store_true')
    usage = commands.add_parser("usage", help="Show locally recorded daily CLI invocation counts")
    window = usage.add_mutually_exclusive_group()
    window.add_argument("--days", type=int, default=14)
    window.add_argument("--since", help="Inclusive start date, YYYY-MM-DD")
    usage.add_argument("--until", help="Inclusive end date, YYYY-MM-DD; default today")
    usage.add_argument("--json", action="store_true", help="Emit structured usage statistics")
    math_cmd = commands.add_parser('math', help='Native immutable objectives and research assets; storage is not mathematical proof')
    math_actions = math_cmd.add_subparsers(dest='action', required=True)
    math_bind = math_actions.add_parser('bind')
    math_bind.add_argument('--objective', required=True)
    math_add = math_actions.add_parser('add')
    math_add.add_argument('--id', required=True)
    math_add.add_argument('--kind', choices=['lemma', 'algebraic-root', 'geometry', 'note'], default='note')
    math_add.add_argument('--file', required=True)
    math_add.add_argument('--depends', action='append', default=[])
    for action in ('get', 'affected'):
        math_actions.add_parser(action).add_argument('--id', required=True)
    math_refute = math_actions.add_parser('refute')
    math_refute.add_argument('--id', required=True)
    math_refute.add_argument('--reason', required=True)
    math_refute.add_argument('--evidence', required=True)
    math_actions.add_parser('status')
    for child in math_actions.choices.values():
        child.add_argument('--json', action='store_true')
    rsi = commands.add_parser('rsi', help='Extract, validate and register local function candidates; no MRS dependency')
    rsi_actions = rsi.add_subparsers(dest='action', required=True)
    rsi_extract = rsi_actions.add_parser('extract')
    rsi_extract.add_argument('--source', required=True)
    rsi_extract.add_argument('--entry', required=True)
    rsi_extract.add_argument('--name', required=True)
    rsi_validate = rsi_actions.add_parser('validate')
    rsi_validate.add_argument('--name', required=True)
    rsi_validate.add_argument('--cases', required=True)
    rsi_validate.add_argument('--timeout', '-t', type=float, default=10)
    rsi_validate.add_argument('--ledger', help='Charge a supplied operational wall-budget ledger before validation')
    rsi_compare = rsi_actions.add_parser('compare', help='Compare measured local tool cost on identical fixed-precision cases')
    rsi_compare.add_argument('--baseline', required=True)
    rsi_compare.add_argument('--candidate', required=True)
    rsi_compare.add_argument('--cases', required=True)
    rsi_compare.add_argument('--precision-key', required=True, help='Explicit precision keyword present in every case')
    rsi_compare.add_argument('--precision', required=True, type=int)
    rsi_compare.add_argument('--timeout', '-t', type=float, default=10)
    rsi_compare.add_argument('--min-speedup', type=float, default=1.1, help='Prospective descriptive wall-time ratio threshold; greater than one')
    rsi_compare.add_argument('--ledger', help='Charge both validations to the existing operational wall-budget ledger')
    rsi_register = rsi_actions.add_parser('register')
    rsi_register.add_argument('--name', required=True)
    rsi_register.add_argument('--validation', help='Optional only when one passing local validation exists')
    rsi_use = rsi_actions.add_parser('use')
    rsi_use.add_argument('--name', required=True)
    rsi_use.add_argument('--output', '-o', help='Export a verified local module to a project-relative .py file without overwriting')
    rsi_list = rsi_actions.add_parser('list', help='Discover local tool entries; registration is not a fresh reuse check')
    rsi_list.add_argument('--name', help='Inspect one exact local tool name without dumping unrelated records')
    rsi_prepare = rsi_actions.add_parser('prepare-application', help='Export a qualified finite task candidate before frozen project init; no execution')
    for field in ('name', 'inputs', 'cases', 'code-path', 'driver', 'request', 'output',
                  'decision', 'candidate', 'run-id', 'obligation'):
        rsi_prepare.add_argument('--' + field, required=True)
    rsi_prepare.add_argument('--action-file', required=True, help='Exact action JSON, including target and operation')
    rsi_prepare.add_argument('--observation-fact', action='append', required=True,
                             help='Owned JSON observations that can carry this result into a decision')
    for child in rsi_actions.choices.values():
        child.add_argument('--json', action='store_true')
    commands.add_parser("init").add_argument("--contract", required=True)
    hypo = commands.add_parser("hypothesis").add_subparsers(dest="action", required=True).add_parser("add")
    hypo.add_argument("--spec", required=True)
    gate = commands.add_parser("gate").add_subparsers(dest="action", required=True).add_parser("check")
    gate.add_argument("--plan", required=True)
    plans = commands.add_parser("plan").add_subparsers(dest="action", required=True)
    plans.add_parser("create").add_argument("--spec", required=True)
    plans.add_parser("cancel").add_argument("--id", required=True)
    runs = commands.add_parser("run").add_subparsers(dest="action", required=True)
    for action in ("execute", "recover"):
        runs.add_parser(action).add_argument("--id", required=True)
    expose = commands.add_parser("data").add_subparsers(dest="action", required=True).add_parser("expose")
    expose.add_argument("--split", required=True)
    expose.add_argument("--purpose", choices=["view", "train", "select", "memory_retrieval", "prior_exposure"], required=True)
    expose.add_argument("--actor", required=True)
    expose.add_argument("--reason", required=True)
    commands.add_parser("decide").add_argument("--run", required=True)
    commands.add_parser("status").add_argument("--brief", "--digest", action="store_true")

    project = commands.add_parser("project", help="Locked local project runner with receipts and resource accounting")
    pr_actions = project.add_subparsers(dest="action", required=True)
    pr_actions.add_parser('discover', help='Find the existing project in this root/ancestors; report mode and next capabilities without execution')
    pr_enable = pr_actions.add_parser('enable-advisor', help='Preview or atomically enable Advisor in this same ledger; preserve history and budget')
    pr_enable.add_argument('--policy', required=True, help='Explicit basic owned Advisor policy JSON')
    pr_enable.add_argument('--apply', action='store_true', help='Apply the exact reviewed snapshot; preview by default')
    pr_enable.add_argument('--expected-snapshot', help='Snapshot SHA256 from the activation preview; required with --apply')
    pr_plan = pr_actions.add_parser('plan', help='Prepare a minimal draft with explicit unknowns; never authorize or launch work')
    pr_plan.add_argument('--intent', help='Optional declared goal/scope/budget/evaluation JSON')
    pr_plan.add_argument('--output', help='Write a new project-relative proposal artifact')
    pr_plan.add_argument('--save-as', help='Retain the draft in an initialized project checkpoint and CAS')
    pr_actions.add_parser('steering', help='Read the current user instruction, active work disposition and live resources')
    pr_steer = pr_actions.add_parser('steer', help='Record a host-attested current-user instruction; preserve original execution authority')
    pr_steer.add_argument('--request', required=True)
    pr_steer.add_argument('--user-directed', action='store_true', help='Caller attests this is a current user request, not imported text')
    pr_steer.add_argument('--source', required=True, help='Locator of the current user request in the trusted host')
    pr_init = pr_actions.add_parser("init")
    pr_source = pr_init.add_mutually_exclusive_group(required=True)
    pr_source.add_argument("--contract")
    pr_source.add_argument("--recipe", help="Compile explicit research declarations into an owned contract")
    pr_init.add_argument('--mode', choices=['full', 'quick'], help='New projects default to FULL with Advisor; QUICK is explicitly limited. Existing exact retries retain their mode')
    pr_init.add_argument('--separate-project', metavar='REASON', help='Explicitly declare an independent project; required under an existing research root')
    pr_init.add_argument("--supersedes", metavar="PREDECESSOR_ROOT",
                         help="Link this new root to a frozen project root by digest; the predecessor is never modified")
    pr_actions.add_parser("revise", help="Adopt a bounded method revision in the same ledger without resetting budget or deadline").add_argument("--proposal", required=True)
    pr_improve = pr_actions.add_parser("improve", help="Prepare receipt diagnostics, editable tool code and a same-ledger revision proposal")
    pr_improve.add_argument("--code-path", required=True)
    pr_improve.add_argument("--id", required=True)
    pr_actions.add_parser("create").add_argument("--manifest", required=True)
    pr_exec = pr_actions.add_parser("execute")
    pr_exec.add_argument("--id", required=True)
    pr_exec.add_argument("--background", action="store_true", help="Use a tool-owned Windows Task Scheduler task")
    pr_advance = pr_actions.add_parser("advance", help="Execute one program-selected route and receive its result automatically")
    pr_advance.add_argument("--background", action="store_true", help="Use the existing authorized background runner")
    pr_advance.add_argument("--brief", "--digest", action="store_true", help="Retain the receipt and advice and return a bounded digest")
    pr_drive = pr_actions.add_parser("drive", help="Drive a bounded owned research loop, including authorized model repair")
    pr_drive.add_argument("--max-steps", type=int, default=8, help="Foreground executions this pass; cumulative frozen cap remains authoritative")
    pr_drive.add_argument("--prepare-only", action="store_true", help="Retain a reviewable model request and pause before its repair worker; ordinary work and recovery continue")
    pr_actions.add_parser("recover").add_argument("--id", required=True)
    pr_actions.add_parser("next", help="Print the single next actionable project step and its command").add_argument("--brief", "--digest", action="store_true")
    pr_actions.add_parser("compare", help="Compare recorded arms against the precommitted min_useful_delta")
    pr_actions.add_parser("status").add_argument("--brief", "--digest", action="store_true")
    pr_actions.add_parser("costs")
    pr_control = pr_actions.add_parser("control-check")
    pr_control.add_argument("--candidate", required=True)
    pr_control.add_argument("--current", required=True)

    hook = commands.add_parser("host-hook", help="Bind host dispatch to ledger admission identities; coverage is not an OS sandbox")
    hook_actions = hook.add_subparsers(dest="action", required=True)
    hook_actions.add_parser("install").add_argument("--permissive", action="store_true",
                                                    help="Record advisory (non-strict) coverage")
    hook_actions.add_parser("coverage")
    hook_validate = hook_actions.add_parser("validate")
    hook_validate.add_argument("--request", required=True)
    for child in hook_actions.choices.values():
        child.add_argument("--json", action="store_true")

    checkpoints = commands.add_parser("checkpoint", help="Record decisions and recover against live project state")
    cp_actions = checkpoints.add_subparsers(dest="action", required=True)
    for action in ("save", "restore"):
        cp = cp_actions.add_parser(action)
        cp.add_argument("--id", required=True)
        cp.add_argument("--kind", choices=["auto", "project", "reference"], default="auto")
        if action == "save":
            cp.add_argument("--decision", help="Decision context; this input does not create execution authority")

    artifacts = commands.add_parser("artifacts", help="Read bounded local records with field-level provenance")
    ai = artifacts.add_subparsers(dest="action", required=True).add_parser("import")
    ai.add_argument("--manifest", required=True)

    formal = commands.add_parser("formal", help="Declare, prove and replay bounded mathematical statements")
    f_actions = formal.add_subparsers(dest="action", required=True)
    f_actions.add_parser("rules")
    f_plan = f_actions.add_parser("plan", help="Inspect a trusted affine plan without generating proofs")
    f_plan.add_argument("--spec", required=True)
    f_plan.add_argument("--output")
    f_plan.add_argument("--max-work-units", type=int,
                        help="Optional nonnegative generation operation-proxy budget after plan construction")
    f_verify = f_actions.add_parser("verify")
    f_verify.add_argument("--brief", "--digest", action="store_true")
    f_verify.add_argument("--spec", required=True)
    f_verify.add_argument("--output")
    f_verify.add_argument("--no-cache", action="store_true")
    f_verify.add_argument("--max-work-units", type=int,
                          help="Affine generation operation-proxy budget; bypasses proof cache, excludes search/replay")
    f_verify.add_argument("--tactics", nargs="+", choices=["rule", "gershgorin", "spectral_radius",
                                                         "scale_invariance", "lean4", "rational", "interval"],
                          help="Run a bounded explicit tactic chain without the default proof cache")
    f_check = f_actions.add_parser("check")
    f_check.add_argument("--brief", "--digest", action="store_true")
    f_check.add_argument("--spec", required=True)
    f_check.add_argument("--certificate", required=True)

    # Policy RSI: Branch & Stagnation Engine
    branch = commands.add_parser("branch", help="RSI Policy Stagnation & Branching engine")
    b_actions = branch.add_subparsers(dest="action", required=True)
    b_actions.add_parser("status")
    b_actions.add_parser("list")
    b_sw = b_actions.add_parser("switch")
    b_sw.add_argument("--id", required=True)
    b_fork = b_actions.add_parser("fork")
    b_fork.add_argument("--spec", required=True)

    # Graph RSI: Meta-Reflection & Rule Evolution Engine
    meta = commands.add_parser("meta", help="RSI Meta-Reflection & Rule Evolution engine")
    m_actions = meta.add_subparsers(dest="action", required=True)
    m_list = m_actions.add_parser("list-rules")
    m_list.add_argument("--graph", default=None)
    m_val = m_actions.add_parser("validate-rule")
    m_val.add_argument("--rule", required=True)
    m_app = m_actions.add_parser("apply-rule")
    m_app.add_argument("--rule", required=True)
    m_app.add_argument("--graph", default=None)
    m_app.add_argument("--force", action="store_true")
    m_app.add_argument("--dry-run", action="store_true")
    m_app.add_argument("--evaluation", help="Hash-bound independently replayed adoption report")
    m_app.add_argument("--cases", help="Original held-out case pack; replayed again before adoption")
    m_eval_rule = m_actions.add_parser("evaluate-rule")
    m_eval_rule.add_argument("--rule", required=True)
    m_eval_rule.add_argument("--graph")
    m_eval_rule.add_argument("--cases", required=True)
    m_eval_rule.add_argument("--output")
    m_rollback = m_actions.add_parser("rollback-rule")
    m_rollback.add_argument("--record", required=True)
    m_rollback.add_argument("--graph")
    m_rollback.add_argument("--dry-run", action="store_true")
    m_ref = m_actions.add_parser("reflect")
    m_ref.add_argument("--terms", default=None)
    m_ref.add_argument("--output", default=None)
    m_fuzz = m_actions.add_parser("fuzz")
    m_fuzz.add_argument("--plan", required=True)
    m_fuzz.add_argument("--type", choices=["all", "self_sign", "split_escalate", "budget_stretch", "control_perturb"], default="all")
    m_eval = m_actions.add_parser("evaluate-alignment")
    m_eval.add_argument("--rule", required=True)
    m_rep = m_actions.add_parser("auto-repair")
    m_rep.add_argument("--graph", default=None)
    m_rep.add_argument("--dry-run", action="store_true")

    history = commands.add_parser("history", help="Read history through the installed Obelisk CLI")
    actions = history.add_subparsers(dest="subcommand", required=True)
    actions.add_parser("preflight", help="Check the optional Obelisk history enhancement; no automatic installation")
    prepare = actions.add_parser("prepare")
    prepare.add_argument("--project-path")
    prepare.add_argument("--terms")
    prepare.add_argument("--offset", type=int, default=0)
    prepare.add_argument("--uuid")
    prepare.add_argument("--raw-offset", type=int, default=0)
    prepare.add_argument("--output", required=True)
    actions.add_parser("query").add_argument("--query", required=True)

    adv = commands.add_parser("advise", help="Get programmatic mathematical and strategic advice for models")
    adv.add_argument("--plan", default=None)
    adv.add_argument("--telemetry", default=None)
    adv.add_argument("--doc", default=None)
    adv.add_argument("--topic", default=None)
    adv.add_argument("--train-loss", default=None)
    adv.add_argument("--val-loss", default=None)
    adv.add_argument("--baseline-loss", default=None)
    adv.add_argument("--fit-telemetry", default=None, help="Paired, comparable curve observations for fit diagnosis")
    adv.add_argument("--literature", default=None, help="Search scoped local primary-source records")
    adv.add_argument("--research-context", "--context", "-c", "--ctx", default=None, help="Sourced facts and the decision for bounded graph search")
    adv.add_argument('--saved-dependencies', action='store_true', help='Read this root\'s program-owned dependency map into the existing Advisor context')
    adv.add_argument("--choose", help="Exact candidate ID to record as the caller's planned route")
    adv.add_argument("--record", help="New checkpoint ID; use with --choose to complete the decision fields")
    adv.add_argument("--brief", "--digest", action="store_true", help="Save full advice and return a bounded digest")
    adv.add_argument('--working-set', action='store_true', help='Project owned final advice and verified scoped feedback for Agent continuation')
    adv.add_argument("--frontier", help="Versioned research graph and evidence for bounded graph-outside exploration questions")
    adv.add_argument("--frontier-proposals", help="AI proposed nodes/relations and discriminating tests; definition checks only")
    adv.add_argument("--research-note", help="Retired: use checkpoint save --decision with a scoped --research-context")
    adv.add_argument("--artifacts", help="Hash-bound artifact manifest; reread originals at advice time")
    adv.add_argument("--templates", help="Versioned finite experiment template pack")
    adv.add_argument("--graph", default=None)

    advancement = commands.add_parser("advancement", help="Score independent intervention predictions under a locked matched budget")
    advancement_actions = advancement.add_subparsers(dest="action", required=True)
    advancement_score = advancement_actions.add_parser("score")
    advancement_score.add_argument("--protocol", required=True)
    advancement_score.add_argument("--trajectories", required=True)
    advancement_score.add_argument("--confirmations", help="Evaluator-only original outcomes; omit to report unmeasured tasks")
    return p


L3_LEDGER_COMMANDS = frozenset({"init", "hypothesis", "gate", "plan", "run", "data", "decide",
                                "branch"})


def _project_ledger_message(root):
    """True split naming for L3 commands in a root that holds only a project ledger (#76)."""
    ledger = Path(root).resolve() / ".rds" / "project.sqlite3"
    if not ledger.is_file():
        return None
    return ("L3 kernel not initialized in this root (project ledger found; L3 commands need "
            "`init --contract`). This root's recorded project state is intact; use "
            "`python -B scripts/rds_cli.py project next` for the campaign's next step")


def _main():
    try:
        args = parser().parse_args()
    except ValueError as exc:
        print("[RDS-REJECT] " + str(exc), file=sys.stderr)
        return 1
    if args.command == "usage":
        from rds_usage import render, summarize
        try:
            result = summarize(days=args.days, since=args.since, until=args.until)
            print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else render(result))
            return 0 if result["logging"] == "ENABLED" else 1
        except (ValueError, OSError, sqlite3.Error) as exc:
            print("[RDS-USAGE] " + str(exc), file=sys.stderr)
            return 1
    rds = RDSState(args.root)
    if (args.command in L3_LEDGER_COMMANDS or args.command == "meta" and args.action == "auto-repair") and not rds.db_path.exists():
        message = _project_ledger_message(args.root)
    elif (args.command == "checkpoint" and args.action == "save"
          and args.kind == "reference" and not rds.db_path.exists()):
        # A reference save reads the L3 snapshot; a project save must stay reachable.
        message = _project_ledger_message(args.root)
    else:
        message = None
    if message:
        print("[RDS-REJECT] " + message, file=sys.stderr)
        print("[RDS-HINT] python -B scripts/rds_cli.py --root \"" + str(args.root) + "\" project next", file=sys.stderr)
        return 1
    try:
        if args.command == "history":
            from rds_obelisk import history_command
            return history_command(args) or 0
        if args.command == "formal":
            from rds_verify import checked_result, plan, rules, verify
            from rds_verify_types import MAX_CERTIFICATE_BYTES
            if args.action == "rules":
                result = {"rules": rules()}
            else:
                spec = strict_json(read_bounded(args.spec, MAX_CERTIFICATE_BYTES).decode("utf-8-sig"))
                if args.action == "check":
                    artifact = strict_json(read_bounded(args.certificate, MAX_CERTIFICATE_BYTES).decode("utf-8-sig"))
                    certificate = artifact.get("certificate", artifact) if isinstance(artifact, dict) else artifact
                    result = checked_result(spec, certificate)
                elif args.action == "plan":
                    result = plan(spec, max_work_units=args.max_work_units)
                elif args.max_work_units is not None:
                    if args.tactics:
                        result = {"status": "UNKNOWN", "assurance": "NONE", "backend": "rds_declarative",
                                  "reason": "Explicit max_work_units is incompatible with --tactics"}
                    else:
                        result = verify(spec, max_work_units=args.max_work_units)
                elif args.tactics:
                    from rds_verify import LeanFormalEngine
                    result = LeanFormalEngine().verify(spec, args.tactics)
                elif args.no_cache:
                    result = verify(spec)
                else:
                    from rds_proof_cache import ProofCache
                    result = ProofCache(rds.directory / "proofs.sqlite3").verify(spec)
                if args.action in {"plan", "verify"} and args.output:
                    raw = canonical(result).encode("utf-8")
                    require(len(raw) <= MAX_CERTIFICATE_BYTES, "Proof artifact exceeds byte limit")
                    Path(args.output).write_bytes(raw)
            if getattr(args, "brief", False):
                from rds_quick import brief
                print(json.dumps(brief(args.root, result, VERSION, formal=True), ensure_ascii=False, separators=(",", ":"), allow_nan=False))
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
            return {"PASS": 0, "FAIL": 1, "UNKNOWN": 2}.get(result.get("status"), 0)
        if args.command == "advancement":
            from rds_advancement import evaluate_advancement
            protocol = strict_json(read_bounded(args.protocol, 1024 * 1024).decode("utf-8-sig"))
            trajectories = strict_json(read_bounded(args.trajectories, 1024 * 1024).decode("utf-8-sig"))
            confirmations = (strict_json(read_bounded(args.confirmations, 1024 * 1024).decode("utf-8-sig"))
                             if args.confirmations else [])
            result = evaluate_advancement(protocol, trajectories, confirmations)
        elif args.command == 'math':
            from rds_math import command
            result = command(args)
        elif args.command == 'rsi':
            if args.action == 'prepare-application':
                from rds_tool_application import prepare
                result = prepare(args)
            else:
                from rds_tools import command
                result = command(args)
        elif args.command == 'guard':
            from rds_guard import evaluate
            result = evaluate(args.policy, args.root)
        elif args.command == 'hypergraph':
            from rds_hypergraph_input import load_input
            from rds_tms_store import maintain
            spec, repairs = None, []
            if args.input:
                spec, repairs = load_input(read_bounded(args.input, 8 * 1024 * 1024).decode('utf-8-sig'))
            require(len(args.update) + len(args.declare) <= 8, 'At most eight dependency declarations')
            updates, locators = [], []
            for update_path in args.update:
                update, fixed = load_input(read_bounded(update_path, 8 * 1024 * 1024).decode('utf-8-sig'))
                updates.append(update)
                locators.append(str(Path(update_path).resolve()))
                repairs.extend(fixed)
            for declaration in args.declare:
                require(len(declaration.encode('utf-8')) <= 128 * 1024, 'Inline declaration exceeds 128 KiB; use --update for larger input')
                update, fixed = load_input(declaration)
                updates.append(update)
                locators.append('command-line declaration')
                repairs.extend(fixed)
            result = maintain(args.root, initial=spec,
                              locator=str(Path(args.input).resolve()) if args.input else 'command-line declaration',
                              source_base=Path(args.input).resolve().parent if args.input else None,
                              audit_files=args.audit_files, format_repairs=repairs,
                              audit_receipts_enabled=args.audit_receipts,
                              retract_nodes=args.retract_node, retract_rules=args.retract_rule,
                              refute_nodes=args.refute_node, refute_rules=args.refute_rule,
                              change_source=args.change_source, trace=args.trace_cone,
                              updates=updates, update_locators=locators)
            if args.output:
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open('x', encoding='utf-8') as handle:
                    handle.write(canonical(result) + '\n')
        elif args.command == "exec":
            from rds_quick import execute
            review = None
            require(bool(args.research_context) == bool(args.ledger) and (not args.choose or args.research_context),
                    "Prospective exec needs --context and --ledger; --choose is optional only for one READY candidate")
            require(not args.saved_dependencies or args.research_context,
                    '--saved-dependencies requires prospective --context and --ledger')
            if args.research_context:
                review_args = argparse.Namespace(root=args.ledger, research_context=args.research_context,
                                                 graph=args.graph, saved_dependencies=args.saved_dependencies)
                advice = cmd_advise(review_args, RDSState(args.ledger))
                context = load_spec(args.research_context)
                if args.saved_dependencies:
                    from rds_tms_store import with_saved_dependencies
                    context = with_saved_dependencies(args.ledger, context)
                from rds_advisor_coverage import project_context
                context = project_context(args.ledger, context)
                review = (advice, context)
            result = execute(args, review=review)
        elif args.command == "reject":
            from rds_quick import reject_route
            result = reject_route(args)
        elif args.command == "advise":
            result = cmd_advise(args, rds)
        elif args.command == 'structure':
            import rds_structure
            if args.action == 'request':
                result = rds_structure.request(args.root, args.limit)
            elif args.action == 'propose':
                result = rds_structure.propose(args.root, load_spec(args.proposal))
            elif args.action == 'next':
                result = rds_structure.next_step(args.root)
            elif args.action == 'list':
                result = rds_structure.inspect(args.root)
            elif args.action == 'recover':
                result = rds_structure.recover_control(args.root)
            elif args.action == 'drive':
                result = rds_structure.drive(args.root, args.steps)
            else:
                result = getattr(rds_structure, args.action)(args.root, args.id)
        elif args.command == "project":
            result = cmd_project(args)
        elif args.command == "host-hook":
            from rds_host_hook import install, coverage, validate_request
            if args.action == "install":
                result = install(args.root, strict=not args.permissive)
            elif args.action == "coverage":
                result = coverage(args.root)
            else:
                result = validate_request(args.root, load_spec(args.request))
        elif args.command == "checkpoint":
            result = cmd_checkpoint(args, rds)
        elif args.command == "artifacts":
            from rds_artifacts import ingest_manifest
            result = ingest_manifest(args.manifest, root=args.root)
        elif args.command == "branch":
            result = cmd_branch(args, rds)
        elif args.command == "meta":
            result = cmd_meta(args, rds)
        elif args.command == "init":
            result = cmd_init(args, rds)
        elif args.command == "hypothesis":
            result = cmd_hypothesis(args, rds)
        elif args.command == "gate":
            result = cmd_plan(args, rds, advisory=True)
        elif args.command == "plan":
            result = cmd_plan(args, rds) if args.action == "create" else cmd_cancel(args, rds)
        elif args.command == "run":
            result = cmd_run(args, rds) if args.action == "execute" else cmd_recover(args, rds)
        elif args.command == "data":
            result = cmd_expose(args, rds)
        elif args.command == "decide":
            result = cmd_decide(args, rds)
        else:
            result = cmd_status(args, rds)
        workflow = None
        if args.command in {'project', 'exec', 'advise', 'init'}:
            from rds_project_lifecycle import describe
            workflow = result.get('workflow') or describe(args.root, quick=args.command in {'exec', 'init'})
            print('[RDS] mode=' + workflow['mode'] + ' advisor=' + workflow['advisor'] +
                  ' root=' + workflow['project_root'], file=sys.stderr)
        compact = getattr(args, "brief", False) or args.command in {"exec", "reject", "guard", "hypergraph", "math", "rsi"} and not args.json
        if compact:
            from rds_quick import brief
            summary = brief(args.root, result, VERSION)
            if workflow is not None:
                summary['workflow'] = {key: workflow[key] for key in ('mode', 'advisor', 'next_action')}
            if args.command == 'rsi' and args.action == 'compare':
                summary.update({k: result[k] for k in ('correctness', 'comparable_context', 'speedup_ratio',
                                                      'precision', 'precision_key', 'case_count', 'samples_per_tool',
                                                      'variability', 'plan_id', 'execution_started', 'total_budget')})
                summary['wall_seconds'] = {k: result[k]['wall_seconds'] for k in ('baseline', 'candidate')}
                summary['reasons'] = result['reasons']
            print(json.dumps(summary, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
        else:
            # Hashed receipts/events keep their exact body; the mode banner is
            # separate. Operational un-hashed responses can carry discovery data.
            shown = {**result, 'workflow': workflow} if workflow is not None and 'sha256' not in result else result
            print(json.dumps(shown, ensure_ascii=False, indent=2, allow_nan=False))
        if args.command == "exec" and (result.get("receipt") or {}).get("run_status") in {"FAILED", "INTERRUPTED", "TIMED_OUT"}:
            return 1
        if args.command == 'guard' or args.command == 'exec' and 'regression_review' in result:
            return {'PASS': 0, 'FAIL': 1, 'UNKNOWN': 2}.get(result.get('regression_review', result).get('status'), 2)
        if args.command == 'hypergraph' and (result.get('truncated') or result.get('input_review', {}).get('errors')
                                              or result.get('status') == 'CONFLICT'):
            return 2
        if args.command == 'host-hook' and result.get('status') == 'HOST_GUARD_MISSING':
            return 2
        if args.command == 'rsi' and args.action == 'validate':
            return {'LOCAL_CASES_PASSED': 0, 'FAILED': 1, 'UNKNOWN': 2}[result['status']]
        if args.command == 'rsi' and args.action == 'compare':
            return 1 if result['correctness'] == 'FAIL' else 2 if result['status'] == 'UNKNOWN' else 0
        if args.command in {"project", "run"} and args.action in {"execute", "recover", "advance"} and result.get('receipt', result).get("run_status") in {"FAILED", "INTERRUPTED", "TIMED_OUT"}:
            return 1
        if result.get('status') in {'COLLECTION_FAILED', 'INCOMPLETE_ANALYSIS'} or (result.get('advisor') or {}).get('status') in {'COLLECTION_FAILED', 'INCOMPLETE_ANALYSIS'}:
            return 2  # The receipt is retained; collection needs attention, never a training retry.
        if args.command == "meta" and args.action == "evaluate-rule" and not result.get("adoption_eligible"):
            return 1
        if args.command == "checkpoint" and args.action == "restore" and result.get("status") == "CONFLICT":
            return 1
    except (ValueError, KeyError, TypeError, RecursionError, OSError, SyntaxError) as exc:
        # KeyError/TypeError also come from unvalidated user specs, so they stay rejections.
        print("[RDS-REJECT] " + str(exc), file=sys.stderr)
        if args.command == "init" or args.command == "project" and args.action == "init":
            from rds_project import _shell_argument
            print('[RDS-HINT] Inspect the existing project first: python -B scripts/rds_cli.py --root ' +
                  _shell_argument(str(args.root)) + ' project discover; see docs/project-lifecycle.md', file=sys.stderr)
        return 1
    except (sqlite3.Error, ImportError, subprocess.SubprocessError) as exc:
        # Nothing in the request was refused: the state database, a dependency or a subprocess failed.
        print("[RDS-ERROR] " + type(exc).__name__ + ": " + str(exc), file=sys.stderr)
        return 1
    return 0


def main():
    from rds_usage import run_logged
    def usage_root():
        entry = parser()
        normalized = entry.normalize_args(sys.argv[1:], quiet=True)
        root = entry.get_default('root')
        if normalized[:1] == ['--root']:
            # Normalization locates values but does not validate them. Use the
            # same argparse value rules before fallback creates any files:
            # '--root --help' has no root value, even during help handling.
            options = argparse.ArgumentParser(add_help=False, exit_on_error=False)
            options.add_argument('--root')
            try:
                root = options.parse_args(normalized[:2]).root
            except argparse.ArgumentError as exc:
                raise ValueError(str(exc)) from exc
        return Path(root).resolve()
    return run_logged(_main, sys.argv[1:], VERSION, root=usage_root)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
