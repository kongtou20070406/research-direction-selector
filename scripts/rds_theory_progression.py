"""Bind the existing bounded model, EGraph and native Lean checks to one claim."""
import hashlib
import json
from fractions import Fraction

from rds_verify_types import canonical, digest, rational

SCHEMA = 1
CONCISE_SCHEMA = 2
CLAIM_ID = "finite_rational_multiplication_commutes"
STAGES = ("bounded_finite_model", "egraph_equivalence_saturation", "native_lean")
TRANSPORTS = (
    "finite_table_is_exact_rational_multiplication",
    "egraph_uses_the_same_rational_multiplication",
    "native_lean_checks_each_declared_domain_pair",
)
MAX_SPEC_BYTES = 16 * 1024
MAX_DOMAIN_SIZE = 4
MAX_NATIVE_PAIRS = 16
MAX_EGRAPH_ITERATIONS = 16


class InvalidProgression(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise InvalidProgression(message)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InvalidProgression("Repeated JSON key: " + key)
        result[key] = value
    return result


def load_spec(path):
    """Read one small JSON request, rejecting ambiguous JSON before validation."""
    with open(path, "rb") as stream:
        raw = stream.read(MAX_SPEC_BYTES + 1)
    _require(len(raw) <= MAX_SPEC_BYTES, "Progression specification exceeds 16 KiB")

    def reject_number(_value):
        raise InvalidProgression("JSON floats and non-finite constants are unsupported")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_pairs,
                          parse_float=reject_number, parse_constant=reject_number)
    except RecursionError as exc:
        raise InvalidProgression("Progression JSON nesting exceeds the parser limit") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidProgression("Invalid UTF-8 JSON progression specification") from exc


def _validate(spec):
    _require(isinstance(spec, dict), "Progression request must be an object")
    schema = spec.get("schema")
    _require(type(schema) is int, "Progression schema must be an integer")
    if schema == SCHEMA:
        _require(set(spec) == {
            "schema", "claim_id", "domain", "assumptions", "transport_obligations", "limits"
        }, "Schema 1 fields must be schema, claim_id, domain, assumptions, transport_obligations, limits")
        _require(spec["claim_id"] == CLAIM_ID, "Unsupported original proposition")
        _require(spec["assumptions"] == [], "This example accepts no added assumptions")
        _require(spec["transport_obligations"] == list(TRANSPORTS),
                 "Transport obligations are fixed and ordered")
    elif schema == CONCISE_SCHEMA:
        _require(set(spec) == {"schema", "domain", "limits"},
                 "Schema 2 fields are schema, domain, limits; claim and transports are supplied by the chain")
    else:
        raise InvalidProgression("Unsupported progression schema")
    _require(isinstance(spec["domain"], list) and 1 <= len(spec["domain"]) <= MAX_DOMAIN_SIZE,
             "Expected 1..4 exact rational domain values")
    values = [rational(item) for item in spec["domain"]]
    _require(len(set(values)) == len(values), "Domain values must be unique after rational normalization")
    limits = spec["limits"]
    _require(isinstance(limits, dict) and set(limits) == {
        "max_domain_size", "max_native_pairs", "egraph_iterations"
    }, "Limits must declare max_domain_size, max_native_pairs, egraph_iterations")
    _require(type(limits["max_domain_size"]) is int and len(values) <= limits["max_domain_size"]
             <= MAX_DOMAIN_SIZE, "Domain exceeds its declared limit of at most 4")
    _require(type(limits["max_native_pairs"]) is int and 1 <= limits["max_native_pairs"]
             <= MAX_NATIVE_PAIRS, "Native pair limit must be an integer in 1..16")
    _require(type(limits["egraph_iterations"]) is int and 1 <= limits["egraph_iterations"]
             <= MAX_EGRAPH_ITERATIONS, "EGraph iteration limit must be an integer in 1..16")
    _require(len(canonical(spec).encode("utf-8")) <= MAX_SPEC_BYTES,
             "Progression specification exceeds 16 KiB")
    return values


def _claim(values):
    return {
        "id": CLAIM_ID,
        "statement": "For all x,y in D, x*y = y*x under exact rational multiplication.",
        "assumptions": [],
        "domain": [str(value) for value in values],
        "operation": "exact rational multiplication",
        "transport_obligations": list(TRANSPORTS),
    }


