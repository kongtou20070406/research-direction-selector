"""Adapt the existing scalar threshold certificate to declarative statements."""
from rds_formal_kernel import check_certificate as check_scalar, exact_probe
from rds_scalar_values import parse_source
from rds_verify_types import require


def _read(spec):
    require(isinstance(spec, dict) and set(spec) == {"schema", "kind", "source", "formal"},
            "Scalar statement requires schema, kind, source and formal")
    require(type(spec["schema"]) is int and spec["schema"] == 1 and spec["kind"] == "scalar_threshold",
            "Unsupported scalar statement")
    require(isinstance(spec["source"], str) and len(spec["source"].encode("utf-8")) <= 8192,
            "Scalar source exceeds limit")
    formal = spec["formal"]
    require(isinstance(formal, dict) and set(formal) == {"domain", "threshold"},
            "Scalar rule requires an explicit domain and threshold")
    return parse_source(spec["source"]), {**formal, "statement": "threshold_separation"}


def verify(spec):
    functions, formal = _read(spec)
    return exact_probe(functions, formal)


def check_certificate(spec, certificate):
    try:
        functions, formal = _read(spec)
        return check_scalar(functions, formal, certificate)
    except (ValueError, TypeError, KeyError, SyntaxError, RecursionError):
        return False
