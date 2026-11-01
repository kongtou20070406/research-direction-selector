"""Declarative theorem elaboration and independent certificate replay."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_verify as engine
import rds_dynamics_verify as dynamics
import rds_verify_registry as registry


def module():
    return json.loads((ROOT / "examples/formal/theorem_module.json").read_text(encoding="utf-8"))


class DeclarativeTests(unittest.TestCase):
    def test_named_definitions_and_conjunction_have_checked_proofs(self):
        spec = module()
        answer = engine.verify(spec)
        self.assertEqual(answer["status"], "PASS")
        self.assertEqual(answer["theorems"]["joint"]["status"], "PASS")
        with patch.object(dynamics, "verify", side_effect=AssertionError("No proof search in replay")):
            self.assertTrue(engine.check_certificate(spec, answer["certificate"]))

    def test_forward_theorem_and_definition_references_are_elaborated(self):
        spec = module()
        spec["theorems"].reverse()
        spec["definitions"]["alias"] = {"$ref": "map"}
        spec["theorems"][-1]["statement"]["model"] = {"$ref": "alias"}
        self.assertEqual(engine.verify(spec)["status"], "PASS")

    def test_failure_is_propagated_through_exact_conjunction(self):
        spec = module()
        spec["theorems"][1]["statement"]["point"] = ["0", "0"]
        answer = engine.verify(spec)
        self.assertEqual(answer["status"], "FAIL")
        self.assertEqual(answer["theorems"]["joint"]["status"], "FAIL")
        self.assertTrue(engine.check_certificate(spec, answer["certificate"]))

    def test_unknown_obligation_cannot_be_hidden_by_a_proved_theorem(self):
        spec = module()
        spec["theorems"][1]["statement"]["kind"] = "training_converges"
        result = engine.verify(spec)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertNotIn("certificate", result)

    def test_cycles_unbound_names_axioms_and_duplicate_theorems_are_rejected(self):
        mutations = [
            lambda s: s["definitions"].update(loop={"$ref": "loop"}),
            lambda s: s["definitions"].update(missing={"$ref": "absent"}),
            lambda s: s["theorems"].append(copy.deepcopy(s["theorems"][0])),
            lambda s: s["theorems"][2]["statement"].update(of=["joint"]),
            lambda s: s["theorems"][2]["by"].update(premises=["joint"]),
            lambda s: s["theorems"][0]["by"].update(axiom=True),
            lambda s: s["theorems"][0]["by"].update(rule="lean4"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                spec = module()
                mutate(spec)
                self.assertEqual(engine.verify(spec)["status"], "UNKNOWN")

    def test_extra_unused_cyclic_definition_is_rejected(self):
        spec = module()
        spec["definitions"]["a"] = {"$ref": "b"}
        spec["definitions"]["b"] = {"$ref": "a"}
        self.assertEqual(engine.verify(spec)["status"], "UNKNOWN")

    def test_tampered_leaf_and_composition_are_rejected(self):
        spec = module()
        original = engine.verify(spec)["certificate"]
        mutations = [
            lambda c: c.update(verdict="FAIL"),
            lambda c: c.update(verifier_sha256="0" * 64),
            lambda c: c["proof"]["theorems"].pop("stable"),
            lambda c: c["proof"]["theorems"]["stable"]["certificate"].update(induced_norm="0"),
            lambda c: c["proof"]["theorems"]["joint"].update(premises=["fixed"]),
            lambda c: c["proof"]["theorems"]["joint"].update(verdict="FAIL"),
            lambda c: c["proof"]["theorems"]["stable"].update(rule="matrix.fixed_point"),
        ]
        for mutate in mutations:
            certificate = copy.deepcopy(original)
            mutate(certificate)
            self.assertFalse(engine.check_certificate(spec, certificate))

    def test_old_proof_cannot_certify_changed_model_or_claim(self):
        spec = module()
        certificate = engine.verify(spec)["certificate"]
        spec["definitions"]["map"]["bias"][0] = "1"
        # Even rebinding a public hash cannot replace the actual domain proof.
        certificate["spec_sha256"] = engine.digest(spec)
        self.assertFalse(engine.check_certificate(spec, certificate))

    def test_scalar_adapter_keeps_original_singularities(self):
        spec = {"schema": 1, "kind": "scalar_threshold",
                "source": "def control(x): return x/(1+x)\ndef treatment(x): return 2*x/(1+x)\n",
                "formal": {"domain": ["0", "2"], "threshold": "1"}}
        answer = engine.verify(spec)
        self.assertEqual(answer["status"], "PASS")
        self.assertTrue(engine.check_certificate(spec, answer["certificate"]))
        spec["source"] = "def control(x): return 0\ndef treatment(x): return x/x\n"
        self.assertEqual(engine.verify(spec)["status"], "UNKNOWN")

    def test_cli_standalone_verify_replay_and_derived_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            path, proof = Path(tmp) / "spec.json", Path(tmp) / "proof.json"
            path.write_text(json.dumps(module()), encoding="utf-8")
            prefix = [sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"), "--root", tmp, "formal"]
            run = subprocess.run(prefix + ["verify", "--spec", str(path), "--output", str(proof)], capture_output=True)
            self.assertEqual(run.returncode, 0, run.stderr.decode())
            artifact = json.loads(proof.read_text(encoding="utf-8"))
            artifact["status"] = "FAIL"  # top-level user metadata has no authority
            proof.write_text(json.dumps(artifact), encoding="utf-8")
            replay = subprocess.run(prefix + ["check", "--spec", str(path), "--certificate", str(proof)], capture_output=True)
            self.assertEqual(replay.returncode, 0, replay.stderr.decode())
            self.assertEqual(json.loads(replay.stdout)["status"], "PASS")
            artifact["certificate"]["verdict"] = "FAIL"
            proof.write_text(json.dumps(artifact), encoding="utf-8")
            replay = subprocess.run(prefix + ["check", "--spec", str(path), "--certificate", str(proof)], capture_output=True)
            self.assertEqual(replay.returncode, 2)
            self.assertFalse((Path(tmp) / ".rds/state.sqlite3").exists())

    def test_declared_rule_registry_is_explicit_and_cannot_load_commands(self):
        known = {r["name"] for r in engine.rules()}
        self.assertIn("tensor.exact_identity", known)
        self.assertIn("network.interval_margin", known)
        spec = module()
        spec["theorems"][0]["by"]["rule"] = "os.system"
        self.assertEqual(engine.verify(spec)["status"], "UNKNOWN")


class RegistryBridgeTests(unittest.TestCase):
    def setUp(self):
        snapshot = (dict(registry._REGISTRY), dict(registry._BY_NAME))
        self.addCleanup(self._restore, snapshot)
        self.calls = []
        calls = self.calls

        class RationalSign(registry.VerifierSpec):
            name = "test.rational_sign"
            kinds = {"registered_sign"}
            semantics = "exact_rational_sign"
            assurance_level = "CERTIFICATE_CHECKED"

            def read_spec(self, spec):
                calls.append("read")
                return {**spec, "value": str(engine.rational(spec["value"]))}

            def verify(self, spec):
                calls.append("verify")
                verdict = "PASS" if engine.rational(spec["value"]) >= 0 else "FAIL"
                return {"status": verdict, "certificate": {
                    "value": spec["value"], "verdict": verdict,
                    "assurance": self.assurance_level, "semantics": self.semantics}}

            def check_certificate(self, spec, certificate):
                calls.append("check")
                verdict = "PASS" if engine.rational(spec["value"]) >= 0 else "FAIL"
                return certificate == {"value": spec["value"], "verdict": verdict,
                                       "assurance": self.assurance_level,
                                       "semantics": self.semantics}

        registry.register_verifier(RationalSign)
        self.verifier = registry.get_verifier("registered_sign")
        self.spec = {"kind": "registered_sign", "value": "2/4"}

    def _restore(self, snapshot):
        registry._REGISTRY.clear()
        registry._REGISTRY.update(snapshot[0])
        registry._BY_NAME.clear()
        registry._BY_NAME.update(snapshot[1])

    def test_public_verify_and_replay_use_registered_reader_and_checker(self):
        answer = engine.verify(self.spec)
        self.assertEqual(answer["status"], "PASS")
        self.assertEqual(answer["semantics"], self.verifier.semantics)
        self.assertEqual(answer["certificate"]["proof"]["rule"], self.verifier.name)
        self.assertEqual(self.calls, ["read", "verify", "read", "check"])
        with patch.object(self.verifier, "verify", side_effect=AssertionError("No search in replay")):
            self.assertTrue(engine.check_certificate(self.spec, answer["certificate"]))
            self.assertEqual(engine.checked_result(self.spec, answer["certificate"])["status"], "PASS")

    def test_registered_failure_is_checked_and_tampering_is_rejected(self):
        self.spec["value"] = "-1"
        answer = engine.verify(self.spec)
        self.assertEqual(answer["status"], "FAIL")
        self.assertTrue(engine.check_certificate(self.spec, answer["certificate"]))
        answer["certificate"]["proof"]["certificate"]["value"] = "1"
        self.assertFalse(engine.check_certificate(self.spec, answer["certificate"]))

    def test_registered_checker_cannot_claim_builtin_assurance(self):
        answer = engine.verify(self.spec)
        answer["certificate"]["proof"]["certificate"]["assurance"] = "LEAN_KERNEL_CHECKED"
        self.assertFalse(engine.check_certificate(self.spec, answer["certificate"]))

    def test_registered_kind_takes_priority_over_legacy_rule(self):
        legacy = engine.RuleRegistry((engine.ProofRule(
            "legacy.sign", ("registered_sign",), "rds_scalar_verify"),))
        with patch.object(engine, "REGISTRY", legacy), patch.object(
                engine.ProofRule, "generate", side_effect=AssertionError("Legacy search")), patch.object(
                engine.ProofRule, "check", side_effect=AssertionError("Legacy replay")):
            self.assertEqual(engine.verify(self.spec)["status"], "PASS")

    def test_builtin_lean_routes_names_and_assurance_cannot_be_shadowed(self):
        class LeanShadow(registry.VerifierSpec):
            name = "custom.lean"
            kinds = {"lean_obligation"}
            semantics = "fake_lean"
            assurance_level = "CERTIFICATE_CHECKED"

            def read_spec(self, spec): return spec
            def verify(self, spec): return {"status": "PASS"}
            def check_certificate(self, spec, certificate): return True

        with self.assertRaisesRegex(ValueError, "shadows built-in route"):
            registry.register_verifier(LeanShadow)
        conjunction_shadow = type("ConjunctionShadow", (registry.VerifierSpec,), {
            "name": "custom.conjunction", "kinds": {"all"}, "semantics": "fake",
            "assurance_level": "CERTIFICATE_CHECKED", "read_spec": lambda self, spec: spec,
            "verify": lambda self, spec: {"status": "PASS"},
            "check_certificate": lambda self, spec, cert: True})
        with self.assertRaisesRegex(ValueError, "shadows built-in route"):
            registry.register_verifier(conjunction_shadow)
        for assurance in ("LEAN_KERNEL_CHECKED", engine.BOUNDED_PROOF_ASSURANCE,
                          engine.AFFINE_CHAIN_ASSURANCE):
            FakeAssurance = type("FakeAssurance", (registry.VerifierSpec,), {
                "name": "fake." + assurance.lower(), "kinds": {"fake_" + assurance.lower()},
                "semantics": "fake", "assurance_level": assurance,
                "read_spec": lambda self, spec: spec,
                "verify": lambda self, spec: {"status": "PASS"},
                "check_certificate": lambda self, spec, cert: True})
            with self.assertRaisesRegex(ValueError, "reserved assurance"):
                registry.register_verifier(FakeAssurance)
        collision = type("RuleNameShadow", (registry.VerifierSpec,), {
            "name": "lean.rational_relation", "kinds": {"harmless_kind"},
            "semantics": "fake", "assurance_level": "CERTIFICATE_CHECKED",
            "read_spec": lambda self, spec: spec,
            "verify": lambda self, spec: {"status": "PASS"},
            "check_certificate": lambda self, spec, cert: True})
        with self.assertRaisesRegex(ValueError, "shadows built-in rule"):
            registry.register_verifier(collision)
        conjunction_name_shadow = type("ConjunctionNameShadow", (registry.VerifierSpec,), {
            "name": "logic.and_intro", "kinds": {"harmless_kind"}, "semantics": "fake",
            "assurance_level": "CERTIFICATE_CHECKED", "read_spec": lambda self, spec: spec,
            "verify": lambda self, spec: {"status": "PASS"},
            "check_certificate": lambda self, spec, cert: True})
        with self.assertRaisesRegex(ValueError, "shadows built-in rule"):
            registry.register_verifier(conjunction_name_shadow)

    def test_rules_reports_effective_registered_routes(self):
        rows = engine.rules()
        lean = next(row for row in rows if "lean_obligation" in row["statement_kinds"])
        self.assertEqual(lean["route"], "builtin")
        registered = next(row for row in rows if row["name"] == self.verifier.name)
        self.assertEqual(registered["route"], "registered")
        self.assertEqual(registered["assurance_level"], self.verifier.assurance_level)
        self.assertTrue(registered["implementation"])

    def test_registered_unknown_and_reader_errors_do_not_fall_back(self):
        with patch.object(self.verifier, "verify", return_value={"status": "UNKNOWN", "reason": "no evidence"}):
            answer = engine.verify(self.spec)
            self.assertEqual(answer["status"], "UNKNOWN")
            self.assertEqual(answer["reason"], "no evidence")
            self.assertNotIn("certificate", answer)
        with patch.object(self.verifier, "read_spec", side_effect=ValueError("Invalid registered spec")):
            answer = engine.verify(self.spec)
            self.assertEqual(answer["status"], "UNKNOWN")
            self.assertIn("Invalid registered spec", answer["reason"])

    def test_registered_theorem_leaf_and_requested_rule_are_checked(self):
        spec = {"schema": 1, "kind": "theorem_module", "definitions": {}, "theorems": [
            {"name": "sign", "statement": self.spec, "by": {"rule": self.verifier.name}},
            {"name": "joint", "statement": {"kind": "all", "of": ["sign"]},
             "by": {"rule": "logic.and_intro", "premises": ["sign"]}}]}
        answer = engine.verify(spec)
        self.assertEqual(answer["status"], "PASS")
        self.assertTrue(engine.check_certificate(spec, answer["certificate"]))
        spec["theorems"][0]["by"]["rule"] = "os.system"
        self.assertEqual(engine.verify(spec)["status"], "UNKNOWN")

    def test_registry_declarations_and_implementation_bind_verifier_identity(self):
        answer = engine.verify(self.spec)
        identity = engine.verifier_id()
        read = Path.read_bytes
        source = Path(__file__)
        with patch.object(Path, "read_bytes", lambda path: b"changed implementation" if
                          path.resolve() == source.resolve() else read(path)):
            self.assertNotEqual(engine.verifier_id(), identity)
            self.assertFalse(engine.check_certificate(self.spec, answer["certificate"]))
        self.verifier.version = "2"
        self.assertNotEqual(engine.verifier_id(), identity)
        self.assertFalse(engine.check_certificate(self.spec, answer["certificate"]))

    def test_unregistered_route_and_invalid_candidate_evidence_fail_closed(self):
        answer = engine.verify(self.spec)
        registry.unregister_verifier("registered_sign")
        self.assertEqual(engine.verify(self.spec)["status"], "UNKNOWN")
        self.assertFalse(engine.check_certificate(self.spec, answer["certificate"]))
        registry.register_verifier(type(self.verifier))
        self.verifier = registry.get_verifier("registered_sign")
        with patch.object(self.verifier, "verify", return_value={"status": "PASS"}):
            self.assertEqual(engine.verify(self.spec)["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
