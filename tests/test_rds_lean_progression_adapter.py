"""Replayable bounded-progression leaves inside the theorem-module DAG."""
import copy
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
import rds_lean_verify as lean
import rds_proof_cache as cache_module
import rds_theory_progression as progression
import rds_verify as engine
from rds_verify_types import canonical, digest


def bounded_spec(domain=("0", "1")):
    return {"schema": 1, "kind": progression.PROOF_KIND, "domain": list(domain)}


def theorem_module():
    return json.loads((ROOT / "examples/formal/bounded_progression_module.json").read_text(encoding="utf-8"))


def native_lean_available():
    try:
        lean._executable()
        return True
    except (lean.NoNativeLean, ValueError, OSError):
        return False


class LeanProgressionAdapterTests(unittest.TestCase):
    def test_rule_is_whitelisted_and_binds_all_checker_sources(self):
        rule = engine.REGISTRY.by_name[progression.PROOF_RULE]
        self.assertEqual(rule.kinds, (progression.PROOF_KIND,))
        self.assertEqual(set(rule.support_files), {"rds_operators.py", "rds_lean_verify.py"})
        self.assertIn(progression.PROOF_RULE, {item["name"] for item in engine.rules()})

    def test_nonclosed_domain_is_unknown_without_widening(self):
        result = engine.verify(bounded_spec(("1", "2")))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("not closed", result["reason"])
        self.assertIn("not widened", result["reason"])
        self.assertNotIn("certificate", result)

    def test_unsupported_assumption_or_claim_override_is_not_ignored(self):
        for extra in ({"assumptions": ["assume multiplication is commutative"]},
                      {"claim_id": "universal_multiplication_commutativity"}):
            with self.subTest(extra=extra):
                result = engine.verify({**bounded_spec(("1",)), **extra})
                self.assertEqual(result["status"], "UNKNOWN")
                self.assertNotIn("certificate", result)

    def test_missing_or_unresolved_lean_leaf_propagates_unknown(self):
        with mock.patch.object(engine.LeanFormalEngine, "verify", return_value={
                "status": "UNKNOWN", "assurance": "NONE", "reason": "native Lean unavailable"}):
            result = engine.verify(bounded_spec(("1",)))
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("native Lean unavailable", result["reason"])
        self.assertNotIn("certificate", result)

    def test_identical_pair_obligations_share_generation_and_replay(self):
        statement = bounded_spec()
        generated = []

        class FakeLeanEngine:
            def verify(self, obligation, tactics):
                generated.append(canonical(obligation))
                certificate = lean.verify_rational(obligation)["certificate"]
                return {"status": "PASS", "assurance": "LEAN_KERNEL_CHECKED", "certificate": {
                    "spec_sha256": digest(obligation), "verdict": "PASS",
                    "proof": {"rule": "lean.rational_relation", "certificate": certificate}}}

        with mock.patch.object(engine, "LeanFormalEngine", FakeLeanEngine):
            result = progression.verify(statement)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(len(generated), 2)
        self.assertEqual(len(set(generated)), 2)
        self.assertEqual(len(result["certificate"]["pairs"]), 4)
        with mock.patch.object(lean, "check_certificate", wraps=lean.check_certificate) as replay:
            self.assertTrue(progression.check_certificate(statement, result["certificate"]))
        self.assertEqual(replay.call_count, 2)

    @unittest.skipUnless(native_lean_available(), "A native Lean toolchain is required for proof replay")
    def test_named_leaf_composes_with_other_rules_and_is_reused_by_the_dag(self):
        spec = theorem_module()
        with mock.patch.object(progression, "verify", wraps=progression.verify) as generate:
            result = engine.verify(spec)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(generate.call_count, 1)
        self.assertEqual(result["theorems"]["commutativity_and_contraction"]["status"], "PASS")
        self.assertEqual(result["theorems"]["commutativity_and_fixed_point"]["status"], "PASS")
        leaf = result["theorems"]["multiplication_commutes_on_domain"]
        self.assertEqual(leaf["assurance"], progression.PROOF_ASSURANCE)
        self.assertEqual(leaf["semantics"], progression.PROOF_SEMANTICS)
        self.assertEqual(leaf["proof_scope"]["domain"], ["0", "1"])
        self.assertEqual(leaf["scientific_assurance"], "UNKNOWN")
        self.assertEqual(leaf["application_status"], "UNKNOWN")
        self.assertEqual(result["scientific_assurance"], "UNKNOWN")
        self.assertEqual(result["application_status"], "UNKNOWN")
        evidence = result["certificate"]["proof"]["theorems"]["multiplication_commutes_on_domain"]["certificate"]
        self.assertEqual(len(evidence["pairs"]), 4)
        self.assertEqual([item["pair"] for item in evidence["pairs"]],
                         [["0", "0"], ["0", "1"], ["1", "0"], ["1", "1"]])
        self.assertNotIn("egraph", evidence)

        # Rechecking a module replays leaf certificates; it never invokes the generator.
        with mock.patch.object(progression, "verify", side_effect=AssertionError("replay must not search")):
            self.assertTrue(engine.check_certificate(spec, result["certificate"]))

    @unittest.skipUnless(native_lean_available(), "A native Lean toolchain is required to mint certificates")
    def test_foreign_missing_reordered_pair_evidence_and_hashes_are_rejected(self):
        spec = bounded_spec()
        result = engine.verify(spec)
        self.assertEqual(result["status"], "PASS", result)
        original = result["certificate"]
        mutations = (
            lambda proof: proof["pairs"].pop(),
            lambda proof: proof["pairs"].reverse(),
            lambda proof: proof["pairs"][0]["certificate"].update(spec_sha256="f" * 64),
            lambda proof: proof.update(table_sha256="f" * 64),
            lambda proof: proof.update(finite_model={"status": "PASS", "assurance": "LEAN_KERNEL_CHECKED"}),
        )
        with mock.patch.object(lean, "_native_check", side_effect=AssertionError("invalid evidence must fail before Lean")):
            for mutate in mutations:
                certificate = copy.deepcopy(original)
                mutate(certificate["proof"]["certificate"])
                with self.subTest(mutation=mutate):
                    self.assertFalse(engine.check_certificate(spec, certificate))

        changed = theorem_module()
        previous = engine.verify(changed)["certificate"]
        changed["definitions"]["domain"] = ["-1", "1"]
        self.assertFalse(engine.check_certificate(changed, previous))

    @unittest.skipUnless(native_lean_available(), "A native Lean toolchain is required for cache replay")
    def test_proof_cache_replays_hits_and_corrupt_cache_cannot_pass(self):
        spec = bounded_spec(("1",))
        with tempfile.TemporaryDirectory(prefix="rds-progression-cache-") as folder:
            cache = cache_module.ProofCache(Path(folder) / "proofs.sqlite3")
            first = cache.verify(spec)
            self.assertEqual(first["status"], "PASS", first)
            self.assertTrue(first["cache"]["stored"])
            with mock.patch.object(lean, "_native_check", wraps=lean._native_check) as replay_native, \
                    mock.patch.object(progression, "verify",
                                      side_effect=AssertionError("cache hit must replay, not regenerate")):
                hit = cache.verify(spec)
            self.assertEqual(hit["status"], "PASS", hit)
            self.assertTrue(hit["cache"]["hit"])
            self.assertEqual(hit["assurance"], progression.PROOF_ASSURANCE)
            self.assertEqual(hit["semantics"], progression.PROOF_SEMANTICS)
            self.assertGreaterEqual(replay_native.call_count, 1)

            key = digest(spec) + engine.verifier_id()
            db = sqlite3.connect(cache.path)
            try:
                raw = db.execute("SELECT certificate FROM proofs WHERE cache_key=?", (key,)).fetchone()[0]
                forged = json.loads(raw.decode("utf-8"))
                forged["proof"]["certificate"]["pairs"][0]["certificate"]["verdict"] = "FAIL"
                db.execute("UPDATE proofs SET certificate=? WHERE cache_key=?",
                           (canonical(forged).encode("utf-8"), key))
                db.commit()
            finally:
                db.close()

            # The corrupted cache record is rejected. If fresh Lean evidence is unavailable,
            # verification remains UNKNOWN instead of trusting its earlier PASS label.
            with mock.patch.object(engine.LeanFormalEngine, "verify", return_value={
                    "status": "UNKNOWN", "assurance": "NONE", "reason": "native Lean unavailable"}):
                result = cache.verify(spec)
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertFalse(result["cache"]["hit"])
            self.assertNotIn("certificate", result)

    @unittest.skipUnless(native_lean_available(), "A native Lean toolchain is required for CLI proof replay")
    def test_formal_cli_verifies_and_replays_a_cached_bounded_leaf(self):
        with tempfile.TemporaryDirectory(prefix="rds-progression-cli-") as folder:
            root = Path(folder)
            spec_path = root / "spec.json"
            proof_path = root / "proof.json"
            spec_path.write_text(json.dumps(bounded_spec(("1",))), encoding="utf-8")
            prefix = [sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"), "--root", folder, "formal"]
            verify_args = prefix + ["verify", "--spec", str(spec_path), "--output", str(proof_path)]
            first = subprocess.run(verify_args, capture_output=True, text=True)
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            self.assertEqual(json.loads(first.stdout)["status"], "PASS")
            second = subprocess.run(verify_args, capture_output=True, text=True)
            self.assertEqual(second.returncode, 0, second.stderr + second.stdout)
            self.assertTrue(json.loads(second.stdout)["cache"]["hit"])
            replay = subprocess.run(prefix + ["check", "--spec", str(spec_path), "--certificate", str(proof_path)],
                                    capture_output=True, text=True)
            self.assertEqual(replay.returncode, 0, replay.stderr + replay.stdout)
            result = json.loads(replay.stdout)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["semantics"], progression.PROOF_SEMANTICS)
            self.assertEqual(result["scientific_assurance"], "UNKNOWN")
            self.assertEqual(result["application_status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
