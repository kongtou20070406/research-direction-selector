"""Synthesize a rational fixed-point witness and discharge generated Lean goals.

The exact eliminator is an untrusted candidate generator. A PASS certificate
contains one native Lean proof for every coordinate of the declared affine map;
replay reconstructs those goals from the original map and candidate.
"""
from fractions import Fraction

from rds_verify_types import MAX_CERTIFICATE_BYTES, bounded, canonical, digest, rational, require

KIND = "affine_fixed_point_synthesis"
RULE = "dynamics.affine_fixed_point_synthesis"
COMPOSITION_KIND = "affine_composition"
COMPOSITION_RULE = "matrix.affine_compose"
ASSURANCE = "COMPOSITION_CHECKED"
BACKEND = "rds_affine_fixed_point_chain"
SEMANTICS = "exact_rational_affine_fixed_point_witness"
MAX_DIMENSION = 32
MAX_COMPOSITION_LENGTH = 8
MAX_SPEC_BYTES = 262144
CERTIFICATE_KEYS = {"version", "spec_sha256", "kind", "dimension", "candidate",
                    "plan", "assurance", "backend", "semantics", "verdict"}
COMPOSITION_CERTIFICATE_KEYS = {"version", "statement_sha256", "composed_model",
                                "assurance", "verdict"}


def _read_model(model):
    require(isinstance(model, dict) and set(model) == {"matrix", "bias"},
            "Affine model requires matrix and bias")
    raw_matrix, raw_bias = model["matrix"], model["bias"]
    require(isinstance(raw_matrix, list) and 1 <= len(raw_matrix) <= MAX_DIMENSION,
            "Affine synthesis dimension must be 1..32")
    dimension = len(raw_matrix)
    require(all(isinstance(row, list) and len(row) == dimension for row in raw_matrix),
            "Affine matrix must be square")
    require(isinstance(raw_bias, list) and len(raw_bias) == dimension,
            "Affine bias must match the matrix dimension")
    require(all(isinstance(value, str) for row in raw_matrix for value in row) and
            all(isinstance(value, str) for value in raw_bias),
            "Affine matrix and bias entries must be rational strings")
    matrix = [[rational(value) for value in row] for row in raw_matrix]
    bias = [rational(value) for value in raw_bias]
    return matrix, bias


def _read_affine_spec(spec):
    require(isinstance(spec, dict), "Affine synthesis specification must be an object")
    require(len(canonical(spec).encode("utf-8")) <= MAX_SPEC_BYTES,
            "Affine synthesis specification exceeds the 256-KiB limit")
    require(type(spec.get("schema")) is int and spec["schema"] == 1,
            "Expected affine synthesis schema 1")
    require(spec.get("kind") == KIND, "Unknown affine synthesis kind")
    if set(spec) == {"schema", "kind", "model"}:
        models = [spec["model"]]
    else:
        require(set(spec) == {"schema", "kind", "composition"},
                "Affine synthesis requires model or composition")
        models = spec["composition"]
        require(isinstance(models, list) and 2 <= len(models) <= MAX_COMPOSITION_LENGTH,
                "Affine composition requires 2..8 transformations")
    parsed = [_read_model(model) for model in models]
    dimension = len(parsed[0][0])
    require(all(len(matrix) == dimension for matrix, _bias in parsed),
            "Affine composition transformations must have the same dimension")
    matrix, bias = _compose_models(parsed)
    return matrix, bias, parsed


def _read_spec(spec):
    matrix, bias, _models = _read_affine_spec(spec)
    return matrix, bias


