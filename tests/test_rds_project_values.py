"""Parity between rds_project value helpers and the rds_project_values leaf.

Covers canonical/digest/require/number value and exception parity, the
digest canonical-call seam, and a fresh-process proof that importing
rds_owned_advisor loads neither the L2 project store nor CLI/QUICK modules.
"""
from pathlib import Path
import hashlib
import json
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import rds_project
import rds_project_values as leaf


class CanonicalDigestParity(unittest.TestCase):
    def assert_same(self, value):
        self.assertEqual(leaf.canonical(value), rds_project.canonical(value))
        self.assertEqual(leaf.digest(value), rds_project.digest(value))

    def test_ordinary_values(self):
        for value in [None, True, False, 0, 1, -3, 2.5, "", "text", [], {},
                      [1, [2, {"a": 3}]], {"a": 1, "b": [None, True]},
                      {"z": {"y": {"x": 1}}}, {"n": 0.1}, {"big": 10 ** 20}]:
            with self.subTest(value=value):
                self.assert_same(value)

    def test_unicode_and_escaping(self):
        for value in ["héllo", "中文", "emoji \U0001f600", {"ключ": "значение"},
                      "quote\"back\\slash\nnewline", " control"]:
            with self.subTest(value=value):
                self.assert_same(value)
        self.assertIn("héllo", leaf.canonical({"k": "héllo"}))

    def test_mixed_key_sorting(self):
        for value in [{"b": 1, "a": 2}, {"10": 1, "2": 2, "1": 3},
                      {"b": {"d": 1, "c": 2}, "a": 1}, {"": 0, "a": 1}]:
            with self.subTest(value=value):
                self.assert_same(value)
        self.assertEqual(leaf.canonical({"b": 1, "a": 2}), '{"a":2,"b":1}')

    def test_nonfinite_rejected_identically(self):
        for value in [float("nan"), float("inf"), float("-inf"),
                      {"x": float("nan")}, [float("inf")]]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError) as original:
                    rds_project.canonical(value)
                with self.assertRaises(ValueError) as candidate:
                    leaf.canonical(value)
                self.assertEqual(str(original.exception), str(candidate.exception))
                with self.assertRaises(ValueError) as original_digest:
                    rds_project.digest(value)
                with self.assertRaises(ValueError) as candidate_digest:
                    leaf.digest(value)
                self.assertEqual(str(original_digest.exception), str(candidate_digest.exception))

    def test_bytes_fail_not_raw_digest(self):
        for value in [b"raw", bytearray(b"raw"), {"k": b"v"}, [b"x"]]:
            with self.subTest(value=value):
                with self.assertRaises(TypeError) as original:
                    rds_project.digest(value)
                with self.assertRaises(TypeError) as candidate:
                    leaf.digest(value)
                self.assertEqual(str(original.exception), str(candidate.exception))
                with self.assertRaises(TypeError):
                    leaf.canonical(value)

    def test_digest_uses_leaf_canonical_seam(self):
        original = leaf.canonical
        try:
            leaf.canonical = lambda value: '"patched"'
            self.assertEqual(leaf.digest({}), hashlib.sha256(b'"patched"').hexdigest())
        finally:
            leaf.canonical = original


class RequireParity(unittest.TestCase):
    def test_truthy_passes(self):
        for condition in [True, 1, "x", [0]]:
            with self.subTest(condition=condition):
                self.assertIsNone(leaf.require(condition, "unused"))
                self.assertIsNone(rds_project.require(condition, "unused"))

    def test_falsy_raises_with_exact_message(self):
        for condition, message in [(False, "boom"), (0, "zero"), ("", "empty"),
                                   (None, "none"), ([], "list")]:
            with self.subTest(condition=condition, message=message):
                with self.assertRaises(ValueError) as original:
                    rds_project.require(condition, message)
                with self.assertRaises(ValueError) as candidate:
                    leaf.require(condition, message)
                self.assertEqual(str(original.exception), message)
                self.assertEqual(str(candidate.exception), message)


class NumberParity(unittest.TestCase):
    def test_valid_values_convert_to_float(self):
        cases = [(5, {}), (5.5, {}), (0, {}), (0.0, {}), (-0.0, {}),
                 (10 ** 20, {}), (3, {"positive": True}), (2.5, {"positive": True})]
        for value, kwargs in cases:
            with self.subTest(value=value, kwargs=kwargs):
                self.assertEqual(leaf.number(value, "n", **kwargs),
                                 rds_project.number(value, "n", **kwargs))
                self.assertIsInstance(leaf.number(value, "n", **kwargs), float)

    def test_invalid_values_raise_identically(self):
        cases = [(True, {}), (False, {}), ("5", {}), (None, {}), ([], {}),
                 (float("nan"), {}), (float("inf"), {}), (float("-inf"), {}),
                 (-1, {}), (-0.5, {}), (0, {"positive": True}),
                 (-1, {"positive": True}), (True, {"positive": True})]
        for value, kwargs in cases:
            with self.subTest(value=value, kwargs=kwargs):
                with self.assertRaises(ValueError) as original:
                    rds_project.number(value, "latency", **kwargs)
                with self.assertRaises(ValueError) as candidate:
                    leaf.number(value, "latency", **kwargs)
                self.assertEqual(str(original.exception), "Invalid latency")
                self.assertEqual(str(candidate.exception), "Invalid latency")


class OwnedAdvisorImportIsolation(unittest.TestCase):
    def test_fresh_import_loads_no_project_store_or_cli_quick(self):
        code = (
            "import sys, json; sys.path.insert(0, r'scripts'); "
            "import rds_owned_advisor, rds_project_values; "
            "bad = sorted(m for m in sys.modules if m == 'rds_project' "
            "or m.startswith('rds_project.') "
            "or (m.startswith('rds') and ('cli' in m.lower() or 'quick' in m.lower()))); "
            "print(json.dumps({'bad': bad, "
            "'advisor_digest_is_leaf': rds_owned_advisor.digest is rds_project_values.digest}))"
        )
        completed = subprocess.run([sys.executable, "-B", "-c", code], cwd=str(ROOT),
                                   capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(report["bad"], [])
        self.assertTrue(report["advisor_digest_is_leaf"])


if __name__ == "__main__":
    unittest.main()
