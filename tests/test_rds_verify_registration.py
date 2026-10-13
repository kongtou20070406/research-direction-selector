import ast
import builtins
import copy
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_builtin_verifiers as builtin
import rds_dynamics_verify as dynamics
import rds_lean_verify as lean
import rds_verify as engine
import rds_verify_registry as registry


FIXED = {"schema": 1, "kind": "affine_fixed_point",
         "model": {"matrix": [["0"]], "bias": ["1/2"]}, "point": ["2/4"]}
RATIONAL = {"schema": 1, "kind": "lean_obligation", "relation": "eq",
            "left": "2/4", "right": "1/2"}


class ExplicitRegistrationTests(unittest.TestCase):
    def test_every_declared_builtin_uses_an_explicit_source_import(self):
        tree = ast.parse((ROOT / "scripts/rds_builtin_verifiers.py").read_text())
        source_imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import)
                          for alias in node.names}
        with patch.object(importlib, "import_module", side_effect=AssertionError("dynamic loader")):
            for rule in engine.REGISTRY.by_name.values():
                with self.subTest(rule=rule.name):
                    self.assertIn(rule.module, source_imports)
                    self.assertIs(builtin.builtin_module(rule.module), sys.modules[rule.module])
                    self.assertTrue(registry.is_builtin(registry.get_verifier(rule.kinds[0])))
        tree = ast.parse((ROOT / "scripts/rds_verify.py").read_text())
        self.assertFalse(any(isinstance(node, ast.Attribute) and node.attr == "import_module"
                             for node in ast.walk(tree)))

    def test_exact_fixed_point_roundtrip_replay_and_tamper_without_dynamic_loading(self):
        with patch.object(importlib, "import_module", side_effect=AssertionError("dynamic loader")):
            result = engine.verify(FIXED)
            self.assertEqual(result["status"], "PASS", result)
            with patch.object(dynamics, "verify", side_effect=AssertionError("replay searched")):
                self.assertTrue(engine.check_certificate(FIXED, result["certificate"]))
            changed = copy.deepcopy(FIXED)
            changed["point"] = ["0"]
            self.assertFalse(engine.check_certificate(changed, result["certificate"]))
            result["certificate"]["verdict"] = "FAIL"
            self.assertFalse(engine.check_certificate(FIXED, result["certificate"]))

    def test_unknown_module_never_imports_configuration_or_falls_back(self):
        with patch.object(importlib, "import_module", side_effect=AssertionError("dynamic loader")):
            for name in ("os", "os.system", "custom_adapter", "rds_verify"):
                rule = engine.ProofRule("uninstalled", ("fresh_kind",), name)
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unavailable builtin"):
                    rule.generate({"kind": "fresh_kind"})
        verifier = registry.get_verifier("affine_fixed_point")
        from dataclasses import replace
        with patch.object(verifier, "rule", replace(verifier.rule, module="os")):
            result = engine.verify(FIXED)
        self.assertEqual(result["status"], "UNKNOWN", result)
        self.assertIn("Unavailable builtin", result["reason"])

    def test_missing_adapter_reports_original_import_reason(self):
        original = builtins.__import__

        def missing(name, *args, **kwargs):
            if name == "rds_dynamics_verify":
                raise ModuleNotFoundError("original missing-adapter reason")
            return original(name, *args, **kwargs)

        with patch.object(builtins, "__import__", side_effect=missing):
            result = engine.verify(FIXED)
        self.assertEqual(result["status"], "UNKNOWN", result)
        self.assertIn("original missing-adapter reason", result["reason"])

    def test_budget_normalizer_and_checker_are_original_module_methods(self):
        rule = engine.REGISTRY.by_name[engine.AFFINE_CHAIN_RULE]
        adapter = builtin.builtin_module(rule.module)
        with patch.object(adapter, "verify", return_value={"status": "UNKNOWN"}) as verify:
            rule.generate({"synthetic": True}, max_work_units=0)
        verify.assert_called_once_with({"synthetic": True}, max_work_units=0)
        normalized = engine.REGISTRY.by_name["lean.rational_relation"].canonical_statement(RATIONAL)
        self.assertEqual(normalized, lean.canonical_statement(RATIONAL))

    def test_rational_tactic_and_native_no_fallback_keep_exact_adapter_semantics(self):
        formal = engine.LeanFormalEngine()
        with patch.object(importlib, "import_module", side_effect=AssertionError("dynamic loader")), \
                patch.object(lean, "verify_rational", wraps=lean.verify_rational) as rational:
            result = formal.verify(RATIONAL, tactics=("rational",))
        self.assertEqual(result["status"], "PASS", result)
        rational.assert_called_once_with(RATIONAL)
        with patch.object(lean, "verify", return_value={"status": "UNKNOWN", "reason": "native missing"}) as native:
            result = formal.verify(RATIONAL, tactics=("lean4",))
        native.assert_called_once_with(RATIONAL, allow_fallback=False)
        self.assertEqual(result["status"], "UNKNOWN", result)
        self.assertEqual(result["tactics"][0]["reason"], "native missing")

    def test_public_plan_costs_budget_and_replay_without_dynamic_loading(self):
        source = ROOT / "examples/formal/affine_fixed_point_synthesis.json"
        spec = json.loads(source.read_text())
        plan = {"version": 1, "artifacts": [], "steps": [
            {"name": "fixed", "statement": FIXED, "required_assurance": "CERTIFICATE_CHECKED"}]}
        with patch.object(importlib, "import_module", side_effect=AssertionError("dynamic loader")):
            result = engine.plan(spec, max_work_units=0)
            self.assertEqual(result["status"], "UNKNOWN", result)
            self.assertEqual(result["reason"], "Estimated proof-plan work exceeds max_work_units")
            result = engine.execute_proof_plan(plan)
            self.assertEqual(result["status"], "PASS", result)
            with patch.object(dynamics, "verify", side_effect=AssertionError("replay searched")):
                self.assertTrue(engine.replay_proof_plan(plan, result["certificate"]))

    def test_new_shared_contract_source_is_bound_to_certificate_identity(self):
        result = engine.verify(FIXED)
        identity = engine.verifier_id()
        original = Path.read_bytes
        with patch.object(Path, "read_bytes", lambda path: b"changed contract" if
                          path.name == "rds_plugin_registry.py" else original(path)):
            self.assertNotEqual(engine.verifier_id(), identity)
            self.assertFalse(engine.check_certificate(FIXED, result["certificate"]))

    def test_extension_assurance_preserved_and_code_change_invalidates_certificate(self):
        import rds_verify_registry as registry
        snapshot = (dict(registry._REGISTRY), dict(registry._BY_NAME))
        self.addCleanup(lambda: (registry._REGISTRY.clear(), registry._REGISTRY.update(snapshot[0]),
                                 registry._BY_NAME.clear(), registry._BY_NAME.update(snapshot[1])))

        class CustomV1(registry.VerifierSpec):
            name = "ext.rt"
            kinds = {"ext_rt_kind"}
            semantics = "custom_rt"
            assurance_level = "RT_ONLY"
            def read_spec(self, s): return dict(s)
            def verify(self, s):
                return {"status": "PASS", "certificate": {
                    "verdict": "PASS", "assurance": "RT_ONLY", "semantics": "custom_rt"}}
            def check_certificate(self, s, c):
                return c.get("verdict") == "PASS"

        registry.register_verifier(CustomV1)
        out = engine.verify({"kind": "ext_rt_kind"})
        self.assertEqual(out["status"], "PASS")
        self.assertEqual(out["assurance"], "RT_ONLY")
        registry.unregister_verifier("ext_rt_kind")

        class CustomV2(registry.VerifierSpec):
            name = "ext.rt"
            kinds = {"ext_rt_kind"}
            semantics = "custom_rt"
            assurance_level = "RT_ONLY"
            def read_spec(self, s): return dict(s)
            def verify(self, s):
                return {"status": "PASS", "certificate": {
                    "verdict": "PASS", "assurance": "RT_ONLY", "semantics": "custom_rt"}}
            def check_certificate(self, s, c):
                return False

        registry.register_verifier(CustomV2)
        self.assertFalse(engine.check_certificate({"kind": "ext_rt_kind"}, out["certificate"]))


if __name__ == "__main__":
    unittest.main()