def _compose_models(models):
    """Compose affine maps in listed application order using exact bounded arithmetic."""
    dimension = len(models[0][0])
    matrix = [[Fraction(1 if i == j else 0) for j in range(dimension)]
              for i in range(dimension)]
    bias = [Fraction(0) for _ in range(dimension)]
    for next_matrix, next_bias in models:
        composed_matrix = []
        for row in next_matrix:
            composed_row = []
            for column in range(dimension):
                total = Fraction(0)
                for coefficient, prior_row in zip(row, matrix):
                    total = bounded(total + bounded(coefficient * prior_row[column]))
                composed_row.append(total)
            composed_matrix.append(composed_row)
        composed_bias = []
        for row, offset in zip(next_matrix, next_bias):
            total = Fraction(0)
            for coefficient, value in zip(row, bias):
                total = bounded(total + bounded(coefficient * value))
            composed_bias.append(bounded(total + offset))
        matrix, bias = composed_matrix, composed_bias
    return matrix, bias


def _encode_model(matrix, bias):
    return {"matrix": [[str(value) for value in row] for row in matrix],
            "bias": [str(value) for value in bias]}


def _parse_canonical_model(model):
    matrix, bias = _read_model(model)
    normalized = _encode_model(matrix, bias)
    require(model == normalized, "Affine composition model must use canonical rationals")
    return matrix, bias


def _read_composition_statement(statement):
    require(isinstance(statement, dict) and
            set(statement) == {"schema", "kind", "transformations", "result"},
            "Affine composition requires transformations and result")
    require(type(statement["schema"]) is int and statement["schema"] == 1 and
            statement["kind"] == COMPOSITION_KIND,
            "Unsupported affine composition statement")
    require(len(canonical(statement).encode("utf-8")) <= MAX_SPEC_BYTES,
            "Affine composition statement exceeds the size limit")
    raw = statement["transformations"]
    require(isinstance(raw, list) and 2 <= len(raw) <= MAX_COMPOSITION_LENGTH,
            "Affine composition requires 2..8 transformations")
    parsed = [_parse_canonical_model(item) for item in raw]
    dimension = len(parsed[0][0])
    require(all(len(matrix) == dimension for matrix, _bias in parsed),
            "Affine composition transformations must have the same dimension")
    actual = _encode_model(*_compose_models(parsed))
    result_matrix, result_bias = _parse_canonical_model(statement["result"])
    require(len(result_matrix) == dimension and _encode_model(result_matrix, result_bias) == actual,
            "Declared affine composition does not match exact matrix multiplication")
    return actual


def _solve(matrix, bias):
    """Return an exact witness when consistent; None is inconclusive, not FAIL."""
    dimension = len(matrix)
    rows = [[bounded((1 if i == j else 0) - matrix[i][j]) for j in range(dimension)] + [bias[i]]
            for i in range(dimension)]
    pivots = []
    pivot_row = 0
    for column in range(dimension):
        selected = next((row for row in range(pivot_row, dimension) if rows[row][column] != 0), None)
        if selected is None:
            continue
        rows[pivot_row], rows[selected] = rows[selected], rows[pivot_row]
        pivot = rows[pivot_row][column]
        rows[pivot_row] = [bounded(value / pivot) for value in rows[pivot_row]]
        for row in range(dimension):
            if row == pivot_row:
                continue
            factor = rows[row][column]
            if factor:
                rows[row] = [bounded(value - bounded(factor * pivot_value))
                             for value, pivot_value in zip(rows[row], rows[pivot_row])]
        pivots.append(column)
        pivot_row += 1
        if pivot_row == dimension:
            break
    for row in rows:
        if all(value == 0 for value in row[:-1]) and row[-1] != 0:
            return None
    candidate = [Fraction(0) for _ in range(dimension)]
    for row, column in enumerate(pivots):
        candidate[column] = rows[row][-1]
    return candidate


def _coordinate_spec(matrix, bias, candidate, coordinate):
    mapped = Fraction(0)
    for coefficient, value in zip(matrix[coordinate], candidate):
        mapped = bounded(mapped + bounded(coefficient * value))
    mapped = bounded(mapped + bias[coordinate])
    return {"schema": 1, "kind": "lean_obligation", "relation": "eq",
            "left": str(candidate[coordinate]), "right": str(mapped)}


