import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rds_lean_verify as lean


SPEC = {"schema": 1, "kind": "lean_obligation", "relation": "lt", "left": "1/2", "right": "3/4"}
VECTOR_SPEC = {"schema": 1, "kind": "lean_vector_obligation", "relations": [
    {"relation": "lt", "left": "1/2", "right": "3/4"},
]}
VERSION = "Lean (version 4.33.1, x86_64-w64-windows-gnu, commit abc, Release)\n"
FINGERPRINT = "a" * 64


class LeanAdapterTests(unittest.TestCase):
    def native(self, stdout=lean.AXIOM_AUDIT + "\n", code=0, version=VERSION):
        return mock.patch.multiple(lean, _executable=mock.Mock(return_value=(Path("/native/lean"), FINGERPRINT)),
                                   _run=mock.Mock(side_effect=[(0, version), (code, stdout)]))

    def certificate(self):
        with self.native():
            result = lean.verify(SPEC)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["assurance"], "LEAN_KERNEL_CHECKED")
        return result["certificate"]

    def test_native_success_requires_exact_axiom_audit(self):
        for text in ("", "proof succeeded", "'RDS.obligation' depends on axioms: [sorryAx]",
                     "'RDS.obligation' depends on axioms: [Custom.fake]",
                     "'RDS.obligation' depends on axioms: [propext]",
                     lean.AXIOM_AUDIT + "\nwarning: unrecognized event"):
            with self.subTest(text=text), self.native(stdout=text):
                self.assertEqual(lean.verify(SPEC)["status"], "UNKNOWN")

    def test_native_scalar_and_vector_outer_semantics_match_certificate(self):
        checked = {"lean_version": VERSION.strip(), "lean_executable_sha256": FINGERPRINT,
                   "stdout": lean.AXIOM_AUDIT + "\n"}
        for spec, expected in ((SPEC, lean.SEMANTICS), (VECTOR_SPEC, lean.VECTOR_SEMANTICS)):
            with self.subTest(kind=spec["kind"]), mock.patch.object(
                    lean, "_native_check", return_value=checked):
                result = lean.verify(spec, allow_fallback=False)
            self.assertEqual(result["status"], "PASS")
            self.assertEqual(result["certificate"]["semantics"], expected)
            self.assertEqual(result["semantics"], result["certificate"]["semantics"])

    def test_no_native_lean_without_fallback_is_unknown_without_assurance(self):
        with mock.patch.object(lean, "_native_check", side_effect=lean.NoNativeLean("not installed")), \
                mock.patch.object(lean, "_fallback_certificate", side_effect=AssertionError("No fallback")):
            result = lean.verify(VECTOR_SPEC, allow_fallback=False)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["assurance"], "NONE")
        self.assertEqual(result["backend"], lean.BACKEND)
        self.assertIsNone(result["certificate"])

    def test_only_native_lean_four_version(self):
        for version in ("Lean (version 3.51.1)", "not lean", "Lean (version 4.33.1)\nextra"):
            with self.subTest(version=version), self.native(version=version):
                self.assertEqual(lean.verify(SPEC)["status"], "UNKNOWN")

    def test_exit_failure_is_unknown(self):
        with self.native(code=1):
            self.assertEqual(lean.verify(SPEC)["status"], "UNKNOWN")

    def test_missing_binary_and_elan_shim_are_unknown(self):
        for path in ("", "relative-lean", str(Path.home() / ".elan" / "bin" / "lean.exe")):
            with self.subTest(path=path), mock.patch.dict(os.environ, {"RDS_LEAN_EXECUTABLE": path}):
                self.assertEqual(lean.verify(SPEC)["status"], "UNKNOWN")

    def test_untrusted_source_and_numeric_inputs_are_rejected(self):
        variants = [{**SPEC, "source": "axiom fake : False"}, {**SPEC, "left": True},
                    {**SPEC, "left": 0.5}, {**SPEC, "schema": True},
                    {**SPEC, "left": "1; #eval panic! \"injection\""},
                    {**SPEC, "relation": "lt := by native_decide"},
                    {**SPEC, "left": "1e9999"}, {**SPEC, "left": "9" * 2501}]
        with mock.patch.object(lean, "_native_check") as native:
            for spec in variants:
                with self.subTest(spec=spec):
                    self.assertEqual(lean.verify(spec)["status"], "UNKNOWN")
            native.assert_not_called()

    def test_render_reduced_literal_not_arbitrary_code(self):
        source = lean.render_source({**SPEC, "left": "2/4", "right": "-1/3"})
        self.assertIn("Rat.mk' (1 : Int) 2 (by decide) (by decide)", source)
        self.assertIn("Rat.mk' (-1 : Int) 3 (by decide) (by decide)", source)
        self.assertNotIn("native_decide", source)

    def test_checker_replays_native_kernel_and_does_not_call_verify(self):
        certificate = self.certificate()
        with self.native(), mock.patch.object(lean, "verify", side_effect=AssertionError("must not search")):
            self.assertTrue(lean.check_certificate(SPEC, certificate))
        with self.native(stdout=""):
            self.assertFalse(lean.check_certificate(SPEC, certificate))

    def test_tampering_with_source_spec_binary_version_or_axioms_fails(self):
        certificate = self.certificate()
        for field, value in (("source", "axiom RDS.obligation : False"), ("source_sha256", "b" * 64),
                             ("lean_executable_sha256", "b" * 64), ("lean_version", "Lean 3"),
                             ("axioms", ["sorryAx"]), ("verdict", "FAIL"),
                             ("schema", True), ("version", True)):
            changed = copy.deepcopy(certificate)
            changed[field] = value
            with self.subTest(field=field), self.native():
                self.assertFalse(lean.check_certificate(SPEC, changed))
        with self.native():
            self.assertFalse(lean.check_certificate({**SPEC, "left": "2/4"}, certificate))

    def test_timeout_becomes_unknown(self):
        with mock.patch.object(lean, "_native_check", side_effect=ValueError("Lean check exceeded the 3-second time budget")):
            result = lean.verify(SPEC)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIn("3-second", result["reason"])

    def test_runner_timeout_kills_child(self):
        process = mock.Mock(stdout=mock.Mock(read=mock.Mock(return_value=b"")), returncode=-1)
        process.wait.side_effect = [subprocess.TimeoutExpired(["lean"], 3), -1]
        with mock.patch.object(subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(ValueError, "3-second"):
                lean._run(["lean"], tempfile.gettempdir())
        process.kill.assert_called_once()

    def test_runner_rejects_incomplete_output(self):
        process = mock.Mock(stdout=mock.Mock(read=mock.Mock(side_effect=OSError("broken pipe"))), returncode=0)
        with mock.patch.object(subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(ValueError, "read completely"):
                lean._run(["lean"], tempfile.gettempdir())

    def test_runner_output_overflow_kills_child(self):
        process = mock.Mock(stdout=mock.Mock(read=mock.Mock(side_effect=[b"x" * (lean.MAX_OUTPUT_BYTES + 1)])), returncode=-1)
        with mock.patch.object(subprocess, "Popen", return_value=process):
            with self.assertRaisesRegex(ValueError, "64-KiB"):
                lean._run(["lean"], tempfile.gettempdir())
        process.kill.assert_called_once()

    def test_certificate_size_limit(self):
        self.assertFalse(lean.check_certificate(SPEC, {"source": "x" * (lean.MAX_CERTIFICATE_BYTES + 1)}))


@unittest.skipUnless(os.environ.get("RDS_LEAN_EXECUTABLE"), "Explicit native Lean executable not configured")
class NativeLeanTests(unittest.TestCase):
    def test_real_rational_proofs_and_kernel_replay(self):
        for relation, left, right in (("lt", "1/2", "3/4"), ("eq", "-1/3", "-2/6"),
                                     ("le", "5/4", "5/4")):
            spec = {**SPEC, "relation": relation, "left": left, "right": right}
            with self.subTest(relation=relation):
                result = lean.verify(spec)
                self.assertEqual(result["status"], "PASS", result)
                self.assertTrue(lean.check_certificate(spec, result["certificate"]))

    def test_real_vector_proof_metadata_and_kernel_replay(self):
        result = lean.verify(VECTOR_SPEC, allow_fallback=False)
        self.assertEqual(result["status"], "PASS", result)
        self.assertEqual(result["semantics"], lean.VECTOR_SEMANTICS)
        self.assertEqual(result["semantics"], result["certificate"]["semantics"])
        self.assertTrue(lean.check_certificate(VECTOR_SPEC, result["certificate"]))

    def test_false_statement_is_unknown_not_a_scientific_refutation(self):
        result = lean.verify({**SPEC, "left": "1", "right": "0"})
        self.assertEqual(result["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
