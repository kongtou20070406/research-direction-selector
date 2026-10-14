"""Bounded validation of recorded checkpoint decisions, without Advisor policy."""
import hashlib
import json
from pathlib import Path
import re

from rds_artifacts import strict_json

MAX_LOOP_BYTES = 256 * 1024
CHECKPOINT_ROUTE_FIELDS = ("goal_revision", "scope", "candidate", "outcome", "evidence")


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 512


def _json(value, cap=MAX_LOOP_BYTES):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
        size = len(raw.encode("utf-8"))
        _require(size <= cap, f"Advisor loop JSON exceeds byte limit ({size} > {cap}); use a scoped context and shared manifest digest/source locator while retaining full originals. For repeated code file-hash maps: scripts/rds_context_compact.py --input CONTEXT --output-dir NEW_DIRECTORY")
        strict_json(raw)
        return raw
    except (TypeError, RecursionError, UnicodeError, OverflowError) as exc:
        raise ValueError("Advisor loop input must be finite, bounded JSON") from exc


def _scope(value):
    _require(isinstance(value, dict), f"scope/parameters must be a JSON object (dict); got {type(value).__name__}")
    _require(len(value) <= 16, f"scope/parameters has {len(value)} fields; at most 16 are allowed")
    for key, child in value.items():
        _require(_text(key), "scope/parameters field names must be non-empty strings of at most 512 characters")
        if child is not None and type(child) not in (str, int, float, bool):
            field = json.dumps(key[:80], ensure_ascii=True) + ("..." if len(key) > 80 else "")
            kind = "array" if isinstance(child, list) else "object" if isinstance(child, dict) else None
            actual = f"{kind} ({type(child).__name__})" if kind else type(child).__name__
            raise ValueError(f"scope/parameters field {field} must be a JSON atom; got {actual}. "
                "Keep the structured original input in a source file and reference it with an explicit source binding.")
    _json(value, 2048)


def _loop_route(candidate):
    """Exact structured route equality; display IDs never establish route identity."""
    if not isinstance(candidate, dict):
        return None
    action = candidate.get("action")
    if isinstance(action, dict) and isinstance(action.get("description"), str) and action["description"].strip():
        display = {"id", "goal_contribution"}
        if any(key in action for key in ("target", "intervention", "parameters", "operation")):
            display.update(("description", "question", "competing_explanations", "required_observables", "outcomes", "discrimination", "stop_condition"))
        route = {"action": {key: value for key, value in action.items() if key not in display}}
    elif isinstance(candidate.get("interventions"), list) and candidate["interventions"] and isinstance(candidate.get("control_bindings"), list):
        route = {"interventions": sorted(candidate["interventions"], key=_json),
                 "controls": sorted([{key: value for key, value in control.items() if key not in
                     ("source", "reason", "truth", "evidence_status")} for control in candidate["control_bindings"]], key=_json)}
    else:
        return None
    return hashlib.sha256(_json(route).encode("utf-8")).hexdigest()


def _loop_family(candidate):
    """Route identity without action parameters: a parameter-only variant shares its family."""
    action = candidate.get("action") if isinstance(candidate, dict) else None
    # Without a structured target, intervention or operation, the description is part of route identity.
    if (not isinstance(action, dict) or not isinstance(action.get("parameters", {}), dict)
            or not any(key in action for key in ("target", "intervention", "operation"))):
        return None
    return _loop_route({**candidate, "action": {**action, "parameters": {}}})


