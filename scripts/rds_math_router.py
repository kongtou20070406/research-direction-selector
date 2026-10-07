"""Bounded mathematical capability routing with independent exact replay.

This is a deliberately small adapter registry, not an open-ended CAS plugin
loader. Search uses only the locally pinned SymPy APIs below; certificates are
replayed by bounded standard-library rational checkers.
"""
from copy import deepcopy
from fractions import Fraction
import hashlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import re


REQUEST_SCHEMA = "rds-math-request-v1"
CERTIFICATE_SCHEMA = "rds-math-certificate-v1"
EXTENSION_SCHEMA = "rds-math-extension-task-v1"
MAX_SPEC_BYTES = 64 * 1024
MAX_RATIONAL_TEXT_CHARS = 2800
MAX_ROWS = 8
MAX_COLUMNS = 8
MAX_COEFFICIENT_BITS = 64
MAX_INTERMEDIATE_BITS = 4096
MAX_POLYNOMIAL_DEGREE = 8
MAX_WORK_UNITS = 1024
MAX_STURM_STEPS = 2048
MAX_ROOT_WIDTH_BITS = 64
SYMPY_VERSION = "1.14.0"

SYMPY_DOMAINMATRIX_DOC = "https://docs.sympy.org/latest/modules/polys/domainmatrix.html"
SYMPY_POLY_DOC = "https://docs.sympy.org/latest/modules/polys/reference.html"
SYMPY_LICENSE = "https://github.com/sympy/sympy/blob/1.14.0/LICENSE"
FLINT_MATRIX_DOC = "https://python-flint.readthedocs.io/en/latest/fmpq_mat.html"
FLINT_POLY_DOC = "https://python-flint.readthedocs.io/en/latest/fmpq_poly.html"
FLINT_REPO = "https://github.com/flintlib/python-flint"
FLINT_LICENSE = "https://github.com/flintlib/flint"
Z3_REPO = "https://github.com/Z3Prover/z3"


class _Unsupported(Exception):
    def __init__(self, gap, reason, details=None):
        super().__init__(reason)
        self.gap = gap
        self.details = details or {}


class _WorkLimit(Exception):
    pass


