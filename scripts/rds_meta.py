"""Meta-Reflection and Judgment Graph Evolution module for RDS (RSI Step 1).

Enables the agent to synthesize, validate, and apply scoped decision rules
derived from experimental falsifications, empirical stagnations, and Obelisk causal traces.
Zero external runtime dependencies; supports PyYAML if installed, with a standard-library fallback.
"""
import json
import os
from pathlib import Path
import re
import shlex
import sys
import time
from contextlib import contextmanager
from copy import deepcopy
from uuid import uuid4

from rds_core import (FORBIDDEN_WORDS, REQUIRED_RULE_KEYS, identity, json_digest,
                      require, validate_rule)
from rds_workspace import (adoption_lock, atomic_bytes, graph_lock, load_object,
                           publish_bytes, workspace_publication)

digest = json_digest

try:
    import yaml
except ImportError:
    yaml = None


def default_graph_path(root_dir=None):
    base = Path(root_dir).resolve() if root_dir else Path(__file__).resolve().parents[1]
    candidates = [
        base / "references/judgment-graph.yaml",
        base / "judgment-graph.yaml",
        Path(__file__).resolve().parents[1] / "references/judgment-graph.yaml"
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def _parse_val(val):
    val = val.strip()
    try:
        return json.loads(val)
    except ValueError:
        pass
    if val.startswith("[") and val.endswith("]"):
        inner = val[1:-1].strip()
        if not inner:
            return []
        lexer = shlex.shlex(inner, posix=True)
        lexer.whitespace = ","
        lexer.whitespace_split = True
        lexer.commenters = ""
        return [item.strip() for item in lexer]
    if val.startswith("{") and val.endswith("}"):
        return {k.strip(): _parse_val(v) for k, v in
                (item.split(":", 1) for item in val[1:-1].split(","))}
    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
        return val[1:-1]
    return val


def parse_simple_yaml(raw):
    """Parse the graph's scalar/flow-list/block-list subset, including edges."""
    lines = raw.splitlines()
    data = {"schema": 1, "nodes": []}
    current_node = None
    current_key = None
    section = None
    
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if line.startswith("schema:"):
            try:
                data["schema"] = int(stripped.split(":", 1)[1].strip())
            except ValueError:
                data["schema"] = 1
            continue
        if line.startswith("nodes:") or line.startswith("edges:"):
            section, value = stripped.split(":", 1)
            data[section] = _parse_val(value) if value.strip() else []
            require(isinstance(data[section], list), f"'{section}' must be a list")
            current_node = None
            current_key = None
            continue
            
        if line.startswith("  - ") or line.startswith("  - id:"):
            require(section in ("nodes", "edges"), "Graph item outside nodes/edges")
            current_node = {}
            current_key = None
            content = line[4:].strip()
            if content.startswith("{"):
                current_node = _parse_val(content)
            elif ":" in content:
                k, v = content.split(":", 1)
                k = k.strip()
                v = v.strip()
                current_key = k
                current_node[k] = _parse_val(v) if v else []
            data[section].append(current_node)
            continue
            
        if current_node is not None:
            if ":" in stripped and not stripped.startswith("- "):
                k, v = stripped.split(":", 1)
                k = k.strip()
                v = v.strip()
                current_key = k
                if v:
                    current_node[k] = _parse_val(v)
                else:
                    current_node[k] = []
            elif stripped.startswith("- "):
                item = _parse_val(stripped[2:])
                if current_key:
                    if not isinstance(current_node.get(current_key), list):
                        current_node[current_key] = []
                    current_node[current_key].append(item)
                    
    return data


def dump_simple_yaml(data):
    """Pure standard-library dumper for judgment-graph.yaml structure."""
    lines = ["# Judgment decision rules", f"schema: {data.get('schema', 1)}",
             "nodes:" if data.get("nodes") else "nodes: []"]
    for node in data.get("nodes", []):
        first = True
        for k, v in node.items():
            prefix = "  - " if first else "    "
            first = False
            if isinstance(v, list):
                items_str = ", ".join(json.dumps(x, ensure_ascii=False) for x in v)
                lines.append(f"{prefix}{k}: [{items_str}]")
            else:
                lines.append(f"{prefix}{k}: {json.dumps(v, ensure_ascii=False)}")
    if "edges" in data:
        lines.append("edges:" if data["edges"] else "edges: []")
        lines.extend("  - " + json.dumps(edge, ensure_ascii=False) for edge in data["edges"])
    return "\n".join(lines) + "\n"


def load_judgment_graph(graph_path=None):
    path = Path(graph_path).resolve() if graph_path else default_graph_path()
    require(path.exists(), f"Judgment graph file not found: {path}")
    raw = path.read_text(encoding="utf-8-sig")
    return path, _parse_graph(raw)


def _parse_graph(raw):
    if yaml is not None:
        try:
            data = yaml.safe_load(raw)
        except Exception:
            data = parse_simple_yaml(raw)
    else:
        try:
            data = json.loads(raw)
        except Exception:
            data = parse_simple_yaml(raw)
    require(isinstance(data, dict) and "nodes" in data, "Judgment graph must have a top-level 'nodes' list")
    return data


def save_judgment_graph(path, data):
    path = Path(path).resolve()
    # Keep saved graphs readable when optional PyYAML is later unavailable.
    content = dump_simple_yaml(data)
    _atomic_bytes(path, content.encode("utf-8"))


# Publication primitives moved to rds_workspace (L1); the names stay so the RSI
# layer keeps its original call sites and semantics.
_workspace_publication = workspace_publication
_atomic_bytes = atomic_bytes
_load_object = load_object
_graph_lock = graph_lock
_adoption_lock = adoption_lock


def _records_dir(path, record_dir):
    base = path.parent.parent if path.parent.name == "references" else path.parent
    return Path(record_dir).resolve() if record_dir else base / ".rds" / "rsi"


def _write_record(directory, record, path=None):
    record = deepcopy(record)
    record.pop("record_sha256", None)
    record["record_sha256"] = digest(record)
    path = Path(path) if path else directory / "records" / f"{time.time_ns()}-{uuid4().hex[:8]}.json"
    _atomic_bytes(path, json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False,
                                indent=2).encode("utf-8"))
    return path


