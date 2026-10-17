#!/usr/bin/env python3
"""Bounded reference runner: a restricted rational AST, never Python eval/exec.

This checks a mathematical model, not Python floating point or a PyTorch export.
Affine rational claims use an independently checked exact certificate. Solver
fallback results are SYMBOLIC_CHECKED, not proof certificates.
"""
import ast
import csv
import io
import json
import sys
from fractions import Fraction
from pathlib import Path

# The isolated CLI worker deliberately imports only this locked sibling module.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from rds_formal_kernel import (ResourceLimit, STATEMENTS, UnsupportedExpression,
                               bounded, check_certificate, exact_probe)

from rds_scalar_values import parse_source, rational

FORMAL_KINDS = {"strict_algebraic_threshold", "contraction_boundary", "dynamics", "threshold_necessity", "declarative"}


def formal_requirement(hypothesis):
    """Only an explicit hypothesis declaration opts into a formal backend.

    A numeric hyperparameter or metric threshold is not a mathematical claim.
    Missing declarations cannot be inferred reliably from natural language.
    """
    if "formal_claim" in hypothesis:
        raise ValueError("Declare the mathematical claim in hypothesis.formal")
    claim = hypothesis.get("formal")
    if claim is None:
        return None
    if not isinstance(claim, dict) or not isinstance(claim.get("kind"), str) or claim["kind"] not in FORMAL_KINDS:
        raise ValueError("Unsupported formal.kind; use an explicit threshold or declarative statement")
    if claim["kind"] == "declarative":
        if set(claim) != {"kind", "statement"} or not isinstance(claim["statement"], dict):
            raise ValueError("Declarative formal requires exactly kind and statement")
        from rds_verify import _bounded_json
        _bounded_json(claim["statement"])
        return claim
    if claim.get("kind") != "dynamics":
        if claim.get("quantity", "scalar_property") != "scalar_property":
            raise ValueError("Reference adapter requires quantity=scalar_property; general matrix/dynamical claims need another verifier")
        if len(claim.get("domain", [])) != 2:
            raise ValueError("formal_claim requires a two-endpoint domain")
        lo, hi = map(rational, claim["domain"])
        if lo > hi:
            raise ValueError("Vacuous domain")
        rational(claim["threshold"])
        if rational(claim["max_loss"]) < 0:
            raise ValueError("Falsifier loss bound must be nonnegative")
        statement = claim.get("statement", "threshold_necessity" if claim["kind"] == "threshold_necessity"
                              else "threshold_separation")
        if statement not in STATEMENTS:
            raise ValueError("formal.statement must be threshold_separation or threshold_necessity")
        claim = {**claim, "statement": statement}
    return claim


def admission_probe(hypothesis, source):
    """AST only for ordinary plans; symbolic feasibility for declared boundaries.

    Admission never reads confirmation targets. Observed crossings are checked
    later by the bounded runner on the committed dataset.
    """
    formal = formal_requirement(hypothesis)
    functions = parse_source(source)
    if formal is None:
        return {"status": "PASS", "assurance": "AST_ONLY", "backend": "ast",
                "reason": "Syntax checked; no formal property was declared"}
    if formal["kind"] == "declarative":
        result = declarative_probe(formal)
        if result.get("application_status", "PASS") != "PASS":
            return dict(result, status="UNKNOWN", reason="Formal application assumptions remain UNKNOWN")
        return result
    if formal["kind"] == "dynamics":
        return {"status": "UNKNOWN", "assurance": "NONE",
                "reason": "Use formal.kind=declarative and an explicit affine dynamics statement"}
    try:
        return formal_probe(functions, formal)
    except (ImportError, NotImplementedError, ValueError, TypeError) as exc:
        return {"status": "UNKNOWN", "assurance": "NONE", "reason": str(exc)}


def declarative_probe(formal, committed=None):
    from rds_verify import checked_result, verify
    if committed is None:
        result = verify(formal["statement"])
    else:
        certificate = committed.get("certificate") if isinstance(committed, dict) else None
        result = checked_result(formal["statement"], certificate)
    return {**result, "claim_relation": "declared_side_condition_only",
            "observed_status": "NOT_APPLICABLE",
            "reason": "Declared mathematical model; no equivalence to runner or training execution was proved"}


def evaluate(node, x):
    if isinstance(node, ast.Constant):
        return Fraction(node.value)
    if isinstance(node, ast.Name):
        return x
    if isinstance(node, ast.UnaryOp):
        value = evaluate(node.operand, x)
        return -value if isinstance(node.op, ast.USub) else value
    left, right = evaluate(node.left, x), evaluate(node.right, x)
    if isinstance(node.op, ast.Add):
        return bounded(left + right)
    if isinstance(node.op, ast.Sub):
        return bounded(left - right)
    if isinstance(node.op, ast.Mult):
        return bounded(left * right)
    if isinstance(node.op, ast.Div):
        return bounded(left / right)
    return bounded(left ** int(right))


