"""Declarative statements, trusted proof rules, and independent certificate replay.

The JSON language can choose registered rules, never load Python or assert an
axiom. This finite certificate framework is not the Lean kernel or a prover for
arbitrary dependent types. Search and certificate checking have separate APIs.
"""
from dataclasses import dataclass
import importlib
from pathlib import Path
import re
import sys

from rds_verify_types import MAX_CERTIFICATE_BYTES, SEMANTICS, bounded_json, canonical, digest, require
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
    ProofRule("lean.rational_relation", ("lean_obligation",), "rds_lean_verify"),
    ProofRule("lean.statistical_obligation", ("statistical_obligation",), "rds_statistical_verify"),
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
        if outcomes is None and spec.get("kind") == BOUNDED_PROOF_KIND:
            item = proof["certificate"]
            if proof.get("rule") == BOUNDED_PROOF_RULE and item.get("assurance") == BOUNDED_PROOF_ASSURANCE:
                bounded_scopes["claim"] = item["scope"]
                result["assurance"] = BOUNDED_PROOF_ASSURANCE
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
        if bounded_scopes:
            result["proof_scope"] = bounded_scopes
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
                          "lean4": {"lean_obligation", "statistical_obligation"},
                          "rational": {"lean_obligation", "unit_disk_cover", "unit_disk_rational_voronoi"},
                          "gershgorin": {"matrix_spectral_bound"},
                          "spectral_radius": {"matrix_spectral_exact"},
                          "scale_invariance": {"scale_equivariance"}}
            for tactic in tactics:
                applies = tactic == "rule" or spec.get("kind") in compatible.get(tactic, set())
                explicit_rational = spec.get("kind") == "lean_obligation" and tactic in {"lean4", "rational"}
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