def _proof_plan(matrix, bias, candidate, source_models=None):
    """Describe typed outputs and artifact-bound obligations for the generic plan runner."""
    artifacts = [
        {"name": "affine_model", "type": "exact_rational_affine_model",
         "value": _encode_model(matrix, bias)},
        {"name": "fixed_point", "type": "exact_rational_vector",
         "value": [str(value) for value in candidate]},
    ]
    steps = []
    if source_models and len(source_models) > 1:
        artifacts.append({"name": "affine_chain", "type": "exact_rational_affine_chain",
                          "value": [_encode_model(*model) for model in source_models]})
        steps.append({"name": "compose_affine_chain", "required_assurance": "CERTIFICATE_CHECKED",
                      "statement": {"schema": 1, "kind": COMPOSITION_KIND,
                                    "transformations": {"$artifact": "affine_chain"},
                                    "result": {"$artifact": "affine_model"}}})
    steps.append({"name": "fixed_point_exact", "required_assurance": "CERTIFICATE_CHECKED",
              "statement": {"schema": 1, "kind": "affine_fixed_point",
                            "model": {"$artifact": "affine_model"},
                            "point": {"$artifact": "fixed_point"}}})
    relations = []
    for coordinate in range(len(candidate)):
        statement = _coordinate_spec(matrix, bias, candidate, coordinate)
        relations.append({"relation": "eq",
                          "left": {"$artifact": "fixed_point", "index": coordinate},
                          "right": statement["right"]})
    steps.append({"name": "coordinate_obligations",
                  "required_assurance": "LEAN_KERNEL_CHECKED",
                  "statement": {"schema": 1, "kind": "lean_vector_obligation",
                                "relations": relations}})
    return {"version": 1, "artifacts": artifacts, "steps": steps}


def _verify_synthesis(spec):
    """Generate a witness, then require native Lean proofs of all coordinate goals."""
    try:
        matrix, bias, source_models = _read_affine_spec(spec)
        candidate = _solve(matrix, bias)
        if candidate is None:
            return {"status": "UNKNOWN", "assurance": "NONE", "backend": BACKEND,
                    "reason": "No rational fixed-point witness was found; nonexistence is not certified"}
        plan = _proof_plan(matrix, bias, candidate, source_models)
        from rds_verify import execute_proof_plan
        answer = execute_proof_plan(plan)
        if answer.get("status") != "PASS":
            return {"status": "UNKNOWN", "assurance": "NONE", "backend": BACKEND,
                    "reason": answer.get("reason", "Generated proof plan was not discharged")}
        certificate = {"version": 1, "spec_sha256": digest(spec), "kind": KIND,
                       "dimension": len(candidate), "candidate": [str(value) for value in candidate],
                       "plan": answer["certificate"], "assurance": ASSURANCE, "backend": BACKEND,
                       "semantics": SEMANTICS, "verdict": "PASS"}
        require(len(canonical(certificate).encode("utf-8")) <= MAX_CERTIFICATE_BYTES,
                "Affine proof certificate exceeds the size limit")
        return {"status": "PASS", "assurance": ASSURANCE, "backend": BACKEND,
                "semantics": SEMANTICS, "candidate": certificate["candidate"],
                "certificate": certificate,
                "scope": ("A synthesized rational point is a fixed point of the exact composition "
                          "of the declared finite affine transformations" if len(source_models) > 1 else
                          "A synthesized rational point is a fixed point of the declared finite affine map")}
    except (ValueError, TypeError, KeyError, AttributeError, OSError, ZeroDivisionError,
            OverflowError, RecursionError, UnicodeError) as exc:
        return {"status": "UNKNOWN", "assurance": "NONE", "backend": BACKEND, "reason": str(exc)}


