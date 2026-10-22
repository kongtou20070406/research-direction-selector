"""L2 pure, bounded export of supplied retained project evidence.

No storage discovery, live-state fallback, execution or scientific authority.
Receipt rows use the original pure codec. Checkpoint rows get byte/contract and
owned-pair checks only; callers still own original decision/lineage validation.
No reader is integrated while that validator depends on mixed store modules.
"""
from copy import deepcopy
import hashlib
import json
import math
import re

from rds_core import canonical, object_digest
from rds_project_records import decode_receipt

SCHEMA = "rds-transition-v1"
MAX_RECEIPTS = 128
MAX_CHECKPOINTS = 256
MAX_ROW_BYTES = 4_000_000
MAX_INPUT_BYTES = 16_000_000
MAX_OUTPUT_BYTES = 16_000_000
MAX_DEPTH = 32
TERMINAL = {"SUCCEEDED", "FAILED", "TIMED_OUT", "CANCELLED", "INTERRUPTED"}


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _hash(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _tree(value, depth=0):
    _require(depth <= MAX_DEPTH, "Transition JSON nesting exceeds 32")
    if isinstance(value, dict):
        for child in value.values():
            _tree(child, depth + 1)
    elif isinstance(value, list):
        for child in value:
            _tree(child, depth + 1)
    elif isinstance(value, float):
        _require(math.isfinite(value), "Transition JSON contains a non-finite number")


def _pairs(items):
    result = {}
    for key, value in items:
        _require(key not in result, "Transition checkpoint repeats a JSON key")
        result[key] = value
    return result


def _rows(rows, maximum, max_row_bytes, total):
    _require(isinstance(rows, (list, tuple)) and len(rows) <= maximum,
             "Transition rows exceed bounded list limit")
    for row in rows:
        _require(isinstance(row, dict) and isinstance(row.get("body"), str),
                 "Transition row needs an original text body")
        try:
            size = len(row["body"].encode("utf-8"))
        except UnicodeError as exc:
            raise ValueError("Transition row is not UTF-8 encodable") from exc
        _require(size <= max_row_bytes, "Transition max_row_bytes exceeded")
        total += size
        _require(total <= MAX_INPUT_BYTES, "Transition max_input_bytes exceeded")
    return total


def _checkpoint(row):
    raw, sha = row["body"], row.get("sha")
    _require(_hash(sha) and hashlib.sha256(raw.encode("utf-8")).hexdigest() == sha,
             "Transition checkpoint original hash mismatch")
    try:
        record = json.loads(raw, object_pairs_hook=_pairs)
        _tree(record)
        canonical(record).encode("utf-8")
    except (UnicodeError, RecursionError, TypeError) as exc:
        raise ValueError("Transition checkpoint is not bounded canonical JSON") from exc
    _require(isinstance(record, dict) and record.get("schema") == "rds-checkpoint-v1"
             and isinstance(row.get("id"), str) and record.get("id") == row["id"]
             and record.get("kind") in {"project", "reference"},
             "Transition checkpoint identity mismatch")
    _require(type(record.get("created_ns")) is int and record["created_ns"] > 0,
             "Transition checkpoint timestamp missing or invalid")
    snapshot = record.get("snapshot")
    _require(isinstance(snapshot, dict) and isinstance(snapshot.get("contract"), dict)
             and object_digest(snapshot["contract"]) == record.get("contract_sha256"),
             "Transition checkpoint contract hash mismatch")
    _require(isinstance(record.get("decision"), dict), "Transition checkpoint decision must be an object")
    return record


def _source(kind, identity, sha, locator):
    return {"kind": kind, "id": identity, "sha256": sha, "locator": locator}


def _unknown(reason):
    return {"status": "UNKNOWN", "reason": reason}


def _cell(value, source, *, scope=None):
    if value is None or value == "UNKNOWN":
        return _unknown("Retained field is unknown")
    result = {"status": "RECORDED", "value": deepcopy(value), "source": source}
    if scope is not None:
        result["scope"] = scope
    return result


def _field(record, key, source, *, scope=None):
    return (_cell(record[key], source, scope=scope) if key in record
            else _unknown("No retained " + key))


def _owned_id(stage, receipt):
    if not all(_hash(receipt.get(key)) for key in ("manifest_sha256", "effective_contract_sha256")):
        return None
    identity = {"run_id": receipt["run_id"], "manifest_sha256": receipt["manifest_sha256"],
                "contract_sha256": receipt["effective_contract_sha256"]}
    return "owned-" + stage + "-" + object_digest(identity)[:48]


def _paired(stage, receipt, checkpoints):
    checkpoint_id = _owned_id(stage, receipt)
    retained = checkpoints.get(checkpoint_id)
    if retained is None:
        return None
    record, sha = retained
    decision = record["decision"]
    identity = decision.get("owned_history")
    _require(record["kind"] == "project" and isinstance(identity, dict)
             and identity.get("run_id") == receipt["run_id"]
             and all(identity.get(key) == receipt[key] for key in
                     ("manifest_sha256", "effective_contract_sha256")),
             "Transition owned checkpoint binding mismatch")
    runs = record["snapshot"].get("runs")
    _require(isinstance(runs, list), "Transition owned checkpoint runs must be a retained list")
    matches = [run for run in runs if isinstance(run, dict) and run.get("id") == receipt["run_id"]]
    _require(len(matches) == 1, "Transition owned checkpoint needs one matching retained run")
    run = matches[0]
    _require(run.get("manifest_sha256") == receipt["manifest_sha256"],
             "Transition checkpoint run manifest mismatch")
    if stage == "before":
        _require(record["contract_sha256"] == receipt["effective_contract_sha256"],
                 "Transition before checkpoint effective contract mismatch")
        _require("execution" not in decision and run.get("status") == "RESERVED"
                 and run.get("attempt_id") is None, "Transition before checkpoint is not decision-time evidence")
        receipts = record["snapshot"].get("receipts", [])
        _require(isinstance(receipts, list) and not any(
            isinstance(item, dict) and item.get("run_id") == receipt["run_id"] for item in receipts),
            "Transition before checkpoint already contains this outcome")
    else:
        expected = {"run_id": receipt["run_id"], "attempt_id": receipt["attempt_id"],
                    "receipt_sha256": receipt["sha256"], "run_status": receipt["run_status"]}
        _require(decision.get("execution") == expected
                 and decision.get("previous_checkpoint") == _owned_id("before", receipt)
                 and run.get("attempt_id") == receipt["attempt_id"]
                 and run.get("status") == receipt.get("process_status"),
                 "Transition after checkpoint receipt binding mismatch")
    return record, sha


def _transition(receipt, checkpoints):
    run_id, sha = receipt["run_id"], receipt["sha256"]
    receipt_source = lambda pointer: _source("receipt", run_id, sha, pointer)
    before, after = (_paired(stage, receipt, checkpoints) for stage in ("before", "after"))
    if before and after:
        _require(before[0]["created_ns"] < after[0]["created_ns"],
                 "Transition checkpoint order conflicts")
    result = {"run_id": run_id, "attempt_id": receipt["attempt_id"], "receipt_sha256": sha,
              "run_status": receipt["run_status"], "scientific_support": "UNKNOWN",
              "observation": _cell(receipt, receipt_source(""), scope="ORIGINAL_RECEIPT"),
              "prior_prediction": _unknown("No retained decision-time prediction"),
              "decision": _unknown("No retained before decision"),
              "provenance": {"policy": _unknown("No retained decision-time policy"),
                             "tool": _unknown("No retained tool provenance"),
                             "model": _unknown("No retained decision-time model provenance")}}
    for stage, retained in (("before", before), ("after", after)):
        if retained:
            record, checkpoint_sha = retained
            source = _source("checkpoint", record["id"], checkpoint_sha, "/snapshot")
            result[stage] = _cell(record["snapshot"], source, scope="RETAINED_CHECKPOINT")
        else:
            key = "bindings_" + stage
            result[stage] = _field(receipt, key, receipt_source("/" + key), scope="BINDINGS_ONLY")
    result["action"] = _field(receipt, "argv", receipt_source("/argv"), scope="EXECUTED_COMMAND")
    if before:
        record, checkpoint_sha = before
        decision = record["decision"]
        source = lambda pointer: _source("checkpoint", record["id"], checkpoint_sha, pointer)
        result["decision"] = _cell(decision, source("/decision"), scope="DECISION_TIME")
        candidate = decision.get("candidate", {})
        if isinstance(candidate, dict) and "action" in candidate:
            result["action"] = _cell(candidate["action"], source("/decision/candidate/action"), scope="SELECTED_ACTION")
        # Multiple retained forecast fields stay separate rather than selecting a favourable one.
        predictions = {key: _field(decision, key, source("/decision/" + key))
                       for key in ("prior_prediction", "prediction", "forecast") if key in decision}
        if isinstance(candidate, dict):
            for key in ("prior_prediction", "prediction", "forecast"):
                if key in candidate:
                    predictions["candidate/" + key] = _field(candidate, key, source("/decision/candidate/" + key))
        if predictions:
            result["prior_prediction"] = _cell(predictions, source("/decision"), scope="DECISION_TIME_FIELDS")
        contract = record["snapshot"]["contract"]
        result["provenance"]["policy"] = _field(contract, "advisor_policy", source("/snapshot/contract/advisor_policy"))
        result["provenance"]["model"] = _field(decision, "model_provenance", source("/decision/model_provenance"))
    tool = {key: _field(receipt, key, receipt_source("/" + key)) for key in
            ("executor_sha256", "runtime_fingerprint", "tool_provenance", "protocol", "argv", "cwd") if key in receipt}
    if tool:
        result["provenance"]["tool"] = _cell(tool, receipt_source(""), scope="RETAINED_FIELDS")
    costs = {key: _field(receipt, key, receipt_source("/" + key)) for key in
             ("resources", "charged_allocation", "elapsed_ms")}
    result["costs"] = costs
    return result


def export_transitions(receipt_rows, checkpoint_rows=(), *, max_row_bytes=MAX_ROW_BYTES,
                       max_output_bytes=MAX_OUTPUT_BYTES):
    """Return a deterministic JSON value, with no file/DB operations.

    Supply original receipt {run_id, sha256, body} and checkpoint {id, sha, body}
    rows selected in one caller-owned coherent read. Missing owned checkpoints
    are unknown; only explicit receipt bindings can provide a narrower state.
    Bounds reject the whole operation, never silently truncate or omit failures.
    This is a projection, not an original checkpoint admission/lineage audit.
    """
    for key, value, hard_limit in (("max_row_bytes", max_row_bytes, MAX_ROW_BYTES),
                                   ("max_output_bytes", max_output_bytes, MAX_OUTPUT_BYTES)):
        _require(type(value) is int and 0 < value <= hard_limit, "Transition invalid " + key)
    total = _rows(receipt_rows, MAX_RECEIPTS, max_row_bytes, 0)
    _rows(checkpoint_rows, MAX_CHECKPOINTS, max_row_bytes, total)
    checkpoints = {}
    for row in checkpoint_rows:
        record = _checkpoint(row)
        _require(record["id"] not in checkpoints, "Transition duplicate checkpoint identity")
        checkpoints[record["id"]] = record, row["sha"]
    receipts = {}
    for row in receipt_rows:
        _require(isinstance(row.get("run_id"), str) and bool(row["run_id"])
                 and _hash(row.get("sha256")), "Transition receipt row identity missing")
        try:
            receipt = decode_receipt(row, ValueError, object_digest)
            _tree(receipt)
        except (UnicodeError, RecursionError, TypeError) as exc:
            raise ValueError("Transition receipt is not bounded canonical JSON") from exc
        _require(isinstance(receipt.get("attempt_id"), str) and bool(receipt["attempt_id"])
                 and receipt.get("run_status") in TERMINAL, "Transition needs a retained terminal attempt")
        _require(receipt["run_id"] not in receipts, "Transition duplicate receipt run identity")
        receipts[receipt["run_id"]] = receipt
    result = {"schema": SCHEMA, "status": "EXPORTED" if receipts else "UNKNOWN",
              "authority": "READ_ONLY_PROJECTION", "scientific_support": "UNKNOWN",
              "checkpoint_validation": "HASH_AND_OWNED_BINDING_ONLY",
              "transitions": [_transition(receipts[key], checkpoints) for key in sorted(receipts)]}
    try:
        _require(len(canonical(result).encode("utf-8")) <= max_output_bytes,
                 "Transition max_output_bytes exceeded")
    except (RecursionError, UnicodeError) as exc:
        raise ValueError("Transition output is not bounded canonical JSON") from exc
    return result


def transition_json(export):
    """Canonical UTF-8 text; caller owns any output IO."""
    _require(isinstance(export, dict) and export.get("schema") == SCHEMA,
             "Transition export schema mismatch")
    try:
        raw = canonical(export)
        _require(len(raw.encode("utf-8")) <= MAX_OUTPUT_BYTES, "Transition max_output_bytes exceeded")
    except (RecursionError, UnicodeError, TypeError) as exc:
        raise ValueError("Transition output is not bounded canonical JSON") from exc
    return raw
