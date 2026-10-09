"""Frozen receipt wheel. Execution belongs exclusively to rds_cli project.

See examples/wheel/README.md for the frozen adapter mapping and trust boundary.
No model is called here; --prompt/--propose are separate, bounded host handoffs.
"""
import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid


TOKEN = re.compile(r"[a-z][a-z0-9_]{0,31}\Z")
HEX = re.compile(r"[0-9a-f]{64}\Z")
CONTRACT_KEYS = {"contract_id", "metric", "threshold", "evaluator_sha256",
                 "data_slice_sha256", "wall_ms", "screen_wall_ms"}
CLI = Path(__file__).with_name("rds_cli.py")


class WheelError(ValueError):
    def __init__(self, message, code=2):
        super().__init__(message)
        self.code = code


def require(condition, message, code=2):
    if not condition:
        raise WheelError(message, code)


def finite(value):
    return type(value) is int or type(value) is float and math.isfinite(value)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique,
                      parse_constant=lambda value: (_ for _ in ()).throw(WheelError("nonfinite JSON")))


def read_json(path):
    return decode(Path(path).read_text(encoding="utf-8"))


def validate_contract(contract):
    require(isinstance(contract, dict) and set(contract) == CONTRACT_KEYS, "wheel contract schema differs")
    require(all(isinstance(contract[k], str) and contract[k] for k in ("contract_id", "metric")),
            "contract_id and metric required")
    require(finite(contract["threshold"]) and contract["threshold"] >= 0, "invalid frozen threshold")
    require(all(isinstance(contract[k], str) and HEX.fullmatch(contract[k])
                for k in ("evaluator_sha256", "data_slice_sha256")), "invalid identity hash")
    require(all(type(contract[k]) is int and contract[k] >= 0 for k in ("wall_ms", "screen_wall_ms")),
            "budgets must be nonnegative integer milliseconds")


def classify(execute_id, factor, control, treatment, threshold, direction):
    """Missing either finite metric is UNKNOWN; process exit status is irrelevant."""
    require(direction in ("min", "max"), "missing metric direction")
    control = control if finite(control) else None
    treatment = treatment if finite(treatment) else None
    state = "UNKNOWN"
    if control is not None and treatment is not None:
        delta = Fraction(str(treatment)) - Fraction(str(control))
        gate = Fraction(str(threshold))
        crossed = delta <= -gate if direction == "min" else delta >= gate
        state = "TRUE" if crossed else "FALSE"
    return {"execute_id": execute_id, "factor": factor, "state": state,
            "metric_control": control, "metric_treatment": treatment}


def rsi_gate(old, new, delta, paired_receipts=None):
    """Descriptive gate only: this function never adopts or edits a rule."""
    valid = (finite(delta) and delta >= 0 and isinstance(old, dict) and isinstance(new, dict)
             and all(finite(item.get(key)) and item[key] >= 0 for item in (old, new)
                     for key in ("violations", "spin_count", "budget_to_true_cell")))
    eligible = bool(valid and new["violations"] == 0 and new["spin_count"] <= old["spin_count"]
                    and new["budget_to_true_cell"] <= old["budget_to_true_cell"] - delta)
    # This change does not implement a paired rule-evaluation protocol. Even an
    # unverified caller-supplied number cannot establish measured regret.
    return {"adoption_eligible": eligible, "regret": "UNKNOWN", "rule_edits": False,
            "scientific_accuracy_gain": "UNKNOWN"}