def _check_synthesis_certificate(spec, certificate):
    """Replay the emitted subgoals; never rerun candidate search."""
    try:
        matrix, _bias, source_models = _read_affine_spec(spec)
        require(isinstance(certificate, dict) and set(certificate) == CERTIFICATE_KEYS,
                "Invalid affine synthesis certificate fields")
        require(len(canonical(certificate).encode("utf-8")) <= MAX_CERTIFICATE_BYTES,
                "Affine proof certificate exceeds the size limit")
        dimension = len(matrix)
        require(type(certificate["version"]) is int and certificate["version"] == 1 and
                certificate["spec_sha256"] == digest(spec) and certificate["kind"] == KIND and
                type(certificate["dimension"]) is int and certificate["dimension"] == dimension,
                "Affine certificate is not bound to this specification")
        candidate = certificate["candidate"]
        require(isinstance(candidate, list) and len(candidate) == dimension and
                all(isinstance(value, str) for value in candidate),
                "Affine certificate requires one rational candidate per coordinate")
        normalized = [str(rational(value)) for value in candidate]
        require(candidate == normalized, "Affine candidate is not canonically encoded")
        plan = _proof_plan(matrix, _bias, [rational(value) for value in candidate], source_models)
        from rds_verify import replay_proof_plan
        require(replay_proof_plan(plan, certificate["plan"]),
                "Generated typed proof plan did not replay")
        require(certificate["assurance"] == ASSURANCE and certificate["backend"] == BACKEND and
                certificate["semantics"] == SEMANTICS and certificate["verdict"] == "PASS",
                "Affine certificate has an unsupported assurance or verdict")
        return True
    except (ValueError, TypeError, KeyError, AttributeError, OSError, ZeroDivisionError,
            OverflowError, RecursionError, UnicodeError):
        return False


def _verify_composition(statement):
    try:
        composed = _read_composition_statement(statement)
        certificate = {"version": 1, "statement_sha256": digest(statement),
                      "composed_model": composed, "assurance": "CERTIFICATE_CHECKED",
                      "verdict": "PASS"}
        require(len(canonical(certificate).encode("utf-8")) <= MAX_CERTIFICATE_BYTES,
                "Affine composition certificate exceeds the size limit")
        return {"status": "PASS", "assurance": "CERTIFICATE_CHECKED",
                "backend": "rds_exact_affine_composition", "certificate": certificate,
                "scope": "Exact rational composition of finite-dimensional affine maps"}
    except (ValueError, TypeError, KeyError, AttributeError, OSError, ZeroDivisionError,
            OverflowError, RecursionError, UnicodeError) as exc:
        return {"status": "UNKNOWN", "assurance": "NONE",
                "backend": "rds_exact_affine_composition", "reason": str(exc)}


def _check_composition_certificate(statement, certificate):
    try:
        expected_model = _read_composition_statement(statement)
        expected = {"version": 1, "statement_sha256": digest(statement),
                    "composed_model": expected_model, "assurance": "CERTIFICATE_CHECKED",
                    "verdict": "PASS"}
        return isinstance(certificate, dict) and set(certificate) == COMPOSITION_CERTIFICATE_KEYS \
            and len(canonical(certificate).encode("utf-8")) <= MAX_CERTIFICATE_BYTES \
            and certificate == expected
    except (ValueError, TypeError, KeyError, AttributeError, OSError, ZeroDivisionError,
            OverflowError, RecursionError, UnicodeError):
        return False


def verify(spec):
    """Dispatch registered proof-plan leaves to affine composition or synthesis."""
    if isinstance(spec, dict) and spec.get("kind") == COMPOSITION_KIND:
        return _verify_composition(spec)
    return _verify_synthesis(spec)


def check_certificate(spec, certificate):
    """Replay either an exact composition leaf or a solver-to-Lean certificate."""
    if isinstance(spec, dict) and spec.get("kind") == COMPOSITION_KIND:
        return _check_composition_certificate(spec, certificate)
    return _check_synthesis_certificate(spec, certificate)