def _require(condition, message):
    if not condition:
        raise ValueError(message)


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def _sha256(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _bits(value):
    value = Fraction(value)
    return max(abs(value.numerator).bit_length(), value.denominator.bit_length())


def _rational(value, name):
    if type(value) is int:
        result = Fraction(value)
    elif isinstance(value, str) and len(value) <= MAX_RATIONAL_TEXT_CHARS and re.fullmatch(
            r"[+-]?\d+(?:/[1-9]\d*)?", value):
        try:
            result = Fraction(value)
        except (ValueError, ZeroDivisionError) as exc:
            raise ValueError(name + " must be an exact rational") from exc
    else:
        raise ValueError(name + " must be an integer or rational string; floats are unsupported")
    return result


def _fraction_json(value):
    value = Fraction(value)
    return str(value.numerator) if value.denominator == 1 else f"{value.numerator}/{value.denominator}"


def _backend_version(import_name, distribution):
    if importlib.util.find_spec(import_name) is None:
        return {"availability": "NOT_INSTALLED", "version": None}
    try:
        return {"availability": "AVAILABLE", "version": importlib.metadata.version(distribution)}
    except importlib.metadata.PackageNotFoundError:
        return {"availability": "VERSION_UNKNOWN", "version": None}


def _sympy_status():
    return _backend_version("sympy", "sympy")


def _unregistered_tools():
    flint = _backend_version("flint", "python-flint")
    z3 = _backend_version("z3", "z3-solver")
    return [
        {"id": "python-flint", "registered": False, "runnable": False,
         "availability": flint["availability"], "version": flint["version"],
         "license": "MIT Python wrapper; FLINT LGPL-3-or-later",
         "possible_scope": ["square nonsingular exact rational matrix solve",
                            "base-ring rational-polynomial roots"],
         "api_sources": [FLINT_MATRIX_DOC, FLINT_POLY_DOC],
         "license_source": FLINT_LICENSE,
         "execution_policy": "Not installed or selected by this router; provisioning needs separate review."},
        {"id": "z3-solver", "registered": False, "runnable": False,
         "availability": z3["availability"], "version": z3["version"],
         "license": "MIT", "possible_scope": ["bounded Boolean and integer constraint families"],
         "api_sources": [Z3_REPO],
         "execution_policy": "Not installed or selected by this router; provisioning needs separate review."},
    ]


_CAPABILITIES = [
    {
        "id": "sympy.qq.linear.unique.v1",
        "input_kind": "rational_linear_system",
        "input_schema": {
            "type": "object", "required": ["schema", "kind", "domain", "matrix", "rhs", "claim"],
            "properties": {"schema": {"const": REQUEST_SCHEMA}, "kind": {"const": "rational_linear_system"},
                           "domain": {"const": "QQ"},
                           "matrix": {"type": "array", "items": {"type": "array", "items": "rational"}},
                           "rhs": {"type": "array", "items": "rational"},
                           "claim": {"const": "unique_solution"},
                           "max_work_units": {"type": "integer", "minimum": 1, "maximum": MAX_WORK_UNITS}},
        },
        "backend": {"name": "SymPy", "distribution": "sympy", "version": SYMPY_VERSION,
                    "license": "BSD-3-Clause", "entrypoint": "DomainMatrix.solve_den"},
        "semantic_contract": {
            "domain": "exact rational field QQ",
            "claim": "A*x=b has exactly one solution over QQ",
            "candidate_output": "rational vector",
            "checker": "exact Fraction substitution and full-column-rank elimination",
            "excluded": ["inconsistent-system certificates", "parametric solution families",
                         "floating-point inputs", "non-rational domains"],
        },
        "applicability": ["1..8 rows", "1..8 columns", "rows >= columns", "rectangular consistent systems allowed",
                          "all coefficients exact rationals with at most 64 input bits"],
        "work_bound": {"unit": "rows * columns * (columns + 1)", "maximum": MAX_WORK_UNITS,
                       "kind": "deterministic structural proxy; not elapsed-time or memory"},
        "selection_evidence": ["SymPy is pinned at 1.14.0 in requirements-formal.txt",
                               "DomainMatrix.solve_den documents its division-free invariant and raises when no unique solution exists"],
        "sources": [SYMPY_DOMAINMATRIX_DOC, SYMPY_LICENSE],
        "performance_claim": "NOT_BENCHMARKED",
    },
    {
        "id": "sympy.qq.univariate.distinct-real-roots.v1",
        "input_kind": "univariate_qq_polynomial_roots",
        "input_schema": {
            "type": "object", "required": ["schema", "kind", "domain", "coefficients_ascending", "claim", "max_interval_width"],
            "properties": {"schema": {"const": REQUEST_SCHEMA}, "kind": {"const": "univariate_qq_polynomial_roots"},
                           "domain": {"const": "QQ"},
                           "coefficients_ascending": {"type": "array", "items": "rational"},
                           "claim": {"const": "complete_distinct_real_root_set"},
                           "max_interval_width": {"type": "rational", "exclusiveMinimum": 0, "maximum": 1},
                           "max_work_units": {"type": "integer", "minimum": 1, "maximum": MAX_WORK_UNITS}},
        },
        "backend": {"name": "SymPy", "distribution": "sympy", "version": SYMPY_VERSION,
                    "license": "BSD-3-Clause", "entrypoint": "Poly.intervals(eps=...) over QQ"},
        "semantic_contract": {
            "domain": "univariate polynomial over exact rational field QQ; roots interpreted in R",
            "claim": "complete set of distinct real roots represented by rational isolating intervals",
            "candidate_output": "ordered pairwise-disjoint rational intervals, one root per interval",
            "checker": "independent standard-library Fraction Sturm count on the square-free part",
            "excluded": ["multiplicities", "complex roots", "multivariate polynomials", "algebraic coefficients",
                         "zero polynomial's infinite root set"],
        },
        "applicability": ["degree 0..8", "nonzero polynomial", "exact rational coefficients with at most 64 input bits",
                          "requested interval width in (0,1]"],
        "work_bound": {"unit": "degree cubed * requested interval precision bits", "maximum": MAX_WORK_UNITS,
                       "kind": "deterministic structural proxy; not elapsed-time or memory"},
        "selection_evidence": ["SymPy is pinned at 1.14.0 in requirements-formal.txt",
                               "Poly.intervals documents exact rational isolating intervals; the independent checker verifies root counts"],
        "sources": [SYMPY_POLY_DOC, SYMPY_LICENSE],
        "performance_claim": "NOT_BENCHMARKED",
    },
]


def capabilities():
    """Return typed, bounded capability declarations and observed local tools."""
    sympy = _sympy_status()
    availability = ("AVAILABLE" if sympy == {"availability": "AVAILABLE", "version": SYMPY_VERSION}
                    else "BACKEND_UNAVAILABLE_OR_VERSION_MISMATCH")
    registered = deepcopy(_CAPABILITIES)
    for capability in registered:
        capability["availability"] = availability
    return {"schema": "rds-math-capability-registry-v1",
            "selection_policy": "exact input-kind/domain/claim applicability, then pinned local backend availability; no performance ranking",
            "registered_backends": [{"id": "sympy", "availability": sympy["availability"],
                                     "version": sympy["version"], "required_version": SYMPY_VERSION,
                                     "license": "BSD-3-Clause", "source": SYMPY_LICENSE}],
            "capabilities": registered,
            "unregistered_tool_candidates": _unregistered_tools(),
            "policy": {"automatic_install": False, "arbitrary_plugin_execution": False,
                       "unsupported_result": "UNKNOWN with rds-math-extension-task-v1"}}


def _validate_common(spec, kind, required, optional=()):
    _require(isinstance(spec, dict), "Math request must be an object")
    _require(spec.get("schema") == REQUEST_SCHEMA, "Unsupported math request schema")
    _require(spec.get("kind") == kind, "Unexpected math request kind")
    _require(set(spec) <= set(required) | set(optional) and set(required) <= set(spec),
             "Math request fields do not match the registered input contract")
    _require(spec.get("domain") == "QQ", "This capability accepts exact rational domain QQ only")
    try:
        size = len(_canonical(spec).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Math request must be finite JSON") from exc
    _require(size <= MAX_SPEC_BYTES, "Math request exceeds 64 KiB")


def _linear_input(spec):
    _validate_common(spec, "rational_linear_system",
                     ("schema", "kind", "domain", "matrix", "rhs", "claim"), ("max_work_units",))
    _require(spec["claim"] == "unique_solution", "Linear-system claim must be unique_solution")
    matrix_raw, rhs_raw = spec["matrix"], spec["rhs"]
    _require(isinstance(matrix_raw, list) and isinstance(rhs_raw, list), "Matrix and rhs must be arrays")
    _require(1 <= len(matrix_raw) <= MAX_ROWS, "Matrix row count must be 1..8")
    _require(1 <= len(rhs_raw) <= MAX_ROWS, "Rhs length must be 1..8")
    _require(len(rhs_raw) == len(matrix_raw), "Rhs length must match matrix row count")
    _require(all(isinstance(row, list) for row in matrix_raw), "Matrix rows must be arrays")
    columns = len(matrix_raw[0]) if matrix_raw else 0
    _require(1 <= columns <= MAX_COLUMNS and all(len(row) == columns for row in matrix_raw),
             "Matrix must have a rectangular 1..8 column shape")
    matrix = [[_rational(value, "matrix coefficient") for value in row] for row in matrix_raw]
    rhs = [_rational(value, "rhs coefficient") for value in rhs_raw]
    if any(_bits(value) > MAX_COEFFICIENT_BITS for row in matrix for value in row) or \
            any(_bits(value) > MAX_COEFFICIENT_BITS for value in rhs):
        raise _Unsupported("exact_coefficient_size", "Coefficient exceeds the registered 64-bit input bound")
    rows, cols = len(matrix), columns
    if rows < cols:
        raise _Unsupported("underdetermined_system",
                           "This route does not determine consistency or characterize solutions when equations are fewer than unknowns",
                           {"rows": rows, "columns": cols})
    work = rows * cols * (cols + 1)
    _require(work <= MAX_WORK_UNITS, "Linear-system structural work bound is inconsistent")
    requested_budget = spec.get("max_work_units", MAX_WORK_UNITS)
    _require(type(requested_budget) is int and 1 <= requested_budget <= MAX_WORK_UNITS,
             "max_work_units must be an integer in 1..1024")
    if work > requested_budget:
        raise _Unsupported("work_budget_exhausted", "Linear-system route exceeds the request's work budget",
                           {"estimated_work_units": work, "max_work_units": requested_budget})
    return matrix, rhs, work, requested_budget


def _poly_trim(poly):
    values = [Fraction(value) for value in poly]
    while len(values) > 1 and values[-1] == 0:
        values.pop()
    return values or [Fraction(0)]


def _poly_derivative(poly):
    if len(poly) <= 1:
        return [Fraction(0)]
    return _poly_trim([Fraction(index) * poly[index] for index in range(1, len(poly))])


def _guard_poly(poly):
    for coefficient in poly:
        if _bits(coefficient) > MAX_INTERMEDIATE_BITS:
            raise _WorkLimit("Exact polynomial intermediate exceeds the 4096-bit bound")
    return poly


def _poly_divmod(dividend, divisor, counter=None):
    dividend, divisor = _poly_trim(dividend), _poly_trim(divisor)
    if divisor == [0]:
        raise ZeroDivisionError("Polynomial division by zero")
    remainder = list(dividend)
    quotient = [Fraction(0)] * max(1, (len(dividend) - len(divisor) + 1))
    while remainder != [0] and len(remainder) >= len(divisor):
        if counter is not None:
            counter[0] += 1
            if counter[0] > MAX_STURM_STEPS:
                raise _WorkLimit("Sturm checker exceeded its deterministic step cap")
        offset = len(remainder) - len(divisor)
        factor = remainder[-1] / divisor[-1]
        quotient[offset] += factor
        for index, coefficient in enumerate(divisor):
            remainder[offset + index] -= factor * coefficient
        remainder = _guard_poly(_poly_trim(remainder))
        _guard_poly(quotient)
    return _poly_trim(quotient), _poly_trim(remainder)


def _poly_gcd(left, right, counter=None):
    left, right = _poly_trim(left), _poly_trim(right)
    while right != [0]:
        _quotient, remainder = _poly_divmod(left, right, counter)
        left, right = right, remainder
    if left == [0]:
        return left
    leading = left[-1]
    return _guard_poly(_poly_trim([value / leading for value in left]))


def _squarefree_part(poly, counter=None):
    poly = _poly_trim(poly)
    if len(poly) <= 1:
        return [Fraction(1)] if poly[0] != 0 else [Fraction(0)]
    common = _poly_gcd(poly, _poly_derivative(poly), counter)
    part, remainder = _poly_divmod(poly, common, counter)
    if remainder != [0]:
        raise _WorkLimit("Could not construct the exact square-free polynomial")
    return _guard_poly(_poly_trim(part))


def _poly_eval(poly, point):
    value = Fraction(0)
    for coefficient in reversed(poly):
        value = value * point + coefficient
        if _bits(value) > MAX_INTERMEDIATE_BITS:
            raise _WorkLimit("Exact polynomial evaluation exceeds the 4096-bit bound")
    return value


def _sturm_sequence(poly, counter):
    first = _squarefree_part(poly, counter)
    if len(first) <= 1:
        return [first]
    second = _poly_derivative(first)
    sequence = [first, second]
    while sequence[-1] != [0]:
        _quotient, remainder = _poly_divmod(sequence[-2], sequence[-1], counter)
        if remainder == [0]:
            break
        sequence.append(_guard_poly([-value for value in remainder]))
        if len(sequence) > MAX_POLYNOMIAL_DEGREE + 2:
            raise _WorkLimit("Sturm sequence exceeded its degree bound")
    return sequence


def _sign_variations(signs):
    nonzero = [value for value in signs if value]
    return sum(left != right for left, right in zip(nonzero, nonzero[1:]))


def _sturm_variations(sequence, endpoint):
    if endpoint in ("-inf", "+inf"):
        signs = []
        for poly in sequence:
            leading = 1 if poly[-1] > 0 else -1
            if endpoint == "-inf" and (len(poly) - 1) % 2:
                leading = -leading
            signs.append(leading)
    else:
        x = Fraction(endpoint)
        signs = [(1 if value > 0 else -1 if value < 0 else 0) for value in (_poly_eval(poly, x) for poly in sequence)]
    return _sign_variations(signs)


def _distinct_real_root_count(poly, counter=None):
    counter = counter if counter is not None else [0]
    squarefree = _squarefree_part(poly, counter)
    if len(squarefree) <= 1:
        return 0
    sequence = _sturm_sequence(squarefree, counter)
    return _sturm_variations(sequence, "-inf") - _sturm_variations(sequence, "+inf")


def _polynomial_input(spec):
    _validate_common(spec, "univariate_qq_polynomial_roots",
                     ("schema", "kind", "domain", "coefficients_ascending", "claim", "max_interval_width"),
                     ("max_work_units",))
    _require(spec["claim"] == "complete_distinct_real_root_set",
             "Polynomial claim must be complete_distinct_real_root_set")
    raw = spec["coefficients_ascending"]
    _require(isinstance(raw, list) and 1 <= len(raw) <= MAX_POLYNOMIAL_DEGREE + 1,
             "Expected 1..9 ascending polynomial coefficients")
    coefficients = _poly_trim([_rational(value, "polynomial coefficient") for value in raw])
    if len(coefficients) - 1 > MAX_POLYNOMIAL_DEGREE:
        raise _Unsupported("polynomial_degree", "Polynomial degree exceeds the registered degree-8 cap")
    if any(_bits(value) > MAX_COEFFICIENT_BITS for value in coefficients):
        raise _Unsupported("exact_coefficient_size", "Coefficient exceeds the registered 64-bit input bound")
    if coefficients == [0]:
        raise _Unsupported("zero_polynomial_root_set", "The zero polynomial has an infinite real root set and cannot be represented by this finite isolator")
    width = _rational(spec["max_interval_width"], "max_interval_width")
    _require(0 < width <= 1, "max_interval_width must lie in (0,1]")
    if _bits(width) > MAX_ROOT_WIDTH_BITS:
        raise _Unsupported("root_interval_precision", "Requested interval width exceeds the registered 64-bit precision bound")
    precision_bits = max(1, width.denominator.bit_length() - width.numerator.bit_length() + 2)
    work = max(1, (len(coefficients) - 1) ** 3) * precision_bits
    requested_budget = spec.get("max_work_units", MAX_WORK_UNITS)
    _require(type(requested_budget) is int and 1 <= requested_budget <= MAX_WORK_UNITS,
             "max_work_units must be an integer in 1..1024")
    if work > requested_budget:
        raise _Unsupported("work_budget_exhausted", "Polynomial route exceeds the request's work budget",
                           {"estimated_work_units": work, "max_work_units": requested_budget})
    return coefficients, width, work, requested_budget


def _linear_rank(matrix):
    rows = [list(map(Fraction, row)) for row in matrix]
    row_count = len(rows)
    column_count = len(rows[0]) if rows else 0
    pivot_row = 0
    pivot_columns = []
    operations = 0
    for column in range(column_count):
        selected = next((index for index in range(pivot_row, row_count) if rows[index][column] != 0), None)
        if selected is None:
            continue
        rows[pivot_row], rows[selected] = rows[selected], rows[pivot_row]
        pivot = rows[pivot_row][column]
        rows[pivot_row] = [value / pivot for value in rows[pivot_row]]
        operations += column_count
        if any(_bits(value) > MAX_INTERMEDIATE_BITS for value in rows[pivot_row]):
            raise _WorkLimit("Exact rank checker exceeded the 4096-bit intermediate bound")
        for index in range(row_count):
            if index == pivot_row:
                continue
            factor = rows[index][column]
            if factor:
                rows[index] = [value - factor * pivot_value
                               for value, pivot_value in zip(rows[index], rows[pivot_row])]
                operations += column_count
                if any(_bits(value) > MAX_INTERMEDIATE_BITS for value in rows[index]):
                    raise _WorkLimit("Exact rank checker exceeded the 4096-bit intermediate bound")
        pivot_columns.append(column)
        pivot_row += 1
        if pivot_row == row_count:
            break
    return len(pivot_columns), pivot_columns, operations


def _matrix_product(matrix, vector):
    result = []
    for row in matrix:
        value = sum((left * right for left, right in zip(row, vector)), Fraction(0))
        if _bits(value) > MAX_INTERMEDIATE_BITS:
            raise _WorkLimit("Exact substitution exceeded the 4096-bit intermediate bound")
        result.append(value)
    return result


def _linear_certificate_valid(spec, certificate):
    try:
        matrix, rhs, _work, _budget = _linear_input(spec)
        if not isinstance(certificate, dict) or set(certificate) != {
                "schema", "kind", "spec_sha256", "solution", "rank_a"}:
            return False
        if (certificate["schema"] != CERTIFICATE_SCHEMA or certificate["kind"] != "unique_linear_solution"
                or certificate["spec_sha256"] != _sha256(spec)):
            return False
        solution_raw = certificate["solution"]
        if not isinstance(solution_raw, list) or len(solution_raw) != len(matrix[0]):
            return False
        solution = [_rational(value, "certificate solution") for value in solution_raw]
        if any(_bits(value) > MAX_INTERMEDIATE_BITS for value in solution):
            return False
        if _matrix_product(matrix, solution) != rhs:
            return False
        rank, _pivots, _operations = _linear_rank(matrix)
        return (rank == len(matrix[0]) and type(certificate["rank_a"]) is int
                and certificate["rank_a"] == rank)
    except (ValueError, TypeError, KeyError, _Unsupported, _WorkLimit, ZeroDivisionError):
        return False


def _root_certificate_valid(spec, certificate):
    try:
        coefficients, width, _work, _budget = _polynomial_input(spec)
        if not isinstance(certificate, dict) or set(certificate) != {
                "schema", "kind", "spec_sha256", "intervals", "distinct_real_root_count"}:
            return False
        if (certificate["schema"] != CERTIFICATE_SCHEMA or certificate["kind"] != "distinct_real_root_intervals"
                or certificate["spec_sha256"] != _sha256(spec)):
            return False
        intervals = certificate["intervals"]
        if not isinstance(intervals, list) or len(intervals) > MAX_POLYNOMIAL_DEGREE:
            return False
        counter = [0]
        squarefree = _squarefree_part(coefficients, counter)
        sturm = _sturm_sequence(squarefree, counter) if len(squarefree) > 1 else [squarefree]
        previous_upper = None
        for item in intervals:
            if not isinstance(item, dict) or set(item) != {"lower", "upper"}:
                return False
            lower, upper = _rational(item["lower"], "root interval lower"), _rational(item["upper"], "root interval upper")
            if (lower > upper or upper - lower > width
                    or _bits(lower) > MAX_INTERMEDIATE_BITS or _bits(upper) > MAX_INTERMEDIATE_BITS):
                return False
            if previous_upper is not None and lower <= previous_upper:
                return False
            if lower == upper:
                if _poly_eval(squarefree, lower) != 0:
                    return False
                interval_count = 1
            else:
                if _poly_eval(squarefree, lower) == 0 or _poly_eval(squarefree, upper) == 0:
                    return False
                interval_count = (_sturm_variations(sturm, lower) - _sturm_variations(sturm, upper))
                if interval_count != 1:
                    return False
            previous_upper = upper
        total = (_sturm_variations(sturm, "-inf") - _sturm_variations(sturm, "+inf")) \
            if len(squarefree) > 1 else 0
        return (type(certificate["distinct_real_root_count"]) is int
                and total == len(intervals) == certificate["distinct_real_root_count"])
    except (ValueError, TypeError, KeyError, _Unsupported, _WorkLimit, ZeroDivisionError):
        return False


def check_certificate(spec, certificate):
    """Replay evidence without importing SymPy or calling any search routine."""
    if not isinstance(spec, dict) or not isinstance(certificate, dict):
        return False
    if spec.get("schema") != REQUEST_SCHEMA:
        return False
    if spec.get("kind") == "rational_linear_system":
        return _linear_certificate_valid(spec, certificate)
    if spec.get("kind") == "univariate_qq_polynomial_roots":
        return _root_certificate_valid(spec, certificate)
    return False


def _candidate_tool_facts(spec):
    kind = spec.get("kind") if isinstance(spec, dict) else None
    candidates = []
    if kind == "rational_linear_system":
        facts = _unregistered_tools()[0]
        facts["possible_scope"] = ["exact square nonsingular rational systems"]
        facts["api_sources"] = [FLINT_MATRIX_DOC]
        candidates.append(facts)
    elif kind == "univariate_qq_polynomial_roots":
        candidates.extend(_unregistered_tools()[:1])
    elif kind in ("integer_constraints", "boolean_constraints", "bounded_smt"):
        candidates.extend(_unregistered_tools()[1:])
    else:
        candidates.extend(_unregistered_tools())
    return candidates


def _extension_task(spec, gap, reason, details=None):
    digest = _sha256(spec)
    kind = spec.get("kind", "unknown") if isinstance(spec, dict) else "unknown"
    task = {
        "schema": EXTENSION_SCHEMA,
        "task_id": "rds.math.adapter." + re.sub(r"[^a-z0-9_.-]", "_", str(kind).lower()) + "." + digest[:16],
        "requested_problem": spec,
        "problem_sha256": digest,
        "capability_gap": gap,
        "reason": reason,
        "details": details or {},
        "required_adapter_contract": {
            "typed_input_schema": "Declare exact domain, quantifiers/claim, assumptions and finite input bounds.",
            "applicability": "State explicit preconditions and excluded cases; no implicit widening or guessed domain.",
            "candidate_generator": "Use only structured data and a registered backend; never evaluate user expression text or shell commands.",
            "independent_checker": "Recompute/check result evidence without calling candidate generation; bind the full request digest.",
            "statuses": ["PASS only for checked scope", "FAIL only with a checked refutation witness", "UNKNOWN on unsupported, over-budget, or unavailable cases"],
            "tests": ["positive exact fixture", "forged/tampered certificate", "boundary/resource cap", "unsupported assumptions retain UNKNOWN"],
        },
        "known_unregistered_tool_candidates": _candidate_tool_facts(spec),
        "execution_policy": {
            "install_authorized": False,
            "generated_code_execution_authorized": False,
            "instruction": "Tool discovery does not authorize dependency installation or source execution. Review and register an adapter only after its code, license, input bounds and independent checker are approved.",
        },
        "return_to_chain": "Return a reviewed typed adapter declaration, its verifier, focused regressions and a checked artifact; only then can the application-owned router register it.",
    }
    return {"status": "UNKNOWN", "assurance": "NONE", "selected_capability": None,
            "selection_evidence": {"policy": "typed applicability and local pinned availability", "performance_ranking": "NOT_BENCHMARKED"},
            "reason": reason, "extension_task": task}


def _sympy_import():
    info = _sympy_status()
    if info["availability"] != "AVAILABLE" or info["version"] != SYMPY_VERSION:
        return None, info
    import sympy
    if sympy.__version__ != SYMPY_VERSION:
        return None, {"availability": "VERSION_MISMATCH", "version": sympy.__version__}
    return sympy, info


def _solve_linear(spec):
    matrix, rhs, work, requested_budget = _linear_input(spec)
    sympy, info = _sympy_import()
    if sympy is None:
        return _extension_task(spec, "registered_backend_unavailable",
                               "The registered SymPy backend is not available at its pinned version", info)
    from sympy.polys.matrices import DomainMatrix
    from sympy.polys.matrices.exceptions import DMNonInvertibleMatrixError
    domain = sympy.QQ
    to_domain = lambda value: domain(int(value.numerator), int(value.denominator))
    matrix_dm = DomainMatrix.from_list([[to_domain(value) for value in row] for row in matrix], domain)
    rhs_dm = DomainMatrix.from_list([[to_domain(value)] for value in rhs], domain)
    try:
        numerator, denominator = matrix_dm.solve_den(rhs_dm)
    except DMNonInvertibleMatrixError as exc:
        # SymPy's documented no-unique-solution exception is not interpreted as
        # UNSAT. An independent exact rank check only classifies the missing
        # adapter obligation so the host model can build it.
        rank_a, _pivots_a, _ops_a = _linear_rank(matrix)
        augmented = [row + [rhs[index]] for index, row in enumerate(matrix)]
        rank_augmented, _pivots_aug, _ops_aug = _linear_rank(augmented)
        if rank_a == rank_augmented < len(matrix[0]):
            gap, label = "parametric_linear_solution_family", "The equations are consistent but do not determine one unique vector"
        elif rank_augmented > rank_a:
            gap, label = "inconsistent_system_certificate", "A unique solution was not returned; an independently checked inconsistency certificate is not implemented"
        else:
            gap, label = "backend_result_classification", "The backend did not return a unique solution and the bounded router cannot classify this result"
        return _extension_task(spec, gap, label,
                               {"rank_a": rank_a, "rank_augmented": rank_augmented,
                                "backend_error_type": type(exc).__name__})
    denominator_value = domain.to_sympy(denominator)
    numerator_values = numerator.to_list()
    solution = [Fraction(str(domain.to_sympy(row[0]) / denominator_value)) for row in numerator_values]
    certificate = {"schema": CERTIFICATE_SCHEMA, "kind": "unique_linear_solution",
                   "spec_sha256": _sha256(spec), "solution": [_fraction_json(value) for value in solution],
                   "rank_a": len(matrix[0])}
    if not _linear_certificate_valid(spec, certificate):
        return {"status": "ERROR", "assurance": "NONE", "selected_capability": "sympy.qq.linear.unique.v1",
                "reason": "Generated solution did not pass the independent exact checker"}
    return {"status": "PASS", "assurance": "CERTIFICATE_CHECKED", "semantics": "unique_solution_over_QQ",
            "selected_capability": "sympy.qq.linear.unique.v1",
            "backend": {"name": "SymPy", "version": info["version"], "api": "DomainMatrix.solve_den",
                        "license": "BSD-3-Clause"},
            "selection_evidence": {"matched_input_kind": spec["kind"], "domain": "QQ",
                                   "availability": "SymPy==" + info["version"],
                                   "basis": "registered unique-system contract; performance ranking not measured"},
            "work": {"estimated_units": work, "request_budget": requested_budget,
                     "unit": "rows * columns * (columns + 1)"},
            "result": {"solution": certificate["solution"], "unique": True},
            "certificate": certificate}


def _solve_roots(spec):
    coefficients, width, work, requested_budget = _polynomial_input(spec)
    sympy, info = _sympy_import()
    if sympy is None:
        return _extension_task(spec, "registered_backend_unavailable",
                               "The registered SymPy backend is not available at its pinned version", info)
    from sympy import Poly, Symbol
    symbol = Symbol("x")
    sympy_coefficients = [sympy.Rational(value.numerator, value.denominator) for value in reversed(coefficients)]
    polynomial = Poly.from_list(sympy_coefficients, symbol, domain=sympy.QQ)
    eps = sympy.Rational(width.numerator, width.denominator)
    if polynomial.degree() <= 0:
        intervals = []
    else:
        try:
            isolated = polynomial.intervals(eps=eps)
        except Exception as exc:
            return _extension_task(spec, "root_isolation_incomplete_or_budget_exhausted",
                                   "SymPy did not return a bounded exact interval list; no root verdict is inferred",
                                   {"backend_error_type": type(exc).__name__})
        intervals = [{"lower": _fraction_json(Fraction(str(bounds[0]))),
                      "upper": _fraction_json(Fraction(str(bounds[1])))}
                     for bounds, _multiplicity in isolated]
    certificate = {"schema": CERTIFICATE_SCHEMA, "kind": "distinct_real_root_intervals",
                   "spec_sha256": _sha256(spec), "intervals": intervals,
                   "distinct_real_root_count": len(intervals)}
    if not _root_certificate_valid(spec, certificate):
        return {"status": "ERROR", "assurance": "NONE",
                "selected_capability": "sympy.qq.univariate.distinct-real-roots.v1",
                "reason": "Generated isolating intervals did not pass the independent Sturm checker"}
    return {"status": "PASS", "assurance": "CERTIFICATE_CHECKED",
            "semantics": "complete_distinct_real_roots_over_R_of_QQ_polynomial; multiplicities_not_reported",
            "selected_capability": "sympy.qq.univariate.distinct-real-roots.v1",
            "backend": {"name": "SymPy", "version": info["version"], "api": "Poly.intervals(eps=...) over QQ",
                        "license": "BSD-3-Clause"},
            "selection_evidence": {"matched_input_kind": spec["kind"], "domain": "QQ",
                                   "availability": "SymPy==" + info["version"],
                                   "basis": "registered exact univariate root contract; performance ranking not measured"},
            "work": {"estimated_units": work, "request_budget": requested_budget,
            "unit": "degree cubed times requested interval precision bits"},
            "result": {"distinct_real_roots": intervals, "root_count": len(intervals),
                       "multiplicities": "NOT_REPORTED", "interval_width_bound": _fraction_json(width)},
            "certificate": certificate}


def route(spec):
    """Select one registered exact adapter or return a machine-readable task."""
    if not isinstance(spec, dict):
        raise ValueError("Math request must be a JSON object")
    try:
        size = len(_canonical(spec).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError("Math request must be finite JSON") from exc
    if size > MAX_SPEC_BYTES:
        raise ValueError("Math request exceeds 64 KiB")
    kind = spec.get("kind")
    if kind == "rational_linear_system":
        try:
            return _solve_linear(spec)
        except _Unsupported as exc:
            return _extension_task(spec, exc.gap, str(exc), exc.details)
    if kind == "univariate_qq_polynomial_roots":
        try:
            return _solve_roots(spec)
        except _Unsupported as exc:
            return _extension_task(spec, exc.gap, str(exc), exc.details)
    if not isinstance(kind, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", kind):
        raise ValueError("Math request kind must be a bounded lowercase identifier")
    return _extension_task(spec, "no_registered_adapter_for_input_kind",
                           "No registered capability accepts this problem kind and its requested semantics")


def read_json(path, max_bytes=MAX_SPEC_BYTES):
    with Path(path).open("rb") as stream:
        raw = stream.read(max_bytes + 1)
    _require(len(raw) <= max_bytes, "JSON input exceeds its size bound")
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError("Duplicate JSON object key: " + key)
            value[key] = item
        return value
    def reject_constant(value):
        raise ValueError("Non-finite JSON number is unsupported: " + value)
    return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=reject_constant)


def solve_and_check(spec):
    result = route(spec)
    if result.get("status") == "PASS":
        if not check_certificate(spec, result.get("certificate")):
            return {"status": "ERROR", "assurance": "NONE", "reason": "Self-check failed during result assembly"}
    return result


def replay_file(spec_path, certificate_path):
    spec = read_json(spec_path)
    certificate = read_json(certificate_path, max_bytes=MAX_SPEC_BYTES)
    if check_certificate(spec, certificate):
        return {"status": "PASS", "assurance": "CERTIFICATE_CHECKED",
                "semantics": "certificate replayed with bounded standard-library exact arithmetic",
                "certificate": certificate}
    return {"status": "INVALID_CERTIFICATE", "assurance": "NONE",
            "reason": "Certificate is malformed, incomplete, tampered or bound to a different request"}