class Wheel:
    def __init__(self, root, project=None):
        self.root = Path(root).resolve()
        self.directory = self.root / ".rds" / "wheel"
        self.project = project or self._project

    def _project(self, action, *args):
        require(action in {"init", "create", "execute", "status", "costs"}, "unsupported project call")
        result = subprocess.run([sys.executable, "-B", str(CLI), "--root", str(self.root),
                                 "project", action, *map(str, args)], capture_output=True)
        try:
            payload = decode(result.stdout.decode("utf-8"))
        except (ValueError, UnicodeError):
            raise WheelError("project " + action + " did not return JSON: "
                             + result.stderr.decode("utf-8", errors="replace")[-2000:]) from None
        # A failed experiment can still have a valid recorded metric.
        receipt = payload.get("receipt", payload) if isinstance(payload, dict) else None
        require(result.returncode == 0 or action == "execute" and isinstance(payload, dict)
                and isinstance(receipt, dict) and isinstance(receipt.get("run_id"), str),
                "project " + action + " rejected the operation")
        return payload

    def path(self, relative):
        require(isinstance(relative, str) and relative and not Path(relative).is_absolute()
                and not Path(relative).drive and ":" not in relative, "expected relative project path")
        path = (self.root / relative).resolve()
        require(path != self.root and path.is_relative_to(self.root), "path escapes project")
        return path

    def state_path(self, relative):
        path = (self.directory / relative).resolve()
        require(path.is_relative_to(self.directory.resolve()) and path != self.directory.resolve(),
                "wheel state path escapes")
        # The state directory itself cannot redirect outside the project.
        require(self.directory.resolve().is_relative_to(self.root), "wheel directory escapes project")
        return path

    def write(self, relative, value, *, once=False, text=False):
        path = self.state_path(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = value if text else canonical(value) + "\n"
        if once:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        else:
            temporary = path.with_name(path.name + ".tmp")
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)

    def append(self, relative, raw):
        with self.state_path(relative).open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def tokens(self, name):
        values = self.state_path(name).read_text(encoding="utf-8").splitlines()
        require(all(TOKEN.fullmatch(value) for value in values), "invalid factor token in " + name)
        return values

    def effect(self, name, after, *, append=False):
        before = self.state_path(name).read_text(encoding="utf-8")
        return {"path": name, "before": before, "after": after, "append": append}

    def commit(self, state, effects):
        # Persist intended byte changes before applying them. Recovery finishes
        # the same change; it never applies a quota multiplier a second time.
        state["effects"] = effects
        self.write("state.json", state)
        self.finish_commit(state)

    def finish_commit(self, state):
        if "effects" not in state:
            return
        for effect in state["effects"]:
            current = self.state_path(effect["path"]).read_text(encoding="utf-8")
            before, after = effect["before"], effect["after"]
            if current == after:
                continue
            if effect["append"]:
                require(after.startswith(current) and current.startswith(before), "append journal differs", 3)
                self.append(effect["path"], after[len(current):])
            else:
                require(current == before, "state journal differs", 3)
                self.write(effect["path"], after, text=True)
        del state["effects"]
        self.write("state.json", state)

    @contextmanager
    def lock(self):
        # OS locks disappear on process exit; no stale PID lease can authorize work.
        stream = self.state_path("writer.lock").open("r+b")
        acquired = False
        try:
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    acquired = True
                except OSError:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    acquired = True
                except BlockingIOError:
                    pass
            yield acquired
        finally:
            if acquired:
                if os.name == "nt":
                    stream.seek(0)
                    msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_UN)
            stream.close()

    def setup(self, project_contract, *, initialized=True):
        require(isinstance(project_contract, dict), "missing project contract")
        configs = [b for b in project_contract.get("bindings", []) if b.get("role") == "config"]
        require(len(configs) == 1, "one frozen wheel setup config mapping required")
        binding = configs[0]
        config_path = self.path(binding["path"])
        require(sha(config_path) == binding["sha256"], "frozen setup hash mismatch", 3)
        setup = read_json(config_path)
        require(setup.get("schema") == "rds-wheel-setup-v1", "missing wheel evaluator mapping")
        contract = setup.get("wheel")
        validate_contract(contract)
        if initialized:
            require(read_json(self.state_path("contract.json")) == contract, "wheel contract changed", 3)
        metric = project_contract.get("primary_metric", {})
        require(metric.get("name") == contract["metric"] and metric.get("direction") in ("min", "max"),
                "missing frozen primary metric mapping")
        require(Fraction(metric.get("min_useful_delta", "-1")) == Fraction(str(contract["threshold"])),
                "wheel threshold differs from frozen project threshold", 3)
        require(Fraction(str(project_contract.get("budget", {}).get("wall_seconds", -1))) * 1000
                == contract["wall_ms"], "wheel wall cap differs from project cap", 3)
        evaluator = self.path(setup["evaluator_path"])
        roots = setup.get("agent_writable_roots")
        require(isinstance(roots, list) and roots, "declare all agent-writable roots before tick 0")
        writable = [self.path(p) for p in roots] + [self.root / "inbox", self.directory]
        writable += [self.path(p) for p in project_contract.get("output_roots", [])]
        require(not any(evaluator == p or evaluator.is_relative_to(p) for p in writable),
                "evaluator is in an agent-writable tree")
        require(evaluator.is_file() and sha(evaluator) == contract["evaluator_sha256"], "evaluator hash mismatch", 3)
        require(sha(self.path(setup["data_path"])) == contract["data_slice_sha256"], "data slice hash mismatch", 3)
        for role, path, expected in (("evaluator", setup["evaluator_path"], contract["evaluator_sha256"]),
                                     ("data", setup["data_path"], contract["data_slice_sha256"])):
            require(any(b.get("role") == role and b.get("path") == path and b.get("sha256") == expected
                        for b in project_contract["bindings"]), "missing frozen " + role + " binding")
        protocols = [b for b in project_contract["bindings"] if b["role"] == "protocol"]
        require(len(protocols) == 1, "one frozen wheel protocol required")
        protocol = read_json(self.path(protocols[0]["path"]))
        require(sha(self.path(protocols[0]["path"])) == protocols[0]["sha256"], "protocol hash mismatch", 3)
        require(protocol.get("data_split") == "wheel-" + hashlib.sha256(contract["contract_id"].encode()).hexdigest()[:16]
                and protocol.get("evaluator_sha256") == contract["evaluator_sha256"],
                "slice/evaluator must be registered in protocol before execution", 3)
        self.mapping, self.contract, self.project_contract = setup, contract, project_contract
        self.protocol = {key: protocols[0][key] for key in ("path", "sha256")}
        self.direction = metric["direction"]
        self.validate_mapping_ids()
        return setup

    def validate_mapping_ids(self):
        """Validate the entire frozen run namespace before any dispatch or write."""
        mapped = []
        for kind in ("control", "initial"):
            template = self.mapping.get(kind)
            require(isinstance(template, dict), "missing " + kind + " mapping")
            mapped.append(self.manifest(template.get("factor"), kind))
        routes = self.mapping.get("routes", {})
        require(isinstance(routes, dict), "routes must be an object")
        for factor, route in routes.items():
            require(isinstance(route, dict), "factor route must be an object")
            for kind in ("main", "screen"):
                if kind in route:
                    mapped.append(self.manifest(factor, kind))
        seen = set()
        for manifest in mapped:
            require(manifest["id"] not in seen, "mapped run IDs must be distinct: " + manifest["id"])
            seen.add(manifest["id"])

    def initialize(self, project_contract_path):
        require(not self.directory.exists(), "wheel is already initialized")
        contract = read_json(project_contract_path)
        setup = self.setup(contract, initialized=False)
        factors = setup.get("factors")
        require(isinstance(factors, list) and all(isinstance(f, str) and TOKEN.fullmatch(f) for f in factors)
                and len(factors) == len(set(factors)), "initial factors must be unique tokens")
        for factor in factors:
            self.manifest(factor, "main")
        self.manifest(setup["initial"]["factor"], "initial")
        self.manifest(setup["control"]["factor"], "control")
        # Kernel validation can reject a contract that passed the wheel checks.
        # Publish wheel state only after the project contract is accepted.
        self.project("init", "--contract", str(project_contract_path))
        self.write("contract.json", self.contract, once=True)
        self.write("factors.txt", "".join(f + "\n" for f in factors), text=True, once=True)
        self.write("dead.txt", "", text=True, once=True)
        self.write("inbox.jsonl", "", text=True, once=True)
        self.write("quota.json", {}, once=True)
        self.write("band.json", {"width": "narrow"}, once=True)
        self.write("writer.lock", "0", text=True, once=True)
        self.write("state.json", {"ticks": 0, "last_state": None, "last_execute_id": None, "seen": [],
                                  "pending": None, "advance": False, "screened": [], "used": []}, once=True)
        self.write("transitions.jsonl", "", text=True, once=True)
        self.write("rsi.json", {"slice_id": protocol_slice(self.contract), "regret": "UNKNOWN",
                                "rule_edits": False, "scientific_accuracy_gain": "UNKNOWN"}, once=True)

    def manifest(self, factor, kind, budget_ms=None):
        require(isinstance(factor, str) and TOKEN.fullmatch(factor), "invalid factor")
        if kind in ("control", "initial"):
            template = self.mapping[kind]
        else:
            template = self.mapping.get("routes", {}).get(factor, {}).get(kind)
        require(isinstance(template, dict), "missing preauthorized evaluator mapping for " + factor)
        argv = template.get("argv")
        require(isinstance(argv, list) and argv in self.project_contract["allowed_commands"],
                "factor command was not frozen")
        control_argv = self.mapping["control"]["argv"]
        require(len(argv) == len(control_argv) and argv.count("--factor") == 1 and argv.count("--output") == 1,
                "mapping requires a single --factor and --output")
        index, output_index = argv.index("--factor") + 1, argv.index("--output") + 1
        require(index < len(argv) and output_index < len(argv) and argv[index] == factor,
                "manifest must flip exactly its declared factor")
        normalized = list(argv)
        normalized[index], normalized[output_index] = control_argv[index], control_argv[output_index]
        require(normalized == control_argv and self.mapping["evaluator_path"] in argv,
                "single-factor mapping changed evaluator or other inputs")
        reserve = template.get("reserve_ms")
        require(type(reserve) is int and reserve > 0, "positive mapped reservation required")
        reserve = reserve if budget_ms is None else budget_ms
        require(type(reserve) is int and reserve > 0, "positive reservation required")
        run_id = template.get("id")
        require(isinstance(run_id, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", run_id), "invalid mapped run ID")
        if kind == "main":
            require(run_id == factor, "main execute ID must equal factor")
        require(set(self.project_contract["budget"]) == {"wall_seconds"}, "wheel requires an explicit wall-only mapping")
        return {"schema": 1, "id": run_id, "arm": "control" if kind == "control" else "treatment",
                "control_id": None if kind == "control" else self.mapping["control"]["id"],
                "argv": deepcopy(argv), "outpaths": [argv[output_index]], "protocol": self.protocol,
                "resource_estimates": {"wall_seconds": reserve / 1000}, "timeout_seconds": reserve / 1000,
                "description": "wheel factor=" + factor + "; evaluator_sha256=" + self.contract["evaluator_sha256"]}

    def remaining_ms(self, snapshot):
        remaining = snapshot.get("budget", {}).get("wall_seconds", {}).get("remaining")
        require(finite(remaining), "remaining wall budget UNKNOWN")
        return max(0, math.floor(Fraction(str(remaining)) * 1000))

    def metric(self, receipt, manifest):
        try:
            # Origin is the status command's owned receipt; verify its bound bytes.
            require(receipt.get("sha256") == digest({k: v for k, v in receipt.items() if k != "sha256"}),
                    "receipt hash mismatch")
            require(receipt.get("manifest_sha256") == digest(manifest)
                    and receipt.get("run_id") == manifest["id"]
                    and receipt.get("arm") == manifest["arm"]
                    and receipt.get("control_id") == manifest["control_id"], "receipt mapping differs")
            for field in ("bindings_before", "bindings_after"):
                bindings = receipt.get(field, [])
                require(any(b.get("role") == "evaluator" and b.get("sha256") == self.contract["evaluator_sha256"]
                            for b in bindings), "receipt evaluator differs")
                require(any(b.get("role") == "data" and b.get("sha256") == self.contract["data_slice_sha256"]
                            for b in bindings), "receipt data differs")
            require(receipt.get("protocol", {}).get("data_split") == protocol_slice(self.contract), "receipt slice differs")
            artifacts = [a for a in receipt.get("artifacts", []) if a.get("kind") == "project_output"]
            require(len(artifacts) == 1, "one recorded metric output required")
            artifact = artifacts[0]
            path = self.path(artifact["path"])
            require(sha(path) == artifact["sha256"], "metric artifact hash mismatch")
            output = read_json(path)
            value = output.get(self.contract["metric"]) if isinstance(output, dict) else None
            return value if finite(value) else None
        except (ValueError, OSError, KeyError, TypeError):
            return None

    def pause(self, reason):
        if not self.state_path("pause.json").exists():
            self.write("pause.json", {"reason": reason}, once=True)

    def mapping_consumed(self, run_id, kind, snapshot):
        if not isinstance(run_id, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,79}", run_id):
            return False  # The normal manifest validator reports an invalid mapping.
        relative = ("screen/" if kind == "screen" else "manifests/") + run_id + ".json"
        return (self.state_path(relative).exists()
                or any(run.get("id") == run_id for run in snapshot.get("runs", []))
                or any(receipt.get("run_id") == run_id for receipt in snapshot.get("receipts", [])))

    def eligible_proposal(self, state, snapshot):
        quota = read_json(self.state_path("quota.json"))
        require(isinstance(quota, dict) and all(type(v) is int and 0 <= v <= 2 for v in quota.values()), "invalid proposer quota")
        dead = set(self.tokens("dead.txt"))
        for line in self.state_path("inbox.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                row = decode(line)
                require(isinstance(row, dict) and set(row) == {"proposal_id", "proposer_id", "kind", "factor", "parent_contract_id", "ts"},
                        "invalid proposal schema")
                require(str(uuid.UUID(row["proposal_id"])) == row["proposal_id"], "invalid proposal UUID")
                require(isinstance(row["proposer_id"], str) and row["proposer_id"], "missing proposer")
                require(row["kind"] == "factor" and isinstance(row["factor"], str) and TOKEN.fullmatch(row["factor"]), "invalid factor")
                require(row["parent_contract_id"] == self.contract["contract_id"], "proposal contract differs")
                require(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:\d\d)", row["ts"]), "invalid RFC3339")
                datetime.fromisoformat(row["ts"].replace("Z", "+00:00"))
                if (row["factor"] not in dead and row["proposal_id"] not in state["screened"]
                        and row["factor"] not in state["used"] and quota.get(row["proposer_id"], 1) > 0):
                    template = self.mapping.get("routes", {}).get(row["factor"], {}).get("screen")
                    if isinstance(template, dict) and self.mapping_consumed(template.get("id"), "screen", snapshot):
                        continue  # Keep duplicate rows and their proposers' quota unchanged.
                    return row
            except (ValueError, TypeError, KeyError):
                continue  # Append-only inbox retains rejected original rows.
        return None

    def dispatch(self, state, manifest, factor, kind, snapshot, proposal=None):
        relative = ("screen/" if kind == "screen" else "manifests/") + manifest["id"] + ".json"
        require(not self.mapping_consumed(manifest["id"], kind, snapshot), "execution mapping already consumed")
        state["pending"] = {"manifest": manifest, "factor": factor, "kind": kind,
                            "proposal": proposal, "previous_state": state["last_state"], "submitted": False}
        state["advance"] = False
        effects = []
        if kind == "main":
            # factors.txt is a pending queue; dead.txt and inbox.jsonl alone are append-only.
            effects.append(self.effect("factors.txt", "".join(f + "\n" for f in self.tokens("factors.txt") if f != factor)))
            state["used"].append(factor)
        self.commit(state, effects)
        self.write(relative, manifest, once=True)
        self.project("create", "--manifest", str(self.state_path(relative)))
        state["pending"]["submitted"] = True
        self.write("state.json", state)  # Ambiguous execution never causes an automatic resend.
        self.project("execute", "--id", manifest["id"])
        return 0

    def next_factor(self, state, snapshot):
        dead = set(self.tokens("dead.txt"))
        factor = next((f for f in self.tokens("factors.txt") if f not in dead), None)
        remaining = self.remaining_ms(snapshot)
        if factor is not None:
            manifest = self.manifest(factor, "main")
            if remaining < math.ceil(manifest["resource_estimates"]["wall_seconds"] * 1000):
                self.pause("insufficient wall for next reservation")
                return 0
            return self.dispatch(state, manifest, factor, "main", snapshot)
        row = self.eligible_proposal(state, snapshot)
        if row is None:
            self.pause("no live factor or eligible proposal")
            return 0
        budget = min(self.contract["screen_wall_ms"], remaining // 10)
        if budget <= 0:
            self.pause("no screen budget")
            return 0
        manifest = self.manifest(row["factor"], "screen", budget)
        return self.dispatch(state, manifest, row["factor"], "screen", snapshot, row)

    def tick(self):
        if self.state_path("pause.json").exists():
            return 0
        with self.lock() as acquired:
            if not acquired or self.state_path("pause.json").exists():
                return 0
            snapshot = self.project("status")
            if any(run.get("status") in {"RUNNING", "STARTING", "COLLECTING"} for run in snapshot.get("runs", [])):
                return 0
            self.setup(snapshot.get("contract"))
            state = read_json(self.state_path("state.json"))
            self.finish_commit(state)
            pending = state["pending"]
            receipts = {r["run_id"]: r for r in snapshot.get("receipts", [])}
            require(all(self.mapping[k]["id"] in receipts for k in ("control", "initial")), "missing tick-0 paired receipts", 4)
            if pending:
                execute_id, factor = pending["manifest"]["id"], pending["factor"]
                require(execute_id in receipts, "pending execution has no receipt; inspect original project status", 4)
                treatment = receipts[execute_id]
                expected = pending["manifest"]
            else:
                pairs = [r for r in receipts.values() if r.get("arm") == "treatment"]
                require(pairs, "missing treatment receipt", 4)
                treatment = max(pairs, key=lambda r: (r.get("ended_at", 0), r["run_id"]))
                execute_id = treatment["run_id"]
                templates = [("initial", self.mapping["initial"])] + [(kind, t) for route in self.mapping["routes"].values() for kind, t in route.items()]
                matches = [(kind, t["factor"]) for kind, t in templates if t["id"] == execute_id]
                require(len(matches) == 1, "receipt has no unique frozen factor mapping")
                kind, factor = matches[0]
                expected = (read_json(self.state_path("screen/" + execute_id + ".json"))
                            if kind == "screen" else self.manifest(factor, kind))
            control = receipts.get(treatment.get("control_id"))
            require(control is not None, "missing paired control receipt", 4)
            control_manifest = self.manifest(self.mapping["control"]["factor"], "control")
            cell = classify(execute_id, factor, self.metric(control, control_manifest), self.metric(treatment, expected),
                            self.contract["threshold"], self.direction)
            cell_path = self.state_path("cells/" + execute_id + ".json")
            if cell_path.exists():
                require(read_json(cell_path) == cell, "retained cell evidence changed", 3)
            else:
                self.write("cells/" + execute_id + ".json", cell, once=True)
            state["ticks"] += 1
            self.write("state.json", state)
            if cell["state"] == "UNKNOWN":
                return 2
            if treatment["sha256"] in state["seen"]:
                return self.next_factor(state, snapshot) if state["advance"] else 0
            previous = state["last_state"]
            changed = previous != cell["state"]
            zero = cell["metric_treatment"] == cell["metric_control"]
            transition = canonical({"execute_id": execute_id, "receipt_sha256": treatment["sha256"],
                        "previous": previous, "state": cell["state"], "changed": changed, "zero_delta": zero,
                        "kind": pending["kind"] if pending else "initial"}) + "\n"
            effects = [self.effect("transitions.jsonl", self.state_path("transitions.jsonl").read_text(encoding="utf-8") + transition, append=True)]
            state.update(last_state=cell["state"], last_execute_id=execute_id, pending=None, advance=changed or zero)
            state["seen"].append(treatment["sha256"])
            if pending and pending["kind"] == "screen":
                row = pending["proposal"]
                quota = read_json(self.state_path("quota.json"))
                old = quota.get(row["proposer_id"], 1)
                quota[row["proposer_id"]] = min(2, old + 1) if changed else max(0, old // 2)
                effects.append(self.effect("quota.json", canonical(quota) + "\n"))
                state["screened"].append(row["proposal_id"])
                if changed and factor not in self.tokens("factors.txt"):
                    effects.append(self.effect("factors.txt", self.state_path("factors.txt").read_text(encoding="utf-8") + factor + "\n", append=True))
                state["advance"] = True  # Consume this screen before the next screen/main dispatch.
            if zero and factor not in self.tokens("dead.txt"):
                effects.append(self.effect("dead.txt", self.state_path("dead.txt").read_text(encoding="utf-8") + factor + "\n", append=True))
            self.commit(state, effects)
            if zero or pending and pending["kind"] == "screen":
                return 0
            if changed or not any(f not in self.tokens("dead.txt") for f in self.tokens("factors.txt")):
                return self.next_factor(state, snapshot)
            return 0

    def proposal_gate(self):
        require(not self.state_path("pause.json").exists(), "wheel is paused")
        snapshot = self.project("status")
        self.setup(snapshot.get("contract"))
        state = read_json(self.state_path("state.json"))
        self.finish_commit(state)
        require(state["ticks"] >= 2 and state["last_execute_id"] and not state["pending"], "proposal handoff is not ready")
        require(not any(f not in self.tokens("dead.txt") for f in self.tokens("factors.txt")), "live factors remain")
        return state

    def prompt(self):
        with self.lock() as acquired:
            require(acquired, "wheel writer is busy")
            state = self.proposal_gate()
            return (self.state_path("dead.txt").read_bytes()
                    + self.state_path("cells/" + state["last_execute_id"] + ".json").read_bytes())

    def propose(self, proposer_id, token):
        with self.lock() as acquired:
            require(acquired, "wheel writer is busy")
            self.proposal_gate()
            if token.endswith("\r\n"):
                token = token[:-2]
            elif token.endswith("\n"):
                token = token[:-1]
            if not TOKEN.fullmatch(token) or token in self.tokens("dead.txt"):
                return False
            require(isinstance(proposer_id, str) and proposer_id, "proposer_id required")
            require(sha(self.path(self.mapping["evaluator_path"])) == self.contract["evaluator_sha256"], "evaluator changed", 3)
            self.append("inbox.jsonl", canonical({"proposal_id": str(uuid.uuid4()), "proposer_id": proposer_id,
                        "kind": "factor", "factor": token, "parent_contract_id": self.contract["contract_id"],
                        "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")}) + "\n")
            return True


def protocol_slice(contract):
    return "wheel-" + hashlib.sha256(contract["contract_id"].encode("utf-8")).hexdigest()[:16]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--initialize", metavar="PROJECT_CONTRACT")
    action.add_argument("--prompt", action="store_true")
    action.add_argument("--propose", metavar="PROPOSER_ID")
    args = parser.parse_args(argv)
    wheel = Wheel(args.root)
    try:
        if args.initialize:
            wheel.initialize(args.initialize)
            return 0
        if args.prompt:
            sys.stdout.buffer.write(wheel.prompt())
            return 0
        if args.propose:
            print(canonical({"accepted": wheel.propose(args.propose, sys.stdin.read(35))}))
            return 0
        return wheel.tick()
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as exc:
        print("[WHEEL] " + str(exc), file=sys.stderr)
        return exc.code if isinstance(exc, WheelError) else 2


if __name__ == "__main__":
    sys.exit(main())
