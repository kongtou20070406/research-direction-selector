"""Bounded search over explicitly configured reasoning dependencies.

No text eval, causal discovery, probability estimates or run execution. Imported
artifacts retain their provenance labels; caller dictionaries remain INPUT_REPORTED.
"""
from copy import deepcopy
from itertools import combinations
import hashlib
import inspect
import json
import math
import re

TRUE, FALSE, UNKNOWN = "TRUE", "FALSE", "UNKNOWN"
# A declared obstruction selects a bounded response; it is never a diagnosis or permission.
MOVE_PRESERVE_CLAUSES = ("Preserve the original goal, scope, revision, budget and method constraints in the referenced inputs. "
                         "Respect an explicitly chosen route; this suggestion does not interrupt it or authorize execution. "
                         "Unknown evidence or a scoped failure does not establish a capacity lower bound.")
# Guidance text is static; record fields stay data and are referenced, never spliced into sentences.
OBSTRUCTION_RESPONSES = {
    "MISSING_INPUT": ("EVIDENCE_REPAIR", "Obtain or measure the missing input named in this record's requirement.input under "
                      "the same scope and source binding; no capability gap or scientific failure is established."),
    "EXECUTION_CAP": ("INCOMPLETE_COMPUTATION", "The computation stopped at a configured cap, timeout or truncation; it is "
                      "incomplete, not a refutation or a capability deficit. Narrow the bounded scope or request an authorized cap change."),
    "DEPENDENCY_UNAVAILABLE": ("DEPENDENCY_REPORT", "Report the unavailable dependency named in this record as a concrete blocker or "
                               "select a compatible available backend; nothing is installed and availability is not inferred."),
    "ADAPTER_MISMATCH": ("ADAPTER_REPAIR", "Repair or validate the adapter/implementation on a known case; the scientific "
                         "claim remains UNKNOWN, not refuted."),
    "UNSUPPORTED_OPERATION": ("CAPABILITY_REQUIRED", "Propose the smallest reusable adaptation, composition or new operation that meets "
                              "this input/operation/output contract, with a checker, and compare it with the smallest repair; "
                              "the shortlist covers only the bounded catalogue."),
}
OBSTRUCTION_CAUSES = tuple(sorted(set(OBSTRUCTION_RESPONSES) | {"UNDETERMINED"}))
OBSTRUCTION_FIELDS = ("cause", "dependency", "id", "obligation", "receipt", "requirement", "scope", "signals", "source")
SOURCE_LOCATOR_KEYS = ("locator", "path", "receipt_id", "url")
UNDETERMINED_NEXT = ("The cause is not established; choose the smallest check that distinguishes missing input, adapter fault, "
                     "execution cap and unsupported operation before treating it as a capability gap.")
OBSTRUCTION_MOVE_TEXT = (
    "Answer each declared obstruction referenced in obstructions with its stated response: evidence repair, incomplete "
    "computation, dependency report, adapter repair, a discriminating check, or for a capability requirement a reusable "
    "operation meeting its input/operation/output contract with a checker, compared with the smallest repair. Declared "
    "obstructions are input-reported, not a diagnosis, and authorize neither execution nor installation. ")
# A sourced capability requirement supersedes only a generic gap/jump move, or a goal-evidence step whose every
# UNKNOWN predicate it covers; integrity, input, search-scope, goal-link and discriminator moves keep precedence.
CAPABILITY_SUPERSEDES = ("REFORMULATE", "REVIEW_ALTERNATIVE", "DIAGNOSE_GOAL_GAP")
GOAL_EVIDENCE_REASON = "The original goal has unresolved evidence; no scientific failure is established."
SPECIFY_CAPABILITY_REASON = ("A sourced unsupported operation blocks an open goal obligation and no other cause is declared "
                             "for it; the reusable operation it requires is the next step.")
SPECIFY_CAPABILITY_TEXT = (
    "Specify the smallest reusable adaptation, composition or new operation that meets each referenced required_capability "
    "input/operation/output contract, with a checker on a known case, and compare it with the smallest repair; routes "
    "recorded as rejected stay rejected. The shortlist covers only the bounded catalogue; availability and prerequisites "
    "are not assessed. Goal predicates stay UNKNOWN or FALSE until the checker's result is recorded. Answer any other "
    "referenced obstruction with its stated response. Declared obstructions are input-reported, not a diagnosis, and "
    "authorize neither execution nor installation. ")
# Triple Affirmative (#80): a solution is found, portable and applicable to the whole declared scope.
# The project declares what portable/applicable mean; only importer-read or program-derived evidence affirms.
AFFIRMATIVES = ("portable", "applicable")
AFFIRMING_ORIGINS = ("ARTIFACT_OBSERVED", "PROGRAM_DERIVED")
AFFIRMATIVE_MOVE_TEXT = (
    "For each affirmative listed in affirmatives, name the smallest observation that would affirm or refute the "
    "project's own declared predicates for it; FOUND needs a reading imported from an artifact or derived by the "
    "program, not a typed or configured value, and each affirmative needs evidence the others do not reuse. Keep "
    "UNKNOWN where unsupported. Do not narrow the scope, weaken the statement or redefine the predicates to close "
    "the goal; a failed affirmative is a scoped gap, not an impossibility result. ")
GOAL_GAP_TEXT = (
    "Inspect the failed predicate's actual value, acceptance condition, scope and original source in the referenced review. "
    "Choose the smallest check that distinguishes an evaluation problem, an incomplete result and a method limitation, "
    "and compare the smallest repair with an alternative only when that evidence warrants it. "
    "State the check's cost and stop condition; a below-threshold result alone does not establish a failed method, "
    "a need to change assumptions or progress toward the goal. ")


def _evidence_status(record):
    # A JSON flag cannot impersonate the read-only artifact importer's type.
    try:
        from rds_artifacts import ArtifactFact
    except ImportError:
        return "INPUT_REPORTED"
    if isinstance(record, ArtifactFact):
        status = getattr(record, "provenance_status", "UNKNOWN")
        if status in {"ARTIFACT_OBSERVED", "ARTIFACT_DECLARED", "PROGRAM_DERIVED", "UNKNOWN"}:
            return status
    return "INPUT_REPORTED"


def _source(record):
    source = record.get("source")
    return ((isinstance(source, str) and bool(source.strip())) or
            (isinstance(source, dict) and any(isinstance(source.get(k), str) and source[k].strip()
             for k in ("path", "url", "receipt_id", "locator"))))


def _reported(record):
    return (isinstance(record, dict) and _source(record) and record.get("reliable") is not False
            and record.get("reliability") not in ("UNRELIABLE", "UNKNOWN"))


def _finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def evaluate_condition(condition, facts):
    """Return three-valued truth and its input evidence, without certifying it."""
    name = condition.get("fact") if isinstance(condition, dict) else None
    record = facts.get(name) if isinstance(name, str) else None
    report = {"fact": name, "operator": condition.get("op", "eq") if isinstance(condition, dict) else None,
              "expected": deepcopy(condition.get("value")) if isinstance(condition, dict) else None,
              "truth": UNKNOWN, "evidence_status": _evidence_status(record)}
    if not _reported(record) or "value" not in record or record["value"] is None:
        report["reason"] = "missing value/source or explicitly unreliable evidence"
        return report
    value, expected, op = record["value"], report["expected"], report["operator"]
    report.update(actual=deepcopy(value), source=deepcopy(record["source"]))
    if not isinstance(op, str):
        report["reason"] = "unsupported operator or incompatible value type"
        return report
    if op in {"eq", "ne"}:
        equal = value == expected and (not isinstance(value, bool) and not isinstance(expected, bool)
                                      or type(value) is type(expected))
        answer = equal if op == "eq" else not equal
    elif op == "in" and isinstance(expected, list):
        answer = any(value == item and (not isinstance(value, bool) and not isinstance(item, bool)
                                       or type(value) is type(item)) for item in expected)
    elif op in {"lt", "lte", "gt", "gte"} and _finite(value) and _finite(expected):
        answer = {"lt": value < expected, "lte": value <= expected,
                  "gt": value > expected, "gte": value >= expected}[op]
    else:
        report["reason"] = "unsupported operator or incompatible value type"
        return report
    report.update(truth=TRUE if answer else FALSE, reason="comparison of sourced input, not independent verification")
    return report


def _all(reports):
    truths = [r["truth"] for r in reports]
    return FALSE if FALSE in truths else UNKNOWN if UNKNOWN in truths else TRUE


def _affirmation_predicates(decision):
    """Validate the opt-in Triple Affirmative declaration before any review uses it."""
    if "affirmations" not in decision:
        return None
    declared = decision["affirmations"]
    if "goal_conditions" not in decision:
        raise ValueError("decision.affirmations requires decision.goal_conditions; FOUND is evaluated on the goal predicates")
    if not isinstance(declared, dict) or not declared:
        raise ValueError("decision.affirmations must be an object with portable and/or applicable predicate lists")
    unknown = sorted(set(declared) - set(AFFIRMATIVES))
    if unknown:
        raise ValueError("decision.affirmations has unsupported key(s) " + ", ".join(map(repr, unknown)) +
                         "; FOUND is derived from goal_conditions, only portable and applicable are declared")
    for name, predicates in declared.items():
        if not (isinstance(predicates, list) and 1 <= len(predicates) <= 32 and all(
                isinstance(p, dict) and isinstance(p.get("fact"), str) and p["fact"].strip() for p in predicates)):
            raise ValueError(f"decision.affirmations.{name} must be 1 to 32 explicit fact predicates")
    return declared


def _affirm(report, reused, grounded=True):
    """One predicate affirms only when TRUE, importer-read or derived from such readings, and not reused.

    A typed or configured FALSE still refutes, exactly as it fails the goal review; it never affirms.
    """
    row = {"fact": report["fact"], "truth": report["truth"], "evidence_status": report["evidence_status"],
           "affirms": report["truth"]}
    if report["truth"] != TRUE:
        return row
    if report["evidence_status"] not in AFFIRMING_ORIGINS:
        row.update(affirms=UNKNOWN, affirmation_reason="reported, not found: the value was not read from an artifact or derived by the program")
    elif not grounded:
        row.update(affirms=UNKNOWN, affirmation_reason="derived from an input that is no longer importer-read evidence (disputed, missing or conflicting)")
    elif reused:
        row.update(affirms=UNKNOWN, affirmation_reason="reuses the evidence of another affirmative; one measurement is not an independent affirmation")
    return row