def _stage(name, claim_sha256, previous_stage_sha256, result):
    binding = {"name": name, "claim_sha256": claim_sha256,
               "previous_stage_sha256": previous_stage_sha256, "result": result}
    return {**binding, "stage_sha256": digest(binding)}


def _check_handoff(envelope, expected_name, claim_sha256, previous_stage_sha256):
    if not isinstance(envelope, dict) or set(envelope) != {
        "name", "claim_sha256", "previous_stage_sha256", "result", "stage_sha256"
    }:
        return False
    binding = {key: envelope[key] for key in
               ("name", "claim_sha256", "previous_stage_sha256", "result")}
    try:
        return (envelope["name"] == expected_name and envelope["claim_sha256"] == claim_sha256
                and envelope["previous_stage_sha256"] == previous_stage_sha256
                and envelope["stage_sha256"] == digest(binding))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return False


def _skipped(name, claim_sha256, previous, reason):
    return _stage(name, claim_sha256, previous,
                  {"status": "SKIPPED", "assurance": "NONE", "reason": reason})


def _invalid_report(reason):
    claim_sha256 = digest({"invalid_input": str(reason)})
    stages, previous = [], None
    for name in STAGES:
        item = _skipped(name, claim_sha256, previous, "Input was rejected before dispatch")
        stages.append(item)
        previous = item["stage_sha256"]
    return {"status": "UNKNOWN", "assurance": "NONE", "scientific_assurance": "UNKNOWN",
            "claim_sha256": claim_sha256, "reason": str(reason), "stages": stages,
            "stage_limits": {"spec_bytes": MAX_SPEC_BYTES, "domain_size": MAX_DOMAIN_SIZE,
                             "native_pairs": MAX_NATIVE_PAIRS, "egraph_iterations": MAX_EGRAPH_ITERATIONS}}


