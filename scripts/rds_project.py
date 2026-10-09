"""Bounded execution of explicitly authorized project commands (stdlib only).

This is an operational ledger, not conversational memory or an OS sandbox.
An allowed project command is trusted code; output paths constrain this runner's
own writes and declared artifacts, not every write performed by that code.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import ctypes
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import shlex
import signal
import sqlite3
import subprocess
import sys
import time
import uuid


TERMINAL = {"COMPLETED", "FAILED", "INTERRUPTED"}
ROLES = {"code", "config", "data", "evaluator", "protocol"}


def _shell_argument(value):
    """Render one display-command argument for PowerShell on Windows, POSIX elsewhere."""
    text = str(value)
    if os.name != 'nt':
        return shlex.quote(text)
    if text and all(c.isascii() and (c.isalnum() or c in '-_./:') for c in text):
        return text
    # PowerShell also recognizes typographic single quotes as delimiters.
    return "'" + text.replace("'", "''").replace('‘', '‘‘').replace('’', '’’') + "'"


def _is_rational_literal(value):
    if isinstance(value, bool):
        return False
    if isinstance(value, float):
        return math.isfinite(value)
    return isinstance(value, (int, str)) and bool(str(value).strip()) and "e" not in str(value).lower()


def _parse_rational_string(value, name):
    """Parse an exact decimal or integer string; reject floats, exponents and junk."""
    require(isinstance(value, str) and value.strip(), f"{name} must be a nonempty rational string")
    text = value.strip()
    require("e" not in text and "E" not in text, f"{name} must not use exponent notation")
    try:
        from fractions import Fraction
        parsed = Fraction(text)
    except (ValueError, ZeroDivisionError) as exc:
        raise ValueError(f"{name} must be an exact rational string such as '1/100' or '0.5'") from exc
    require(parsed >= 0, f"{name} must be nonnegative")
    return parsed


IDENTITY = ("code_sha256", "config_sha256", "data_sha256", "data_split",
            "init", "seed", "checkpoint", "schedule", "sample_work", "numeric_protocol")
# A successor chain (project init --supersedes) is read at most this many roots back by default (#178).
PREDECESSOR_DEPTH = 8
PREDECESSOR_DEPTH_CAP = 32


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


UNINITIALIZED = "Project contract has not been initialized"


class ReceiptIntegrityError(ValueError):
    """A damaged owned receipt row: a rejection of the read, never another run's admission outcome (#104)."""


class RunIntegrityError(ValueError):
    """A damaged retained run is not another run's failed admission or execution."""


def execution_route(argv, bindings, outpaths, root, objective=None, route=None, arm=None, executor_sha256=None):
    """Exact declared contents/roles; names, destinations and allowances are not new work."""
    inputs = {}
    for binding in bindings:
        key = os.path.normcase(str((Path(root) / binding['path']).resolve()))
        item = inputs.setdefault(key, {'sha256': binding['sha256'], 'roles': set()})
        require(item['sha256'] == binding['sha256'], 'Conflicting execution input hashes')
        item['roles'].update(binding.get('roles', [binding.get('role')]))
    normalized = {path: 'input:' + digest({'sha256': item['sha256'], 'roles': sorted(item['roles'])})
                  for path, item in inputs.items()}
    outputs = {os.path.normcase(str((Path(root) / path).resolve())) for path in outpaths}
    command = []
    for value in argv[1:]:
        prefix, separator, tail = value.partition('=')
        token = tail if separator and prefix.startswith('-') else value
        path = os.path.normcase(str((Path(root) / token).resolve()))
        replacement = normalized.get(path, 'declared-output' if path in outputs else token)
        command.append(prefix + '=' + replacement if separator and prefix.startswith('-') else replacement)
    return digest({'executor_sha256': executor_sha256 if executor_sha256 is not None else file_sha(argv[0]), 'argv': command,
                   'inputs': sorted(set(normalized.values())), 'objective_sha256': objective,
                   'scoped_route': route, 'arm': arm})


def number(value, name, positive=False):
    require(not isinstance(value, bool) and isinstance(value, (int, float))
            and math.isfinite(value) and (value > 0 if positive else value >= 0),
            f"Invalid {name}")
    return float(value)


def load_json(path, *, max_bytes=None, expected_sha256=None):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f"Duplicate JSON key: {key}")
            result[key] = value
        return result
    if max_bytes is None and expected_sha256 is None:
        source = Path(path).read_text(encoding='utf-8')
    else:
        with Path(path).open('rb') as stream:
            raw = stream.read(max_bytes + 1) if max_bytes is not None else stream.read()
        require(max_bytes is None or len(raw) <= max_bytes, 'Maintenance context exceeds byte limit')
        require(expected_sha256 is None or hashlib.sha256(raw).hexdigest() == expected_sha256,
                'Maintenance context binding changed')
        source = raw.decode('utf-8')
    return json.loads(source, object_pairs_hook=pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))


def _alive(pid):
    if not pid:
        return False
    if os.name == "nt":
        # os.kill(pid, 0) can terminate processes on Windows. Never use it here.
        from ctypes import wintypes
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x100000, False, pid)
        if not handle:
            return False if ctypes.get_last_error() == 87 else None
        try:
            return api.WaitForSingleObject(handle, 0) == 258
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return None