def _evidence_identity(name, facts, memo, seen=()):
    """Read locations behind a fact: (file hash, locator) per reading, followed through derivations.

    Returns (identities, grounded); grounded is False when a derivation input is no longer importer evidence.
    """
    if name in memo:
        return memo[name]
    record = facts.get(name)
    status = _evidence_status(record)
    source = record.get("source") if isinstance(record, dict) else None
    if status == "PROGRAM_DERIVED" and isinstance(record.get("input_fact_ids"), list) and name not in seen:
        parts = [_evidence_identity(i, facts, memo, seen + (name,)) for i in record["input_fact_ids"] if isinstance(i, str)]
        result = (frozenset().union(*(p[0] for p in parts)),
                  bool(parts) and all(p[1] for p in parts))
    elif status == "ARTIFACT_OBSERVED" and isinstance(source, dict) and isinstance(source.get("sha256"), str):
        locator = source.get("locator")
        reading = getattr(record, "reading_identity", None)
        if not (isinstance(reading, tuple) and len(reading) == 2 and all(isinstance(value, str) for value in reading)):
            # Older trusted ArtifactFact callers retain their located identity.
            reading = (source["sha256"], locator if isinstance(locator, str) else None)
        result = (frozenset({("read", *reading)}), True)
    else:
        result = (frozenset({("fact", name)}), False)
    memo[name] = result
    return result


def _triple_affirmative(declared, goal_reports, goals, facts):
    """Strict completion review; never changes the reported goal status."""
    predicates = {"found": goals, **{name: declared.get(name, []) for name in AFFIRMATIVES}}
    memo = {}
    owners = {name: frozenset().union(*(_evidence_identity(p["fact"], facts, memo)[0] for p in rows))
              for name, rows in predicates.items()}
    result = {}
    for name in ("found",) + AFFIRMATIVES:
        if name != "found" and name not in declared:
            result[name] = {"status": UNKNOWN, "reason": "No predicate declared; an undeclared affirmative is never vacuously true."}
            continue
        others = frozenset().union(*(owned for other, owned in owners.items() if other != name))
        reports = goal_reports if name == "found" else [evaluate_condition(p, facts) for p in predicates[name]]
        rows = []
        for predicate, report in zip(predicates[name], reports):
            identity, grounded = _evidence_identity(predicate["fact"], facts, memo)
            row = _affirm(report, bool(identity & others), grounded)
            rows.append(row if name == "found" else {**report, **row})
        result[name] = {"status": _all([{"truth": r["affirms"]} for r in rows]), "conditions": rows}
    names = ("found",) + AFFIRMATIVES
    return {"status": _all([{"truth": result[name]["status"]} for name in names]),
            "open": [name.upper() for name in names if result[name]["status"] != TRUE],
            **result, "assurance": "INPUT_REPORTED_NOT_SCIENTIFIC_VERIFICATION"}


def _affirmative_move(triple):
    """As in _next_move, unresolved evidence comes before a reported failure."""
    failed = [name for name in triple["open"] if triple[name.lower()]["status"] == FALSE]
    unresolved = [name for name in triple["open"] if name not in failed]
    if unresolved:
        kind = "RESOLVE_PREMISE"
        reason = ("The goal predicates compare TRUE, but " + ", ".join(unresolved) + " " +
                  ("is" if len(unresolved) == 1 else "are") + " not affirmed by unreused importer-read or "
                  "program-derived evidence; resolve " + ("it" if len(unresolved) == 1 else "them") + " first.")
        if failed:
            reason += " " + ", ".join(failed) + " already failed and stays open."
    else:
        kind = "DIAGNOSE_GOAL_GAP"
        reason = ("The found result fails the declared " + ", ".join(failed) + " affirmative" + ("s" if len(failed) > 1 else "") +
                  "; this is a scoped gap, not an impossibility result or a causal diagnosis.")
    return {"kind": kind, "reason": reason, "basis": "INPUT_REVIEW_HEURISTIC_NOT_SCIENTIFIC_PROOF",
            "authorization": "UNCHANGED", "affirmatives": list(triple["open"]),
            "source": "selection_review.goal.triple_affirmative",
            "preserve_refs": ["search.decision", "context.budget", "context.resources", "context.method_constraints"],
            "prompt": AFFIRMATIVE_MOVE_TEXT + (GOAL_GAP_TEXT if failed else "") + MOVE_PRESERVE_CLAUSES}


def _action_valid(action, current_choice):
    if not isinstance(action, dict) or not isinstance(action.get("id"), str):
        return False, "missing action identity"
    outcomes = action.get("outcomes", [])
    if not isinstance(outcomes, list) or not all(isinstance(o, dict) and isinstance(o.get("observation"), str)
            and isinstance(o.get("next_decision"), str) and o["next_decision"].strip() for o in outcomes):
        return False, "outcomes must bind observations to next decisions"
    decisions = {o["next_decision"] for o in outcomes}
    if len(decisions) < 2 and not (action.get("kind") == "INTERPRETATION_UPDATE" and current_choice
                                 and decisions and current_choice not in decisions):
        return False, "no outcome can distinguish next decisions"
    if action.get("kind") == "OBLIGATION_CHECK":
        if not all(isinstance(action.get(k), str) and action[k].strip() for k in ("description", "target", "claim")):
            return False, "obligation checks need an explicit target and scoped claim"
        labels = [o["observation"] for o in outcomes]
        if len(labels) != 3 or set(labels) != {"verified", "counterexample", "unresolved"}:
            return False, "obligation outcomes must distinguish verified, counterexample and unresolved"
        if "discrimination" in action:
            return False, "obligation checks cannot claim empirical rival discrimination"
    elif not action.get("description") or not isinstance(action.get("competing_explanations"), list) or len(action["competing_explanations"]) < 2:
        return False, "missing competing explanations or action"
    if not isinstance(action.get("required_observables"), list) or not action["required_observables"]:
        return False, "missing required observables"
    return True, None


def _cost_identity(record):
    if not isinstance(record, dict):
        return None
    for key in ("resource", "unit", "comparison_group"):
        if key == "resource" and key not in record:  # Legacy costs omit resource entirely.
            continue
        value = record.get(key)
        if not isinstance(value, str) or not value.strip() or value.strip().upper() == UNKNOWN:
            return None
    return record.get("resource"), record["unit"], record["comparison_group"]


def _cost(ids, costs):
    records = [costs.get(i) for i in ids]
    if not records or any(not _reported(r) or not _finite(r.get("value")) or r["value"] < 0
                          or _cost_identity(r) is None for r in records):
        return {"status": UNKNOWN, "reason": "missing sourced, comparable incremental cost"}
    groups = {_cost_identity(r) for r in records}
    total = sum(r["value"] for r in records)
    if len(groups) != 1 or not _finite(total):
        return {"status": UNKNOWN, "reason": "mixed resources/units/comparison groups or nonfinite total"}
    resource, unit, group = groups.pop()
    return {"status": "INPUT_REPORTED", "value": total, "unit": unit, "comparison_group": group,
            **({"resource": resource} if resource is not None else {}),
            "sources": [deepcopy(r["source"]) for r in records], "components": list(ids),
            "evidence_statuses": [_evidence_status(r) for r in records]}


def _discrimination(action, facts):
    """Describe conditional rival-pair coverage from supplied prediction sets."""
    spec = action.get("discrimination")
    spec = spec if isinstance(spec, dict) else {}
    explanations = action["competing_explanations"]
    issues = []
    if not (2 <= len(explanations) <= 32 and all(isinstance(e, str) and e.strip() for e in explanations)
            and len(set(explanations)) == len(explanations)):
        issues.append("competing_explanations must be 2 to 32 unique explanation IDs")
        explanations = []
    explanations = sorted(explanations)
    scope = spec.get("scope_id")
    if not isinstance(scope, str) or not scope.strip():
        issues.append("missing explicit scope_id")
    if not _reported(spec):
        issues.append("missing sourced or reliable prediction support")
    allowed = {outcome["observation"] for outcome in action["outcomes"]}
    predictions = spec.get("predictions")
    if not (isinstance(predictions, dict) and set(predictions) == set(explanations) and explanations
            and all(isinstance(labels, list) and 1 <= len(labels) <= 32
                    and all(isinstance(label, str) and label in allowed for label in labels)
                    for labels in predictions.values())):
        issues.append("predictions must give 1 to 32 declared outcome labels for every explanation ID")
    conditions = spec.get("conditions", [])
    reports = []
    if not (isinstance(conditions, list) and len(conditions) <= 32
            and all(isinstance(c, dict) and isinstance(c.get("fact"), str) and c["fact"].strip() for c in conditions)):
        issues.append("conditions must be up to 32 explicit fact predicates")
        applicability = UNKNOWN
    else:
        reports = [evaluate_condition(condition, facts) for condition in conditions]
        applicability = _all(reports)
    supported = not issues and applicability == TRUE
    distinguishing, unresolved = [], []
    for pair in combinations(explanations, 2):
        if not supported:
            reason = "; ".join(issues) if issues else "prediction applicability is " + applicability
        elif set(predictions[pair[0]]).isdisjoint(predictions[pair[1]]):
            distinguishing.append(list(pair))
            continue
        else:
            reason = "allowed predictions overlap"
        unresolved.append({"pair": list(pair), "reason": reason})
    return {"scope_id": deepcopy(scope), "source": deepcopy(spec.get("source")),
            "evidence_status": _evidence_status(spec), "conditions": reports,
            "applicability": applicability, "valid_prediction_support": supported,
            "conditional_distinguishing_pairs": distinguishing, "unresolved_pairs": unresolved,
            "issues": issues}


