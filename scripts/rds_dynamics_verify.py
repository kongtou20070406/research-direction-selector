"""Exact certificates for finite rational affine maps f(x) = A x + b.

The contraction obligation is a global infinity-norm bound. Its failure says
nothing about contraction in a different norm, or stochastic training behavior.
"""
from fractions import Fraction

from rds_verify_types import (MAX_CERTIFICATE_BYTES, bounded, canonical, digest,
                              rational, require)

MAX_DIMENSION = 128
KINDS = {"affine_contraction", "affine_fixed_point", "affine_dynamics"}
CERTIFICATE_KEYS = {"version", "spec_sha256", "kind", "dimension", "row_norms",
                    "induced_norm", "fixedpoint_residual", "obligations",
                    "case", "witness", "verdict"}


def _vector(values, dimension, name):
    require(isinstance(values, list) and len(values) == dimension,
            name + " must contain one rational string per coordinate")
    require(all(isinstance(v, str) for v in values), name + " entries must be rational strings")
    return [rational(v) for v in values]


def _read_spec(spec):
    require(isinstance(spec, dict), "Affine specification must be an object")
    require(len(canonical(spec).encode("utf-8")) <= MAX_CERTIFICATE_BYTES,
            "Affine specification exceeds the byte limit")
    require(type(spec.get("schema")) is int and spec["schema"] == 1, "Expected affine schema 1")
    kind = spec.get("kind")
    require(isinstance(kind, str) and kind in KINDS, "Unknown affine verification kind")
    contraction = kind in {"affine_contraction", "affine_dynamics"}
    fixed_point = kind in {"affine_fixed_point", "affine_dynamics"}
    fields = {"schema", "kind", "model"}
    if contraction:
        fields.add("threshold")
    if fixed_point:
        fields.add("point")
    require(set(spec) == fields, "Affine specification fields do not match its kind")
    model = spec["model"]
    require(isinstance(model, dict) and set(model) == {"matrix", "bias"},
            "Affine model requires matrix and bias")
    matrix = model["matrix"]
    require(isinstance(matrix, list) and 1 <= len(matrix) <= MAX_DIMENSION,
            "Affine matrix dimension must be 1..128")
    dimension = len(matrix)
    matrix = [_vector(row, dimension, "Matrix row") for row in matrix]
    bias = _vector(model["bias"], dimension, "Bias")
    threshold = None
    if contraction:
        require(isinstance(spec["threshold"], str), "Threshold must be a rational string")
        threshold = rational(spec["threshold"])
        require(0 < threshold <= 1, "Contraction threshold must satisfy 0 < threshold <= 1")
    point = _vector(spec["point"], dimension, "Point") if fixed_point else None
    return matrix, bias, threshold, point


def estimate_cost(spec):
    """Use matrix dimensions as a coarse exact-arithmetic work proxy."""
    model = spec.get("model", {}) if isinstance(spec, dict) else {}
    matrix = model.get("matrix", []) if isinstance(model, dict) else []
    dimension = len(matrix) if isinstance(matrix, list) else 0
    return {"phase": "exact_affine_check", "work_units": max(1, dimension * dimension)}


def _sum(values):
    total = Fraction(0)
    for value in values:
        total = bounded(total + value)
    return total


def _matvec(matrix, vector):
    return [_sum(bounded(a * x) for a, x in zip(row, vector)) for row in matrix]


def _case(contraction_failed, fixed_point_failed):
    if contraction_failed and fixed_point_failed:
        return "contraction_and_fixed_point_counterexamples"
    if contraction_failed:
        return "contraction_counterexample"
    if fixed_point_failed:
        return "fixed_point_counterexample"
    return "verified"


def verify(spec):
    """Generate and independently check an exact PASS/FAIL certificate."""
    matrix, bias, threshold, point = _read_spec(spec)
    row_norms = [_sum(abs(a) for a in row) for row in matrix]
    norm = max(row_norms)
    residual, mapped = None, None
    if point is not None:
        mapped = [bounded(value + b) for value, b in zip(_matvec(matrix, point), bias)]
        residual = [bounded(value - p) for value, p in zip(mapped, point)]
    contraction_failed = threshold is not None and norm >= threshold
    fixed_point_failed = residual is not None and any(v != 0 for v in residual)
    witness = {}
    if contraction_failed:
        row = row_norms.index(norm)
        direction = [Fraction(1 if a > 0 else -1 if a < 0 else 0) for a in matrix[row]]
        image = _matvec(matrix, direction)
        witness["contraction"] = {"row": row, "direction": [str(v) for v in direction],
                                  "image": [str(v) for v in image], "input_norm": "1",
                                  "image_norm": str(max(abs(v) for v in image))}
    if fixed_point_failed:
        coordinate = next(i for i, value in enumerate(residual) if value != 0)
        witness["fixed_point"] = {"coordinate": coordinate, "residual": str(residual[coordinate]),
                                 "mapped_point": [str(v) for v in mapped]}
    verdict = "FAIL" if contraction_failed or fixed_point_failed else "PASS"
    certificate = {
        "version": 1, "spec_sha256": digest(spec), "kind": spec["kind"], "dimension": len(matrix),
        "row_norms": [str(v) for v in row_norms], "induced_norm": str(norm),
        "fixedpoint_residual": [str(v) for v in residual] if residual is not None else None,
        "obligations": {"contraction": "NOT_APPLICABLE" if threshold is None else
                        "FAIL" if contraction_failed else "PASS",
                        "fixed_point": "NOT_APPLICABLE" if point is None else
                        "FAIL" if fixed_point_failed else "PASS"},
        "case": _case(contraction_failed, fixed_point_failed), "witness": witness, "verdict": verdict,
    }
    require(check_certificate(spec, certificate), "Generated affine certificate failed independent check")
    return {"status": verdict, "assurance": "CERTIFICATE_CHECKED", "backend": "rds_exact_affine_dynamics",
            "certificate": certificate,
            "scope": "exact rational affine map; global infinity norm and declared fixed point"}