def _loop_context(candidate, evidence):
    """Only declared dependencies and review conditions can reopen a route."""
    derivation, action = candidate.get("derivation", []), candidate.get("action", {})
    _require(isinstance(derivation, list) and all(isinstance(row, dict) for row in derivation), "Invalid recorded candidate derivation")
    traces = [item for row in derivation if row.get("step") == "rule_to_template" for item in row.get("trace", [])]
    _require(all(isinstance(row, dict) for row in traces), "Invalid recorded rule trace")
    reports = [row for row in derivation + traces if row.get("step") == "fact_to_rule"]
    reports += candidate.get("facts", []) + candidate.get("control_bindings", []) + candidate.get("observables", [])
    reports += candidate.get("discrimination", {}).get("conditions", [])
    _require(all(isinstance(row, dict) for row in reports), "Invalid recorded candidate predicates")
    observable_names = action.get("required_observables", candidate.get("required_observables", []))
    names = {row["fact"] for row in reports if isinstance(row.get("fact"), str)}
    names.update(name for name in observable_names if isinstance(name, str) and name in evidence)
    selected = {}
    for name in names:
        fact = evidence.get(name, {})
        if isinstance(fact, dict):
            source = fact.get("source", {})
            selected[name] = {"value": fact.get("value"), "binding": fact.get("binding", {}),
                "reliable": fact.get("reliable") is not False and fact.get("reliability") not in ("UNRELIABLE", "UNKNOWN"),
                "source_sha256": source.get("sha256") if isinstance(source, dict) else None}
        else:
            selected[name] = fact
    predicates = sorted([{key: row[key] for key in ("fact", "operator", "expected", "role") if key in row}
                         for row in reports], key=_json)
    return _json({"facts": selected, "conditions": predicates, "observables": observable_names,
        "outcomes": action.get("outcomes", candidate.get("result_to_decision", [])),
        "stop": action.get("stop_condition", candidate.get("stop_conditions", []))})


def _checkpoint_route(prior, checkpoint_id, directory, checked_witnesses, same_question):
    """Structural checks loop-history review applies to one recorded route decision."""
    _require(_text(prior["goal_revision"]) and prior["outcome"] in
             ("rejected", "accepted", "plan_locked", "deferred") and isinstance(prior["evidence"], dict),
             "Invalid checkpoint decision: " + checkpoint_id)
    _scope(prior["scope"])
    _json(prior["evidence"])
    route = _loop_route(prior["candidate"])
    _require(route is not None, "Checkpoint decision needs a structured candidate: " + checkpoint_id)
    # Declared domains prune only within their own question, so only that question checks the witness.
    if same_question and prior['outcome'] == 'rejected' and 'rejected_domain' in prior:
        from rds_guard_values import validate_domain, MAX_BYTES
        validate_domain(prior['rejected_domain'], prior['candidate'])
        witness = prior.get('falsification', {})
        witness_sha = witness.get('sha256')
        _require(isinstance(witness_sha, str) and re.fullmatch('[0-9a-f]{64}', witness_sha), 'Declared-domain rejection needs a witness hash')
        original = Path(witness['path']).resolve()
        _require(original.is_relative_to((directory / 'cas').resolve()), 'Domain witness must be in the project CAS')
        if (str(original), witness_sha) not in checked_witnesses:
            with original.open('rb') as handle:
                witness_raw = handle.read(MAX_BYTES + 1)
            _require(len(witness_raw) <= MAX_BYTES and hashlib.sha256(witness_raw).hexdigest() == witness_sha,
                     'Declared-domain witness CAS integrity failure')
            checked_witnesses.add((str(original), witness_sha))
    return {**prior, "route_sha256": route, "family_sha256": _loop_family(prior["candidate"]),
            "review_context": _loop_context(prior["candidate"], prior["evidence"])}


def validate_checkpoint_decision(decision, checkpoint_id, directory):
    """Refuse at save time a route record that its question's loop-history review would always refuse."""
    if not (isinstance(decision, dict) and _text(decision.get("question_id"))
            and all(key in decision for key in CHECKPOINT_ROUTE_FIELDS)):
        return  # Opaque contexts are never read as route decisions.
    try:
        _checkpoint_route(decision, checkpoint_id, Path(directory), set(), True)
        if decision["outcome"] == "rejected" and "rejected_domain" in decision:
            # Review resolves the witness from its own working directory; only an absolute path stays valid.
            _require(Path(decision["falsification"]["path"]).is_absolute(), "Domain witness path must be absolute")
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError, OSError) as exc:
        raise ValueError("Checkpoint decision for question " + json.dumps(decision["question_id"][:80], ensure_ascii=False)
                         + " would block loop-history review: "
                         + str(exc) + ". Record the structured candidate from the Advisor output, "
                         "or save the note without the route fields (" + ", ".join(CHECKPOINT_ROUTE_FIELDS) + ")") from exc
