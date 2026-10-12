"""Deterministic public-entry regressions for the installed builtin routes."""
import copy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import rds_verify as engine
import rds_verify_registry as registry
import rds_dynamics_verify as dynamics
import rds_affine_toolchain as affine
from rds_builtin_verifiers import BuiltinVerifier


def fixed():
    return {"schema": 1, "kind": "affine_fixed_point",
            "model": {"matrix": [["0"]], "bias": ["1/2"]}, "point": ["2/4"]}


def theorem_module():
    return {"schema": 1, "kind": "theorem_module", "definitions": {}, "theorems": [
        {"name": "fixed", "statement": fixed(), "by": {"rule": "matrix.fixed_point"}},
        {"name": "joint", "statement": {"kind": "all", "of": ["fixed"]},
         "by": {"rule": "logic.and_intro", "premises": ["fixed"]}}]}


class Extension(registry.VerifierSpec):
    name = "test.multi_extension"
    kinds = {"extension_one", "extension_two"}
    semantics = "test_exact_equality"
    assurance_level = "CERTIFICATE_CHECKED"

    def read_spec(self, spec):
        return {**spec, "value": str(engine.rational(spec["value"]))}

    def verify(self, spec):
        return {"status": "PASS", "certificate": {
            "verdict": "PASS", "value": spec["value"], "assurance": self.assurance_level}}

    def check_certificate(self, spec, certificate):
        return certificate == self.verify(spec)["certificate"]


