"""Declarative statements, trusted proof rules, and independent certificate replay.

The JSON language can choose registered rules, never load Python or assert an
axiom. This finite certificate framework is not the Lean kernel or a prover for
arbitrary dependent types. Search and certificate checking have separate APIs.
"""
from copy import deepcopy
from dataclasses import dataclass
import importlib
from pathlib import Path
import re
import sys

from rds_verify_types import MAX_CERTIFICATE_BYTES, SEMANTICS, bounded_json, canonical, digest, rational, require
from rds_affine_toolchain import ASSURANCE as AFFINE_CHAIN_ASSURANCE
from rds_affine_toolchain import COMPOSITION_KIND as AFFINE_COMPOSITION_KIND
from rds_affine_toolchain import COMPOSITION_RULE as AFFINE_COMPOSITION_RULE
from rds_affine_toolchain import KIND as AFFINE_CHAIN_KIND
from rds_affine_toolchain import RULE as AFFINE_CHAIN_RULE
from rds_theory_progression import PROOF_ASSURANCE as BOUNDED_PROOF_ASSURANCE
from rds_theory_progression import PROOF_KIND as BOUNDED_PROOF_KIND
from rds_theory_progression import PROOF_RULE as BOUNDED_PROOF_RULE
from rds_theory_progression import PROOF_SEMANTICS as BOUNDED_PROOF_SEMANTICS

MAX_THEOREMS = 64
MAX_NODES = 100000
MAX_DEPTH = 64


@dataclass(frozen=True)
class ProofRule:
    name: str
    kinds: tuple
    module: str
    version: str = "1"
    support_files: tuple = ()

    def generate(self, statement):
        return importlib.import_module(self.module).verify(statement)

    def check(self, statement, certificate):
        return importlib.import_module(self.module).check_certificate(statement, certificate)

    @staticmethod
    def checked_assurance(certificate):
        """Use assurance embedded in checked evidence, otherwise the registered checker label."""
        value = certificate.get("assurance")
        return value if isinstance(value, str) else "CERTIFICATE_CHECKED"