def read_rows(raw):
    reader = csv.DictReader(io.StringIO(raw))
    if reader.fieldnames != ["sample_id", "x", "y"]:
        raise ValueError("Expected CSV columns sample_id,x,y in that order")
    rows, ids = [], set()
    for row in reader:
        sid = row["sample_id"]
        if not sid or sid in ids or None in row:
            raise ValueError("Missing/duplicate sample identity or extra CSV field")
        ids.add(sid)
        rows.append((sid, rational(row["x"]), rational(row["y"])))
        if len(rows) > 10000:
            raise ValueError("Reference runner limited to 10000 paired samples")
    if not rows:
        raise ValueError("An empty dataset cannot confirm anything")
    return rows


def _symbolic_budget(functions):
    """Conservative numerator/denominator degree and coefficient bit bounds.

    This rejects explosive nested powers before asking SymPy to expand them.
    Bounds can reject a canceling expression; they never approximate its value.
    """
    def size(node):
        if isinstance(node, ast.Constant):
            return 0, 0, max(1, abs(node.value).bit_length()), 1
        if isinstance(node, ast.Name):
            return 1, 0, 1, 1
        if isinstance(node, ast.UnaryOp):
            return size(node.operand)
        n, d, nb, db = size(node.left)
        if isinstance(node.op, ast.Pow):
            power = node.right.value
            result = n * power, d * power, max(1, nb * power), max(1, db * power)
        else:
            other_n, other_d, other_nb, other_db = size(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                result = (max(n + other_d, other_n + d), d + other_d,
                          max(nb + other_db, other_nb + db) + 1, db + other_db)
            elif isinstance(node.op, ast.Mult):
                result = n + other_n, d + other_d, nb + other_nb, db + other_db
            else:
                result = n + other_d, d + other_n, nb + other_db, db + other_nb
        if max(result[:2]) > 64 or max(result[2:]) > 4096:
            raise ResourceLimit("Symbolic expression exceeds degree 64 or coefficient 4096-bit bounds")
        return result
    for expression in functions.values():
        size(expression)


def formal_probe(functions, formal, committed_probe=None):
    """Accept a replay only after checking its proof object against this claim."""
    if committed_probe is not None:
        if not isinstance(committed_probe, dict):
            return {"status": "UNKNOWN", "assurance": "NONE", "reason": "Malformed admission proof"}
        certificate = committed_probe.get("certificate")
        if certificate is not None:
            if (isinstance(certificate, dict) and committed_probe.get("status") == "PASS"
                    and certificate.get("verdict") == "PASS"
                    and check_certificate(functions, formal, certificate)):
                return {"status": "PASS", "assurance": "CERTIFICATE_CHECKED",
                        "backend": "rds_exact_affine", "backend_version": str(certificate["version"]),
                        "semantics": "exact_rational_execution_and_real_affine_model",
                        "statement": formal["statement"], "domain": formal["domain"],
                        "threshold": formal["threshold"], "certificate": certificate,
                        "certificate_reused": True,
                        "reason": "threshold discrimination only; no causal mechanism conclusion"}
            return {"status": "UNKNOWN", "assurance": "NONE", "reason": "Admission certificate did not verify"}
    try:
        return exact_probe(functions, formal)
    except ResourceLimit as exc:
        return {"status": "UNKNOWN", "assurance": "NONE", "reason": str(exc)}
    except UnsupportedExpression:
        try:
            return symbolic_probe(functions, formal)
        except Exception as exc:
            # Solver failures (including SymPy PolynomialError) are lack of a
            # result, never scientific FAIL. Process deadlines are enforced by
            # the isolated CLI caller; BaseException is not swallowed here.
            return {"status": "UNKNOWN", "assurance": "NONE", "reason": str(exc)}


def symbolic_probe(functions, formal):
    _symbolic_budget(functions)
    import sympy as sp

    x = sp.Symbol("x", real=True)
    denominators = []

    def translate(node):
        if isinstance(node, ast.Constant):
            return sp.Integer(node.value)
        if isinstance(node, ast.Name):
            return x
        if isinstance(node, ast.UnaryOp):
            value = translate(node.operand)
            return -value if isinstance(node.op, ast.USub) else value
        a, b = translate(node.left), translate(node.right)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.Div):
            # Preserve original denominator obligations BEFORE cancellation.
            denominators.append(b)
            return a / b
        return a ** b

    lo, hi = [sp.Rational(str(rational(v))) for v in formal["domain"]]
    threshold = sp.Rational(str(rational(formal["threshold"])))
    domain = [x >= lo, x <= hi]
    expressions = {k: translate(v) for k, v in functions.items()}
    if lo > hi:
        raise ValueError("Vacuous domain")
    for den in denominators:
        zeros = sp.reduce_inequalities(domain + [sp.Eq(den, 0)], x).as_set()
        if zeros.is_empty is not True:
            return {"status": "UNKNOWN", "reason": "denominator may vanish in domain",
                    "singular_set": str(zeros), "assurance": "SYMBOLIC_CHECKED"}
    # Boolean conjunctions may be contradictory without simplifying to False.
    counter = sp.reduce_inequalities(domain + [expressions["control"] >= threshold], x).as_set()
    crossing = sp.reduce_inequalities(domain + [expressions["treatment"] >= threshold], x).as_set()
    status = ("UNKNOWN" if counter.is_empty is None or crossing.is_empty is None else
              "PASS" if counter.is_empty and not crossing.is_empty else "FAIL")
    return {
        "status": status,
        "assurance": "SYMBOLIC_CHECKED", "backend": "sympy", "backend_version": sp.__version__,
        "semantics": "exact_rational_execution_and_real_symbolic_model",
        "domain": formal["domain"], "threshold": formal["threshold"],
        "statement": formal["statement"],
        "control_violation_set": str(counter), "treatment_crossing_set": str(crossing),
        "expressions": {k: str(v) for k, v in expressions.items()},
        "reason": "threshold discrimination only; no causal mechanism conclusion",
    }