def run_progression(spec, *, native_verify=None):
    """Run one fixed three-check example without accepting imported stage verdicts."""
    try:
        values = _validate(spec)
    except (InvalidProgression, TypeError, ValueError, ZeroDivisionError, OverflowError, UnicodeError) as exc:
        return _invalid_report(str(exc))

    claim = _claim(values)
    claim_sha256 = digest(claim)
    stages, previous_sha256 = [], None
    labels = [str(value) for value in values]
    from rds_operators import BoundedFiniteModelOperator, EGraphEquivalenceOperator

    table = {}
    for left, a in zip(labels, values):
        for right, b in zip(labels, values):
            product = a * b
            table[left, right] = str(product)
    encoded_table = {left + "|" + right: product for (left, right), product in table.items()}
    table_sha256 = hashlib.sha256(canonical(encoded_table).encode("utf-8")).hexdigest()
    model_result = BoundedFiniteModelOperator.verify_cayley_property(labels, table, "commutative")
    model_ok = (model_result.get("status") == "PASS"
                and model_result.get("assurance") == "BOUNDED_FINITE_MODEL_VERIFIED"
                and model_result.get("property") == "commutative"
                and model_result.get("domain_size") == len(values)
                and model_result.get("combinations_checked") == len(values) ** 2)
    model_refutes = (model_result.get("status") == "FAIL"
                     and model_result.get("assurance") == "COUNTEREXAMPLE_FOUND"
                     and model_result.get("property") == "commutative"
                     and isinstance(model_result.get("counterexample"), dict)
                     and isinstance(model_result["counterexample"].get("witness"), list)
                     and len(model_result["counterexample"]["witness"]) == 2
                     and set(model_result["counterexample"]["witness"]) <= set(labels))
    finite = _stage("bounded_finite_model", claim_sha256, None,
                    {**model_result,
                     "status": "PASS" if model_ok else "FAIL" if model_refutes else "UNKNOWN",
                     "reported_status": model_result.get("status", "UNKNOWN"),
                     "table_sha256": table_sha256,
                     "domain": labels, "property_scope": "all ordered pairs in the declared finite domain",
                     "transport": {"obligation": TRANSPORTS[0],
                                   "status": "PASS" if model_ok else "FAIL" if model_refutes else "UNKNOWN",
                                   "operation": "exact rational multiplication",
                                   "table_sha256": table_sha256}})
    stages.append(finite)
    status = ("PASS" if model_ok else "FAIL" if model_refutes else "UNKNOWN")
    final_status = status if status in {"FAIL", "PASS"} else "UNKNOWN"
    if status != "PASS":
        if status == "FAIL" and not model_refutes:
            final_status = "UNKNOWN"
        why = "Finite-model stage did not pass; later stages were not run"
        previous_sha256 = finite["stage_sha256"]
        for name in STAGES[1:]:
            item = _skipped(name, claim_sha256, previous_sha256, why)
            stages.append(item)
            previous_sha256 = item["stage_sha256"]
        return {"status": final_status, "assurance": model_result.get("assurance", "NONE"),
                "scientific_assurance": "UNKNOWN", "application_status": "UNKNOWN",
                "claim": claim, "claim_sha256": claim_sha256, "declared_limits": spec["limits"], "stages": stages,
                "reason": "Finite-domain check did not establish the requested chain"}

    previous_sha256 = finite["stage_sha256"]
    if not _check_handoff(finite, STAGES[0], claim_sha256, None):
        for name in STAGES[1:]:
            item = _skipped(name, claim_sha256, previous_sha256, "Prior stage handoff failed integrity binding")
            stages.append(item)
            previous_sha256 = item["stage_sha256"]
        return {"status": "UNKNOWN", "assurance": "NONE", "scientific_assurance": "UNKNOWN",
                "application_status": "UNKNOWN", "claim": claim, "claim_sha256": claim_sha256,
                "declared_limits": spec["limits"], "stages": stages,
                "reason": "Finite-model handoff failed integrity binding"}

    egraph_result = EGraphEquivalenceOperator.verify_algebraic_equivalence(
        ("*", "a", "b"), ("*", "b", "a"), variables=("a", "b"),
        max_iter=spec["limits"]["egraph_iterations"])
    egraph_ok = (egraph_result.get("status") == "PASS"
                 and egraph_result.get("assurance") == "BOUNDED_REWRITE_CHECK"
                 and egraph_result.get("certificate_status") == "NOT_EMITTED"
                 and egraph_result.get("equivalent") is True
                 and egraph_result.get("domain") == EGraphEquivalenceOperator.DOMAIN
                 and egraph_result.get("variables") == ["a", "b"]
                 and isinstance(egraph_result.get("input_sha256"), str)
                 and len(egraph_result["input_sha256"]) == 64)
    egraph = _stage("egraph_equivalence_saturation", claim_sha256, previous_sha256,
                    {**egraph_result,
                     "status": "PASS" if egraph_ok else "UNKNOWN",
                     "reported_status": egraph_result.get("status", "UNKNOWN"),
                     "transport": {"obligation": TRANSPORTS[1],
                                   "status": "PASS" if egraph_ok else "UNKNOWN",
                                   "expression": ["*", "a", "b"],
                                   "equivalent_expression": ["*", "b", "a"],
                                   "scope": "rational_polynomials"}})
    stages.append(egraph)
    previous_sha256 = egraph["stage_sha256"]
    if not _check_handoff(egraph, STAGES[1], claim_sha256, finite["stage_sha256"]):
        item = _skipped(STAGES[2], claim_sha256, previous_sha256,
                        "EGraph handoff failed integrity binding")
        stages.append(item)
        return {"status": "UNKNOWN", "assurance": "NONE", "scientific_assurance": "UNKNOWN",
                "application_status": "UNKNOWN", "claim": claim, "claim_sha256": claim_sha256,
                "declared_limits": spec["limits"], "stages": stages,
                "reason": "EGraph handoff failed integrity binding"}
    if not egraph_ok:
        item = _skipped(STAGES[2], claim_sha256, previous_sha256,
                        "EGraph did not return its bounded rewrite PASS; no Lean stage was inferred")
        stages.append(item)
        return {"status": "UNKNOWN", "assurance": "NONE", "scientific_assurance": "UNKNOWN",
                "application_status": "UNKNOWN", "claim": claim, "claim_sha256": claim_sha256,
                "declared_limits": spec["limits"], "stages": stages,
                "reason": "EGraph result is inconclusive for this handoff"}

    if len(values) ** 2 > spec["limits"]["max_native_pairs"]:
        item = _skipped(STAGES[2], claim_sha256, previous_sha256,
                        "Declared native pair budget is below the finite-domain obligation count")
        stages.append(item)
        return {"status": "UNKNOWN", "assurance": "NONE", "scientific_assurance": "UNKNOWN",
                "application_status": "UNKNOWN", "claim": claim, "claim_sha256": claim_sha256,
                "declared_limits": spec["limits"], "stages": stages,
                "reason": "Native pair budget was exhausted before Lean dispatch"}

    if native_verify is None:
        from rds_verify import LeanFormalEngine
        native_verify = lambda obligation: LeanFormalEngine().verify(obligation, ("lean4",))
    leaves, leaf_failure, leaf_failed = [], None, False
    for left_index, a in enumerate(values):
        for right_index, b in enumerate(values):
            left_value = Fraction(table[labels[left_index], labels[right_index]])
            right_value = Fraction(table[labels[right_index], labels[left_index]])
            obligation = {"schema": 1, "kind": "lean_obligation", "relation": "eq",
                          "left": str(left_value), "right": str(right_value)}
            try:
                answer = native_verify(obligation)
            except (ValueError, TypeError, KeyError, OSError, RuntimeError) as exc:
                answer = {"status": "UNKNOWN", "assurance": "NONE", "reason": str(exc)}
            if not isinstance(answer, dict):
                answer = {"status": "UNKNOWN", "assurance": "NONE",
                          "reason": "Native Lean returned a malformed result"}
            certificate = answer.get("certificate") if isinstance(answer, dict) else None
            native_pass = (isinstance(answer, dict) and answer.get("status") == "PASS"
                           and answer.get("assurance") == "LEAN_KERNEL_CHECKED"
                           and isinstance(certificate, dict) and certificate.get("verdict") == "PASS"
                           and certificate.get("spec_sha256") == digest(obligation))
            leaf = {"pair": [labels[left_index], labels[right_index]],
                    "claim_sha256": claim_sha256, "table_sha256": table_sha256,
                    "obligation": obligation,
                    "status": "PASS" if native_pass else "UNKNOWN",
                    "assurance": "LEAN_KERNEL_CHECKED" if native_pass else "NONE",
                    "backend": answer.get("backend", "unknown"),
                    "attempts": answer.get("tactics", []),
                    "certificate": certificate if native_pass else None}
            leaves.append(leaf)
            if leaf["status"] != "PASS":
                leaf_failed = True
                attempts = answer.get("tactics", [])
                attempts = attempts if isinstance(attempts, list) else []
                attempt_reasons = [item["reason"] for item in attempts if isinstance(item, dict)
                                   and isinstance(item.get("reason"), str) and item["reason"].strip()]
                reason = answer.get("reason")
                leaf_failure = "; ".join(attempt_reasons) or (
                    reason if isinstance(reason, str) and reason.strip()
                    else "Native Lean did not check this pair")
                break
        if leaf_failed:
            break
    pairs_checked = sum(leaf["status"] == "PASS" for leaf in leaves)
    native_status = "PASS" if pairs_checked == len(values) ** 2 else "UNKNOWN"
    native = _stage("native_lean", claim_sha256, previous_sha256,
                    {"status": native_status,
                     "assurance": "LEAN_KERNEL_CHECKED" if native_status == "PASS" else "NONE",
                     "transport": {"obligation": TRANSPORTS[2], "status": native_status,
                                   "source_table_sha256": table_sha256,
                                   "pairs_required": len(values) ** 2,
                                   "pairs_checked": pairs_checked},
                     "leaves": leaves, "reason": leaf_failure})
    stages.append(native)
    chain_valid = all(_check_handoff(stages[index], STAGES[index], claim_sha256,
                                     None if index == 0 else stages[index - 1]["stage_sha256"])
                      for index in range(len(stages)))
    overall = "PASS" if chain_valid and native_status == "PASS" else "UNKNOWN"
    return {"status": overall,
            "assurance": "FINITE_DOMAIN_EXHAUSTIVE_PLUS_NATIVE_LEAVES" if overall == "PASS" else "NONE",
            "scientific_assurance": "UNKNOWN", "application_status": "UNKNOWN",
            "claim": claim, "claim_sha256": claim_sha256, "stages": stages,
            "declared_limits": spec["limits"],
            "reason": "Finite-domain equality was exhaustively checked and every instantiated closed-rational leaf was checked by native Lean"
                      if overall == "PASS" else "One or more required stages or handoffs remain unresolved"}