class RuleRegistry:
    """Application-owned whitelist; declarations cannot register executable code.

    New domain rules implement verify/check_certificate and are registered by
    trusted Python code. Their source files participate in the verifier hash.
    """
    def __init__(self, rules):
        self.by_name, self.by_kind = {}, {}
        for rule in rules:
            require(isinstance(rule.support_files, tuple) and all(isinstance(name, str) and
                    re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*\.py", name) for name in rule.support_files),
                    "Proof rule support files must be local Python basenames")
            require(rule.name not in self.by_name, "Duplicate proof rule")
            self.by_name[rule.name] = rule
            for kind in rule.kinds:
                require(kind not in self.by_kind, "Ambiguous statement kind")
                self.by_kind[kind] = rule

    def rule(self, statement, requested=None):
        kind = statement.get("kind")
        require(isinstance(kind, str), "Statement requires a kind")
        rule = self.by_kind.get(kind)
        require(rule is not None, "Unsupported statement kind: " + kind)
        require(requested is None or requested == rule.name, "Rule does not prove this statement kind")
        return rule


REGISTRY = RuleRegistry((
    ProofRule("scalar.threshold_separation", ("scalar_threshold",), "rds_scalar_verify"),
    ProofRule("lean.rational_relation", ("lean_obligation", "lean_vector_obligation"), "rds_lean_verify"),
    ProofRule("lean.statistical_obligation", ("statistical_obligation",), "rds_statistical_verify"),
    ProofRule(AFFINE_CHAIN_RULE, (AFFINE_CHAIN_KIND,), "rds_affine_toolchain",
              support_files=("rds_dynamics_verify.py", "rds_lean_verify.py")),
    ProofRule(AFFINE_COMPOSITION_RULE, (AFFINE_COMPOSITION_KIND,), "rds_affine_toolchain"),
    ProofRule("matrix.infinity_contraction", ("affine_contraction",), "rds_dynamics_verify"),
    ProofRule("matrix.fixed_point", ("affine_fixed_point",), "rds_dynamics_verify"),
    ProofRule("dynamics.affine", ("affine_dynamics",), "rds_dynamics_verify"),
    ProofRule("matrix.gershgorin", ("matrix_spectral_bound",), "rds_matrix_verify"),
    ProofRule("matrix.spectral_radius", ("matrix_spectral_exact",), "rds_matrix_verify"),
    ProofRule("network.positive_homogeneity", ("scale_equivariance",), "rds_scale_verify"),
    ProofRule("network.interval_bounds", ("network_bounds",), "rds_nn_verify"),
    ProofRule("network.interval_margin", ("network_margin",), "rds_nn_verify"),
    ProofRule("tensor.exact_identity", ("tensor_identity",), "rds_tensor_verify"),
    ProofRule("tensor.exact_bounds", ("tensor_bounds",), "rds_tensor_verify"),
    ProofRule("geometry.unit_disk_quadtree", ("unit_disk_cover",), "rds_disk_cover_verify"),
    ProofRule("geometry.unit_disk_rational_voronoi", ("unit_disk_rational_voronoi",),
              "rds_rational_voronoi_verify", support_files=("rds_unit_disk_voronoi_core.py",)),
    ProofRule(BOUNDED_PROOF_RULE, (BOUNDED_PROOF_KIND,), "rds_theory_progression",
              support_files=("rds_operators.py", "rds_lean_verify.py")),
))


def _bounded_json(value):
    bounded_json(value, max_nodes=MAX_NODES, max_depth=MAX_DEPTH)


def verifier_id():
    """Bind certificates/cache to the checker implementation and runtime."""
    here = Path(__file__).resolve().parent
    names = {rule.module + ".py" for rule in REGISTRY.by_name.values()}
    names.update(name for rule in REGISTRY.by_name.values() for name in rule.support_files)
    names.update({"rds_verify.py", "rds_verify_types.py", "rds_formal_kernel.py", "rds_probe.py"})
    return digest({"sources": {name: digest((here / name).read_bytes()) for name in sorted(names)},
                   "python": sys.version,
                   "rules": [(r.name, r.kinds, r.version) for r in REGISTRY.by_name.values()]})


def rules():
    return [{"name": rule.name, "statement_kinds": list(rule.kinds), "version": rule.version,
             "support_files": list(rule.support_files)}
            for rule in REGISTRY.by_name.values()] + [
                {"name": "logic.and_intro", "statement_kinds": ["all"], "version": "1"}]


def _name(value):
    require(isinstance(value, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,79}", value),
            "Declaration names require 1..80 ASCII letters, digits, _, . or -")
    return value


PLAN_ASSURANCES = {"LEAN_KERNEL_CHECKED", "CERTIFICATE_CHECKED"}
PLAN_CERTIFICATE_KEYS = {"version", "plan_sha256", "artifacts", "steps", "verdict"}


def _plan_artifacts(plan):
    require(isinstance(plan, dict) and set(plan) == {"version", "artifacts", "steps"},
            "Proof plan requires version, artifacts and steps")
    require(type(plan["version"]) is int and plan["version"] == 1,
            "Unsupported proof-plan version")
    artifacts = plan["artifacts"]
    require(isinstance(artifacts, list) and len(artifacts) <= MAX_THEOREMS,
            "Proof plan has too many artifacts")
    by_name = {}
    for item in artifacts:
        require(isinstance(item, dict) and set(item) == {"name", "type", "value"},
                "Invalid proof-plan artifact")
        name = _name(item["name"])
        require(name not in by_name, "Duplicate proof-plan artifact")
        value = item["value"]
        if item["type"] == "exact_rational_vector":
            require(isinstance(value, list) and 1 <= len(value) <= 32 and
                    all(isinstance(entry, str) for entry in value),
                    "Exact rational vector artifacts require 1..32 rational strings")
            normalized = [str(rational(entry)) for entry in value]
            require(value == normalized, "Proof-plan vector values must use canonical rationals")
        elif item["type"] == "exact_rational_affine_model":
            require(isinstance(value, dict) and set(value) == {"matrix", "bias"},
                    "Exact affine model artifacts require matrix and bias")
            matrix, bias = value["matrix"], value["bias"]
            require(isinstance(matrix, list) and 1 <= len(matrix) <= 32 and
                    all(isinstance(row, list) and len(row) == len(matrix) for row in matrix) and
                    isinstance(bias, list) and len(bias) == len(matrix) and
                    all(isinstance(entry, str) for row in matrix for entry in row) and
                    all(isinstance(entry, str) for entry in bias),
                    "Exact affine model artifacts require a square matrix and matching bias")
            normalized = {"matrix": [[str(rational(entry)) for entry in row] for row in matrix],
                          "bias": [str(rational(entry)) for entry in bias]}
            require(value == normalized, "Affine model artifacts must use canonical rationals")
        elif item["type"] == "exact_rational_affine_chain":
            require(isinstance(value, list) and 2 <= len(value) <= 8,
                    "Exact affine chains require 2..8 transformations")
            dimension = None
            normalized = []
            for model in value:
                require(isinstance(model, dict) and set(model) == {"matrix", "bias"},
                        "Affine chain entries require matrix and bias")
                matrix, bias = model["matrix"], model["bias"]
                require(isinstance(matrix, list) and 1 <= len(matrix) <= 32 and
                        all(isinstance(row, list) and len(row) == len(matrix) for row in matrix) and
                        isinstance(bias, list) and len(bias) == len(matrix) and
                        all(isinstance(entry, str) for row in matrix for entry in row) and
                        all(isinstance(entry, str) for entry in bias),
                        "Affine chain entries require a square matrix and matching bias")
                require(dimension is None or dimension == len(matrix),
                        "Affine chain transformations must have the same dimension")
                dimension = len(matrix)
                normalized.append({"matrix": [[str(rational(entry)) for entry in row]
                                               for row in matrix],
                                   "bias": [str(rational(entry)) for entry in bias]})
            require(value == normalized, "Affine chain entries must use canonical rationals")
        else:
            raise ValueError("Unsupported proof-plan artifact type: " + str(item["type"]))
        by_name[name] = item
    return artifacts, by_name


def _resolve_plan_value(value, artifacts):
    if isinstance(value, dict):
        if "$artifact" in value:
            require(set(value) in ({"$artifact"}, {"$artifact", "index"}),
                    "Artifact references require an artifact name and optional vector index")
            name = _name(value["$artifact"])
            require(name in artifacts, "Unbound proof-plan artifact: " + name)
            artifact = artifacts[name]
            if "index" not in value:
                return deepcopy(artifact["value"])
            require(artifact["type"] == "exact_rational_vector",
                    "Only rational vector artifacts support indexed binding")
            require(type(value["index"]) is int, "Artifact vector index must be an integer")
            vector = artifact["value"]
            require(0 <= value["index"] < len(vector), "Artifact vector index is out of range")
            return deepcopy(vector[value["index"]])
        return {key: _resolve_plan_value(part, artifacts) for key, part in value.items()}
    if isinstance(value, list):
        return [_resolve_plan_value(part, artifacts) for part in value]
    return value


def _prepare_proof_plan(plan):
    _bounded_json(plan)
    artifacts, by_name = _plan_artifacts(plan)
    raw_steps = plan["steps"]
    require(isinstance(raw_steps, list) and 1 <= len(raw_steps) <= MAX_THEOREMS,
            "Proof plan requires 1..64 steps")
    prepared, seen = [], set()
    for item in raw_steps:
        require(isinstance(item, dict) and set(item) == {"name", "required_assurance", "statement"},
                "Invalid proof-plan step")
        name = _name(item["name"])
        require(name not in seen, "Duplicate proof-plan step")
        seen.add(name)
        assurance = item["required_assurance"]
        require(isinstance(assurance, str) and assurance in PLAN_ASSURANCES,
                "Unsupported proof-plan assurance requirement")
        statement = _resolve_plan_value(item["statement"], by_name)
        require(isinstance(statement, dict), "Proof-plan step must resolve to a statement")
        rule = REGISTRY.rule(statement)
        prepared.append({"name": name, "required_assurance": assurance,
                         "statement": statement, "rule": rule})
    return artifacts, prepared


def execute_proof_plan(plan):
    """Resolve typed artifact references, route obligations by kind, and generate proofs."""
    try:
        artifacts, steps = _prepare_proof_plan(plan)
        proofs, discharged = [], {}
        for step in steps:
            key = digest({"rule": step["rule"].name,
                          "required_assurance": step["required_assurance"],
                          "statement": step["statement"]})
            fields = {"name": step["name"],
                      "required_assurance": step["required_assurance"],
                      "rule": step["rule"].name, "statement": step["statement"]}
            if key in discharged:
                proofs.append({**fields, "reuses": discharged[key]})
                continue
            answer = step["rule"].generate(step["statement"])
            if not isinstance(answer, dict):
                return _unknown("Proof-plan step " + step["name"] + " returned no structured result")
            evidence = answer.get("certificate")
            actual_assurance = (step["rule"].checked_assurance(evidence)
                                if isinstance(evidence, dict) else None)
            if (answer.get("status") != "PASS" or not isinstance(evidence, dict) or
                    evidence.get("verdict") != "PASS" or
                    answer.get("assurance") != actual_assurance or
                    actual_assurance != step["required_assurance"]):
                return _unknown("Proof-plan step " + step["name"] + " was not discharged at the required assurance: " +
                                str(answer.get("reason", answer.get("status", "UNKNOWN"))))
            proofs.append({**fields, "certificate": evidence})
            discharged[key] = step["name"]
        certificate = {"version": 1, "plan_sha256": digest(plan), "artifacts": artifacts,
                       "steps": proofs, "verdict": "PASS"}
        require(len(canonical(certificate).encode("utf-8")) <= MAX_CERTIFICATE_BYTES,
                "Proof-plan certificate exceeds the size limit")
        return {"status": "PASS", "certificate": certificate}
    except (ValueError, TypeError, KeyError, AttributeError, ImportError, OSError,
            ZeroDivisionError, OverflowError, RecursionError) as exc:
        return _unknown("Invalid or unsupported proof plan: " + str(exc))


def replay_proof_plan(plan, certificate):
    """Replay a plan using registered checkers only; never call a proof generator."""
    try:
        artifacts, steps = _prepare_proof_plan(plan)
        _bounded_json(certificate)
        require(isinstance(certificate, dict) and set(certificate) == PLAN_CERTIFICATE_KEYS,
                "Invalid proof-plan certificate fields")
        require(type(certificate["version"]) is int and certificate["version"] == 1 and
                certificate["plan_sha256"] == digest(plan) and certificate["artifacts"] == artifacts and
                certificate["verdict"] == "PASS", "Proof-plan certificate binding mismatch")
        proofs = certificate["steps"]
        require(isinstance(proofs, list) and len(proofs) == len(steps),
                "Proof-plan certificate omits or adds steps")
        discharged, evidence_by_name = {}, {}
        for expected, item in zip(steps, proofs):
            require(isinstance(item, dict) and
                    set(item) in ({"name", "required_assurance", "rule", "statement", "certificate"},
                                  {"name", "required_assurance", "rule", "statement", "reuses"}),
                    "Invalid proof-plan step certificate")
            require(item["name"] == expected["name"] and
                    item["required_assurance"] == expected["required_assurance"] and
                    item["rule"] == expected["rule"].name and
                    item["statement"] == expected["statement"],
                    "Proof-plan step changed its registered rule or bound statement")
            key = digest({"rule": expected["rule"].name,
                         "required_assurance": expected["required_assurance"],
                         "statement": expected["statement"]})
            if key in discharged:
                require(set(item) == {"name", "required_assurance", "rule", "statement", "reuses"} and
                        item["reuses"] == discharged[key],
                        "Repeated proof-plan steps must reuse their first matching proof")
                evidence_by_name[expected["name"]] = evidence_by_name[discharged[key]]
                continue
            require(set(item) == {"name", "required_assurance", "rule", "statement", "certificate"},
                    "First proof-plan step must carry its certificate")
            evidence = item["certificate"]
            require(isinstance(evidence, dict) and evidence.get("verdict") == "PASS" and
                    expected["rule"].check(expected["statement"], evidence) and
                    expected["rule"].checked_assurance(evidence) == expected["required_assurance"],
                    "Proof-plan child certificate did not replay")
            evidence_by_name[expected["name"]] = evidence
            discharged[key] = expected["name"]
        return True
    except (ValueError, TypeError, KeyError, AttributeError, ImportError, OSError,
            ZeroDivisionError, OverflowError, RecursionError):
        return False


def _elaborate(spec):
    """Resolve named definitions and a finite theorem DAG without executing code."""
    require(set(spec) == {"schema", "kind", "definitions", "theorems"}, "Invalid theorem module fields")
    require(type(spec["schema"]) is int and spec["schema"] == 1, "Expected module schema 1")
    definitions = spec["definitions"]
    require(isinstance(definitions, dict) and len(definitions) <= MAX_THEOREMS, "Too many definitions")
    for name in definitions:
        _name(name)
    resolved, visiting = {}, set()

    def resolve(value, depth=0):
        require(depth <= MAX_DEPTH, "Definition expansion exceeds depth limit")
        if isinstance(value, dict) and "$ref" in value:
            require(set(value) == {"$ref"}, "A definition reference cannot have extra fields")
            name = _name(value["$ref"])
            require(name in definitions, "Unbound definition: " + name)
            require(name not in visiting, "Cyclic definition: " + name)
            if name not in resolved:
                visiting.add(name)
                resolved[name] = resolve(definitions[name], depth + 1)
                visiting.remove(name)
                _bounded_json(resolved[name])
            return resolved[name]
        if isinstance(value, dict):
            return {k: resolve(v, depth + 1) for k, v in value.items()}
        if isinstance(value, list):
            return [resolve(v, depth + 1) for v in value]
        return value

    # Unused definitions still must be well formed; no hidden axioms or cycles.
    for name in definitions:
        resolve({"$ref": name})
    raw = spec["theorems"]
    require(isinstance(raw, list) and 1 <= len(raw) <= MAX_THEOREMS, "Module requires 1..64 theorems")
    declarations = {}
    for item in raw:
        require(isinstance(item, dict) and set(item) == {"name", "statement", "by"}, "Invalid theorem declaration")
        name = _name(item["name"])
        require(name not in declarations, "Duplicate theorem name")
        statement = resolve(item["statement"])
        _bounded_json(statement)
        require(isinstance(statement, dict), "Theorem statement must be an object")
        proof = item["by"]
        require(isinstance(proof, dict) and isinstance(proof.get("rule"), str), "Theorem requires a proof rule")
        if statement.get("kind") == "all":
            require(set(statement) == {"kind", "of"} and set(proof) == {"rule", "premises"},
                    "Conjunction requires exactly its named premises")
            refs = statement["of"]
            require(isinstance(refs, list) and 1 <= len(refs) <= MAX_THEOREMS,
                    "Conjunction requires 1..64 premises")
            require(all(isinstance(ref, str) for ref in refs) and len(set(refs)) == len(refs),
                    "Conjunction premises must be unique theorem names")
            require(proof["rule"] == "logic.and_intro" and proof["premises"] == refs,
                    "Conjunction proof does not match its statement")
        else:
            require(set(proof) == {"rule"}, "Atomic rules cannot assert premises or axioms")
            refs = []
        declarations[name] = {"statement": statement, "rule": proof["rule"], "premises": refs}
    order, active, done = [], set(), set()

    def visit(name):
        require(name in declarations, "Unbound theorem: " + name)
        require(name not in active, "Cyclic theorem dependency: " + name)
        if name in done:
            return
        active.add(name)
        for premise in declarations[name]["premises"]:
            visit(premise)
        active.remove(name)
        done.add(name)
        order.append(name)

    for name in declarations:
        visit(name)
    return declarations, order


def _unknown(reason):
    return {"status": "UNKNOWN", "assurance": "NONE", "backend": "rds_declarative",
            "reason": str(reason)}


def _wrap(spec, verdict, proof):
    return {"version": 1, "spec_sha256": digest(spec), "verifier_sha256": verifier_id(),
            "verdict": verdict, "proof": proof}


def _atomic(statement, requested=None):
    rule = REGISTRY.rule(statement, requested)
    result = rule.generate(statement)
    verdict, evidence = result.get("status"), result.get("certificate")
    if verdict not in {"PASS", "FAIL"} or not isinstance(evidence, dict):
        return dict(_unknown(result.get("reason", "Proof search was inconclusive")),
                    **{key: result[key] for key in ("conditional_statement", "application_status", "assumptions_required")
                       if key in result})
    require(evidence.get("verdict") == verdict, "Proof rule has inconsistent candidate evidence")
    # Final checked_result replays every leaf and composition exactly once.
    # Checking here as well would duplicate native Lean compilation.
    return {"status": verdict, "proof": {"rule": rule.name, "certificate": evidence}}


def verify(spec):
    """Search for a proof, then independently check it; unknown is never admitted."""
    try:
        _bounded_json(spec)
        require(isinstance(spec, dict), "Specification must be an object")
        if spec.get("kind") != "theorem_module":
            answer = _atomic(spec)
            if answer["status"] == "UNKNOWN":
                return answer
            certificate = _wrap(spec, answer["status"], answer["proof"])
        else:
            declarations, order = _elaborate(spec)
            proofs, outcomes = {}, {}
            for name in order:
                declaration = declarations[name]
                if declaration["premises"]:
                    statuses = [outcomes[ref]["status"] for ref in declaration["premises"]]
                    status = "FAIL" if "FAIL" in statuses else "UNKNOWN" if "UNKNOWN" in statuses else "PASS"
                    answer = {"status": status, "proof": {"rule": "logic.and_intro",
                              "premises": declaration["premises"], "verdict": status}}
                else:
                    try:
                        answer = _atomic(declaration["statement"], declaration["rule"])
                    except (ValueError, TypeError, KeyError, ImportError) as exc:
                        answer = _unknown(exc)
                outcomes[name] = {"status": answer["status"]}
                for key in ("reason", "conditional_statement", "application_status", "assumptions_required"):
                    if key in answer:
                        outcomes[name][key] = answer[key]
                if "proof" in answer:
                    proofs[name] = answer["proof"]
            statuses = [outcome["status"] for outcome in outcomes.values()]
            if "UNKNOWN" in statuses:
                result = dict(_unknown("Some theorem obligations remain unproved"), theorems=outcomes)
                conditional = [item for item in outcomes.values() if item.get("conditional_statement") is True]
                if conditional:
                    result.update(conditional_statement=True, application_status="UNKNOWN",
                                  assumptions_required=sorted({assumption for item in conditional
                                                               for assumption in item["assumptions_required"]}))
                return result
            verdict = "FAIL" if "FAIL" in statuses else "PASS"
            certificate = _wrap(spec, verdict, {"rule": "module", "theorems": proofs})
        return checked_result(spec, certificate)
    except (ValueError, TypeError, KeyError, AttributeError, ImportError, OSError,
            ZeroDivisionError, OverflowError, RecursionError) as exc:
        return _unknown(exc)


def _check_atomic(statement, proof, requested=None):
    require(isinstance(proof, dict) and set(proof) == {"rule", "certificate"}, "Invalid atomic proof")
    rule = REGISTRY.rule(statement, requested)
    require(proof["rule"] == rule.name and rule.check(statement, proof["certificate"]), "Invalid domain certificate")
    verdict = proof["certificate"]["verdict"]
    require(verdict in {"PASS", "FAIL"}, "Certificate has no checked verdict")
    return verdict


def _replay(spec, certificate):
    _bounded_json(spec)
    _bounded_json(certificate)
    require(isinstance(spec, dict) and isinstance(certificate, dict), "Expected declaration and certificate")
    require(set(certificate) == {"version", "spec_sha256", "verifier_sha256", "verdict", "proof"},
            "Invalid framework certificate fields")
    require(type(certificate["version"]) is int and certificate["version"] == 1,
            "Unsupported framework certificate version")
    require(certificate["spec_sha256"] == digest(spec) and certificate["verifier_sha256"] == verifier_id(),
            "Certificate does not bind this declaration and verifier")
    proof = certificate["proof"]
    if spec.get("kind") != "theorem_module":
        verdict = _check_atomic(spec, proof)
        outcomes = None
    else:
        declarations, order = _elaborate(spec)
        require(isinstance(proof, dict) and set(proof) == {"rule", "theorems"} and proof["rule"] == "module",
                "Invalid module proof")
        claims = proof["theorems"]
        require(isinstance(claims, dict) and set(claims) == set(declarations), "Module proof omits or adds theorems")
        outcomes = {}
        for name in order:
            declaration, item = declarations[name], claims[name]
            if declaration["premises"]:
                require(isinstance(item, dict) and set(item) == {"rule", "premises", "verdict"}, "Invalid conjunction proof")
                require(item["rule"] == "logic.and_intro" and item["premises"] == declaration["premises"],
                        "Conjunction changes the theorem dependencies")
                verdict = "FAIL" if any(outcomes[ref]["status"] == "FAIL" for ref in declaration["premises"]) else "PASS"
                require(item["verdict"] == verdict, "Conjunction verdict does not follow from its premises")
            else:
                verdict = _check_atomic(declaration["statement"], item, declaration["rule"])
            outcomes[name] = {"status": verdict}
        verdict = "FAIL" if any(v["status"] == "FAIL" for v in outcomes.values()) else "PASS"
    require(certificate["verdict"] == verdict, "Framework verdict does not follow from the proof")
    return verdict, outcomes


def check_certificate(spec, certificate):
    """Replay only; this function never calls a proof generator or solver."""
    try:
        _replay(spec, certificate)
        return True
    except (ValueError, TypeError, KeyError, AttributeError, ImportError, OSError,
            ZeroDivisionError, OverflowError, RecursionError):
        return False


def checked_result(spec, certificate):
    try:
        verdict, outcomes = _replay(spec, certificate)
        proof = certificate["proof"]
        leaves = ([proof["certificate"]] if outcomes is None else
                  [item["certificate"] for item in proof["theorems"].values() if "certificate" in item])
        native = bool(leaves) and all(leaf.get("assurance") == "LEAN_KERNEL_CHECKED" for leaf in leaves)
        atomic = outcomes is None
        result = {"status": verdict, "assurance": "LEAN_KERNEL_CHECKED" if native else "CERTIFICATE_CHECKED",
                  "backend": leaves[0].get("backend", "rds_declarative") if atomic else "rds_declarative",
                  "semantics": SEMANTICS, "spec_sha256": certificate["spec_sha256"],
                  "verifier_sha256": certificate["verifier_sha256"], "certificate": certificate}
        if outcomes is not None:
            result["theorems"] = outcomes
        elif atomic:
            result["semantics"] = leaves[0].get("semantics", SEMANTICS)
        bounded_scopes = {}
        composed_scopes = {}
        if outcomes is None and spec.get("kind") == BOUNDED_PROOF_KIND:
            item = proof["certificate"]
            if proof.get("rule") == BOUNDED_PROOF_RULE and item.get("assurance") == BOUNDED_PROOF_ASSURANCE:
                bounded_scopes["claim"] = item["scope"]
                result["assurance"] = BOUNDED_PROOF_ASSURANCE
        elif (outcomes is None and spec.get("kind") == AFFINE_CHAIN_KIND and
              proof.get("rule") == AFFINE_CHAIN_RULE and
              proof.get("certificate", {}).get("assurance") == AFFINE_CHAIN_ASSURANCE):
            item = proof["certificate"]
            result["assurance"] = AFFINE_CHAIN_ASSURANCE
            result["proof_scope"] = item["semantics"]
            result["candidate"] = item["candidate"]
            result["artifacts"] = {"fixed_point": {
                "type": "exact_rational_vector", "dimension": item["dimension"],
                "value": item["candidate"]}}
            result["proof_components"] = {
                "candidate": "exact_rational_gaussian_elimination",
                "fixed_point": "registered_exact_rational_checker",
                "translation": "bounded_exact_affine_evaluation",
                "coordinate_obligations": "native_Lean_kernel_checked",
            }
            result["scientific_assurance"] = "UNKNOWN"
            result["application_status"] = "UNKNOWN"
        elif outcomes is not None:
            declarations, _ = _elaborate(spec)
            for name, declaration in declarations.items():
                item = proof["theorems"].get(name)
                if (declaration["statement"].get("kind") == BOUNDED_PROOF_KIND
                        and isinstance(item, dict) and item.get("rule") == BOUNDED_PROOF_RULE
                        and isinstance(item.get("certificate"), dict)
                        and item["certificate"].get("assurance") == BOUNDED_PROOF_ASSURANCE):
                    scope = item["certificate"]["scope"]
                    bounded_scopes[name] = scope
                    result["theorems"][name].update({"assurance": BOUNDED_PROOF_ASSURANCE,
                                                     "semantics": BOUNDED_PROOF_SEMANTICS,
                                                     "proof_scope": scope,
                                                     "scientific_assurance": "UNKNOWN",
                                                     "application_status": "UNKNOWN"})
                elif (declaration["statement"].get("kind") == AFFINE_CHAIN_KIND
                      and isinstance(item, dict) and item.get("rule") == AFFINE_CHAIN_RULE
                      and isinstance(item.get("certificate"), dict)
                      and item["certificate"].get("assurance") == AFFINE_CHAIN_ASSURANCE):
                    affine_certificate = item["certificate"]
                    composed_scopes[name] = affine_certificate["semantics"]
                    result["theorems"][name].update({
                        "assurance": AFFINE_CHAIN_ASSURANCE,
                        "semantics": affine_certificate["semantics"],
                        "proof_scope": affine_certificate["semantics"],
                        "candidate": affine_certificate["candidate"],
                        "artifacts": {"fixed_point": {
                            "type": "exact_rational_vector",
                            "dimension": affine_certificate["dimension"],
                            "value": affine_certificate["candidate"]}},
                        "scientific_assurance": "UNKNOWN",
                        "application_status": "UNKNOWN"})
        if bounded_scopes:
            result["proof_scope"] = bounded_scopes
            result["scientific_assurance"] = "UNKNOWN"
            result["application_status"] = "UNKNOWN"
        if composed_scopes:
            result["composed_proof_scopes"] = composed_scopes
            result["scientific_assurance"] = "UNKNOWN"
            result["application_status"] = "UNKNOWN"
        conditional = [leaf for leaf in leaves if leaf.get("conditional_statement") is True]
        if conditional:
            result.update(conditional_statement=True, application_status="UNKNOWN",
                          assumptions_required=sorted({assumption for leaf in conditional
                                                       for assumption in leaf["assumptions_required"]}))
        return result
    except (ValueError, TypeError, KeyError, AttributeError, ImportError, OSError,
            ZeroDivisionError, OverflowError, RecursionError) as exc:
        return _unknown("Invalid certificate: " + str(exc))


class LeanFormalEngine:
    """Bounded tactic-chain facade; unsupported tactics cannot certify a claim.

    `rule` selects the trusted registry. Other tactics select compatible domain
    rules. Lean4 accepts only supported generated native obligations; a compiler
    exit code alone cannot certify a claim. Rational uses exact Python only.
    """
    TACTICS = ("rule", "gershgorin", "spectral_radius", "scale_invariance", "lean4", "rational", "interval")

    def verify(self, spec, tactics=("rule", "interval")):
        try:
            require(isinstance(tactics, (list, tuple)) and 1 <= len(tactics) <= len(self.TACTICS),
                    f"Tactic chain requires 1..{len(self.TACTICS)} tactics")
            require(all(isinstance(t, str) and t in self.TACTICS for t in tactics), "Unknown tactic")
            require(len(set(tactics)) == len(tactics), "Repeated tactics are not allowed")
            _bounded_json(spec)
            require(isinstance(spec, dict), "Specification must be an object")
            attempts = []
            tried = set()
            compatible = {"interval": {"network_bounds", "network_margin"},
                          "lean4": {"lean_obligation", "lean_vector_obligation", "statistical_obligation"},
                          "rational": {"lean_obligation", "lean_vector_obligation", "unit_disk_cover", "unit_disk_rational_voronoi"},
                          "gershgorin": {"matrix_spectral_bound"},
                          "spectral_radius": {"matrix_spectral_exact"},
                          "scale_invariance": {"scale_equivariance"}}
            for tactic in tactics:
                applies = tactic == "rule" or spec.get("kind") in compatible.get(tactic, set())
                explicit_rational = spec.get("kind") in {"lean_obligation", "lean_vector_obligation"} and tactic in {"lean4", "rational"}
                method = tactic if explicit_rational else "rule"
                if applies and method not in tried:
                    if explicit_rational:
                        rule = REGISTRY.rule(spec)
                        adapter = importlib.import_module(rule.module)
                        answer = (adapter.verify_rational(spec) if tactic == "rational" else
                                  adapter.verify(spec, allow_fallback=False))
                        if answer["status"] == "PASS":
                            proof = {"rule": rule.name, "certificate": answer["certificate"]}
                            answer = checked_result(spec, _wrap(spec, "PASS", proof))
                    else:
                        answer = verify(spec)
                    tried.add(method)
                    if tactic == "lean4" and answer["status"] in {"PASS", "FAIL"}:
                        require(answer["assurance"] == "LEAN_KERNEL_CHECKED", "Lean4 requires native kernel evidence")
                else:
                    answer = _unknown("Tactic is unavailable or incompatible with this declaration")
                attempts.append({"tactic": tactic, "status": answer["status"],
                                 "reason": answer.get("reason")})
                if answer["status"] in {"PASS", "FAIL"}:
                    return dict(answer, tactics=attempts)
            return dict(_unknown("No tactic supplied a checked proof or counterexample"), tactics=attempts)
        except (ValueError, TypeError, KeyError, RecursionError) as exc:
            return _unknown(exc)