def execute(payload):
    formal = formal_requirement(payload.get("hypothesis", {}))
    if "formal" in payload:
        raise ValueError("Declare formal on the hypothesis, not an unbound payload.formal")
    if formal and formal["kind"] == "dynamics":
        return {"probe": {"status": "UNKNOWN", "assurance": "NONE",
                          "reason": "Use a declarative affine dynamics statement"}}
    declarative = None
    if formal and formal["kind"] == "declarative":
        declarative = declarative_probe(formal, payload.get("admission_probe"))
        if declarative.get("status") != "PASS":
            return {"probe": declarative}
        if declarative.get("application_status", "PASS") != "PASS":
            return {"probe": dict(declarative, status="UNKNOWN", reason="Formal application assumptions remain UNKNOWN")}
    functions = parse_source(payload["source"])
    rows = read_rows(payload["data"])
    cached_control = payload.get("cached_control")
    observations = []
    for sid, x, target in rows:
        if formal and formal["kind"] != "declarative" and not rational(formal["domain"][0]) <= x <= rational(formal["domain"][1]):
            raise ValueError("Observed input outside committed formal domain")
        if cached_control and sid in cached_control:
            control = bounded(Fraction(cached_control[sid]["control"]))
            control_loss = bounded(Fraction(cached_control[sid]["control_loss"]))
        else:
            control = evaluate(functions["control"], x)
            control_loss = bounded(bounded(control - target) ** 2)
        treatment = evaluate(functions["treatment"], x)
        treatment_loss = bounded(bounded(treatment - target) ** 2)
        observations.append({"sample_id": sid, "x": str(x), "y": str(target),
                             "control": str(control), "treatment": str(treatment),
                             "control_loss": str(control_loss),
                             "treatment_loss": str(treatment_loss)})
    def mean(key):
        total = Fraction()
        for row in observations:
            total = bounded(total + Fraction(row[key]))
        return bounded(total / len(rows))
    probe = {"status": "NOT_APPLICABLE", "assurance": "NONE"}
    if formal and formal["kind"] == "declarative":
        probe = declarative
        probe["execution_assurance"] = "EXACT_OBSERVATION_CHECKED"
    elif formal:
        try:
            probe = formal_probe(functions, formal, payload.get("admission_probe"))
        except (ImportError, NotImplementedError, ValueError, TypeError) as exc:
            probe = {"status": "UNKNOWN", "reason": str(exc), "assurance": "NONE"}
        threshold, max_loss = rational(formal["threshold"]), rational(formal["max_loss"])
        crossed = [r["sample_id"] for r in observations
                   if Fraction(r["control"]) < threshold <= Fraction(r["treatment"])]
        crossed_set = set(crossed)
        falsifiers = [r["sample_id"] for r in observations
                       if r["sample_id"] in crossed_set
                       and Fraction(r["treatment_loss"]) <= max_loss
                       and Fraction(r["control_loss"]) <= max_loss
                       and formal["statement"] == "threshold_necessity"]
        probe.update({"admission_status": probe["status"],
                      "admission_assurance": probe.get("assurance", "NONE"),
                      "observed_status": "PASS" if crossed else "FAIL",
                      "execution_assurance": "EXACT_OBSERVATION_CHECKED",
                      "observed_crossings": crossed, "necessity_counterexamples": falsifiers})
        if probe["status"] == "PASS" and not crossed:
            probe.update(status="FAIL", assurance="EXACT_OBSERVATION_CHECKED",
                         reason="Crossing is possible but was never executed")
    control_mean, treatment_mean = mean("control_loss"), mean("treatment_loss")
    return {"metric": "mse", "n": len(rows), "observations": observations,
            "control_mean": str(control_mean),
            "treatment_mean": str(treatment_mean),
            "gain": str(bounded(control_mean - treatment_mean)),
            "control_reused": bool(cached_control), "probe": probe}


if __name__ == "__main__":
    try:
        payload = json.load(sys.stdin)
        result = (admission_probe(payload["hypothesis"], payload["source"])
                  if payload.get("operation") == "admission" else execute(payload))
        print(json.dumps(result, allow_nan=False))
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}))
        sys.exit(1)