def check_certificate(spec, certificate):
    """Reconstruct obligations using bounded arithmetic; never call verify."""
    try:
        if not isinstance(certificate, dict) or set(certificate) != CERTIFICATE_KEYS:
            return False
        if len(canonical(certificate).encode("utf-8")) > MAX_CERTIFICATE_BYTES:
            return False
        matrix, bias, threshold, point = _read_spec(spec)
        dimension = len(matrix)
        if (type(certificate["version"]) is not int or certificate["version"] != 1
                or certificate["spec_sha256"] != digest(spec) or certificate["kind"] != spec["kind"]
                or type(certificate["dimension"]) is not int or certificate["dimension"] != dimension):
            return False
        rows = []
        for row in matrix:
            total = Fraction(0)
            for value in row:
                total = bounded(total + abs(value))
            rows.append(total)
        norm = max(rows)
        if (certificate["row_norms"] != [str(v) for v in rows]
                or certificate["induced_norm"] != str(norm)):
            return False
        mapped, residual = None, None
        if point is not None:
            mapped = []
            residual = []
            for row, b, p in zip(matrix, bias, point):
                value = Fraction(0)
                for coefficient, coordinate in zip(row, point):
                    value = bounded(value + bounded(coefficient * coordinate))
                value = bounded(value + b)
                mapped.append(value)
                residual.append(bounded(value - p))
        expected_residual = [str(v) for v in residual] if residual is not None else None
        if certificate["fixedpoint_residual"] != expected_residual:
            return False
        contraction_failed = threshold is not None and norm >= threshold
        fixed_point_failed = residual is not None and any(v != 0 for v in residual)
        obligations = {"contraction": "NOT_APPLICABLE" if threshold is None else
                       "FAIL" if contraction_failed else "PASS",
                       "fixed_point": "NOT_APPLICABLE" if point is None else
                       "FAIL" if fixed_point_failed else "PASS"}
        verdict = "FAIL" if contraction_failed or fixed_point_failed else "PASS"
        if (certificate["obligations"] != obligations or certificate["verdict"] != verdict
                or certificate["case"] != _case(contraction_failed, fixed_point_failed)):
            return False
        witness = certificate["witness"]
        expected_keys = set()
        if contraction_failed:
            expected_keys.add("contraction")
        if fixed_point_failed:
            expected_keys.add("fixed_point")
        if not isinstance(witness, dict) or set(witness) != expected_keys:
            return False
        if contraction_failed:
            item = witness["contraction"]
            if not isinstance(item, dict) or set(item) != {"row", "direction", "image", "input_norm", "image_norm"}:
                return False
            row = item["row"]
            if type(row) is not int or not 0 <= row < dimension or rows[row] != norm:
                return False
            direction = [Fraction(1 if a > 0 else -1 if a < 0 else 0) for a in matrix[row]]
            if item["direction"] != [str(v) for v in direction] or item["input_norm"] != "1":
                return False
            image = _matvec(matrix, direction)
            image_norm = max(abs(v) for v in image)
            if (max(abs(v) for v in direction) != 1 or image_norm != norm or image_norm < threshold
                    or item["image"] != [str(v) for v in image] or item["image_norm"] != str(image_norm)):
                return False
        if fixed_point_failed:
            item = witness["fixed_point"]
            if not isinstance(item, dict) or set(item) != {"coordinate", "residual", "mapped_point"}:
                return False
            coordinate = item["coordinate"]
            if (type(coordinate) is not int or not 0 <= coordinate < dimension or residual[coordinate] == 0
                    or item["residual"] != str(residual[coordinate])
                    or item["mapped_point"] != [str(v) for v in mapped]):
                return False
        return True
    except (ValueError, TypeError, KeyError, AttributeError, ZeroDivisionError, OverflowError, RecursionError):
        return False