def _dependency_review(context, *, audit_receipts=False, audit_files=False, read_receipt=None):
    """Consume the existing bounded AND/OR analyzer only when a map is supplied.

    ``audit_receipts``/``audit_files`` mirror the analyzer CLI flags: receipt
    grounding and byte checks change which records the closure may use. Both
    stay read-only; a run or file bytes are never statement verification.
    ``read_receipt`` is the operation's shared lookup, so a receipt also named
    by an obstruction is read once per call.
    """
    if "dependency_map" not in context:
        return None
    try:
        spec = context["dependency_map"]
        raw = json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) > 128 * 1024:
            raise ValueError("dependency_map exceeds 128 KiB; complete analysis is unavailable within this input bound")
        from rds_hypergraph import _validate, analyze_hypergraph
        # Existing internal maps stay strict. The compact declaration adapter
        # and program-owned snapshots let agents avoid writing these rows.
        if isinstance(spec, dict) and all(key in spec for key in ('nodes', 'hyperedges', 'goals')):
            _validate(deepcopy(spec))
        if not isinstance(spec, dict) or not any(key in spec for key in
                ('nodes', 'claims', 'hyperedges', 'rules', 'goals', 'goal', 'dependency_map')):
            raise ValueError('No dependency declarations supplied')
        from rds_hypergraph_input import prepare_input
        spec, input_review = prepare_input(spec)
        if input_review['errors']:
            raise ValueError('; '.join(row['path'] + ': ' + row['reason'] for row in input_review['errors']))
        try:
            shared = {'analysis_targets': 'all'}
            if read_receipt is not None:
                # An analyzer without the shared lookup still audits, reading on its own.
                shared["read_receipt"] = read_receipt
            if audit_receipts:
                shared['audit_receipts_enabled'] = True
            result = analyze_hypergraph(spec, **shared)
        except TypeError:
            # Older analyzers may still audit without the shared lookup. Retain
            # every supported check; missing all-node coverage stays incomplete.
            parameters = inspect.signature(analyze_hypergraph).parameters
            compatible = {key: value for key, value in shared.items() if key in parameters}
            if compatible == shared or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values()):
                raise  # Do not retry or hide an internal analyzer TypeError.
            result = analyze_hypergraph(spec, **compatible)
        if audit_files:
            from rds_hypergraph import audit_sources
            result["source_file_audit"] = audit_sources(spec)
        if "receipt_blocked_node_ids" not in result:
            # Analyzer without the receipts slice: a binding it cannot check must
            # not be silently trusted, whether or not an audit was requested.
            result["receipt_blocked_node_ids"] = [
                record["id"] for kind in ("nodes", "hyperedges") for record in spec.get(kind, [])
                if isinstance(record, dict) and record.get("evidence") is not None]
            result["receipt_audit"] = {"assurance": "RECEIPT_EXECUTION_NOT_STATEMENT_VERIFICATION",
                                       "audits": [], "grounded_receipts": [],
                                       "all_receipts_grounded": False,
                                       "status": UNKNOWN,
                                       "reason": "the installed analyzer cannot audit receipts; "
                                                 "declared bindings stay fail-closed"}
        if input_review['repairs'] or input_review['warnings']:
            normalized = json.dumps(spec, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
            result.update(input_review=input_review, dependency_map=spec,
                          dependency_map_sha256=hashlib.sha256(normalized).hexdigest())
        return {**result, "input_sha256": hashlib.sha256(raw).hexdigest(),
                "status": "INCOMPLETE" if result["truncated"] or result.get('coverage', {}).get('full') is not True else "ANALYZED",
                "authorization": "UNCHANGED"}
    except (ValueError, TypeError, KeyError) as exc:
        return {"status": UNKNOWN, "reason": str(exc), "authorization": "UNCHANGED",
                "assurance": "INPUT_REPORTED_DEPENDENCY_ANALYSIS_NOT_PROOF"}


def _operation_dependency(context, *, audit_receipts=False, audit_files=False, read_receipt=None):
    """Own one map snapshot and reuse its analysis only within this operation."""
    snapshot = dict(context)
    snapshot["dependency_map"] = deepcopy(context["dependency_map"])
    cached = []

    def review():
        if not cached:
            cached.append(_dependency_review(snapshot, audit_receipts=audit_receipts,
                                             audit_files=audit_files, read_receipt=read_receipt))
        return deepcopy(cached[0])

    return review


def _blocked_bindings(dependency):
    """Records whose checked evidence must not be relied on, with the repair token.

    Receipt-blocked nodes come from ``receipt_blocked_node_ids`` (fail-closed even
    when the caller never enabled the audit); a source-file ``MISMATCH`` adds the
    affected record regardless of its declared label. Every entry carries the
    direct-evidence/RECEIPT_REVALIDATION repair token the analyzer already names.
    """
    blocked = []
    for ident in dependency.get("receipt_blocked_node_ids") or []:
        blocked.append({"token": "node:" + ident, "kind": "RECEIPT_REVALIDATION",
                        "reason": "declared receipt is not grounded in its named ledger"})
    audit = dependency.get("source_file_audit") or {}
    for row in audit.get("audits", []):
        if row.get("status") == "MISMATCH":
            blocked.append({"token": row["kind"] + ":" + row["id"], "kind": "EVIDENCE_REPAIR",
                            "reason": "declared source file bytes no longer match the recorded sha256"})
    return blocked


def _mapped_path(spec, dependency, action):
    """Check a contributory path, keeping every AND premise as a separate obligation."""
    report = {"status": UNKNOWN, "assurance": "INPUT_REPORTED_GRAPH_PATH_NOT_PROOF"}
    if dependency["status"] == UNKNOWN:
        return {**report, "reason": dependency["reason"]}
    path, target = spec["path"], spec["target"]
    nodes = {n["id"]: n for n in dependency["reported_nodes"]}
    if target not in dependency["goals"] or path[-1] != target:
        return {**report, "reason": "The path must end at its target in dependency_map.goals."}
    if dependency['goals'][target]['status'] == 'UNRESOLVED':
        return {**report, "reason": "The supplied map has no grounded closing route to this goal; inspect cyclic or missing premises."}
    edges = dependency["reported_hyperedges"]
    rule_start = None if path[0] in nodes else next((e for e in edges if 'rule:' + e['id'] == path[0]), None)
    node_path = path[1:] if rule_start is not None else path
    blocked = _blocked_bindings(dependency)
    # Refuse the path only when it actually relies on a blocked record: the
    # start token, any crossed node, or any edge used by the mapped steps.
    blocked_tokens = {b["token"] for b in blocked}
    touched = {'node:' + n for n in node_path} | {'rule:' + rule_start['id']} if rule_start is not None else {'node:' + n for n in node_path}
    for left, right in zip(node_path, node_path[1:]):
        touched.update('rule:' + e['id'] for e in edges if e['status'] != 'CONTRADICTED'
                       and left in e['premises'] and e['conclusion'] == right
                       and right not in e['premises'])
    if blocked_tokens & touched:
        first = min((b for b in blocked if b["token"] in touched), key=lambda b: b["token"])
        return {**report, "reason": "The path relies on a record whose checked evidence is not grounded; "
                                    f"repair {first['token']} ({first['reason']}) before relying on this route.",
                "blocked_bindings": [b for b in blocked if b["token"] in touched]}
    # A downstream conclusion cannot justify a prerequisite of this same path,
    # even when a different OR route could independently prove that conclusion.
    downstream = set(node_path if rule_start is not None else node_path[1:])
    grounded = (set(dependency['declared_supported_closure']) | set(dependency['direct_evidence_node_ids'])) - downstream

    def extend_grounded():
        changed = True
        while changed:
            changed = False
            for edge in edges:
                if (edge['status'] != 'CONTRADICTED' and nodes[edge['conclusion']]['status'] != 'CONTRADICTED'
                        and set(edge['premises']) <= grounded and edge['conclusion'] not in grounded | downstream):
                    grounded.add(edge['conclusion'])
                    changed = True

    extend_grounded()
    if rule_start is not None and (len(path) < 2 or rule_start['conclusion'] != path[1]
            or rule_start['status'] == 'CONTRADICTED'
            or rule_start['conclusion'] in rule_start['premises']
            or not set(rule_start['premises']) <= grounded):
        return {**report, "reason": "The starting rule is unavailable or does not conclude the next node."}
    if len(path) != len(set(path)) or any(n not in nodes or nodes[n]["status"] == "CONTRADICTED" for n in node_path):
        return {**report, "reason": "The path has unknown, contradicted or repeated node IDs."}
    links = []
    if rule_start is not None:
        links.append([rule_start['id']])
        grounded.add(rule_start['conclusion'])
        downstream.discard(rule_start['conclusion'])
        extend_grounded()
    for left, right in zip(node_path, node_path[1:]):
        available = [e['id'] for e in edges if e['status'] != 'CONTRADICTED'
                     and left in e['premises'] and e['conclusion'] == right
                     and right not in e['premises'] and set(e['premises']) <= grounded]
        if not available:
            return {**report, "reason": "A path step has no grounded non-contradicted hyperedge to its next conclusion."}
        links.append(available)
        grounded.add(right)
        downstream.discard(right)
        extend_grounded()
    return {**report, "status": "DECLARED_CONNECTED_PATH", "link_rule_alternatives": links,
            "start_token": 'rule:' + rule_start['id'] if rule_start is not None else 'node:' + path[0],
            "goal_review": deepcopy(dependency["goals"][target]),
            "reason": "The path is connected in the supplied map; AND premises, proposed rules and source validity still need evidence."}


def _goal_targets(context):
    decision = context.get("decision", {})
    goals = decision.get("goal_conditions", []) if isinstance(decision, dict) else []
    targets = {g["fact"] for g in goals if isinstance(g, dict) and isinstance(g.get("fact"), str)} if isinstance(goals, list) else set()
    if isinstance(context.get("objective_binding"), dict) and context["objective_binding"]:
        targets.add("completion_standard")
    return targets


def _goal_contribution(action, context, dependency=None):
    """Review a declared path to an existing goal, never its scientific validity."""
    targets = _goal_targets(context)
    if not targets and "goal_contribution" not in action:
        return None
    report = {"status": "UNDECLARED", "assurance": "DECLARED_LINK_NOT_SCIENTIFIC_PROOF",
              "authorization": "UNCHANGED"}
    if "goal_contribution" not in action:
        return {**report, "reason": "No dependency path from this action to an explicit original goal was declared."}
    spec = action["goal_contribution"]
    bounded_text = lambda value: isinstance(value, str) and bool(value.strip()) and len(value) <= 512
    if not (isinstance(spec, dict) and set(spec) == {"target", "path", "source"}
            and bounded_text(spec["target"]) and bounded_text(spec["source"])
            and isinstance(spec["path"], list) and 1 <= len(spec["path"]) <= 8
            and all(bounded_text(step) for step in spec["path"])):
        return {**report, "status": UNKNOWN,
                "reason": "A contribution needs target, source and 1 to 8 nonempty path steps, each at most 512 characters."}
    if action.get("target") != spec["path"][0]:
        return {**report, "status": UNKNOWN,
                "reason": "A contribution path must start at action.target, the obligation being checked or measured."}
    if spec["target"] not in targets:
        return {**report, "status": UNKNOWN,
                "reason": "The contribution target is not an existing goal predicate or bound completion_standard."}
    result = {**report, **deepcopy(spec), "status": "DECLARED_PATH",
              "reason": "The supplied text path names an existing goal; it is not an evaluated graph path, proof or verified completion."}
    if dependency is not None:
        result["graph_path"] = _mapped_path(spec, dependency, action)
        if result["graph_path"]["status"] == UNKNOWN:
            result.update(status=UNKNOWN, reason=result["graph_path"]["reason"])
        else:
            result["reason"] = result["graph_path"]["reason"]
    return result


def _text(value, limit=512):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _obstruction_records(context):
    """Validate declared obstructions with field-level repair messages; nothing is inferred."""
    records = context["obstructions"]
    if not isinstance(records, list) or not 1 <= len(records) <= 8:
        raise ValueError("advisor_context.obstructions must be a list of 1 to 8 records")
    seen = set()
    for index, record in enumerate(records):
        field = f"advisor_context.obstructions[{index}]"
        if not isinstance(record, dict):
            raise ValueError(f"{field} must be an object")
        unknown = sorted(set(record) - set(OBSTRUCTION_FIELDS))
        if unknown:
            raise ValueError(f"{field} has unknown field(s): {', '.join(unknown)}; allowed: {', '.join(OBSTRUCTION_FIELDS)}")
        if not _text(record.get("id"), 128):
            raise ValueError(f"{field}.id must be nonempty text of at most 128 characters")
        if record["id"] in seen:
            raise ValueError(f"{field}.id duplicates an earlier obstruction id")
        seen.add(record["id"])
        if not _text(record.get("obligation")):
            raise ValueError(f"{field}.obligation must name a goal predicate or completion_standard in at most 512 characters")
        if "source" in record:
            source = record["source"]
            if isinstance(source, dict):
                valid = bool(source) and set(source) <= set(SOURCE_LOCATOR_KEYS) and all(_text(v) for v in source.values())
            else:
                valid = _text(source)
            if not valid:
                raise ValueError(f"{field}.source must be locator text of at most 512 characters or an object "
                                 f"with only {', '.join(SOURCE_LOCATOR_KEYS)} text fields")
        if record.get("cause") not in OBSTRUCTION_CAUSES:
            raise ValueError(f"{field}.cause must be one of {', '.join(OBSTRUCTION_CAUSES)}")
        if "requirement" in record:
            requirement = record["requirement"]
            if not isinstance(requirement, dict) or set(requirement) != {"input", "operation", "output"}:
                raise ValueError(f"{field}.requirement must have exactly input, operation and output")
            for key in ("input", "operation", "output"):
                if not _text(requirement[key]):
                    raise ValueError(f"{field}.requirement.{key} must be nonempty text of at most 512 characters")
        if "dependency" in record:
            if record["cause"] != "DEPENDENCY_UNAVAILABLE":
                raise ValueError(f"{field}.dependency is only valid for DEPENDENCY_UNAVAILABLE")
            if not _text(record["dependency"], 128):
                raise ValueError(f"{field}.dependency must be nonempty text of at most 128 characters")
        if "signals" in record:
            if record["cause"] != "UNSUPPORTED_OPERATION":
                raise ValueError(f"{field}.signals is only valid for UNSUPPORTED_OPERATION")
            signals = record["signals"]
            if not (isinstance(signals, list) and 1 <= len(signals) <= 8 and all(
                    isinstance(s, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", s) for s in signals)):
                raise ValueError(f"{field}.signals must be 1 to 8 lowercase theory-tool tags such as proof_bottleneck")
        if "scope" in record and not isinstance(record["scope"], dict):
            raise ValueError(f"{field}.scope must be an object compared with the current decision scope")
        if "receipt" in record:
            receipt = record["receipt"]
            if not (isinstance(receipt, dict) and set(receipt) == {"project_root", "sha256"}
                    and _text(receipt["project_root"]) and isinstance(receipt["sha256"], str)
                    and re.fullmatch(r"[0-9a-fA-F]{64}", receipt["sha256"])):
                raise ValueError(f"{field}.receipt must be exactly project_root (a nonempty path of at most 512 "
                                 "characters) and sha256 (64 hexadecimal characters) naming a project-ledger receipt")
    return records


def _receipt_audits(records, context, read_receipt=None):
    """Read each distinct declared receipt once, read-only, only under the audit_receipts opt-in.

    A receipt records execution, not why a goal is blocked: only a recorded timeout or stop policy
    is reported, as an execution cap. Nothing is inferred from exit status or success.
    """
    keys = {(r["receipt"]["project_root"], r["receipt"]["sha256"].lower()) for r in records if "receipt" in r}
    if context.get("audit_receipts") is not True:
        return dict.fromkeys(keys, {"status": "NOT_AUDITED"})
    if read_receipt is None:
        from rds_hypergraph import read_project_receipt as read_receipt
    audits = {}
    for root_text, digest_sha in sorted(keys):
        found = read_receipt(root_text, digest_sha)
        body = found.get("body")
        if found["status"] == "RECEIPT_FOUND" and not (isinstance(body, dict) and body.get("sha256") == digest_sha):
            found = {"status": "RECEIPT_BODY_MISMATCH"}
        if found["status"] != "RECEIPT_FOUND":
            audits[root_text, digest_sha] = found
            continue
        # The ledger writes stop_reason only when a stop policy fired, so its presence alone records a cap.
        stop = body.get("stop_reason")
        cap = ("TIMEOUT" if body.get("timeout") is True else None if stop is None
               else stop if _text(stop, 64) else "STOP_POLICY")
        audits[root_text, digest_sha] = {
            "status": "RECEIPT_FOUND", "run_id": body.get("run_id"), "run_status": body.get("run_status"),
            "execution_cap": cap, "assurance": "RECEIPT_EXECUTION_NOT_STATEMENT_VERIFICATION"}
    return audits


def _catalogue_shortlist(signals):
    """Read the bounded theory-tool catalogue by explicit tags only; never scan or run tools."""
    report = {"coverage": "BOUNDED_CATALOGUE_NOT_EXHAUSTIVE", "prerequisites": "NOT_ASSESSED"}
    if not signals:
        return {**report, "status": "NOT_SEARCHED", "searched": [], "shortlist": [],
                "reason": "No theory-tool signals were declared."}
    try:
        from rds_theory_tools import shortlist
        found = shortlist(signals, limit=3)
    except (ImportError, OSError, ValueError, KeyError, TypeError) as exc:
        return {**report, "status": UNKNOWN, "searched": [], "shortlist": [], "reason": str(exc)[:200]}
    return {**report, "status": "SEARCHED", "searched": ["references/theory-tools.json"],
            "catalogue_sha256": found["catalogue_sha256"], "unmatched_signal_count": found["unmatched_signal_count"],
            "shortlist": [{key: card[key] for key in ("id", "locator", "matched_tags", "runnable_operator")}
                          for card in found["cards"]]}


def _obstruction_response(record, audit=None):
    cause, requirement = record["cause"], record.get("requirement")
    if not _source(record):
        why = "No source locator was declared for this obstruction."
    elif cause == "UNDETERMINED":
        why = "The declared cause is undetermined."
    elif cause == "UNSUPPORTED_OPERATION" and requirement is None:
        why = "An unsupported operation needs requirement input/operation/output before it can become a capability requirement."
    elif cause == "DEPENDENCY_UNAVAILABLE" and "dependency" not in record:
        why = "An unavailable dependency needs its dependency name."
    elif audit is not None and audit["status"] not in {"RECEIPT_FOUND", "NOT_AUDITED"}:
        # Fail closed like receipt-bound dependency evidence: an audited binding that cannot be read supports nothing.
        why = "The declared receipt could not be read from its named project ledger."
    else:
        why = None
    if why is not None:
        return {"response": "DISCRIMINATING_CHECK", "cause_status": UNKNOWN, "reason": why, "next": UNDETERMINED_NEXT}
    response, text = OBSTRUCTION_RESPONSES[cause]
    result = {"response": response, "cause_status": "INPUT_REPORTED", "next": text}
    if cause == "DEPENDENCY_UNAVAILABLE":
        from rds_capabilities import CAPABILITIES
        dependency = record["dependency"]
        # Only a fixed supported name forms a command; other names stay data.
        result.update(dependency=dependency, live_check=(
            "python -B scripts/rds_cli.py exec --timeout 10 -- python -B scripts/rds_capabilities.py --capability " + dependency
            if dependency in CAPABILITIES else None))
    elif cause == "UNSUPPORTED_OPERATION":
        result["required_capability"] = {
            "obligation": record["obligation"], **deepcopy(requirement), "source_ref": "source",
            "status": "REQUIRED_AVAILABILITY_UNKNOWN", "catalogue": _catalogue_shortlist(record.get("signals"))}
    if requirement is not None and cause != "UNSUPPORTED_OPERATION":
        result["requirement"] = deepcopy(requirement)
    return result


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _obstruction_review(records, context, goal, read_receipt=None):
    """Map declared obstructions on open goal obligations to bounded responses."""
    targets = _goal_targets(context)
    truths = {}
    for condition in (goal or {}).get("conditions", []):
        truths.setdefault(condition["fact"], []).append(condition["truth"])
    decision = context.get("decision")
    # The ledger keys loop history by decision.scope; the context-level scope is the documented fallback.
    scope = decision["scope"] if isinstance(decision, dict) and "scope" in decision else context.get("scope")
    audits = _receipt_audits(records, context, read_receipt)
    entries = []
    for record in records:
        entry = {"id": record["id"], "obligation": record["obligation"], "cause": record["cause"]}
        if "source" in record:
            entry["source"] = deepcopy(record["source"])
        audit = None
        if "receipt" in record:
            key = record["receipt"]["project_root"], record["receipt"]["sha256"].lower()
            entry["receipt"] = dict(zip(("project_root", "sha256"), key))
            audit = audits[key]
            entry["receipt_audit"] = deepcopy(audit)
        entry.update(assurance="INPUT_REPORTED_OBSTRUCTION_NOT_DIAGNOSIS", authorization="UNCHANGED")
        obligation = record["obligation"]
        if obligation not in targets:
            status, reason = "NOT_APPLICABLE", "The obligation is not an existing goal predicate or bound completion_standard."
        elif "scope" in record and scope is None:
            status, reason = UNKNOWN, "No current scope is declared, so this scoped obstruction cannot be matched to it."
        elif "scope" in record and _canonical(record["scope"]) != _canonical(scope):
            status, reason = "NOT_APPLICABLE", "The declared scope differs from the current scope; this obstruction was not observed here."
        elif truths.get(obligation) and all(truth == TRUE for truth in truths[obligation]):
            status, reason = "NOT_APPLICABLE", "The obligation's current goal predicates are satisfied; the obstruction no longer applies."
        else:
            entries.append({**entry, "status": "APPLICABLE", **_obstruction_response(record, audit)})
            continue
        entries.append({**entry, "status": status, "reason": reason})
    # No cause is established while a different cause is declared for the same open obligation, including one
    # whose applicability is unknown; only a cause ruled out (NOT_APPLICABLE) is ignored. Same-cause records
    # do not conflict. A read receipt that records a timeout or stop policy counts as a declared EXECUTION_CAP:
    # incomplete computation must be resolved before any other cause. Declared contracts, dependency names
    # and live checks stay as data for the check.
    causes, capped = {}, set()
    for entry in entries:
        if entry["status"] in {"APPLICABLE", UNKNOWN}:
            causes.setdefault(entry["obligation"], set()).add(entry["cause"])
            if (entry.get("receipt_audit") or {}).get("execution_cap"):
                capped.add(entry["obligation"])
    for record, entry in zip(records, entries):
        others = sorted(causes.get(entry["obligation"], set()) - {entry["cause"]})
        receipt_cap = entry["obligation"] in capped and entry["cause"] != "EXECUTION_CAP"
        if (entry["status"] != "APPLICABLE" or entry["response"] == "DISCRIMINATING_CHECK"
                or not (others or receipt_cap)):
            continue
        entry.pop("required_capability", None)
        if record.get("requirement") is not None:
            entry["requirement"] = deepcopy(record["requirement"])
        reasons = []
        if others:
            reasons.append("Co-declared " + ", ".join(others) + " on this obligation must be resolved or ruled out "
                           "before this cause is treated as established.")
        if receipt_cap:
            reasons.append("A named receipt records an execution cap on this obligation; that incomplete computation "
                           "must be resolved or ruled out before this cause is treated as established.")
        entry.update(response="DISCRIMINATING_CHECK", cause_status=UNKNOWN, next=UNDETERMINED_NEXT,
                     reason=" ".join(reasons))
    return entries


def _specify_capability(move, review, entries, integrity):
    """Whether the applicable capability requirements supersede the existing move kind."""
    capability = {e["obligation"] for e in entries if e["status"] == "APPLICABLE" and e["response"] == "CAPABILITY_REQUIRED"}
    goal = review.get("goal") or {}
    # A satisfied goal is not blocked; historical warnings cannot make it a capability step.
    if integrity or not capability or goal.get("status") == TRUE:
        return False
    unknown = {c["fact"] for c in goal.get("conditions", []) if c["truth"] == UNKNOWN}
    # Any UNKNOWN goal predicate must be covered, whichever branch produced the move, so recorded
    # rejections never make superseding easier than the plain goal-evidence step.
    if not unknown <= capability:
        return False
    return (move["kind"] in CAPABILITY_SUPERSEDES or
            (move["kind"] == "RESOLVE_PREMISE" and move["reason"] == GOAL_EVIDENCE_REASON and bool(unknown)))


def review_obstructions(search, context, *, _read_receipt=None):
    """Validate and consume declared obstructions after every existing context check.

    Responses are added to selection_review and referenced from the existing next_move. A move is
    never created or removed and authorization is unchanged; an established capability requirement
    turns only a generic jump or fully covered goal-evidence move into SPECIFY_CAPABILITY. Only the
    Advisor entry calls this: direct search_directions callers neither validate nor receive obstruction_review.
    """
    records = _obstruction_records(context)
    review = search.get("selection_review") if isinstance(search, dict) else None
    if not isinstance(review, dict):
        return None
    review["obstruction_review"] = entries = _obstruction_review(records, context, review.get("goal"), _read_receipt)
    move = review.get("next_move")
    applicable = [(index, entry) for index, entry in enumerate(entries) if entry["status"] == "APPLICABLE"]
    if move is None or not applicable:
        return entries
    move["obstructions"] = [{"id": entry["id"], "response": entry["response"],
                             "ref": f"selection_review.obstruction_review[{index}]"} for index, entry in applicable]
    integrity = any(f.get("kind") == "LOOP_HISTORY_REVIEW_ERROR" for f in search.get("loop_review", {}).get("flags", []))
    if _specify_capability(move, review, entries, integrity):
        # The superseded kind and reason stay as data, so recorded rejections remain visible.
        move.update(supersedes={"kind": move["kind"], "reason": move["reason"]}, kind="SPECIFY_CAPABILITY",
                    reason=SPECIFY_CAPABILITY_REASON,
                    source="selection_review.obstruction_review",
                    prompt=SPECIFY_CAPABILITY_TEXT + MOVE_PRESERVE_CLAUSES)
    elif not integrity and move["prompt"].endswith(MOVE_PRESERVE_CLAUSES):
        move["prompt"] = move["prompt"][:-len(MOVE_PRESERVE_CLAUSES)] + OBSTRUCTION_MOVE_TEXT + MOVE_PRESERVE_CLAUSES
    return entries


def _goal_discriminators(review, search):
    """Return declared links for ready rival checks, never inferred goal repair."""
    unresolved = {c['fact'] for c in review.get('goal', {}).get('conditions', []) if c['truth'] == UNKNOWN}
    if not unresolved:
        return {}
    actions = {c['id']: c.get('action', {}) for c in search.get('candidates', []) if 'id' in c}
    linked = {}
    for candidate in review['candidates']:
        if candidate['basis'] != 'CONDITIONAL_RIVAL_TEST' or candidate['unresolved_pairs']:
            continue
        contribution = candidate.get('goal_contribution', {})
        if contribution.get('status') == 'DECLARED_PATH':
            target = contribution['target']
            # The existing graph consumer validates mapped paths. Text-only paths
            # must also end at the goal they claim to inform.
            if contribution['path'][-1] != target:
                continue
        elif contribution.get('status') == 'UNDECLARED':
            # A direct goal-predicate check needs no invented intermediate bridge.
            target = actions.get(candidate['id'], {}).get('target')
        else:
            # An explicitly invalid/UNKNOWN path cannot be bypassed by its label.
            continue
        if isinstance(target, str) and target in unresolved:
            linked[candidate['id']] = target
    return linked


def _next_move(review, search):
    """Suggest a bounded reasoning step from input review, without changing a route."""
    flags = {f["kind"] for f in review["flags"]}
    ready_ids = {c["id"] for c in review["candidates"]}
    history_flags = search.get("loop_review", {}).get("flags", [])
    loop_flags = {f.get("kind") for f in history_flags
                  if f.get("kind") not in {"REPEAT_REJECTED_ROUTE", "REPEAT_DECLARED_REJECTED_DOMAIN"}
                  or not ready_ids or f.get("candidate_id") in ready_ids}
    if "DECISION_OSCILLATION" in loop_flags and ready_ids:
        if not any(ready_ids.intersection(f.get("candidate_ids", []))
                   for f in history_flags if f.get("kind") == "DECISION_OSCILLATION"):
            loop_flags.remove("DECISION_OSCILLATION")
    unlinked = bool(ready_ids) and all(c.get("goal_contribution", {}).get("status") in {"UNDECLARED", UNKNOWN}
                                     for c in review["candidates"])
    # Local warnings remain visible, but do not block an available supported route.
    supported_route = any(c["basis"] == "SCOPED_OBLIGATION" or
                          (c["basis"] == "CONDITIONAL_RIVAL_TEST" and not c["unresolved_pairs"])
                          for c in review["candidates"])
    goal = review.get("goal", {})
    measuring = _goal_discriminators(review, search)
    pending = any(c.get("status") in {"NEEDS_EVIDENCE", "NEEDS_METHOD_CLARIFICATION", "NEEDS_METHOD_DESCRIPTION"}
                  for c in search.get("candidates", []))
    blocked = any(c.get("status") in {"BLOCKED_PREREQUISITE", "BLOCKED_METHOD", "BLOCKED_BUDGET"}
                  for c in search.get("blocked_candidates", []))
    goal_rejections = next((f for f in history_flags if f.get("kind") == "GOAL_ROUTES_REJECTED"), None)
    # A new variant after rejections recorded for the same goal; an unmeasured current scope does not erase them.
    goal_history = (goal_rejections is not None and bool(ready_ids.intersection(goal_rejections.get("candidate_ids", [])))
                    and not supported_route and goal.get("status") in {UNKNOWN, FALSE})
    if "LOOP_HISTORY_REVIEW_ERROR" in loop_flags:
        kind, reason = "RESOLVE_PREMISE", "Recorded history integrity is unresolved; inspect the existing loop review."
    elif goal.get("status") == TRUE:
        # Historical/scope warnings stay in their reports. They do not overturn current acceptance;
        # review_selection still checks any independently declared affirmative below.
        return None
    elif measuring:
        unresolved_facts = {c["fact"] for c in goal["conditions"] if c["truth"] == UNKNOWN}
        remaining = unresolved_facts - set(measuring.values())
        links = ', '.join(f'{ident} -> {target}' for ident, target in sorted(measuring.items()))
        kind, reason = "DESIGN_DISCRIMINATOR", (
            "Local success does not measure an open goal, including an application/transfer obligation; ready rival checks "
            f"have declared goal links ({links}). These checks may inform the named predicates; "
            "a declared link is not scientific proof or goal completion. "
            + (f"Other unresolved predicates remain open: {', '.join(sorted(remaining))}. " if remaining else "")
            + "Replication and healthy local routes stay preserved; another local qualification is not application progress.")
    elif (goal.get("status") == UNKNOWN or any(c["truth"] == UNKNOWN for c in goal.get("conditions", []))) and not goal_history:
        kind, reason = "RESOLVE_PREMISE", "The original goal has unresolved evidence; no scientific failure is established."
    elif ("PREDICTION_PREMISES_UNRESOLVED" in flags and not supported_route) or (not ready_ids and (pending or blocked)):
        kind, reason = "RESOLVE_PREMISE", "Resolve the affected evidence, prediction scope, method or budget conditions first."
    elif flags & {"SEARCH_TRUNCATED", "DEPENDENCY_MAP_INCOMPLETE"}:
        kind, reason = "RESOLVE_PREMISE", "The bounded search omitted part of the supplied scope."
    elif loop_flags & {"REPEAT_REJECTED_ROUTE", "REPEAT_DECLARED_REJECTED_DOMAIN"}:
        kind, reason = "REFORMULATE", "Recorded choices repeat a rejected route/domain within the reviewed scope."
    elif "DECISION_OSCILLATION" in loop_flags:
        kind, reason = "REVIEW_DECISION_HISTORY", (
            "Recorded choices oscillate within the reviewed scope; this order is not a rejected route or a method-failure diagnosis.")
    elif unlinked:
        kind, reason = "REVIEW_GOAL_LINK", "The ready actions have no declared dependency path to an explicit original goal."
    elif review["basis"] == "SCOPED_OBLIGATION" and goal.get("status") != FALSE:
        return None
    elif "RIVAL_PREDICTIONS_OVERLAP" in flags and not supported_route:
        kind, reason = "DESIGN_DISCRIMINATOR", "Supported same-scope prediction sets overlap; seek a distinguishing observation."
    elif goal_history:
        kind, reason = "REFORMULATE", (
            f"The ready action changes only parameters of {goal_rejections['rejected_routes']} route(s) recorded as rejected "
            "for this goal revision and acceptance predicates; compare a changed premise, representation or method with the smallest repair. "
            "Recorded rejections are scoped choices, not a capacity bound or a guilty premise.")
    elif goal.get("status") == FALSE:
        kind, reason = "DIAGNOSE_GOAL_GAP", (
            "The reported goal predicate failed; this is a scoped gap, not a failed-method diagnosis, "
            "a capacity lower bound or evidence that the goal needs reformulation.")
    elif {"SINGLE_CONFIGURED_DIRECTION", "RIVAL_PREDICTIONS_MISSING"} <= flags and not supported_route:
        kind, reason = "REVIEW_ALTERNATIVE", "One procedure was supplied; review a useful alternative if it could change the decision."
    elif "RIVAL_PREDICTIONS_MISSING" in flags and not supported_route:
        kind, reason = "DESIGN_DISCRIMINATOR", "The supplied procedures do not yet bind competing predictions to outcomes."
    elif not review["ready_graph_directions"]:
        kind, reason = "RESOLVE_PREMISE", "No ready direction is defined; inspect the existing queries and candidate reviews."
    else:
        return None
    prompt = (
        "Resolve only the affected evidence or scope using original sources and explicit predicates; keep UNKNOWN where unsupported. "
        "State the smallest deciding observation or proof check and its stop condition. Continue independent authorized work. "
        if kind == "RESOLVE_PREMISE" else
        "Name the original acceptance condition and explain how this action's output resolves a remaining obligation or decision. "
        "Retain useful lemmas and diagnostics; a missing declaration does not show they are useless. "
        "Supply a sourced dependency path without treating the text declaration as an evaluated graph, proof or goal completion. "
        if kind == "REVIEW_GOAL_LINK" else
        "Retain the existing rival hypotheses and design one observation or scoped proof check with different predictions. "
        "Give the counterfactual difference each rival predicts, the check's cost and a stop condition. "
        if kind == "DESIGN_DISCRIMINATOR" else
        GOAL_GAP_TEXT
        if kind == "DIAGNOSE_GOAL_GAP" else
        "Inspect the recorded choices and their reasons, original evidence and current goal acceptance. "
        "Determine whether returning to the route is justified by changed evidence or an unfinished obligation; "
        "compare alternatives only if that review warrants it. The sequence alone does not justify changing method. "
        if kind == "REVIEW_DECISION_HISTORY" else
        "Propose one concrete candidate changing an assumption, representation or computational method, and compare it with the smallest repair. "
        "Give a counterfactual difference: which observation or scoped proof obligation differs if the proposed change is made? "
        "State a decisive check, its cost and a stop condition; added complexity is not itself progress. "
        "Renaming alone supplies no new mechanism; route identity here is structured input, not semantic equivalence. "
    )
    source = ("selection_review.goal.conditions" if kind == "DIAGNOSE_GOAL_GAP" else
              "search.loop_review.flags" if kind in {"REFORMULATE", "REVIEW_DECISION_HISTORY"} else
              "selection_review")
    return {"kind": kind, "reason": reason, "basis": "INPUT_REVIEW_HEURISTIC_NOT_SCIENTIFIC_PROOF", "source": source,
            "authorization": "UNCHANGED",
            "preserve_refs": ["search.decision", "context.budget", "context.resources", "context.method_constraints"],
            "prompt": prompt + MOVE_PRESERVE_CLAUSES}


def review_selection(search, context, *, _dependency=None, audit_receipts=False, audit_files=False,
                     _read_receipt=None):
    """Expose what the supplied directions can decide; never invent utility."""
    flags, candidates = [], []
    dependency = _dependency() if _dependency is not None else \
        _dependency_review(context, audit_receipts=audit_receipts, audit_files=audit_files,
                           read_receipt=_read_receipt)
    from rds_advisor_coverage import assess
    search['analysis_coverage'] = assess(search, dependency)
    if not search['analysis_coverage']['full']:
        flags.append({'kind': 'COMPLETE_GRAPH_ANALYSIS_REQUIRED',
                      'next': 'Resolve analysis_coverage before choosing or dispatching any experiment; preserve all project graph records.'})
        for candidate in search.get('candidates', []):
            if candidate.get('status') == 'READY':
                candidate.update(status='NEEDS_COMPLETE_ANALYSIS', local_status='READY')
    ready = [c for c in search.get('candidates', []) if c.get('status') == 'READY']
    if search.get("truncation", {}).get("truncated"):
        flags.append({"kind": "SEARCH_TRUNCATED", "next": "Review the omitted search scope before claiming a best route."})
    obligations = all(c.get("action", {}).get("kind") == "OBLIGATION_CHECK" for c in ready)
    if len(ready) == 1 and not obligations:
        flags.append({"kind": "SINGLE_CONFIGURED_DIRECTION", "next": "Only one ready graph direction was supplied; review a serious alternative when it could change the decision."})
    for c in ready:
        report = {"id": c["id"], "basis": "SCOPED_OBLIGATION"}
        if c.get("action", {}).get("kind") != "OBLIGATION_CHECK":
            disc = c.get("discrimination")
            if disc is None:
                report["basis"] = "PROCEDURE_ONLY"
                flags.append({"kind": "RIVAL_PREDICTIONS_MISSING", "candidate": c["id"],
                              "next": "Bind same-scope rival predictions to observed outcomes, or describe this as a premise/procedure check."})
            elif not disc["valid_prediction_support"]:
                report["basis"] = "PREDICTION_PREMISES_UNRESOLVED"
                flags.append({"kind": "PREDICTION_PREMISES_UNRESOLVED", "candidate": c["id"],
                              "next": "Resolve the prediction's evidence and scope conditions before using its rival coverage."})
            elif not disc["conditional_distinguishing_pairs"]:
                report["basis"] = "NONDISCRIMINATING"
                flags.append({"kind": "RIVAL_PREDICTIONS_OVERLAP", "candidate": c["id"],
                              "next": "Find an observation with different rival predictions; shared pass/fail labels do not distinguish causes."})
            else:
                report.update(basis="CONDITIONAL_RIVAL_TEST", distinguishing_pairs=len(disc["conditional_distinguishing_pairs"]),
                              unresolved_pairs=len(disc["unresolved_pairs"]))
                if disc["unresolved_pairs"]:
                    flags.append({"kind": "RIVAL_PREDICTIONS_OVERLAP", "candidate": c["id"],
                                  "next": "Some rival predictions still overlap; identify which additional observation would distinguish them."})
        contribution = _goal_contribution(c.get("action", {}), context, dependency)
        if contribution is not None:
            report["goal_contribution"] = contribution
            if contribution["status"] != "DECLARED_PATH":
                flags.append({"kind": "GOAL_CONTRIBUTION_UNDECLARED" if contribution["status"] == "UNDECLARED" else "GOAL_CONTRIBUTION_INVALID",
                              "candidate": c["id"], "next": contribution["reason"]})
        candidates.append(report)
    if dependency is not None and dependency['status'] != 'ANALYZED':
        flags.append({"kind": "DEPENDENCY_MAP_INCOMPLETE",
                      "next": "Repair the map or inspect its truncated scope; missing blocker sets do not close the goal."})
    basis = "NO_READY_DIRECTION" if not ready else "SCOPED_OBLIGATION" if obligations else "REVIEW_ONLY"
    # Ranking can also compare conditional routes whose prerequisites remain open.
    ready_ids = {c["id"] for c in ready}
    if any(row["better"] in ready_ids and row["worse"] in ready_ids
           for row in search.get("ranking", {}).get("dominance", [])):
        basis = "CONDITIONAL_COMPARISON"
    review = {"basis": basis, "ready_graph_directions": len(ready), "candidates": candidates, "flags": flags,
              "assurance": "INPUT_REPORTED_NOT_SCIENTIFIC_VERIFICATION", "authorization": "UNCHANGED"}
    if dependency is not None:
        review["dependency_review"] = dependency
    if "objective_binding" in context:
        review["objective_binding"] = deepcopy(context["objective_binding"])
    decision = context.get("decision")
    if isinstance(decision, dict) and "goal_conditions" in decision:
        goals = decision["goal_conditions"]
        if not (isinstance(goals, list) and 1 <= len(goals) <= 32 and all(
                isinstance(g, dict) and isinstance(g.get("fact"), str) and g["fact"].strip() for g in goals)):
            raise ValueError("decision.goal_conditions must be 1 to 32 explicit fact predicates")
        facts = context.get("facts", {})
        if not isinstance(facts, dict):
            raise ValueError("Context facts must be an object for decision.goal_conditions")
        reports = [evaluate_condition(g, facts) for g in goals]
        review["goal"] = {"status": _all(reports), "conditions": reports, "assurance": "INPUT_REPORTED"}
        if review["goal"]["status"] != TRUE:
            flags.append({"kind": "GOAL_BRIDGE_OPEN", "next": "Keep task acceptance separate from local/procedure success; choose a check or intervention that can close this declared gap."})
    declared = _affirmation_predicates(decision) if isinstance(decision, dict) else None
    triple_flag = None
    if declared is not None:
        triple = review["goal"]["triple_affirmative"] = _triple_affirmative(declared, reports, goals, facts)
        if review["goal"]["status"] == TRUE and triple["status"] != TRUE:
            triple_flag = {"kind": "TRIPLE_AFFIRMATIVE_OPEN", "open": triple["open"],
                           "next": "The goal predicates compare TRUE, but the declared Triple Affirmative is not met; "
                                   "affirm or refute each open affirmative before calling this a solution."}
            flags.append(triple_flag)
    next_move = _next_move(review, search)
    # Existing moves keep precedence; this fills only the slot a TRUE goal would otherwise leave empty.
    if next_move is None and triple_flag is not None:
        next_move = _affirmative_move(review["goal"]["triple_affirmative"])
        # The flag that explains the move comes first, so a bounded digest keeps it.
        flags.remove(triple_flag)
        flags.insert(0, triple_flag)
    if next_move is not None:
        review["next_move"] = next_move
    return review


def search_directions(graph, context, *, max_candidates=12, max_depth=8, max_nodes=128,
                      templates=None, max_combinations=128, max_compose_depth=2,
                      _dependency=None, _defer_selection_review=False, _frontier=None,
                      audit_receipts=False, audit_files=False, priority_action_ids=()):
    """Compose source-labelled checks and tests for the supplied next decision.

    Nodes opt in via executable.decisions, preconditions, satisfied_when, action.
    Only prerequisite_for edges are traversed; other relations stay descriptive.
    UNKNOWN prerequisites produce read-only queries, FALSE prerequisites prevent
    the downstream test. A configured condition.on_false can offer a repair.
    """
    if not isinstance(graph, dict) or not isinstance(context, dict):
        raise ValueError("Graph and advisor context must be objects")
    if (not isinstance(priority_action_ids, (tuple, list, set, frozenset)) or len(priority_action_ids) > 64
            or not all(isinstance(ident, str) and 1 <= len(ident) <= 128 for ident in priority_action_ids)):
        raise ValueError("priority_action_ids must contain up to 64 bounded action IDs")
    priority_action_ids = frozenset(priority_action_ids)
    for value, cap, name in ((max_candidates, 64, "max_candidates"), (max_depth, 32, "max_depth"), (max_nodes, 512, "max_nodes")):
        if type(value) is not int or not 1 <= value <= cap:
            raise ValueError(f"{name} must be an integer between 1 and {cap}")
    raw_nodes, raw_edges = graph.get("nodes", []), graph.get("edges", [])
    if not isinstance(raw_nodes, list) or not isinstance(raw_edges, list):
        raise ValueError("Graph nodes/edges must be lists")
    # Validate the whole input before honoring any computation bound. Invalid
    # records beyond a prefix are not silently omitted from the review.
    nodes = {}
    for node in raw_nodes:
        if not isinstance(node, dict) or not isinstance(node.get('id'), str) or not node['id'] or node['id'] in nodes:
            raise ValueError('Each graph node needs a unique nonempty string id')
        nodes[node['id']] = node
    for edge in raw_edges:
        if (not isinstance(edge, dict) or not isinstance(edge.get('relation'), str)
                or not edge['relation'] or not isinstance(edge.get('from'), str)
                or not isinstance(edge.get('to'), str)):
            raise ValueError('Each graph edge needs explicit from/to/relation strings')
    decision = context.get("decision")
    decision_id = decision.get("id") if isinstance(decision, dict) else decision
    targets = decision.get("target_rules") if isinstance(decision, dict) else None
    current_choice = decision.get("current_choice") if isinstance(decision, dict) else None
    result = {"advisor_type": "EXECUTABLE_DIRECTION_SEARCH", "decision": deepcopy(decision), "candidates": [],
              "blocked_candidates": [], "discarded_candidates": [], "queries": [], "cycles": [],
              "truncation": {"truncated": False, "reasons": [], "limits": {
                  "max_candidates": max_candidates, "max_depth": max_depth, "max_nodes": max_nodes}},
              "ranking": {"method": "PARETO_PARTIAL_ORDER", "dominance": [], "cost_unknown": []},
              "limitations": ["Derivations are reasoning dependencies, not causal proof. Imported source labels do not certify causal claims.",
                               "Only explicit executable configuration is searched; text triggers and unconfigured rules are not evaluated.",
                               "Dominance requires valid same-scope rival predictions; decision labels alone do not establish scientific value."]}
    graph_sha = hashlib.sha256(json.dumps(graph, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    from rds_project import digest
    try:
        result['context_sha256'] = digest(context)
    except (ValueError, TypeError):
        # Invalid dependency input still gets its read-only diagnostic. It
        # cannot carry a selection identity or authorize a candidate.
        result['context_sha256'] = None
    result['graph_coverage'] = {'status': 'INCOMPLETE', 'full': False, 'input_sha256': graph_sha,
                              'node_count': len(nodes), 'edge_count': len(raw_edges),
                              'analyzed_nodes': [], 'analyzed_edges': [],
                              'reasons': [] if result['context_sha256'] else ['Context has no valid analysis identity']}
    if 'frontier' in context:
        from rds_advisor_coverage import _operation_frontier
        frontier = _frontier if _frontier is not None else _operation_frontier(context)
        result['frontier_coverage'] = deepcopy(frontier['coverage'])
    def finish():
        chosen_templates = templates if templates is not None else context.get("templates")
        dependency = _dependency
        if dependency is None and chosen_templates is not None and "dependency_map" in context:
            dependency = _operation_dependency(context, audit_receipts=audit_receipts, audit_files=audit_files)
        if chosen_templates is not None:
            from rds_experiments import compose_experiments
            result["experiment_composition"] = compose_experiments(
                graph, context, chosen_templates, max_candidates=max_candidates, max_depth=max_compose_depth,
                max_combinations=max_combinations, _dependency=dependency,
                search_limits={"max_candidates": max_candidates, "max_depth": max_depth, "max_nodes": max_nodes})
        if not _defer_selection_review:
            result["selection_review"] = review_selection(result, context, _dependency=dependency,
                                                          audit_receipts=audit_receipts, audit_files=audit_files)
        return result
    if len(raw_nodes) > max_nodes or len(raw_edges) > 4096:
        result["truncation"].update(truncated=True)
        reason = 'node limit' if len(raw_nodes) > max_nodes else 'edge limit'
        result["truncation"]["reasons"].append(reason)
        result['graph_coverage']['reasons'].append(reason)
        return finish()
    if not isinstance(decision_id, str) or not decision_id.strip():
        result["limitations"].append("No next decision supplied; no test was inferred.")
    facts, costs = context.get("facts", {}), context.get("costs", {})
    if not isinstance(facts, dict) or not isinstance(costs, dict):
        raise ValueError("Context facts/costs must be objects")
    budget = context.get("budget", {})
    parents = {n: [] for n in nodes}
    for edge in raw_edges:
        if isinstance(edge, dict) and edge.get("relation") == "prerequisite_for" and edge.get("to") in nodes:
            parents[edge["to"]].append(edge.get("from"))
    queries = {}
    candidates = {}
    memo = {}

    def discard(rule_id, action, reason):
        record = {"rule_id": rule_id, "reason": reason}
        if isinstance(action, dict) and isinstance(action.get("id"), str):
            record.update(id=f"{rule_id}:{action['id']}", action_id=action["id"])
        result["discarded_candidates"].append(record)

    def query(rule_id, condition, reason):
        name = condition.get("fact", f"rule:{rule_id}:satisfied")
        query_id = f"query:{rule_id}:{name}"
        item = {"id": query_id, "rule_id": rule_id, "fact": name, "reason": reason,
                "kind": "READ_ONLY_EVIDENCE_REQUEST", "query": condition.get("query") or
                f"Read the code/log or original receipt for '{name}'; record its value and exact source locator under the same run identity.",
                "outcomes": [{"observation": "required predicate supported", "next_decision": "allow dependent check"},
                             {"observation": "required predicate contradicted", "next_decision": "block or revise dependent test"}]}
        queries[query_id] = item
        return query_id

    def emit(rule_id, action, status, derivation, pending=()):
        valid, reason = _action_valid(action, current_choice)
        if not valid:
            discard(rule_id, action, reason)
            return
        candidate_id = f"{rule_id}:{action['id']}"
        if candidate_id in candidates:
            return
        full = len(candidates) >= max_candidates
        steps = [deepcopy(queries[q]) for q in dict.fromkeys(pending)]
        steps.append({"id": action["id"], "rule_id": rule_id, "kind": action.get("kind", "BOUNDED_CHECK"),
                      "description": action["description"], "conditional": status != "READY",
                      "stop_condition": action.get("stop_condition", "Stop on budget limit or invalid prerequisites.")})
        cost = _cost([step["id"] for step in steps], costs)
        budget_status = UNKNOWN
        if cost["status"] != UNKNOWN and _reported(budget) and _finite(budget.get("value")) and budget["value"] >= 0:
            if _cost_identity(budget) == _cost_identity(cost):
                budget_status = "WITHIN_REPORTED_BUDGET" if cost["value"] <= budget["value"] else "OVER_REPORTED_BUDGET"
        candidate = {"id": candidate_id, "rule_id": rule_id, "status": status, "action": deepcopy(action),
                     "competing_explanations": deepcopy(action.get("competing_explanations", [])),
                     "required_observables": deepcopy(action["required_observables"]), "outcomes": deepcopy(action["outcomes"]),
                     "decision_coverage": sorted({o["next_decision"] for o in action["outcomes"]}),
                     "steps": steps, "derivation": deepcopy(derivation) + [{"step": "action", "rule_id": rule_id,
                         "action_id": action["id"], "reason": "outcomes distinguish declared next decisions"}],
                     "incremental_cost": cost, "budget_status": budget_status, "dominated_by": [],
                     "evidence_status": "INPUT_REPORTED"}
        if "discrimination" in action:
            candidate["discrimination"] = _discrimination(action, facts)
        from rds_methods import review_candidate
        review_candidate(context, candidate)
        # Resource-only blockage requires resolved evidence and method gates.
        # Keep budget_status on all candidates without hiding earlier blockers.
        if budget_status == "OVER_REPORTED_BUDGET" and candidate["status"] == "READY":
            candidate["status"] = "BLOCKED_BUDGET"
            result["blocked_candidates"].append(candidate)
        elif candidate["status"] == "BLOCKED_METHOD":
            result["blocked_candidates"].append(candidate)
        else:
            if full:
                result['truncation']['truncated'] = True
                if 'candidate limit' not in result['truncation']['reasons']:
                    result['truncation']['reasons'].append('candidate limit')
                if action['id'] not in priority_action_ids:
                    return
                # An owned active route may be emitted by any root or fallback.
                # It can replace a nonpriority slot only after the same method,
                # budget and prerequisite checks establish current readiness.
                replace = next((key for key in reversed(candidates)
                                if candidates[key]['action']['id'] not in priority_action_ids), None)
                if candidate['status'] != 'READY' or replace is None:
                    return
                del candidates[replace]
            candidates[candidate_id] = candidate

    def conditions(rule_id, cfg, key, derivation, pending, fallbacks):
        items = cfg.get(key, [])
        if not isinstance(items, list) or len(items) > 32 or not all(isinstance(c, dict) and isinstance(c.get("fact"), str) for c in items):
            raise ValueError(f"{rule_id}.{key} must be up to 32 explicit fact conditions")
        reports = []
        for condition in items:
            if not isinstance(condition.get('op', 'eq'), str) or condition.get('op', 'eq') not in {'eq', 'ne', 'in', 'lt', 'lte', 'gt', 'gte'}:
                raise ValueError(f'{rule_id}.{key} has an unsupported condition operator')
            report = evaluate_condition(condition, facts)
            derivation.append({"step": "fact_to_rule", "rule_id": rule_id, "role": key, **report})
            reports.append(report)
            if report["truth"] == UNKNOWN:
                pending.append(query(rule_id, condition, report["reason"]))
            elif report["truth"] == FALSE and isinstance(condition.get("on_false"), dict):
                fallbacks.append((rule_id, condition["on_false"], deepcopy(derivation)))
        return _all(reports)

    def visit(rule_id, path, derivation, pending, fallbacks, as_prerequisite=True):
        if rule_id in path:
            cycle = path[path.index(rule_id):] + [rule_id]
            if cycle not in result["cycles"]:
                result["cycles"].append(cycle)
            derivation.append({"step": "cycle", "path": cycle})
            return FALSE
        if len(path) >= max_depth:
            result["truncation"].update(truncated=True)
            if "depth limit" not in result["truncation"]["reasons"]:
                result["truncation"]["reasons"].append("depth limit")
            derivation.append({"step": "depth_limit", "rule_id": rule_id})
            return FALSE
        if as_prerequisite and rule_id in memo:
            derivation.append({"step": "dependency_reuse", "rule_id": rule_id, "truth": memo[rule_id]})
            return memo[rule_id]
        node = nodes.get(rule_id, {})
        cfg = node.get("executable")
        if not isinstance(cfg, dict) or not isinstance(cfg.get("preconditions"), list):
            pending.append(query(rule_id, {"query": f"Review original rule '{rule_id}', its scope and sources; supply explicit bounded prerequisite conditions before treating it as satisfied."}, "no executable prerequisite configuration"))
            derivation.append({"step": "unconfigured_prerequisite", "rule_id": rule_id, "truth": UNKNOWN})
            memo[rule_id] = UNKNOWN
            return UNKNOWN
        applicability = conditions(rule_id, cfg, "preconditions", derivation, pending, fallbacks)
        if applicability == FALSE:
            # Exact conjunction short-circuit. The whole-graph pass still
            # evaluates every parent independently; irrelevant fallback actions
            # must not become candidates for a disabled child.
            memo[rule_id] = FALSE
            return FALSE
        statuses = [applicability]
        for parent in dict.fromkeys(parents.get(rule_id, [])):
            derivation.append({"step": "dependency", "from": parent, "relation": "prerequisite_for", "to": rule_id})
            statuses.append(visit(parent, path + [rule_id], derivation, pending, fallbacks))
        if as_prerequisite:
            if not isinstance(cfg.get("satisfied_when"), list) or not cfg["satisfied_when"]:
                pending.append(query(rule_id, {}, "no explicit completion predicate"))
                statuses.append(UNKNOWN)
            else:
                statuses.append(conditions(rule_id, cfg, "satisfied_when", derivation, pending, fallbacks))
        truth = FALSE if FALSE in statuses else UNKNOWN if UNKNOWN in statuses else TRUE
        memo[rule_id] = truth
        return truth

    # Evaluate every node, including disconnected and nonselected directions.
    # Candidate generation remains scoped to the declared decision below.
    coverage = result['graph_coverage']
    for rid in nodes:
        memo.clear()
        derivation, pending, fallbacks = [], [], []
        truth = visit(rid, [], derivation, pending, fallbacks, as_prerequisite=False)
        cfg = nodes[rid].get('executable')
        completion = []
        satisfaction = UNKNOWN
        if isinstance(cfg, dict):
            satisfaction = conditions(rid, cfg, 'satisfied_when', completion, [], [])
            if not completion:
                satisfaction = UNKNOWN
        action = cfg.get('action') if isinstance(cfg, dict) else None
        valid, reason = _action_valid(action, current_choice)
        coverage['analyzed_nodes'].append({'id': rid, 'action_readiness': truth, 'satisfaction': satisfaction,
                                          'disposition': 'EVALUATED' if isinstance(nodes[rid].get('executable'), dict)
                                                         else 'UNCONFIGURED_UNKNOWN',
                                          'derivation': derivation, 'completion_conditions': completion,
                                          'action_validation': {'valid': valid, 'reason': reason},
                                          'discrimination': _discrimination(action, facts) if valid
                                              and 'discrimination' in action else None})
    for index, edge in enumerate(raw_edges):
        missing = [edge[k] for k in ('from', 'to') if edge[k] not in nodes]
        coverage['analyzed_edges'].append({'index': index, 'from': edge['from'], 'to': edge['to'],
            'relation': edge['relation'], 'disposition': 'MISSING_ENDPOINT' if missing else
                'PREREQUISITE_EVALUATED' if edge['relation'] == 'prerequisite_for' else 'DESCRIPTIVE_ONLY',
            'reason': 'Missing endpoint: ' + ', '.join(missing) if missing else
                'Explicit prerequisite relation' if edge['relation'] == 'prerequisite_for' else
                'Declared descriptive relation; supplies no executable inference'})
        if missing:
            coverage['reasons'].append('Graph edge has missing endpoint: ' + ', '.join(missing))
    coverage['reasons'].extend(result['truncation']['reasons'])
    coverage.update(full=not coverage['reasons'], status='INCOMPLETE' if coverage['reasons'] else 'FULL')
    roots = [rid for rid, node in nodes.items() if isinstance(decision_id, str) and isinstance(node.get("executable"), dict)
             and decision_id in node["executable"].get("decisions", []) and (targets is None or rid in targets)]
    for rid in roots:
        memo.clear()
        action = nodes[rid]["executable"].get("action")
        valid, reason = _action_valid(action, current_choice)
        if not valid:
            discard(rid, action, reason)
            continue
        derivation = [{"step": "decision_to_rule", "decision": decision_id, "rule_id": rid,
                       "rule_sources": deepcopy(nodes[rid].get("sources", []))}]
        pending, fallbacks = [], []
        status = visit(rid, [], derivation, pending, fallbacks, as_prerequisite=False)
        for fallback_rid, fallback, chain in fallbacks:
            emit(fallback_rid, fallback, "READY", chain)
        if status == FALSE:
            result["blocked_candidates"].append({"rule_id": rid, "action_id": action["id"], "status": "BLOCKED_PREREQUISITE",
                                                  "reason": "false prerequisite, cycle or bounded-search limit", "derivation": derivation})
        else:
            emit(rid, action, "READY" if status == TRUE else "NEEDS_EVIDENCE", derivation, pending)
    result["candidates"] = list(candidates.values())
    used_queries = {step["id"] for candidate in result["candidates"] for step in candidate["steps"] if step["kind"] == "READ_ONLY_EVIDENCE_REQUEST"}
    result["queries"] = [q for qid, q in queries.items() if qid in used_queries]
    for worse in result["candidates"]:
        wcost = worse["incremental_cost"]
        if wcost["status"] == UNKNOWN:
            result["ranking"]["cost_unknown"].append(worse["id"])
            continue
        for better in result["candidates"]:
            bcost = better["incremental_cost"]
            if better is worse or bcost["status"] == UNKNOWN or better["status"] != worse["status"]:
                continue
            if _cost_identity(bcost) != _cost_identity(wcost):
                continue
            bdisc, wdisc = better.get("discrimination"), worse.get("discrimination")
            if (bdisc is None or wdisc is None or not bdisc["valid_prediction_support"]
                    or not wdisc["valid_prediction_support"] or bdisc["scope_id"] != wdisc["scope_id"]
                    or set(better["competing_explanations"]) != set(worse["competing_explanations"])):
                continue
            bpairs = {tuple(pair) for pair in bdisc["conditional_distinguishing_pairs"]}
            wpairs = {tuple(pair) for pair in wdisc["conditional_distinguishing_pairs"]}
            if not bpairs or not wpairs or not bpairs >= wpairs:
                continue
            if bcost["value"] <= wcost["value"] and (bpairs != wpairs or bcost["value"] < wcost["value"]):
                worse["dominated_by"].append(better["id"])
                result["ranking"]["dominance"].append({"better": better["id"], "worse": worse["id"],
                    "basis": "supplied same-scope conditional distinguishing pairs are a superset and no higher comparable sourced incremental cost"})
    result["ranking"]["pareto_front"] = [c["id"] for c in result["candidates"] if not c["dominated_by"]]
    return finish()