class BuiltinRoutesTests(unittest.TestCase):
    def test_public_entries_use_installed_adapter_and_never_legacy_lookup(self):
        verifier = registry.get_verifier("affine_fixed_point")
        with patch.object(engine.RuleRegistry, "rule", side_effect=AssertionError("legacy route")), \
                patch.object(verifier, "verify", wraps=verifier.verify) as generate, \
                patch.object(verifier, "check_certificate", wraps=verifier.check_certificate) as check:
            result = engine.verify(fixed())
            self.assertEqual(result["status"], "PASS", result)
            self.assertEqual(generate.call_count, 1)
            self.assertEqual(check.call_count, 1)
            with patch.object(verifier, "verify", side_effect=AssertionError("replay searched")):
                self.assertTrue(engine.check_certificate(fixed(), result["certificate"]))
            rows = engine.rules()
        self.assertEqual(check.call_count, 2)
        expected = {rule.name for rule in engine.REGISTRY.by_name.values()} | {"logic.and_intro"}
        self.assertEqual({row["name"] for row in rows if row["route"] == "builtin"}, expected)
        self.assertEqual(len({kind for row in rows for kind in row["statement_kinds"]}),
                         sum(len(row["statement_kinds"]) for row in rows))
        for row in rows:
            for kind in row["statement_kinds"]:
                self.assertEqual(registry.get_verifier(kind).name, row["name"])

    def test_missing_builtin_route_does_not_fall_back(self):
        with patch.dict(registry._REGISTRY):
            del registry._REGISTRY["affine_fixed_point"]
            result = engine.verify(fixed())
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertIn("Unsupported verifier kind", result["reason"])

    def test_same_builtin_instance_owns_both_lean_kinds(self):
        scalar = registry.get_verifier("lean_obligation")
        self.assertIs(scalar, registry.get_verifier("lean_vector_obligation"))
        self.assertIsInstance(scalar, BuiltinVerifier)
        self.assertEqual(scalar.name, "lean.rational_relation")

    def test_builtin_conjunction_generation_and_replay_use_the_registered_route(self):
        verifier = registry.get_verifier("all")
        with patch.object(verifier, "compose", wraps=verifier.compose) as compose, \
                patch.object(verifier, "replay_composition", wraps=verifier.replay_composition) as replay:
            result = engine.verify(theorem_module())
            self.assertEqual(result["status"], "PASS", result)
            self.assertTrue(engine.check_certificate(theorem_module(), result["certificate"]))
        self.assertEqual(replay.call_count, 2)
        self.assertEqual(compose.call_count, 3)
        bad = copy.deepcopy(result["certificate"])
        bad["proof"]["theorems"]["joint"]["verdict"] = "FAIL"
        self.assertFalse(engine.check_certificate(theorem_module(), bad))

    def test_conjunction_has_no_standalone_axiom_route(self):
        result = engine.verify({"kind": "all", "of": ["unproved"]})
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("checked theorem premises", result["reason"])

    def test_builtin_routes_cannot_be_unregistered_or_shadowed(self):
        for verifier in registry.registered_verifiers():
            if not registry.is_builtin(verifier):
                continue
            for kind in verifier.kinds:
                with self.subTest(kind=kind):
                    with self.assertRaisesRegex(ValueError, "Cannot unregister"):
                        registry.unregister_verifier(kind)
                    shadow = type("Shadow", (Extension,), {"name": "shadow.route", "kinds": {kind}})
                    with self.assertRaisesRegex(ValueError, "shadows built-in route"):
                        registry.register_verifier(shadow)
            shadow = type("Shadow", (Extension,), {"name": verifier.name, "kinds": {"fresh_route"}})
            with self.assertRaisesRegex(ValueError, "shadows built-in rule"):
                registry.register_verifier(shadow)
        self.assertNotIn("fresh_route", registry.registered_kinds())

    def test_compatibility_rule_registry_retains_local_lookup_without_installation(self):
        rule = engine.ProofRule("custom.rule", ("custom_kind",), "rds_scalar_verify",
                                support_files=("rds_unit_disk_voronoi_core.py",))
        local = engine.RuleRegistry((rule,))
        self.assertIs(local.rule({"kind": "custom_kind"}, "custom.rule"), rule)
        with self.assertRaises(ValueError):
            registry.get_verifier("custom_kind")
        with self.assertRaises(ValueError):
            local.rule({"kind": "custom_kind"}, "wrong.rule")
        with patch.object(engine, "REGISTRY", local):
            row = next(row for row in engine.rules() if row["name"] == rule.name)
            self.assertEqual(row["support_files"], list(rule.support_files))
            self.assertEqual(row["route"], "uninstalled")
            self.assertEqual(engine.verify({"kind": "custom_kind"})["status"], "UNKNOWN")
            self.assertEqual(engine.verify(fixed())["status"], "PASS")

    def test_budget_and_normalizer_are_forwarded_to_original_builtin(self):
        spec = json.loads((ROOT / "examples/formal/affine_fixed_point_synthesis.json").read_text())
        verifier = registry.get_verifier(affine.KIND)
        with patch.object(verifier, "verify", wraps=verifier.verify) as generate, \
                patch.object(dynamics, "verify", side_effect=AssertionError("budget bypass")):
            result = engine.verify(spec, max_work_units=0)
        self.assertEqual(result["status"], "UNKNOWN", result)
        generate.assert_called_once_with(spec, max_work_units=0)
        lean = registry.get_verifier("lean_obligation")
        declaration = {"schema": 1, "kind": "lean_obligation", "relation": "eq",
                       "left": "2/4", "right": "1/2"}
        with patch.object(lean, "canonical_statement", wraps=lean.canonical_statement) as normalize:
            result = engine.optimize_proof_plan({"version": 1, "artifacts": [], "steps": [
                {"name": "one", "statement": declaration, "required_assurance": "LEAN_KERNEL_CHECKED"},
                {"name": "two", "statement": {**declaration, "left": "1/2"},
                 "required_assurance": "LEAN_KERNEL_CHECKED"}]})
        self.assertEqual(result["status"], "READY", result)
        self.assertEqual(result["optimization"]["reused_steps"], 1)
        self.assertEqual(normalize.call_count, 2)

    def test_plan_generation_and_replay_use_the_installed_route(self):
        declaration = {"version": 1, "artifacts": [], "steps": [
            {"name": "fixed", "statement": fixed(), "required_assurance": "CERTIFICATE_CHECKED"}]}
        verifier = registry.get_verifier("affine_fixed_point")
        with patch.object(verifier, "verify", wraps=verifier.verify) as generate, \
                patch.object(engine.RuleRegistry, "rule", side_effect=AssertionError("legacy route")):
            result = engine.execute_proof_plan(declaration)
            self.assertEqual(result["status"], "PASS", result)
            generate.assert_called_once()
            with patch.object(verifier, "verify", side_effect=AssertionError("replay searched")):
                self.assertTrue(engine.replay_proof_plan(declaration, result["certificate"]))