def apply_rule(rule_dict, graph_path=None, force=False, dry_run=False, *,
               evaluation=None, cases=None, record_dir=None):
    """Adopt only after independent CPU replay; force never bypasses this gate."""
    from rds_rsi import evaluate_candidate
    path = Path(graph_path).resolve() if graph_path else default_graph_path()
    original = path.read_bytes()
    graph = _parse_graph(original.decode("utf-8-sig"))
    nodes = graph.get("nodes", [])
    existing_map = {n["id"]: idx for idx, n in enumerate(nodes)}
    validate_rule(rule_dict, existing_ids=set(existing_map), allow_update=force)
    rid = rule_dict["id"]
    updated = rid in existing_map
    rule_sha = digest(rule_dict)
    result = {"status": "VALIDATED_ONLY", "action": "UPDATED" if updated else "CREATED",
              "rule_id": rid, "rule_sha256": rule_sha, "graph_path": str(path),
              "total_rules": len(nodes) + int(not updated), "adopted": False,
              "research_policy_gain_measured": False}
    if dry_run and evaluation is None and cases is None:
        result["assurance"] = "SCHEMA_ONLY"
        return result
    directory = _records_dir(path, record_dir)
    _atomic_bytes(directory / "candidates" / f"{rule_sha}.json",
                  json.dumps(rule_dict, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8"))
    record = {"status": "REJECTED", "rule_id": rid, "candidate_sha256": rule_sha,
              "graph_path": str(path), "before_raw_sha256": digest(original),
              "base_graph_sha256": digest(graph), "research_policy_gain_measured": False}
    try:
        supplied = _load_object(evaluation, "Evaluation report")
        pack = _load_object(cases, "Original casepack")
        # Do not trust report flags: execute both variants again and bind outputs.
        replay = evaluate_candidate(rule_dict, graph, pack, confirmation_dir=directory)
        require(json.dumps({k: v for k, v in supplied.items() if k != "elapsed_ms"}, sort_keys=True, allow_nan=False) ==
                json.dumps({k: v for k, v in replay.items() if k != "elapsed_ms"}, sort_keys=True, allow_nan=False),
                "Evaluation report differs from independently executed replay")
        require(supplied.get("bindings") == replay.get("bindings"), "Evaluation bindings changed or are absent")
        require(supplied.get("replay_sha256") == replay.get("replay_sha256") and replay.get("replay_sha256"),
                "Evaluation outputs were rewritten or do not match independent replay")
        require(supplied.get("status") == replay["status"] == "ACCEPTABLE_REGRESSION_CHANGE"
                and supplied.get("adoption_eligible") is True and replay["adoption_eligible"] is True,
                "Independent case replay rejects adoption: " + "; ".join(replay["rejection_reasons"]))
        require(supplied.get("cases_evaluated") == replay["cases_evaluated"] > 0,
                "Evaluation case counts do not match actual replay")
        record.update(bindings=replay["bindings"], replay_sha256=replay["replay_sha256"],
                      cases_evaluated=replay["cases_evaluated"], counts=replay["counts"])
        result.update(assurance="CPU_CASE_REPLAY", evaluation=replay)
        if dry_run:
            record.update(status="VALIDATED_ONLY", reason="Dry run; no adoption")
            result["record_path"] = str(_write_record(directory, record))
            return result
        if updated:
            nodes[existing_map[rid]] = deepcopy(rule_dict)
        else:
            nodes.append(deepcopy(rule_dict))
        new_bytes = dump_simple_yaml(graph).encode("utf-8")
        backup = directory / "backups" / f"{digest(original)}.graph"
        with _graph_lock(directory):
            require(path.read_bytes() == original, "Graph changed during evaluation; rerun against the current graph")
            _atomic_bytes(backup, original)
            _atomic_bytes(directory / "casepacks" / f"{digest(pack)}.json",
                          json.dumps(pack, sort_keys=True, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8"))
            record.update(status="PREPARED_ADOPTION", backup_path=str(backup),
                          after_raw_sha256=digest(new_bytes), candidate_graph_sha256=digest(graph))
            record_path = _write_record(directory, record)
            _atomic_bytes(path, new_bytes)
            try:
                record["status"] = "ADOPTED"
                _write_record(directory, record, record_path)
            except Exception:
                _atomic_bytes(path, original)
                raise
        result.update(status="APPLIED", adopted=True, record_path=str(record_path),
                      backup_path=str(backup), total_rules=len(nodes))
        return result
    except (ValueError, TypeError, KeyError, OSError) as exc:
        record["status"] = "REJECTED"
        record["reason"] = str(exc)
        rejected = _write_record(directory, record)
        raise ValueError(f"{exc}; rejection record: {rejected}") from exc


def rollback_rule(record_path, graph_path=None, dry_run=False, *, record_dir=None):
    """Restore the complete original bytes, refusing to overwrite newer edits."""
    record = _load_object(record_path, "Adoption record")
    signature = record.pop("record_sha256", None)
    require(signature == digest(record), "Adoption record was changed")
    require(record.get("status") in {"ADOPTED", "PREPARED_ADOPTION"}, "Record is not an adoption")
    path = Path(graph_path or record["graph_path"]).resolve()
    require(str(path) == record["graph_path"], "Rollback graph does not match adoption record")
    original = Path(record["backup_path"]).read_bytes()
    require(digest(original) == record["before_raw_sha256"], "Original graph backup was changed")
    require(digest(path.read_bytes()) == record["after_raw_sha256"], "Graph has newer edits; rollback would overwrite them")
    result = {"status": "VALIDATED_ONLY" if dry_run else "ROLLED_BACK", "graph_path": str(path),
              "restored_raw_sha256": digest(original), "adoption_record": str(Path(record_path).resolve())}
    if not dry_run:
        directory = _records_dir(path, record_dir)
        with _workspace_publication(record_path), _graph_lock(directory):
            current = path.read_bytes()
            require(digest(current) == record["after_raw_sha256"], "Graph changed before rollback")
            rollback = {"status": "PREPARED_ROLLBACK", "adoption_record": result["adoption_record"],
                        "adoption_record_sha256": signature, "graph_path": str(path),
                        "restored_raw_sha256": digest(original)}
            result["record_path"] = str(_write_record(directory, rollback))
            _atomic_bytes(path, original)
            try:
                rollback["status"] = "ROLLED_BACK"
                _write_record(directory, rollback, result["record_path"])
            except Exception:
                _atomic_bytes(path, current)
                raise
    return result


def reflect_from_state(state, receipts, obelisk_evidence=None):
    """Synthesizes candidate rules from empirical failures, refutations, or stagnations."""
    proposals = []
    # Execution diagnostics are scoped to registered run identities, not science.
    runs = state.get("runs", {})
    if isinstance(runs, list):
        runs = {r.get("run_id", r.get("id")): r for r in runs if isinstance(r, dict)}
    if not isinstance(runs, dict):
        runs = {}
    failures = []
    for receipt in receipts:
        if not isinstance(receipt, dict) or receipt.get("run_status") not in {"FAILED", "INTERRUPTED"}:
            continue
        rid, receipt_id = receipt.get("run_id"), receipt.get("sha256")
        run = runs.get(rid)
        if not isinstance(rid, str) or not rid or not isinstance(receipt_id, str) or not receipt_id or not isinstance(run, dict):
            continue
        if run.get("attempt_id") and run["attempt_id"] != receipt.get("attempt_id"):
            continue
        errors = receipt.get("errors", [])
        if not isinstance(errors, list) or not all(isinstance(error, str) for error in errors):
            continue
        if not errors and receipt["run_status"] != "INTERRUPTED":
            continue
        failures.append((receipt, errors))
    for receipt, errors in failures[:16]:
        rid = receipt["run_id"]
        proposals.append({
            "id": "execution-review-" + identity(rid),
            "scope": "execution-protocol-review",
            "trigger": f"Registered run {rid} has a {receipt['run_status']} receipt",
            "correction": "Inspect this attempt's execution error and protocol before interpreting task or mechanism outcomes",
            "alternatives": ["runtime or dependency failure", "invalid protocol, binding, or missing output"],
            "discriminator": "Read the existing stderr, stdout, manifest and receipt for this run and attempt; locate the failing check",
            "primary_gate": "Any authorized repair must complete the execution checks; successful execution alone does not establish research gain",
            "falsifier": "Reject this diagnostic if the receipt or log belongs to a different registered run or attempt",
            "sources": [f"Project receipt run_id={rid} attempt_id={receipt.get('attempt_id')} sha256={receipt['sha256']}", *errors[:8]],
            "source_receipt": {"run_id": rid, "attempt_id": receipt.get("attempt_id"), "sha256": receipt["sha256"]},
            "candidate_only": True, "auto_apply": False, "research_policy_gain_measured": False,
            "reflection_limits": {"failed_candidates": 16, "truncated": len(failures) > 16}
        })
    
    # 1. Check for mechanism refutations or failed manipulation gates
    for hid, node in state.get("hypotheses", {}).items():
        mech = node.get("mechanism")
        if mech == "REFUTED":
            spec = node.get("spec", {})
            proposals.append({
                "id": f"refuted-boundary-{hid.lower()}",
                "scope": state.get("contract", {}).get("project_id", "empirical-audit"),
                "trigger": f"Claimed scalar necessity boundary in hypothesis '{hid}' is violated by executed counterexample",
                "correction": "Crossing a hypothesized bound with loss <= max_loss empirically refutes the necessity model",
                "alternatives": ["true mechanism requirement", "falsified necessity model"],
                "discriminator": "Run exact rational symbolic probe and check dataset crossings before asserting necessity",
                "primary_gate": "Formal boundary test must observe no necessity counterexamples under matched budget",
                "falsifier": "If counterexample is shown to lie outside the declared operational domain, revise scope",
                "sources": [f"RDS Run Refutation for hypothesis {hid}", "RDS-L3 State Machine Engine"]
            })
        elif mech == "NOT_TESTED":
            proposals.append({
                "id": f"unexecuted-crossing-{hid.lower()}",
                "scope": state.get("contract", {}).get("project_id", "empirical-audit"),
                "trigger": f"Hypothesis '{hid}' declared a boundary crossing but treatment failed to reach or execute it",
                "correction": "A treatment that stays on the same side of a threshold property as the control cannot decide that property",
                "alternatives": ["unresponsive intervention", "miscalculated domain bounds"],
                "discriminator": "Inspect executed treatment range against declared threshold before running full dataset",
                "primary_gate": "Executed treatment must physically cross declared threshold with bounded loss",
                "falsifier": "If control and treatment are shown to have distinct unmodeled mechanisms, redesign probe",
                "sources": [f"RDS Unexecuted Crossing Diagnostic for hypothesis {hid}"]
            })
    
    # 2. Check for repeated INCONCLUSIVE runs (parameter tuning without causal separation)
    inconclusive_count = 0
    for r in receipts:
        plan = state.get("plans", {}).get(r.get("plan_id"), {})
        assessment = plan.get("assessment") or {}
        if (r.get("run_id") and r.get("run_status") == "SUCCEEDED"
                and assessment.get("run_id") == r.get("run_id")
                and assessment.get("task_gain") == "INCONCLUSIVE"):
            inconclusive_count += 1
            
    if inconclusive_count >= 2:
        proposals.append({
            "id": "parameter-tuning-without-causal-gain",
            "scope": state.get("contract", {}).get("project_id", "exploration"),
            "trigger": "At least two assessed runs fail to exceed the predeclared minimum useful delta",
            "correction": "Repeated sub-threshold results motivate review; they do not establish a causal failure or rule out parameter tuning",
            "alternatives": ["subtle quantitative tuning gain", "stagnation requiring orthogonal mechanism branch"],
            "discriminator": "Inspect per-run assessments and branch stagnation counters before comparing alternative interventions",
            "primary_gate": "Predeclared minimum useful delta must be exceeded on held-out development set",
            "falsifier": "If micro-tuning independently demonstrates reproducible large primary metric leap, retain parameter search",
            "sources": ["FML-Bench v2 Stagnation Evidence", "RDS State Ledger"]
        })
    
    # 3. Incorporate Obelisk evidence if provided
    if obelisk_evidence and isinstance(obelisk_evidence, dict):
        terms = obelisk_evidence.get("terms", "general")
        proposals.append({
            "id": f"obelisk-prior-{identity(terms[:20])}",
            "scope": obelisk_evidence.get("scope", "cross-session"),
            "trigger": f"Historical prior identified in past Obelisk sessions for terms: {terms}",
            "correction": "Incorporate documented historical falsifiers before committing new GPU training allocations",
            "alternatives": ["rediscovering known failure", "verifying changed conditions"],
            "discriminator": "Query Obelisk CodeAct sandbox before finalizing experiment contract",
            "primary_gate": "Prior session evidence reviewed and logged in decision contract",
            "falsifier": "If changed context invalidates the historical falsifier, state explicit discriminating condition",
            "sources": ["Obelisk Historical Causal Graph"]
        })
        
    return proposals