class _Job:
    """Own the launched process tree, without enumerating unrelated processes."""
    def __init__(self, process):
        self.process = process
        self.handle = None
        if os.name != "nt":
            return
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                        ("max_working", ctypes.c_size_t), ("active", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", ctypes.c_ulonglong * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process", ctypes.c_size_t), ("peak_job", ctypes.c_size_t)]
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = self.api.CreateJobObjectW(None, None)
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not handle or not self.api.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            if handle:
                self.api.CloseHandle(handle)
            raise OSError("Cannot establish project process job")
        if not self.api.AssignProcessToJobObject(handle, int(process._handle)):
            self.api.CloseHandle(handle)
            raise OSError("Cannot bind owned process to project job")
        self.handle = handle

    def stop(self):
        if self.handle:
            self.api.TerminateJobObject(self.handle, 1)
        elif self.process.poll() is None:
            if os.name == "nt":
                self.process.kill()
            else:
                os.killpg(self.process.pid, signal.SIGKILL)

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class ProjectStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        require(self.root.is_dir(), "Project root must exist")
        self.state_dir = self.root / ".rds"
        require(self.state_dir.resolve().is_relative_to(self.root), "State directory escapes project root")
        self.path = self.state_dir / "project.sqlite3"
        self.artifact_dir = self.state_dir / "project-artifacts"
        self.last_advisor_review = None
        self.last_advisor_observation = None

    def _advisor_prepare(self, spec, contract):
        """Read original evidence before taking the execution write lock."""
        if "advisor_policy" not in contract:
            return None
        from rds_owned_advisor import prepare_admission
        return prepare_admission(self, spec)

    def _advisor_prepare_run(self, run_id, *, allow_observation=False):
        with self._db(True) as db:
            contract = self._contract(db)
            if "advisor_policy" not in contract:
                return None
            run = self._run(db, run_id)
            if (allow_observation and ("execution_policy" in contract or "method_evolution" in contract)
                    and (run["status"] != "RESERVED" or run["attempt_id"] is not None)):
                return None
        return self._advisor_prepare(run["manifest"], contract)

    def _advisor_check(self, db, spec, token):
        if "advisor_policy" not in self._contract(db):
            return
        require(token is not None, "Advisor admission is missing; collect and select the current route first")
        from rds_owned_advisor import check_admission
        check_admission(self, db, spec, token)

    def _advisor_finished(self, contract):
        """Collection is post-commit; never mutate the owned receipt or repeat a run."""
        if "advisor_policy" not in contract:
            return
        try:
            from rds_owned_advisor import after_finish
            self.last_advisor_review = after_finish(self)
        except Exception as exc:
            # Even an unexpected collector failure leaves the receipt and settled
            # budget intact. A later review/recovery retries collection only.
            failure = {"status": "COLLECTION_FAILED", "reason": f"{type(exc).__name__}: {exc}",
                       "next_move": "Retry Advisor collection; do not repeat the completed attempt"}
            self.last_advisor_review = failure
            try:
                with self._db() as db:
                    db.execute("INSERT INTO events(body) VALUES (?)", (canonical({
                        "kind": "ADVISOR_COLLECTION_FAILED", **failure}),))
            except Exception as record_error:
                failure["record_error"] = f"{type(record_error).__name__}: {record_error}"
        if isinstance(self.last_advisor_review, dict) and self.last_advisor_review.get("status") == "COLLECTION_FAILED":
            print("Advisor collection pending: " + str(self.last_advisor_review.get("reason", "unknown error")), file=sys.stderr)

    def _path(self, relative, output=False, contract=None):
        require(isinstance(relative, str) and relative and not Path(relative).is_absolute()
                and not Path(relative).drive and ":" not in relative, "Expected project-relative path")
        if output and os.name == "nt":
            require(all(part in (".", "..") or not part.endswith((".", " ")) for part in Path(relative).parts),
                    "Windows output path components cannot end in a dot or space")
        path = (self.root / relative).resolve()
        require(path.is_relative_to(self.root) and path != self.root, "Path escapes project root")
        require(not path.is_relative_to(self.state_dir.resolve()), "Project files cannot address operational state")
        if output:
            # `output_roots` authorizes directories (strict containment); `output_files`
            # authorizes exact root-level files that cannot sit strictly below a root.
            roots = [(self.root / item).resolve() for item in contract["output_roots"]]
            files = [(self.root / item).resolve() for item in contract.get("output_files", [])]
            require(path in files or any(path.is_relative_to(item) and path != item for item in roots),
                    "Output is outside authorized roots; declare a directory root or a single-component output file")
            require(path not in {(self.root / b["path"]).resolve() for b in contract["bindings"]}, "Output overwrites bound input")
        return path

    def _output_key(self, path):
        if os.name == "nt":
            # Win32 aliases apply before the output exists, also in old ledgers.
            path = Path(*(part if part in (".", "..") else part.rstrip(". ") for part in Path(path).parts))
        return os.path.normcase(str((self.root / path).resolve()))

    def _output_claims(self, db):
        # Keep legacy absolute/relative, case and Win32 suffix aliases exclusive.
        claims = {}
        for row in db.execute("SELECT path,run_id FROM output_claims"):
            claims.setdefault(self._output_key(row["path"]), set()).add(row["run_id"])
        return claims

    def _check_start(self, db, run):
        from rds_steering import check_dispatch
        check_dispatch(db, run['id'])
        from rds_method_revision import pending_revision
        require(pending_revision(db) is None, 'Resume prepared method revision before executing')
        # This run is already reserved. Do not charge its estimate a second time,
        # but do not let that reservation override costs settled since admission.
        for resource, amount in run["resource_estimates"].items():
            row = db.execute("SELECT * FROM budget WHERE resource=?", (resource,)).fetchone()
            require(row is not None and row["reserved"] + 1e-9 >= amount,
                    f"Missing {resource} reservation")
            require(row["spent"] + row["charged"] + row["reserved"] <= row["cap"] + 1e-9,
                    f"Insufficient {resource} budget before start")
        contract = self._contract(db)
        if 'confirmation' in contract.get('advisor_policy', {}):
            from rds_postcommit_confirmation import check_run as check_confirmation_run
            check_confirmation_run(self, db, contract, run)
        if 'autonomy' in contract.get('advisor_policy', {}):
            from rds_autonomy import check_run
            check_run(self, db, contract, run)
        if contract.get('advisor_policy', {}).get('feasibility') is not None:
            from rds_feasibility import check_start
            check_start(self, db, run['id'])
        claims = self._output_claims(db)
        self._campaign_deadline(db, contract)
        if 'maintenance' in run['manifest']:
            self._maintenance_review(contract, run['manifest']['maintenance'], run['manifest']['argv'])
            _, used = self._maintenance_spend(db)
            require(used <= contract['maintenance_allowance']['wall_seconds'] + 1e-9,
                    'Maintenance wall allowance exhausted before start')
        for path in run["manifest"]["outpaths"]:
            key = self._output_key(self._path(path, True, contract))
            require(claims.get(key) == {run["id"]}, "Output is not exclusively claimed by this run")

    @staticmethod
    def _campaign_deadline(db, contract, *, admit=False):
        """One immutable deadline per owning ledger, including idle/recovery time."""
        if 'stop_policy' not in contract:
            return None
        # Method adoption changes the effective digest, never the owning T0.
        genesis_sha = db.execute('SELECT sha256 FROM contract WHERE id=1').fetchone()[0]
        row = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='CAMPAIGN_STARTED' ORDER BY id LIMIT 1").fetchone()
        if row is None:
            require(admit, 'Stop policy: campaign admission is missing')
            event = {'kind': 'CAMPAIGN_STARTED', 'contract_sha256': genesis_sha, 'started_at': time.time()}
            db.execute('INSERT INTO events(body) VALUES (?)', (canonical(event),))
        else:
            event = json.loads(row['body'])
            require(event['contract_sha256'] == genesis_sha, 'Stop policy: campaign binding differs')
        deadline = event['started_at'] + contract['stop_policy']['wall_seconds']
        require(time.time() < deadline, 'Stop policy: CAMPAIGN_DEADLINE')
        return deadline

    def _maintenance_context(self, contract):
        from rds_math import check_context
        require('objective_sha256' in contract, 'Maintenance requires a frozen native objective')
        ref = contract['maintenance_allowance']['context']
        require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'}, 'Maintenance context requires path and sha256')
        require(any(b['role'] == 'config' and b['path'] == ref['path'] and b['sha256'] == ref['sha256']
                    for b in contract['bindings']), 'Maintenance context must be a frozen config binding')
        path = self._path(ref['path'])
        context = load_json(path, max_bytes=128 * 1024, expected_sha256=ref['sha256'])
        binding = check_context(self.root, context)
        require(binding and context.get('objective_binding') == binding
                and binding['sha256'] == contract['objective_sha256'], 'Maintenance objective binding differs')
        require(isinstance(context.get('action'), dict), 'Maintenance context requires a bound repair action')
        return context

    def _maintenance_review(self, contract, maintenance, argv):
        from rds_advisor_search import _dependency_review, _goal_contribution
        require('maintenance_allowance' in contract, 'Maintenance runs require a contract maintenance_allowance')
        context = self._maintenance_context(contract)
        action = context['action']
        require(action.get('argv') == argv and action.get('target') == maintenance['affected_obligation']
                and action.get('goal_contribution') == maintenance['goal_contribution'],
                'Maintenance command and original-goal target differ from the bound repair action')
        dependency = _dependency_review(context)
        require(dependency is not None and dependency['status'] == 'ANALYZED',
                'Maintenance requires a complete dependency review')
        contribution = _goal_contribution(action, context, dependency)
        mapped = (contribution or {}).get('graph_path', {})
        goal = mapped.get('goal_review', {})
        require(maintenance['goal_contribution'].get('target') == 'completion_standard'
                and mapped.get('status') == 'DECLARED_CONNECTED_PATH'
                and goal.get('blocker_sets_complete') is True and goal.get('status') != 'DECLARED_SUPPORTED',
                'Maintenance must address an unresolved original-goal obligation')
        token = mapped['start_token']
        require(token == maintenance['blocker'] and token in {r['token'] for r in dependency['ready_obligations']}
                and any(token in missing for missing in goal['minimal_missing_evidence_sets'])
                and not (set(contribution['path'][1:-1]) & set(dependency['declared_supported_closure'])),
                'Maintenance blocker must be a current ready original-goal obligation')
        return {'objective_binding': context['objective_binding'], 'context_sha256': digest(context),
                'dependency_map_sha256': dependency['input_sha256'], 'contribution': contribution,
                'assurance': 'INPUT_REPORTED_GRAPH_PATH_NOT_PROOF'}

    @classmethod
    def _maintenance_spend(cls, db):
        prior = [json.loads(row['body']) for row in db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='MAINTENANCE_USE'")]
        used = 0.0
        for event in prior:
            receipt = db.execute('SELECT run_id,sha256,body FROM receipts WHERE run_id=?', (event['run_id'],)).fetchone()
            row = db.execute('SELECT id,status,body FROM runs WHERE id=?', (event['run_id'],)).fetchone()
            if row is None:
                raise RunIntegrityError(f"Project run integrity failure: run {event['run_id']!r} is missing; inspect retained state")
            run = cls._run_row(row)
            resource = cls._receipt(receipt)['resources']['wall_seconds'] if receipt else {}
            observed = run['observed_wall_seconds']
            used += max(event['wall_seconds'], observed, resource.get('measured') or 0.0, resource.get('charged_estimate') or 0.0)
        return len(prior), used

    @contextmanager
    def _db(self, readonly=False):
        if readonly:
            if not self.path.is_file():
                raise FileNotFoundError("Project contract has not been initialized")
            db = sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True, timeout=10)
            db.execute("PRAGMA query_only=ON")
        else:
            require(self.path.resolve().is_relative_to(self.root), "Project database escapes root")
            db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _contract(db):
        # Native research records can share this database before project init.
        require(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone(),
                UNINITIALIZED)
        from rds_method_revision import effective_contract
        return effective_contract(db)

    def _bindings(self, contract):
        found = []
        errors = []
        checked = {}
        if 'objective_sha256' in contract:
            from rds_math import objective
            try:
                goal = objective(self.root)
                require(goal and goal['asset']['sha256'] == contract['objective_sha256'], 'Frozen mathematical objective differs')
            except (ValueError, OSError, sqlite3.Error) as exc:
                errors.append('Objective binding unavailable: ' + str(exc))
        for b in contract["bindings"]:
            try:
                path = self._path(b["path"])
                if path not in checked:
                    checked[path] = file_sha(path)
                actual = checked[path]
            except (OSError, ValueError) as exc:
                actual = None
                errors.append(f"Binding unavailable: {b['path']}: {exc}")
            found.append({"path": b["path"], "role": b["role"], "sha256": actual})
            if actual != b["sha256"]:
                errors.append(f"Binding changed: {b['path']}")
        return found, errors

    @staticmethod
    def _role_sha(contract, role):
        values = [{"path": b["path"], "sha256": b["sha256"]}
                  for b in contract["bindings"] if b["role"] == role]
        return values[0]["sha256"] if len(values) == 1 else digest(sorted(values, key=lambda b: b["path"]))

    @classmethod
    def _protocol_conflict(cls, contract, protocol):
        """The rejection for the first declared identity hash that disagrees with the bindings, else None."""
        for role in ("code", "config", "data"):
            if role + "_sha256" not in protocol:
                continue
            expected = cls._role_sha(contract, role)
            if protocol[role + "_sha256"] != expected:
                count = sum(b["role"] == role for b in contract["bindings"])
                return (f"Protocol identity conflicts with bindings: {role}_sha256 must be {expected} ("
                        + (f"the SHA256 of the one '{role}' binding)" if count == 1 else
                           f"the canonical digest of the {count} '{role}' bindings as {{path, sha256}} sorted by path)"))
        return None

    @classmethod
    def _protocol_error(cls, contract, protocol):
        """Registration's identity check on a run protocol: the rejection, or None."""
        if not isinstance(protocol, dict):
            return "Protocol must be an object"
        missing = [k for k in IDENTITY if k not in protocol]
        if missing:
            return "Protocol identity fields required: " + ", ".join(missing) + "; exec can complete operational identity fields"
        if {"path", "sha256"} & set(protocol):
            return "Protocol identity uses reserved fields"
        return cls._protocol_conflict(contract, protocol)

    def _ledger_pins(self):
        """This root's verified contract, receipt, checkpoint and predecessor-link digests, plus its link record.

        Any damaged record is rejected. The link digest is None for a root that supersedes nothing, so a link
        added, removed or replaced later changes the pins as much as a changed receipt does.
        """
        require(self.path.is_file(), f"No project ledger at {self.root}")
        with self._db(True) as db:
            db.execute("BEGIN")
            contract = self._contract(db)
            receipts = [self._receipt(row) for row in db.execute("SELECT run_id,sha256,body FROM receipts ORDER BY run_id")]
            checkpoints = []
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone():
                for checkpoint_id, sha, body in db.execute("SELECT id,sha,body FROM checkpoints ORDER BY rowid"):
                    require(isinstance(body, str) and hashlib.sha256(body.encode("utf-8")).hexdigest() == sha,
                            f"Checkpoint integrity failure: {checkpoint_id}")
                    checkpoints.append({"id": checkpoint_id, "sha256": sha})
            link, link_sha = self._link_record(db)
        return {"contract_sha256": digest(contract),
                "receipt_digests": [{"run_id": r["run_id"], "sha256": r["sha256"]} for r in receipts],
                "checkpoint_shas": checkpoints, "predecessor_sha256": link_sha}, link

    @staticmethod
    def _link_record(db):
        """The predecessor link stored in db and its digest, or (None, None); a malformed link is damage, not a link."""
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='predecessor'").fetchone():
            return None, None
        row = db.execute("SELECT body,sha256 FROM predecessor WHERE id=1").fetchone()
        require(row is not None and isinstance(row["body"], str), "Predecessor record is missing")
        try:
            record = json.loads(row["body"])
        except (ValueError, RecursionError):
            raise ValueError("Predecessor record is not valid JSON") from None
        require(digest(record) == row["sha256"], "Predecessor record integrity failure")

        def sha(value):
            return isinstance(value, str) and len(value) == 64 and set(value) <= set("0123456789abcdef")

        def pins(items, key):
            return isinstance(items, list) and all(isinstance(item, dict) and set(item) == {key, "sha256"}
                                                   and isinstance(item[key], str) and item[key] and sha(item["sha256"])
                                                   for item in items)

        require(isinstance(record, dict) and set(record) == {"schema", "root_path", "contract_sha256", "receipt_digests",
                                                             "checkpoint_shas", "predecessor_sha256", "assurance"},
                "Predecessor record is malformed: unexpected fields")
        for field, valid in (("schema", type(record["schema"]) is int and record["schema"] == 1),
                             ("root_path", isinstance(record["root_path"], str) and 0 < len(record["root_path"]) <= 4096),
                             ("contract_sha256", sha(record["contract_sha256"])),
                             ("receipt_digests", pins(record["receipt_digests"], "run_id")),
                             ("checkpoint_shas", pins(record["checkpoint_shas"], "id")),
                             ("predecessor_sha256", record["predecessor_sha256"] is None or sha(record["predecessor_sha256"])),
                             ("assurance", record["assurance"] == "RECORDED_INPUT_NOT_SCIENTIFIC_VERIFICATION")):
            require(valid, f"Predecessor record is malformed: {field}")
        return record, row["sha256"]

    def _predecessor(self):
        """The predecessor this root recorded at init, or None for a root that supersedes nothing."""
        if not self.path.is_file():
            return None
        with self._db(True) as db:
            return self._link_record(db)[0]

    def predecessor_chain(self, depth=PREDECESSOR_DEPTH):
        """Check each recorded predecessor against the pins its successor stored; stop at the first failure.

        A hop is VERIFIED when its contract, its own predecessor link and every pinned receipt and checkpoint are
        present and unchanged. The next hop follows the link read in that same verified transaction.
        Records the predecessor gained after it was superseded are counted, never pinned.
        """
        require(type(depth) is int and 0 <= depth <= PREDECESSOR_DEPTH_CAP,
                f"Predecessor depth must be an integer in 0..{PREDECESSOR_DEPTH_CAP}")
        chain, store, seen = [], self, {self.root}
        # Damage of any shape at the traversal boundary is a MISMATCH; it never escapes into snapshot() or Advisor.
        damaged = (ValueError, OSError, RuntimeError, sqlite3.Error, TypeError, KeyError, AttributeError)
        try:
            record = self._predecessor()
        except damaged as exc:
            return [{"root": str(self.root), "status": "MISMATCH", "reason": str(exc)}]
        while record is not None:
            hop = {"root": record["root_path"], "superseded_by": str(store.root), "contract_sha256": record["contract_sha256"]}
            try:
                target = (store.root / record["root_path"]).resolve()
                hop["root"] = str(target)
                if len(chain) >= depth:
                    chain.append({**hop, "status": "TRUNCATED", "reason": f"Predecessor chain is longer than {depth} roots"})
                    break
                require(target not in seen, "Predecessor chain repeats a root")
                seen.add(target)
                require(target.is_dir() and (target / ".rds" / "project.sqlite3").is_file(), "Predecessor ledger is missing")
                predecessor = ProjectStore(target)
                current, link = predecessor._ledger_pins()
                require(current["contract_sha256"] == record["contract_sha256"], "Predecessor contract differs from the recorded digest")
                require(current["predecessor_sha256"] == record["predecessor_sha256"],
                        "Predecessor's own link differs from the recorded digest")
                for key, label in (("receipt_digests", "receipt"), ("checkpoint_shas", "checkpoint")):
                    present = {json.dumps(item, sort_keys=True) for item in current[key]}
                    for item in record[key]:
                        require(json.dumps(item, sort_keys=True) in present,
                                f"Predecessor {label} differs from the recorded digest: {item.get('run_id', item.get('id'))}")
                hop.update(status="VERIFIED", checkpoint_ids=[item["id"] for item in record["checkpoint_shas"]],
                           checkpoint_shas=record["checkpoint_shas"],
                           unpinned_receipts=len(current["receipt_digests"]) - len(record["receipt_digests"]),
                           unpinned_checkpoints=len(current["checkpoint_shas"]) - len(record["checkpoint_shas"]))
            except damaged as exc:
                chain.append({**hop, "status": "MISMATCH", "reason": str(exc)})
                break
            chain.append(hop)
            store, record = predecessor, link
        return chain

    def initialize(self, contract, supersedes=None, *, scope_declaration=None):
        require(isinstance(contract, dict) and type(contract.get("schema")) is int
                and contract["schema"] == 1, "Project contract schema must be 1")
        require(set(contract) <= {"schema", "bindings", "allowed_commands", "output_roots", "output_files", "budget", "description",
                                  "primary_metric", "objective_sha256", "execution_policy", "stop_policy", "maintenance_allowance", "advisor_policy", "method_evolution"}, "Unknown contract fields")
        if "primary_metric" in contract:
            metric = contract["primary_metric"]
            require(isinstance(metric, dict) and set(metric) == {"name", "direction", "min_useful_delta"},
                    "primary_metric must define name, direction and min_useful_delta")
            require(isinstance(metric["name"], str) and metric["name"], "primary_metric.name must be a nonempty string")
            require(metric["direction"] in ("min", "max"), "primary_metric.direction must be 'min' or 'max'")
            _parse_rational_string(metric["min_useful_delta"], "primary_metric.min_useful_delta")
        if "stop_policy" in contract:
            policy = contract["stop_policy"]
            require(isinstance(policy, dict) and set(policy) == {"schema", "wall_seconds", "progress"},
                    "stop_policy must define schema, wall_seconds and progress")
            require(type(policy["schema"]) is int and policy["schema"] == 1, "stop_policy schema must be 1")
            number(policy["wall_seconds"], "stop_policy.wall_seconds", True)
            progress = policy["progress"]
            require(isinstance(progress, dict) and set(progress) == {"window_seconds", "min_bytes"},
                    "stop_policy.progress must define window_seconds and min_bytes")
            number(progress["window_seconds"], "stop_policy.progress.window_seconds", True)
            require(type(progress["min_bytes"]) is int and not isinstance(progress["min_bytes"], bool)
                    and progress["min_bytes"] >= 0, "stop_policy.progress.min_bytes must be a nonnegative integer")
        if "maintenance_allowance" in contract:
            allowance = contract["maintenance_allowance"]
            require(isinstance(allowance, dict) and set(allowance) == {"schema", "wall_seconds", "max_uses", "context"},
                    "maintenance_allowance must define schema, wall_seconds, max_uses and context")
            require(type(allowance["schema"]) is int and allowance["schema"] == 1, "maintenance_allowance schema must be 1")
            number(allowance["wall_seconds"], "maintenance_allowance.wall_seconds")
            require(type(allowance["max_uses"]) is int and not isinstance(allowance["max_uses"], bool)
                    and 1 <= allowance["max_uses"] <= 64, "maintenance_allowance.max_uses must be an integer in 1..64")
        if 'execution_policy' in contract:
            policy = contract['execution_policy']
            require(isinstance(policy, dict) and set(policy) == {'schema', 'max_attempts'}
                    and type(policy['schema']) is int and policy['schema'] == 1
                    and type(policy['max_attempts']) is int and 1 <= policy['max_attempts'] <= 32,
                    'Execution policy requires schema 1 and max_attempts in 1..32')
        if 'objective_sha256' in contract:
            from rds_math import objective
            goal = objective(self.root)
            require(goal and goal['asset']['sha256'] == contract['objective_sha256'], 'Initialize the matching native objective first')
        require(isinstance(contract.get("bindings"), list) and contract["bindings"], "Bindings required")
        paths = {}
        binding_roles = set()
        roles = set()
        for b in contract["bindings"]:
            require(isinstance(b, dict) and set(b) == {"path", "sha256", "role"}, "Invalid binding")
            require(b["role"] in ROLES, "Unknown binding role")
            path = self._path(b["path"])
            require((path, b["role"]) not in binding_roles and path.is_file(), "Missing or duplicate binding role")
            if path not in paths:
                paths[path] = file_sha(path)
            require(paths[path] == b["sha256"], "Initial binding mismatch")
            binding_roles.add((path, b["role"]))
            roles.add(b["role"])
        require(ROLES <= roles, "code/config/data/evaluator/protocol bindings required")
        from rds_method_revision import validate_envelope
        validate_envelope(self, contract)
        # A protocol file freezes with the contract, so a conflict here would reject every run that names it.
        for b in contract["bindings"]:
            if b["role"] != "protocol":
                continue
            try:
                protocol = load_json(self._path(b["path"]))
            except (ValueError, UnicodeDecodeError):
                continue  # Not a JSON identity file; registration rejects it if a run names it.
            if not isinstance(protocol, dict):
                continue  # JSON but not an identity object; registration rejects it if a run names it.
            error = self._protocol_error(contract, protocol)
            require(not error, f"{error} in {b['path']}; the protocol is frozen with the contract, "
                    "so correct it and its binding SHA256 before project init")
        if 'maintenance_allowance' in contract:
            self._maintenance_context(contract)
        commands = contract.get("allowed_commands")
        require(isinstance(commands, list) and commands, "Allowed argv commands required")
        for argv in commands:
            self._command(argv)
        roots = contract.get("output_roots")
        require(isinstance(roots, list) and roots, "Output roots required")
        for item in roots:
            self._path(item)
        files = contract.get("output_files")
        if files is not None:
            require(isinstance(files, list) and all(isinstance(i, str) and i for i in files), "Output files must be a list of project-relative paths")
            for item in files:
                self._path(item)
        budget = contract.get("budget")
        require(isinstance(budget, dict) and "wall_seconds" in budget, "Budget vector requires wall_seconds")
        for unit, cap in budget.items():
            require(isinstance(unit, str) and unit and len(unit) <= 64, "Invalid resource name")
            number(cap, f"budget.{unit}")
        if "advisor_policy" in contract:
            from rds_owned_advisor import validate_policy
            validate_policy(self, contract)
            # Owned routes register their frozen manifests, so each named protocol must pass registration now.
            for route in contract["advisor_policy"]["routes"]:
                path = route["manifest"]["protocol"]["path"]
                try:
                    protocol = load_json(self._path(path))
                except (ValueError, UnicodeDecodeError):
                    require(False, f"Frozen route '{route['manifest']['id']}' protocol {path} is not a JSON "
                            "identity object; registration would reject every run naming it")
                error = self._protocol_error(contract, protocol)
                require(not error, f"Frozen route '{route['manifest']['id']}' cannot register with {path} ({error}). "
                        "The protocol is frozen with the contract, so correct it and its binding SHA256 before project init")
        canonical(contract)
        predecessor = None
        if supersedes is not None:
            # A successor links a frozen root by digest only; the predecessor's bytes are never written (#178).
            source = Path(supersedes).resolve()
            require(source != self.root, "A project root cannot supersede itself")
            require(source.is_dir() and (source / ".rds" / "project.sqlite3").is_file(),
                    f"Predecessor has no project ledger: {source}")
            try:
                pins, _ = ProjectStore(source)._ledger_pins()
            except (ValueError, sqlite3.Error) as exc:
                raise ValueError(f"Predecessor ledger cannot be superseded: {exc}") from exc
            try:
                root_path = os.path.relpath(source, self.root)
            except ValueError:
                # Windows has no relative path across drives or to a UNC share; traversal joins an absolute path as-is.
                root_path = str(source)
            predecessor = {"schema": 1, "root_path": root_path, **pins,
                           "assurance": "RECORDED_INPUT_NOT_SCIENTIFIC_VERIFICATION"}
        self.state_dir.mkdir(exist_ok=True)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS contract(id INTEGER PRIMARY KEY,sha256 TEXT NOT NULL,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS budget(resource TEXT PRIMARY KEY,cap REAL NOT NULL,spent REAL NOT NULL DEFAULT 0,charged REAL NOT NULL DEFAULT 0,reserved REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,status TEXT NOT NULL,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS output_claims(path TEXT PRIMARY KEY,run_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts(run_id TEXT PRIMARY KEY,sha256 TEXT NOT NULL,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS exposures(id INTEGER PRIMARY KEY,run_id TEXT NOT NULL,body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,body TEXT NOT NULL);
            """)
            for table in ("contract", "receipts", "exposures", "events"):
                for action in ("UPDATE", "DELETE"):
                    db.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'{table} is append-only'); END")
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT sha256 FROM contract WHERE id=1").fetchone()
            if old:
                require(old["sha256"] == digest(contract), "Contract is frozen; reuse this project for additional runs. "
                        "Use project enable-advisor for same-ledger activation, or project revise for authorized method changes. "
                        "A genuinely changed research contract needs an explicit successor "
                        f"(project init --supersedes {_shell_argument(str(self.root))})")
                if predecessor is not None:
                    recorded = self._link_record(db)[0] or {}
                    require(recorded.get("root_path") == predecessor["root_path"]
                            and recorded.get("contract_sha256") == predecessor["contract_sha256"],
                            "Predecessor is frozen with the contract; use a new project root")
            else:
                from rds_owned_tools import preparation_costs
                native_preparation = preparation_costs(self, contract, db=db)
                db.execute("INSERT INTO contract VALUES (1,?,?)", (digest(contract), canonical(contract)))
                db.executemany("INSERT INTO budget(resource,cap) VALUES (?,?)", list(budget.items()))
                from rds_owned_tools import charge_preparation
                charge_preparation(self, db, contract, native_preparation)
                if predecessor is not None:
                    db.execute("CREATE TABLE predecessor(id INTEGER PRIMARY KEY CHECK (id = 1),sha256 TEXT NOT NULL,body TEXT NOT NULL)")
                    for action in ("UPDATE", "DELETE"):
                        db.execute(f"CREATE TRIGGER predecessor_no_{action.lower()} BEFORE {action} ON predecessor "
                                   "BEGIN SELECT RAISE(ABORT,'predecessor is append-only'); END")
                    db.execute("INSERT INTO predecessor VALUES (1,?,?)", (digest(predecessor), canonical(predecessor)))
            if scope_declaration is not None:
                row = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='PROJECT_SCOPE_DECLARED' LIMIT 1").fetchone()
                require(not old or row is not None,
                        'Independent project scope must be declared with the original contract; it cannot be added after initialization')
                require(row is None or json.loads(row['body']) == scope_declaration,
                        'Independent project declaration is already recorded')
                if row is None:
                    db.execute('INSERT INTO events(body) VALUES (?)', (canonical(scope_declaration),))
        return self.snapshot()

    @staticmethod
    def _command(argv):
        require(isinstance(argv, list) and argv and all(isinstance(a, str) and a and "\x00" not in a for a in argv), "Command must be a nonempty argv list")
        found = shutil.which(argv[0])
        require(found is not None and Path(found).is_file(), "Command executable unavailable")
        require(Path(found).stem.lower() not in {"cmd", "powershell", "pwsh", "sh", "bash", "zsh", "fish"}
                and Path(found).suffix.lower() not in {".bat", ".cmd", ".ps1"}, "Project commands cannot invoke a shell")
        return str(Path(found).resolve())

    @contextmanager
    def theory_allowance(self, spec, request, allowance):
        """Precharge bounded controller work; unused allowances are not refunded."""
        started = time.monotonic()
        require(isinstance(spec, dict), "Run manifest must be an object")
        run_id = spec.get("id", "")
        require(isinstance(run_id, str) and 1 <= len(run_id) <= 80 and all(c.isalnum() or c in "-_" for c in run_id), "Invalid run ID")
        event = {"kind": "THEORY_ALLOWANCE", "attempt_id": uuid.uuid4().hex,
                 "run_id": run_id, "manifest_sha256": digest(spec),
                 "request_sha256": digest(request), "accounting": "CONSERVATIVE_ALLOWANCE"}
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            contract = self._contract(db)
            require('advisor_policy' not in contract,
                    'Program-owned Advisor requires project advance/create/execute; theory allowance cannot bypass it')
            from rds_steering import check_dispatch
            check_dispatch(db, run_id)
            require('stop_policy' not in contract and 'maintenance_allowance' not in contract,
                    'Configured stop/maintenance policies require project create/execute; theory allowance cannot bypass them')
            require(db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone() is None, "Run ID already exists")
            require(db.execute("SELECT 1 FROM events WHERE json_extract(body,'$.kind')='THEORY_ALLOWANCE' "
                               "AND json_extract(body,'$.run_id')=? AND json_extract(body,'$.manifest_sha256')=? "
                               "AND json_extract(body,'$.request_sha256')=? LIMIT 1",
                               (run_id, event["manifest_sha256"], event["request_sha256"])).fetchone() is None,
                    "Theory request already attempted")
            self._command(spec.get("argv"))
            require(spec["argv"] in contract["allowed_commands"], "Command is not authorized")
            _, errors = self._bindings(contract)
            require(not errors, "; ".join(errors))
            require(isinstance(allowance, dict) and set(allowance) == set(contract["budget"]), "Theory allowance must match budget dimensions")
            amounts = {key: number(value, f"allowance.{key}", key == "wall_seconds")
                       for key, value in allowance.items()}
            for resource, amount in amounts.items():
                row = db.execute("SELECT * FROM budget WHERE resource=?", (resource,)).fetchone()
                require(row["spent"] + row["charged"] + row["reserved"] + amount <= row["cap"] + 1e-9, f"Insufficient {resource} budget")
            for resource, amount in amounts.items():
                db.execute("UPDATE budget SET charged=charged+? WHERE resource=?", (amount, resource))
            event["allowance"] = amounts
            db.execute("INSERT INTO events(body) VALUES (?)", (canonical(event),))
        record = {"attempt_id": event["attempt_id"], "allowance": dict(amounts)}
        try:
            yield record
        finally:
            wall = time.monotonic() - started
            overrun = max(wall - amounts["wall_seconds"], 0.0)
            outcome = {**event, "kind": "THEORY_OUTCOME", "status": record.get("status", "INTERRUPTED"),
                       "observed_wall_seconds": wall, "wall_overrun_seconds": overrun}
            for key in ("result_sha256", "result", "worker_output"):
                if key in record:
                    outcome[key] = record[key]
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE budget SET charged=charged+? WHERE resource='wall_seconds'", (overrun,))
                db.execute("INSERT INTO events(body) VALUES (?)", (canonical(outcome),))

    def theory_record(self, attempt_id):
        """Read recorded theory evidence; this never admits an empirical run."""
        with self._db(True) as db:
            row = db.execute("SELECT body FROM events WHERE json_extract(body,'$.attempt_id')=? "
                             "AND json_extract(body,'$.kind') IN ('THEORY_ALLOWANCE','THEORY_OUTCOME') "
                             "ORDER BY id DESC LIMIT 1", (attempt_id,)).fetchone()
            require(row is not None, "Unknown theory attempt ID")
            return json.loads(row["body"])

    def register(self, spec, *, executor_sha256=None):
        require(isinstance(spec, dict) and type(spec.get("schema")) is int
                and spec["schema"] == 1, "Run manifest schema must be 1")
        require(set(spec) <= {"schema", "id", "arm", "control_id", "protocol", "argv", "outpaths", "resource_estimates",
                              "timeout_seconds", "description", "maintenance"}, "Unknown manifest fields; handwritten verification is not accepted")
        run_id = spec.get("id", "")
        require(isinstance(run_id, str) and 1 <= len(run_id) <= 80 and all(c.isalnum() or c in "-_" for c in run_id), "Invalid run ID")
        require(spec.get("arm") in {"control", "treatment", "tool"}, "Invalid arm")
        require(spec.get("control_id") is None or isinstance(spec["control_id"], str), "Invalid control ID")
        require(spec["arm"] != "treatment" or spec.get("control_id"), "Treatment requires a control ID")
        timeout = number(spec.get("timeout_seconds"), "timeout_seconds", True)
        executor = self._command(spec.get("argv"))
        if "maintenance" in spec:
            maintenance = spec["maintenance"]
            require(isinstance(maintenance, dict)
                    and set(maintenance) == {"reason", "blocker", "affected_obligation", "repair", "acceptance", "goal_contribution"},
                    "maintenance must declare reason, blocker, affected_obligation, repair, acceptance and goal_contribution")
            require(maintenance.get("reason") == "MAINTENANCE", "Maintenance reason must be MAINTENANCE")
            require(all(isinstance(maintenance[key], str) and maintenance[key].strip() and len(maintenance[key]) <= 2048
                        for key in ("blocker", "affected_obligation", "repair", "acceptance")),
                    "Maintenance blocker, affected_obligation, repair and acceptance must be nonempty strings")
            require(isinstance(maintenance['goal_contribution'], dict), 'Maintenance goal_contribution must be an object')
        with self._db() as db:
            contract = self._contract(db)
            require(spec["argv"] in contract["allowed_commands"], "Command is not authorized")
            bindings, errors = self._bindings(contract)
            require(not errors, "; ".join(errors))
            if 'maintenance_allowance' in contract and 'maintenance' not in spec:
                require(spec['argv'] != self._maintenance_context(contract)['action'].get('argv'),
                        'Bound repair command requires a maintenance declaration')
            estimates = spec.get("resource_estimates")
            require(isinstance(estimates, dict) and set(estimates) == set(contract["budget"]), "Resource estimates must match budget dimensions")
            estimates = {key: number(value, f"estimate.{key}") for key, value in estimates.items()}
            require(estimates["wall_seconds"] >= timeout, "Wall reservation must cover timeout")
            protocol_ref = spec.get("protocol")
            require(isinstance(protocol_ref, dict) and set(protocol_ref) == {"path", "sha256"}, "Protocol must bind a file and SHA256")
            require(any(b["role"] == "protocol" and b["path"] == protocol_ref["path"] and b["sha256"] == protocol_ref["sha256"] for b in contract["bindings"]), "Protocol is not bound by contract")
            protocol = load_json(self._path(protocol_ref["path"]))
            error = self._protocol_error(contract, protocol)
            require(not error, error)
            outpaths = spec.get("outpaths")
            require(isinstance(outpaths, list) and (outpaths or spec["arm"] == "tool"), "Expected output paths required")
            outputs = [self._path(p, True, contract) for p in outpaths]
            require(len(set(outputs)) == len(outputs) and all(not p.exists() for p in outputs), "Outputs must be unique and absent before registration")
            run = {"id": run_id, "run_id": run_id, "status": "RESERVED", "run_status": "RESERVED",
                   "manifest": spec, "manifest_sha256": digest(spec), "resource_estimates": estimates,
                   "protocol": {**protocol, **protocol_ref}, "executor": executor,
                   "executor_sha256": file_sha(executor), "attempt_id": None, "worker_pid": None,
                   "pid": None, "started_at": None, "finished_at": None, "observed_wall_seconds": 0.0,
                   "scheduler": None}
            from rds_feasibility import runtime_fingerprint
            run['effective_contract_sha256'] = digest(contract)
            run['runtime_fingerprint'] = runtime_fingerprint()
            require(executor_sha256 is None or run['executor_sha256'] == executor_sha256,
                    'Execution policy: command executable changed before registration')
            if 'execution_policy' in contract:
                run['execution_route_sha256'] = execution_route(spec['argv'], bindings, outpaths, self.root,
                    contract.get('objective_sha256'), arm=spec['arm'], executor_sha256=run['executor_sha256'])
            advisor_token = self._advisor_prepare(spec, contract)
            db.execute("BEGIN IMMEDIATE")
            from rds_method_revision import pending_revision
            require(pending_revision(db) is None, 'Resume prepared method revision before registering')
            require(digest(self._contract(db)) == run['effective_contract_sha256'],
                    'Method revision changed during run registration')
            from rds_steering import check_dispatch
            check_dispatch(db, run_id)
            self._advisor_check(db, spec, advisor_token)
            if 'autonomy' in contract.get('advisor_policy', {}):
                from rds_autonomy import bind_run
                bind_run(self, db, contract, run)
            require(db.execute("SELECT 1 FROM runs WHERE id=?", (run_id,)).fetchone() is None, "Run ID already exists")
            retained = self._runs(db)
            if 'execution_policy' in contract:
                previous = [row for row in retained if row.get('execution_route_sha256') == run['execution_route_sha256']]
                active = next((row for row in previous if row['status'] in {'RESERVED', 'RUNNING', 'COMPLETED'}), None)
                require(active is None, 'Execution policy: observe or recover existing run ' + (active or {}).get('id', ''))
                require(len(previous) < contract['execution_policy']['max_attempts'],
                        'Execution policy: unchanged route reached max_attempts; retained failures are not a scientific impossibility claim')
            if spec.get("control_id"):
                require(db.execute("SELECT 1 FROM runs WHERE id=?", (spec["control_id"],)).fetchone() is not None, "Unknown control ID")
            if "maintenance" in spec:
                run['maintenance_review'] = self._maintenance_review(contract, spec['maintenance'], spec['argv'])
                uses, used = self._maintenance_spend(db)
                require(uses < contract['maintenance_allowance']['max_uses'], 'Maintenance allowance is exhausted')
                cap = contract['maintenance_allowance']['wall_seconds']
                require(cap > 0 and used + estimates['wall_seconds'] <= cap + 1e-9,
                        'Maintenance wall estimate exceeds the frozen maintenance_allowance.wall_seconds total')
                db.execute("INSERT INTO events(body) VALUES (?)",
                           (canonical({"kind": "MAINTENANCE_USE", "run_id": run_id,
                                       "manifest_sha256": digest(spec), 'wall_seconds': estimates['wall_seconds'],
                                       'review': run['maintenance_review'], **spec["maintenance"]}),))
            for resource, amount in estimates.items():
                row = db.execute("SELECT * FROM budget WHERE resource=?", (resource,)).fetchone()
                require(row["spent"] + row["charged"] + row["reserved"] + amount <= row["cap"] + 1e-9, f"Insufficient {resource} budget")
            claims = self._output_claims(db)
            for path in outputs:
                key = self._output_key(path)
                require(key not in claims, "Output is already claimed by another run")
                db.execute("INSERT INTO output_claims VALUES (?,?)", (key, run_id))
            self._campaign_deadline(db, contract, admit=True)
            if 'confirmation' in contract.get('advisor_policy', {}):
                from rds_postcommit_confirmation import bind_run as bind_confirmation_run
                bind_confirmation_run(self, db, contract, run)
            for resource, amount in estimates.items():
                db.execute("UPDATE budget SET reserved=reserved+? WHERE resource=?", (amount, resource))
            if advisor_token is not None:
                run['owned_history_recorded'] = True
            db.execute("INSERT INTO runs VALUES (?,?,?)", (run_id, "RESERVED", canonical(run)))
            if advisor_token is not None:
                from rds_owned_history import capture_choice
                capture_choice(self, db, run, advisor_token['decision'])
        return run

    @classmethod
    def _run(cls, db, run_id):
        row = db.execute("SELECT id,status,body FROM runs WHERE id=?", (run_id,)).fetchone()
        require(row is not None, "Unknown run ID")
        return cls._run_row(row)

    @classmethod
    def _runs(cls, db):
        return [cls._run_row(row) for row in db.execute("SELECT id,status,body FROM runs ORDER BY id")]

    @staticmethod
    def _run_row(row):
        """Validate retained writer values before displaying, dispatching or settling them.

        No schema migration, content repair or new evidence claim. Optional later
        aliases/policy fields are not required, and hot-loop reads remain keyed by id.
        """
        prefix = f"Project run integrity failure: run {row['id']!r} body "
        suffix = "; inspect retained state"

        def reject(reason):
            raise RunIntegrityError(prefix + reason + suffix)

        def unique(items):
            result = {}
            for key, value in items:
                if key in result:
                    reject("repeats a JSON key")
                result[key] = value
            return result

        try:
            run = json.loads(row["body"], object_pairs_hook=unique)
        except RunIntegrityError:
            raise
        except (ValueError, TypeError, RecursionError):
            reject("is not valid JSON")
        if not isinstance(run, dict):
            reject("is not an object")
        if run.get("id") != row["id"]:
            reject("names a different run")
        if not isinstance(run.get("status"), str) or run["status"] not in {"RESERVED", "RUNNING", *TERMINAL} or run["status"] != row["status"]:
            reject("does not match its recorded status")
        if not isinstance(run.get("manifest"), dict) or run["manifest"].get("id") != row["id"]:
            reject("has no matching manifest")
        try:
            canonical(run).encode("utf-8")
            manifest_sha = digest(run["manifest"])
        except (ValueError, RecursionError):
            reject("has no canonical encoding")
        if manifest_sha != run.get("manifest_sha256"):
            reject("has a Manifest integrity failure")
        required = {"resource_estimates", "protocol", "executor", "executor_sha256", "attempt_id",
                    "worker_pid", "pid", "started_at", "finished_at", "observed_wall_seconds", "scheduler"}
        if not required <= run.keys():
            reject("is missing execution state")
        if (not isinstance(run["resource_estimates"], dict)
                or not isinstance(run["protocol"], dict)
                or not isinstance(run["executor"], str) or not run["executor"]
                or not isinstance(run["executor_sha256"], str)
                or (run["attempt_id"] is not None and not isinstance(run["attempt_id"], str))
                or (run["scheduler"] is not None and not isinstance(run["scheduler"], dict))):
            reject("has invalid execution state")
        for value in [run["observed_wall_seconds"], *run["resource_estimates"].values()]:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                reject("has invalid recorded costs")
            try:
                finite = math.isfinite(value)
            except OverflowError:
                finite = False
            if not finite:
                reject("has invalid recorded costs")
        for key in ("worker_pid", "pid"):
            if run[key] is not None and (type(run[key]) is not int or run[key] <= 0):
                reject("has an invalid process identity")
        return run

    @staticmethod
    def _receipt(row):
        """One owned receipt row (run_id, sha256, body); a damaged row is rejected, never repaired (#104)."""
        prefix = f"Project receipt integrity failure: run {row['run_id']} body "
        suffix = "; inspect retained state"

        def unique(items):
            keys = [key for key, _ in items]
            if len(set(keys)) != len(keys):
                raise ReceiptIntegrityError(prefix + "repeats a JSON key" + suffix)
            return dict(items)
        try:
            receipt = json.loads(row["body"], object_pairs_hook=unique)
        except ReceiptIntegrityError:
            raise
        except (ValueError, RecursionError):
            raise ReceiptIntegrityError(prefix + "is not valid JSON" + suffix) from None
        if not isinstance(receipt, dict):
            kind = ("null" if receipt is None else "boolean" if isinstance(receipt, bool) else "array"
                    if isinstance(receipt, list) else "string" if isinstance(receipt, str) else "number")
            raise ReceiptIntegrityError(prefix + f"is a JSON {kind}, not an object" + suffix)
        if receipt.get("run_id") != row["run_id"]:
            raise ReceiptIntegrityError(prefix + "names a different run" + suffix)
        # The writer stores digest(body without sha256) in both the body and the row; bind the value read.
        mismatch = ReceiptIntegrityError(prefix + "does not match its recorded sha256" + suffix)
        if receipt.get("sha256") != row["sha256"]:
            raise mismatch
        try:
            recomputed = digest({key: value for key, value in receipt.items() if key != "sha256"})
        except (ValueError, RecursionError):
            # The decoder accepts NaN/Infinity, overflowing floats and lone surrogate escapes; the writer emits none.
            raise ReceiptIntegrityError(prefix + "has no canonical encoding (e.g. a non-finite number or an unpaired"
                                        " surrogate)" + suffix) from None
        if recomputed != row["sha256"]:
            raise mismatch
        return receipt

    @staticmethod
    def _save(db, run):
        run["run_status"] = "SUCCEEDED" if run["status"] == "COMPLETED" else run["status"]
        db.execute("UPDATE runs SET status=?,body=? WHERE id=?", (run["status"], canonical(run), run["id"]))

    def execute(self, run_id, background=False, *, admission_guard=None):
        # An internal caller may restrict admission after all ordinary checks.
        # This callback grants no authority and is never supplied by the CLI.
        require(admission_guard is None or callable(admission_guard), "Invalid admission guard")
        require(isinstance(background, bool), "background must be Boolean")
        if background and os.name != "nt":
            raise NotImplementedError("Background execution requires Windows Task Scheduler")
        advisor_token = self._advisor_prepare_run(run_id, allow_observation=True)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._run(db, run_id)
            self._runs(db)
            contract = self._contract(db)
            if (('execution_policy' in contract or 'method_evolution' in contract)
                    and (run['status'] != 'RESERVED' or run['attempt_id'] is not None)):
                return self._observe(db, run)
            require(run["status"] == "RESERVED" and run["attempt_id"] is None, "Run already dispatched or started; recover never reruns it")
            self._advisor_check(db, run["manifest"], advisor_token)
            self._check_start(db, run)
            if admission_guard is not None:
                require(admission_guard(db, run) is None, "Admission guard must allow or raise")
            run["attempt_id"] = uuid.uuid4().hex
            if background:
                run["scheduler"] = {"task_id": "RDS-Project-" + run["attempt_id"], "status": "REGISTERING"}
            else:
                # Keep the foreground controller identifiable before it claims
                # the worker, so recovery cannot close this startup window.
                run["worker_pid"] = os.getpid()
            self._save(db, run)
        if background:
            try:
                self._schedule(run)
            except Exception as exc:
                # A controller error is not proof that the scheduled worker did
                # not start. Never close an active worker's reservation here.
                return self._finish(run_id, run["attempt_id"], "FAILED", None, None, None,
                                    [f"Scheduler dispatch failed: {exc}; launch/costs may be unknown"], only_unstarted=True)
            with self._db() as db:
                db.execute("BEGIN IMMEDIATE")
                current = self._run(db, run_id)
                if current["status"] == "RESERVED":
                    current["scheduler"]["status"] = "REGISTERED"
                self._save(db, current)
            return current
        return self._execute_claim(run_id, run["attempt_id"])

    def _observe(self, db, run):
        """Existing operational evidence only; never launch, refund or infer scientific success."""
        contract = self._contract(db)
        bindings, errors = self._bindings(contract)
        require(not errors, '; '.join(errors))
        row = db.execute('SELECT run_id,sha256,body FROM receipts WHERE run_id=?', (run['id'],)).fetchone()
        if row is None:
            require(run['status'] not in TERMINAL, 'Existing terminal run has no owned receipt; inspect retained state')
            return {**run, 'execution_started': False, 'policy_observation': 'Existing attempt; inspect or recover it'}
        receipt = self._receipt(row)
        require(receipt.get('sha256') == row['sha256'] == digest({key: value for key, value in receipt.items() if key != 'sha256'}),
                'Existing receipt integrity failure')
        require(receipt.get('manifest_sha256') == run['manifest_sha256'] and receipt.get('attempt_id') == run['attempt_id'],
                'Existing receipt differs from its run')
        require(receipt.get('run_id') == run['id'] and receipt.get('process_status') == run['status']
                and receipt.get('argv') == run['manifest']['argv'] and receipt.get('executor_sha256') == run['executor_sha256'],
                'Existing receipt operation differs from its owned run')
        require(db.execute("SELECT 1 FROM events WHERE json_extract(body,'$.kind')='ATTEMPT_FINISHED' "
                           "AND json_extract(body,'$.run_id')=? AND json_extract(body,'$.sha256')=?",
                           (run['id'], receipt['sha256'])).fetchone() is not None,
                'Existing receipt has no matching owned completion event')
        if receipt.get('run_status') == 'SUCCEEDED':
            original_bindings = bindings
            if 'method_evolution' in contract:
                from rds_method_revision import contract_history
                history = contract_history(db)
                ancestor = next((h['contract'] for h in history if h['sha256'] ==
                                 run.get('effective_contract_sha256', history[0]['sha256'])), None)
                require(ancestor is not None, 'Existing run contract is outside verified method lineage')
                original_bindings = ancestor['bindings']
            require(receipt.get('bindings_before') == original_bindings == receipt.get('bindings_after'),
                    'Existing successful receipt input bindings differ')
            inventory = {entry['path']: entry['sha256'] for entry in receipt.get('artifacts', [])}
            for output in run['manifest']['outpaths']:
                path = self._path(output, True, contract)
                require(output in inventory and path.is_file() and file_sha(path) == inventory[output],
                        'Existing successful output unavailable or changed: ' + output)
        observation = {'execution_started': False, 'policy_observation': 'Retained receipt; scientific assessment is unchanged'}
        if "advisor_policy" in contract:
            # Keep metadata outside the receipt's signed body for the new API.
            self.last_advisor_observation = observation
            return receipt
        return {**receipt, **observation}

    def _execute_claim(self, run_id, attempt_id):
        attempt_start = time.monotonic()
        admission_error = None
        advisor_token = self._advisor_prepare_run(run_id)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            run = self._run(db, run_id)
            require(run["status"] == "RESERVED" and run["attempt_id"] == attempt_id, "Run cannot be started twice")
            self._runs(db)
            self._advisor_check(db, run["manifest"], advisor_token)
            try:
                self._check_start(db, run)
            except ValueError as exc:
                # A damaged prior receipt leaves this run RESERVED and unsettled, recoverable once restored.
                if isinstance(exc, (ReceiptIntegrityError, RunIntegrityError)) or (
                        'stop_policy' not in self._contract(db) and 'maintenance' not in run['manifest']):
                    raise
                admission_error = str(exc)
            if admission_error is None:
                run.update(status="RUNNING", worker_pid=os.getpid(), started_at=time.time())
                if run["scheduler"]:
                    run["scheduler"]["status"] = "RUNNING"
                self._save(db, run)
            contract = self._contract(db)
        if admission_error is not None:
            return self._finish(run_id, attempt_id, 'FAILED', None, time.monotonic() - attempt_start,
                                False, [admission_error], stop_reason='CAMPAIGN_DEADLINE'
                                if admission_error == 'Stop policy: CAMPAIGN_DEADLINE' else None)
        before, errors = self._bindings(contract)
        with self._db() as db:
            current = self._run(db, run_id)
            current["bindings_before"] = before
            self._save(db, current)
        try:
            if file_sha(run["executor"]) != run["executor_sha256"]:
                errors.append("Command executable changed")
            if any(self._path(p, True, contract).exists() for p in run["manifest"]["outpaths"]):
                errors.append("Output already exists before start")
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
        work = self.artifact_dir / run_id
        require(work.resolve().is_relative_to(self.root), "Artifact directory escapes root")
        work.mkdir(parents=True, exist_ok=True)
        manifest_path = work / "manifest.json"
        manifest_path.write_text(canonical(run["manifest"]), encoding="utf-8")
        if errors:
            return self._finish(run_id, attempt_id, "FAILED", None, time.monotonic() - attempt_start, False, errors, before)
        for p in run["manifest"]["outpaths"]:
            self._path(p, True, contract).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("INSERT INTO exposures(run_id,body) VALUES (?,?)", (run_id, canonical({"run_id": run_id, "attempt_id": attempt_id, "at": time.time(), "kind": "declared_input_access", "data": [b for b in before if b["role"] == "data"], "eligibility_change": "none"})))
        start = time.monotonic()
        process = job = None
        started = False
        timeout = False
        stop_reason = None
        exit_code = None
        progress = contract.get("stop_policy", {}).get("progress")
        policy_deadline = None
        stream_bytes = [(work / "stdout.bin").stat().st_size if (work / "stdout.bin").exists() else 0,
                        (work / "stderr.bin").stat().st_size if (work / "stderr.bin").exists() else 0]
        samples = [(start, sum(stream_bytes))]
        try:
            with (work / "stdout.bin").open("xb") as out, (work / "stderr.bin").open("xb") as err:
                flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
                argv = [run["executor"], *run["manifest"]["argv"][1:]]
                advisor_token = self._advisor_prepare_run(run_id)
                with self._db() as db:
                    db.execute("BEGIN IMMEDIATE")
                    current = self._run(db, run_id)
                    require(current["status"] == "RUNNING" and current["attempt_id"] == attempt_id
                            and current["pid"] is None, "Run cannot be started twice")
                    self._advisor_check(db, current["manifest"], advisor_token)
                    self._check_start(db, current)
                    deadline = self._campaign_deadline(db, contract)
                    if deadline is not None:
                        policy_deadline = time.monotonic() + max(0.0, deadline - time.time())
                    process = subprocess.Popen(argv, cwd=self.root, shell=False, stdin=subprocess.DEVNULL,
                                               stdout=out, stderr=err, creationflags=flags, start_new_session=os.name != "nt")
                    started = True
                    job = _Job(process)
                    current["pid"] = process.pid
                    self._save(db, current)
                while process.poll() is None:
                    elapsed = time.monotonic() - start
                    with self._db() as db:
                        current = self._run(db, run_id)
                        current["observed_wall_seconds"] = elapsed
                        self._save(db, current)
                    if elapsed >= run["manifest"]["timeout_seconds"]:
                        timeout = True
                        job.stop()
                        break
                    now = time.monotonic()
                    if policy_deadline is not None and now >= policy_deadline:
                        stop_reason = "CAMPAIGN_DEADLINE"
                        job.stop()
                        break
                    if progress is not None:
                        total = sum(path.stat().st_size if path.exists() else 0
                                    for path in ((work / "stdout.bin"), (work / "stderr.bin")))
                        samples.append((now, total))
                        cutoff = now - progress["window_seconds"]
                        # Keep exactly one baseline sample at or before the cutoff.
                        while len(samples) > 2 and samples[1][0] <= cutoff:
                            del samples[0]
                        baseline = samples[0]
                        if now - baseline[0] >= progress["window_seconds"] and total - baseline[1] < progress["min_bytes"]:
                            stop_reason = "PROGRESS_NO_GROWTH"
                            job.stop()
                            break
                    time.sleep(min(0.05, max(0.001, run["manifest"]["timeout_seconds"] - elapsed)))
                exit_code = process.wait()
                if not timeout and stop_reason is None and policy_deadline is not None and time.monotonic() >= policy_deadline:
                    stop_reason = 'CAMPAIGN_DEADLINE'
                status = "COMPLETED" if exit_code == 0 and not timeout and not stop_reason else "FAILED"
                if timeout:
                    errors.append("Process timeout")
                elif stop_reason:
                    errors.append(f"Stop policy: {stop_reason}")
                elif exit_code != 0:
                    errors.append(f"Nonzero process exit: {exit_code}")
        except BaseException as exc:
            if process is not None:
                if job:
                    job.stop()
                elif process.poll() is None:
                    process.kill()
                exit_code = process.wait()
            if isinstance(exc, (ReceiptIntegrityError, RunIntegrityError)):
                # Retain the attempt and unknown costs. Recovery can reconcile it
                # after the damaged history is restored; this is no FAILED receipt.
                raise
            status = "INTERRUPTED" if isinstance(exc, (KeyboardInterrupt, SystemExit)) else "FAILED"
            if str(exc) == 'Stop policy: CAMPAIGN_DEADLINE':
                stop_reason = 'CAMPAIGN_DEADLINE'
            errors.append(f"Execution interrupted: {type(exc).__name__}: {exc}")
        finally:
            if job:
                job.close()
        return self._finish(run_id, attempt_id, status, exit_code, time.monotonic() - attempt_start,
                            started, errors, before, timeout, stop_reason=stop_reason)

    def _finish(self, run_id, attempt_id, status, exit_code, wall, started, errors, before=None, timeout=False,
                only_unstarted=False, recovering=False, stop_reason=None):
        with self._db() as db:
            run = self._run(db, run_id)
            contract = self._contract(db)
        require(run["attempt_id"] == attempt_id, "Attempt mismatch")
        after, binding_errors = self._bindings(contract)
        before = before if before is not None else run.get("bindings_before")
        errors = list(errors) + binding_errors
        try:
            require(file_sha(run["executor"]) == run["executor_sha256"], "Command executable changed")
        except (OSError, ValueError) as exc:
            errors.append(str(exc))
        artifacts = []
        work = self.artifact_dir / run_id
        for name in ("manifest.json", "stdout.bin", "stderr.bin", "scheduler.stdout.bin", "scheduler.stderr.bin", "worker.log"):
            path = work / name
            if path.is_file():
                artifacts.append({"path": path.relative_to(self.root).as_posix(), "sha256": file_sha(path), "size": path.stat().st_size, "kind": name})
        if (work / "manifest.json").is_file() and file_sha(work / "manifest.json") != run["manifest_sha256"]:
            errors.append("Recorded manifest artifact changed")
        if started is not False:
            for relative in run["manifest"]["outpaths"]:
                try:
                    path = self._path(relative, True, contract)
                    require(path.exists(), f"Missing output: {relative}")
                    require(path.is_file(), f"Output is a directory; expected a file: {relative}"
                            if path.is_dir() else f"Output is not a file: {relative}")
                    artifacts.append({"path": relative, "sha256": file_sha(path), "size": path.stat().st_size, "kind": "project_output"})
                except (OSError, ValueError) as exc:
                    errors.append(str(exc))
        if errors and status == "COMPLETED":
            status = "FAILED"
        resources = {}
        for key, estimate in run["resource_estimates"].items():
            measured = wall if key == "wall_seconds" else None
            resources[key] = {"unit": "seconds" if key.endswith("_seconds") else key,
                              "measured": measured, "unknown": measured is None,
                              "charged_estimate": estimate if measured is None else 0.0}
            if key == "wall_seconds" and wall is None:
                resources[key]["observed_lower_bound"] = run["observed_wall_seconds"]
        receipt = {"schema": 1, "run_id": run_id, "attempt_id": attempt_id,
                   "run_status": "SUCCEEDED" if status == "COMPLETED" else status, "process_status": status,
                   "arm": run["manifest"]["arm"], "control_id": run["manifest"].get("control_id"),
                   "protocol": run["protocol"], "manifest_sha256": run["manifest_sha256"],
                   "bindings_before": before, "bindings_after": after,
                   "argv": run["manifest"]["argv"], "cwd": str(self.root),
                   "executor_sha256": run["executor_sha256"], "worker_pid": run["worker_pid"],
                   "pid": run["pid"], "started_at": run["started_at"], "ended_at": time.time(),
                   "exit_code": exit_code, "timeout": timeout, "process_started": started,
                   "resources": resources, "artifacts": artifacts,
                   "assessment": {"task_gain": "UNKNOWN", "mechanism": "UNKNOWN"},
                   # RDS attempt lifecycle, not a query of the OS task's current state.
                   "errors": errors, "scheduler": ({**run["scheduler"], "status": status}
                                                     if run["scheduler"] else None)}
        if stop_reason is not None:
            receipt["stop_reason"] = stop_reason
        for field in ('effective_contract_sha256', 'runtime_fingerprint', 'autonomy_request', 'confirmation_challenge'):
            if field in run:
                receipt[field] = run[field]
        if run["manifest"].get("maintenance") is not None:
            receipt["maintenance"] = True
            receipt['maintenance_review'] = run['maintenance_review']
            receipt["assessment"] = {"task_gain": "UNKNOWN", "mechanism": "UNKNOWN", "purpose": "MAINTENANCE"}
        receipt["sha256"] = digest(receipt)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            self._runs(db)
            old = db.execute("SELECT run_id,sha256,body FROM receipts WHERE run_id=?", (run_id,)).fetchone()
            if old:
                receipt = self._receipt(old)
            else:
                current = self._run(db, run_id)
                require(current["attempt_id"] == attempt_id and current["status"] not in TERMINAL, "Run already terminal")
                # Evidence collection can race with startup or progress. Recheck
                # the latest identity while holding the settlement write lock.
                if recovering and (current != run or _alive(current["worker_pid"]) is not False
                                   or _alive(current["pid"]) is not False):
                    return {**current, "recovery": "Run changed or process may still be active; no rerun or termination"}
                if only_unstarted and current["status"] == "RUNNING":
                    return {**current, "dispatch_error": errors[0]}
                for key, resource in resources.items():
                    db.execute("UPDATE budget SET reserved=reserved-?,spent=spent+?,charged=charged+? WHERE resource=?",
                               (run["resource_estimates"][key], resource["measured"] or 0.0, resource["charged_estimate"], key))
                current.update(status=status, finished_at=receipt["ended_at"], scheduler=receipt["scheduler"])
                self._save(db, current)
                db.execute("INSERT INTO receipts VALUES (?,?,?)", (run_id, receipt["sha256"], canonical(receipt)))
                db.execute("INSERT INTO events(body) VALUES (?)", (canonical({"kind": "ATTEMPT_FINISHED", "run_id": run_id, "sha256": receipt["sha256"]}),))
            if self._run(db, run_id).get('owned_history_recorded') is True:
                from rds_owned_history import capture_completion
                capture_completion(self, db, self._run(db, run_id), receipt)
        self._advisor_finished(contract)
        return receipt

    def recover(self, run_id):
        with self._db(True) as db:
            db.execute("BEGIN")
            run = self._run(db, run_id)
            self._runs(db)
            contract = self._contract(db)
            old = db.execute("SELECT run_id,sha256,body FROM receipts WHERE run_id=?", (run_id,)).fetchone()
            receipt = self._receipt(old) if old else None
        if receipt is not None:
            self._advisor_finished(contract)
            return receipt
        if run["attempt_id"] is None:
            return {**run, "recovery": "Unstarted reservation; no process to restart"}
        if run["status"] == "RESERVED" and run["scheduler"]:
            return {**run, "recovery": "Dispatched scheduler task; do not start another process"}
        if _alive(run["worker_pid"]) is not False or _alive(run["pid"]) is not False:
            return {**run, "recovery": "Process may still be active; no rerun or termination"}
        return self._finish(run_id, run["attempt_id"], "INTERRUPTED", None, None, None,
                            ["Worker and process unavailable; final costs and exit status unknown; no automatic rerun"], recovering=True)

    def snapshot(self, check_bindings=False):
        require(isinstance(check_bindings, bool), "check_bindings must be Boolean")
        with self._db(True) as db:
            db.execute("BEGIN")
            contract = self._contract(db)
            budget = {}
            for row in db.execute("SELECT * FROM budget ORDER BY resource"):
                budget[row["resource"]] = {"cap": row["cap"], "spent_measured": row["spent"],
                                           "charged_estimate": row["charged"], "reserved": row["reserved"],
                                           "remaining": row["cap"] - row["spent"] - row["charged"] - row["reserved"],
                                           "unit": "seconds" if row["resource"].endswith("_seconds") else row["resource"]}
            snapshot = {"schema": 1, "contract": contract, "contract_sha256": digest(contract), "budget": budget,
                    "runs": self._runs(db),
                    "exposures": [json.loads(r["body"]) for r in db.execute("SELECT body FROM exposures ORDER BY id")],
                    "receipts": [self._receipt(r) for r in db.execute("SELECT run_id,sha256,body FROM receipts ORDER BY run_id")]}
            from rds_steering import current, view
            steering = current(db)
            if steering is not None:
                snapshot['steering'] = view(steering)
            from rds_method_revision import contract_history, pending_revision
            history = contract_history(db)
            if 'method_evolution' in contract or len(history) > 1:
                snapshot['contract_history'] = history
                pending = pending_revision(db)
                snapshot['method_revision_pending'] = ({'id': pending['id'], 'sha256': pending['sha256']}
                                                       if pending else None)
        if check_bindings:
            found, errors = self._bindings(contract)
            snapshot["binding_check"] = {"files": found, "errors": errors}
        chain = self.predecessor_chain()
        if chain:  # Only successor roots carry the field; every other snapshot is unchanged.
            # Pinned checkpoint digests stay in the link record; the snapshot names the IDs only.
            snapshot["predecessor_chain"] = [{k: v for k, v in hop.items() if k != "checkpoint_shas"} for hop in chain]
        return snapshot

    def _receipt_result(self, receipt):
        """Read the primary metric value from one receipt's declared output artifact.

        The receipt itself carries no metric; the value is read from the run's
        recorded project_output artifact, whose sha256 the receipt binds.
        """
        out = next((a for a in receipt.get("artifacts", []) if a.get("kind") == "project_output"), None)
        require(out is not None, f"Run {receipt.get('run_id')} has no recorded project_output artifact")
        path = self.root / out["path"]
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError(f"Output artifact missing: {out['path']}: {exc}") from exc
        require(hashlib.sha256(raw).hexdigest() == out["sha256"], f"Output artifact hash mismatch: {out['path']}")
        metric_name = self.snapshot()["contract"].get("primary_metric", {}).get("name")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            raise ValueError(f"Output artifact is not readable JSON: {out['path']}") from exc
        require(isinstance(payload, dict), f"Output artifact is not a JSON object: {out['path']}")
        if metric_name:
            require(metric_name in payload, f"Output artifact lacks metric '{metric_name}': {out['path']}")
            value = payload[metric_name]
        else:
            require("mse" in payload or "mean" in payload,
                    "Output artifact has no metric and the contract declares no primary_metric")
            value = payload.get("mse", payload.get("mean"))
        require(_is_rational_literal(value), f"Metric value must be a finite rational literal, got {value!r}")
        if isinstance(value, float):
            require(math.isfinite(value), "Metric value must be finite")
            value = repr(value)
        return _parse_rational_string(str(value), "metric value")

    def next_move(self):
        """Derive the single next actionable step from recorded ledger state only.

        Pure function of the snapshot; carries no advice beyond recorded facts.
        """
        initialize = UNINITIALIZED + "; run: python -B scripts/rds_cli.py project init --contract <contract.json>"
        if not self.path.is_file():
            raise ValueError(initialize)
        command = f"python -B scripts/rds_cli.py --root {_shell_argument(self.root)}"
        try:
            snap = self.snapshot()
        except ValueError as exc:
            # A bound native objective creates the database before project init.
            if str(exc) != UNINITIALIZED:
                raise
            raise ValueError(initialize) from None
        runs = snap["runs"]
        if snap.get('steering', {}).get('paused'):
            return {'next_move': 'New dispatch is paused by the current user; inspect the retained instruction and original attempts',
                    'command': f'{command} project steering', 'steering': snap['steering']}
        receipts = {r["run_id"]: r for r in snap["receipts"]}
        live = [r for r in runs if r["status"] == "RUNNING"]
        failed = [r for r in runs if r["status"] in ("FAILED", "INTERRUPTED")]
        executed = [r["id"] for r in runs if r["id"] in receipts]
        arms = {rid: (receipts[rid].get("arm"), receipts[rid].get("control_id")) for rid in executed}
        control_id = next((rid for rid, (arm, _) in arms.items() if arm == "control"), None)
        treatment_id = next((rid for rid, (_, cid) in arms.items() if cid is not None), None)
        if failed:
            run = failed[0]
            return {"next_move": "recover the failed run to a terminal recorded state",
                    "command": f"{command} project recover --id={_shell_argument(run['id'])}"}
        if live:
            run = live[0]
            return {"next_move": "wait for the running attempt, then re-check status",
                    "command": f"{command} project status --brief"}
        if not executed:
            if not runs:
                return {"next_move": "register the control arm from its manifest",
                        "command": f"{command} project create --manifest {_shell_argument('<control-manifest.json>')}"}
            pending = next((r for r in runs if r["id"] not in executed), None)
            return {"next_move": f"execute run {pending['id']}",
                    "command": f"{command} project execute --id={_shell_argument(pending['id'])}"}
        if control_id is None or treatment_id is None:
            pending = next((r for r in runs if r["id"] not in executed), None)
            if pending is not None:
                bound = pending.get("control_id")
                label = f"treatment arm, control {bound}" if bound else f"run {pending['id']}"
                return {"next_move": f"execute {label}",
                        "command": f"{command} project execute --id={_shell_argument(pending['id'])}"}
            return {"next_move": "register the remaining arm bound to the recorded control",
                    "command": f"{command} project create --manifest {_shell_argument('<treatment-manifest.json>')}"}
        verdict = self.compare()
        if verdict is None:
            return {"next_move": "resolve the missing arm state before comparison",
                    "command": f"{command} project status --brief"}
        if verdict.get("status") == "UNKNOWN":
            return {"next_move": "resolve the recorded comparison blocker, then re-run project compare",
                    "command": f"{command} project compare", "comparison": verdict}
        if self._latest_project_checkpoint() is None:
            return {"next_move": "record the decision with the kernel comparison as evidence",
                    "command": (f"{command} checkpoint save --kind project "
                                f"--id {_shell_argument('<decision-id>')} "
                                f"--decision {_shell_argument('<decision; see project compare>')}"),
                    "comparison": verdict}
        return {"next_move": "campaign reached a recorded decision; archive outputs or propose the next delta",
                "command": f"{command} project status",
                "comparison": verdict}

    def _latest_project_checkpoint(self):
        with self._db(True) as db:
            table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone()
            if not table:
                return None
            rows = db.execute("SELECT sha,body FROM checkpoints ORDER BY rowid DESC").fetchall()
        for row in rows:
            if hashlib.sha256(row["body"].encode("utf-8")).hexdigest() != row["sha"]:
                require(False, "Checkpoint integrity failure")
            body = json.loads(row["body"])
            if body.get("kind") == "project":
                return body
        return None

    def compare(self):
        """Kernel-computed control/treatment verdict against the precommitted delta.

        Returns None when the comparison is not yet derivable. The verdict is
        exact over the recorded rational values; it never rounds a loss into a win.
        """
        contract = self.snapshot()["contract"]
        metric = contract.get("primary_metric")
        if not metric:
            return {"status": "UNKNOWN", "reason": "contract declares no primary_metric"}
        runs = {r["id"]: r for r in self.snapshot()["runs"]}
        receipts = {r["run_id"]: r for r in self.snapshot()["receipts"]}
        pairs = [(rid, r.get("arm"), r.get("control_id")) for rid, r in receipts.items()]
        control_id = next((rid for rid, arm, _ in pairs if arm == "control"), None)
        treatment_id = next((rid for rid, arm, cid in pairs if cid is not None and cid == control_id), None)
        if control_id is None or treatment_id is None:
            return None
        try:
            control = self._receipt_result(receipts[control_id])
            treatment = self._receipt_result(receipts[treatment_id])
        except (ValueError, OSError) as exc:
            return {"status": "UNKNOWN", "reason": str(exc)}
        if runs.get(control_id, {}).get("run_status") != "SUCCEEDED" or runs.get(treatment_id, {}).get("run_status") != "SUCCEEDED":
            return None
        delta = treatment - control
        threshold = _parse_rational_string(metric["min_useful_delta"], "primary_metric.min_useful_delta")
        useful = delta <= -threshold if metric["direction"] == "min" else delta >= threshold
        harmful = delta > 0 if metric["direction"] == "min" else delta < 0
        verdict = "GAIN_CONFIRMED" if useful else ("NOT_CONFIRMED" if harmful else "BELOW_RESOLUTION")
        return {"status": verdict, "metric": metric["name"], "direction": metric["direction"],
                "control": {"run_id": control_id, "value": str(control)},
                "treatment": {"run_id": treatment_id, "value": str(treatment)},
                "delta": str(delta), "min_useful_delta": metric["min_useful_delta"],
                "receipt_sha256": {control_id: receipts[control_id].get("sha256"),
                                   treatment_id: receipts[treatment_id].get("sha256")}}

    def _schedule(self, run):
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        require(pythonw.is_file(), "Hidden scheduler execution requires pythonw.exe")
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        require(powershell is not None, "PowerShell unavailable for Task Scheduler")
        worker = Path(__file__).with_name("rds_project_worker.py").resolve()
        argv = [str(worker), "--root", str(self.root), "--run-id", run["id"], "--attempt-id", run["attempt_id"]]
        quote = lambda s: "'" + str(s).replace("'", "''") + "'"
        task_id = run["scheduler"]["task_id"]
        script = ("$ErrorActionPreference='Stop'; "
                  f"$action=New-ScheduledTaskAction -Execute {quote(pythonw)} -Argument {quote(subprocess.list2cmdline(argv))} -WorkingDirectory {quote(self.root)}; "
                  "$settings=New-ScheduledTaskSettingsSet -Hidden -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 30); "
                  f"Register-ScheduledTask -TaskName {quote(task_id)} -Action $action -Settings $settings -Description 'RDS owned bounded project attempt' | Out-Null; "
                  f"Start-ScheduledTask -TaskName {quote(task_id)}")
        encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
        result = subprocess.run([powershell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                cwd=self.root, shell=False, capture_output=True, timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        log = self.artifact_dir / run["id"]
        require(log.resolve().is_relative_to(self.root), "Scheduler log escapes root")
        log.mkdir(parents=True, exist_ok=True)
        (log / "scheduler.stdout.bin").write_bytes(result.stdout)
        (log / "scheduler.stderr.bin").write_bytes(result.stderr)
        require(result.returncode == 0, f"Task Scheduler exit {result.returncode}; see recorded scheduler logs")