class SourceIdentityTests(unittest.TestCase):
    def test_domain_adapter_registry_and_support_sources_invalidate_old_evidence(self):
        result = engine.verify(fixed())
        identity = engine.verifier_id()
        read = Path.read_bytes
        for filename in ("rds_verify.py", "rds_verify_registry.py", "rds_builtin_verifiers.py",
                         "rds_dynamics_verify.py", "rds_lean_verify.py", "rds_unit_disk_voronoi_core.py",
                         "rds_core.py"):
            with self.subTest(source=filename), patch.object(
                    Path, "read_bytes", lambda path: b"changed source" if path.name == filename else read(path)):
                self.assertNotEqual(engine.verifier_id(), identity)
                self.assertFalse(engine.check_certificate(fixed(), result["certificate"]))
                checked = engine.checked_result(fixed(), result["certificate"])
                self.assertEqual(checked["status"], "UNKNOWN")
                self.assertIn("does not bind", checked["reason"])

    def test_effective_routing_and_implementation_mapping_are_identity_bound(self):
        original = engine.verifier_id()
        with patch.dict(registry._REGISTRY, affine_fixed_point=registry.get_verifier("affine_contraction")):
            self.assertNotEqual(engine.verifier_id(), original)
        verifier = registry.get_verifier("affine_fixed_point")
        with patch.object(verifier, "rule", replace(verifier.rule, module="rds_matrix_verify")):
            self.assertNotEqual(engine.verifier_id(), original)
        with patch.object(verifier, "rule", replace(verifier.rule, support_files=("rds_operators.py",))):
            self.assertNotEqual(engine.verifier_id(), original)

    def test_historical_identity_fails_without_rewriting_original_certificate(self):
        result = engine.verify(fixed())
        historical = copy.deepcopy(result["certificate"])
        historical["verifier_sha256"] = "0" * 64
        original = copy.deepcopy(historical)
        self.assertFalse(engine.check_certificate(fixed(), historical))
        self.assertEqual(engine.checked_result(fixed(), historical)["status"], "UNKNOWN")
        self.assertEqual(historical, original)


class ExtensionBoundaryTests(unittest.TestCase):
    def setUp(self):
        registry.register_verifier(Extension)
        self.addCleanup(registry.unregister_verifier, "extension_one")

    def test_extension_multikind_public_roundtrip_and_rules(self):
        self.assertIs(registry.get_verifier("extension_one"), registry.get_verifier("extension_two"))
        for kind in sorted(Extension.kinds):
            spec = {"kind": kind, "value": "2/4"}
            result = engine.verify(spec)
            self.assertEqual(result["status"], "PASS", result)
            self.assertTrue(engine.check_certificate(spec, result["certificate"]))
        row = next(row for row in engine.rules() if row["name"] == Extension.name)
        self.assertEqual(row["route"], "registered")

    def test_reserved_assurance_rejected_even_after_mutating_extension(self):
        verifier = registry.get_verifier("extension_one")
        for assurance in sorted(registry._RESERVED_ASSURANCES):
            with self.subTest(assurance=assurance), patch.object(verifier, "assurance_level", assurance):
                result = engine.verify({"kind": "extension_one", "value": "1"})
                self.assertEqual(result["status"], "UNKNOWN", result)
                self.assertEqual(result["assurance"], "NONE")

    def test_fake_builtin_marker_cannot_grant_reserved_assurance(self):
        verifier = registry.get_verifier("extension_one")
        with patch.object(verifier, "builtin", True, create=True), \
                patch.object(verifier, "assurance_level", "LEAN_KERNEL_CHECKED"):
            self.assertFalse(registry.is_builtin(verifier))
            self.assertEqual(engine.verify({"kind": "extension_two", "value": "1"})["status"], "UNKNOWN")

    def test_checker_cannot_relabel_evidence_after_assurance_guard(self):
        verifier = registry.get_verifier("extension_one")
        spec = {"kind": "extension_one", "value": "1"}
        result = engine.verify(spec)
        original = copy.deepcopy(result["certificate"])

        def relabel(_spec, certificate):
            certificate["assurance"] = "LEAN_KERNEL_CHECKED"
            return True

        with patch.object(verifier, "check_certificate", side_effect=relabel):
            replay = engine.checked_result(spec, result["certificate"])
            self.assertEqual(replay["status"], "UNKNOWN", replay)
            self.assertEqual(result["certificate"], original)
            self.assertEqual(engine.verify(spec)["status"], "UNKNOWN")

    def test_constructor_cannot_upgrade_extension_assurance(self):
        class ConstructorUpgrade(Extension):
            name = "test.constructor_upgrade"
            kinds = {"constructor_upgrade"}

            def __init__(self):
                self.assurance_level = "LEAN_KERNEL_CHECKED"

        with self.assertRaisesRegex(ValueError, "reserved"):
            registry.register_verifier(ConstructorUpgrade)
        self.assertNotIn("constructor_upgrade", registry.registered_kinds())


class ImportOrderTests(unittest.TestCase):
    def isolated(self, body):
        run = subprocess.run([sys.executable, "-I", "-S", "-B", "-c",
                              "import sys; sys.path.insert(0, sys.argv[1]);\n" + body, str(SCRIPTS)],
                             capture_output=True, text=True, timeout=20)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)

    def test_preimport_route_name_all_and_reserved_assurance_collisions_fail_closed(self):
        for name, kinds, assurance in (
                ("early.shadow", {"lean_obligation"}, "CERTIFICATE_CHECKED"),
                ("lean.rational_relation", {"early_kind"}, "CERTIFICATE_CHECKED"),
                ("early.shadow", {"all"}, "CERTIFICATE_CHECKED"),
                ("logic.and_intro", {"early_kind"}, "CERTIFICATE_CHECKED"),
                ("early.shadow", {"early_kind"}, engine.AFFINE_CHAIN_ASSURANCE),
                ("early.shadow", {"early_kind"}, engine.BOUNDED_PROOF_ASSURANCE),
                ("early.shadow", {"early_kind"}, "LEAN_KERNEL_CHECKED")):
            with self.subTest(name=name, kinds=kinds, assurance=assurance):
                self.isolated(f'''
import rds_verify_registry as r
class Early(r.VerifierSpec):
    name = {name!r}
    kinds = {kinds!r}
    semantics = "early"
    assurance_level = {assurance!r}
    def read_spec(self, spec): return spec
    def verify(self, spec): return {{"status": "PASS"}}
    def check_certificate(self, spec, certificate): return True
try:
    r.register_verifier(Early)
    import rds_verify
except ValueError:
    assert not r._BUILTINS, "partial builtin installation"
else:
    raise AssertionError("builtin collision admitted")
''')

    def test_benign_preimport_extension_coexists_with_builtin_routes(self):
        self.isolated('''
import rds_verify_registry as r
assert "rds_verify" not in sys.modules
class Early(r.VerifierSpec):
    name = "early.benign"
    kinds = {"early_kind"}
    semantics = "early"
    assurance_level = "CERTIFICATE_CHECKED"
    def read_spec(self, spec): return spec
    def verify(self, spec): return {"status": "UNKNOWN"}
    def check_certificate(self, spec, certificate): return False
r.register_verifier(Early)
import rds_verify as e
assert r.get_verifier("early_kind").name == Early.name
assert r.is_builtin(r.get_verifier("all"))
assert e.verify({"kind": "early_kind"})["status"] == "UNKNOWN"
''')


if __name__ == "__main__":
    unittest.main()
