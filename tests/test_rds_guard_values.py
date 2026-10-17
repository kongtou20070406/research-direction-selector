"""Focused coverage for the shared, pure guard value contracts."""
import copy
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import rds_guard
import rds_guard_values as values


class GuardValuesTests(unittest.TestCase):
    def test_exact_accepts_numeric_rational_and_rejects_unbounded_or_invalid(self):
        from fractions import Fraction
        for raw, expected in ((4, Fraction(4)), ("3/7", Fraction(3, 7)),
                              (0.125, Fraction(1, 8)), ("-2.5e1", Fraction(-25))):
            with self.subTest(raw=raw):
                self.assertEqual(values.exact(raw), expected)
        for raw in (True, "1/0", "NaN", "9" * 81, "1" * 181):
            with self.subTest(raw=repr(raw)), self.assertRaises(ValueError):
                values.exact(raw)
        self.assertEqual(values.MAX_BYTES, 2 * 1024 * 1024)

    def test_domain_bounds_are_inclusive_and_validation_does_not_mutate_inputs(self):
        domain = {"parameters": {"rate": {"min": "1/3", "max": "2/3"},
                                 "seed": {"eq": 2}},
                  "justification": "bounded fixture"}
        candidate = {"action": {"parameters": {"rate": "1/3", "seed": 2}}}
        before = copy.deepcopy((domain, candidate))
        self.assertIs(values.validate_domain(domain, candidate), domain)
        self.assertTrue(values.in_domain(domain["parameters"], candidate["action"]["parameters"]))
        self.assertEqual((domain, candidate), before)
        for rate in ("1/3", "2/3"):
            self.assertTrue(values.in_domain({"rate": {"min": "1/3", "max": "2/3"}}, {"rate": rate}))
        for rate in ("0.32", "0.67", "1/0", True):
            self.assertFalse(values.in_domain({"rate": {"min": "1/3", "max": "2/3"}}, {"rate": rate}))
        self.assertFalse(values.in_domain({"missing": {"eq": 1}}, {}))

    def test_original_guard_exports_are_the_shared_objects(self):
        for name in ("exact", "validate_domain", "in_domain", "MAX_BYTES"):
            with self.subTest(name=name):
                self.assertIs(getattr(rds_guard, name), getattr(values, name))

    def test_cold_rejected_domain_cas_validation_stays_on_leaf_import_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cas = root / ".rds" / "cas"
            cas.mkdir(parents=True)
            witness = cas / "witness.json"
            raw = b'{"metric":0.1}\n'
            witness.write_bytes(raw)
            code = """import hashlib, sys
from pathlib import Path
sys.path.insert(0, {scripts!r})
import rds_checkpoint_decision as kernel
witness = Path({witness!r})
raw = witness.read_bytes()
decision = {{"question_id":"q", "goal_revision":"g", "scope":{{}},
 "candidate":{{"action":{{"description":"fit", "target":"model", "parameters":{{"rate":2}}}}}},
 "outcome":"rejected", "evidence":{{}},
 "rejected_domain":{{"parameters":{{"rate":{{"min":1,"max":3}}}},"justification":"bounded"}},
 "falsification":{{"path":str(witness),"sha256":hashlib.sha256(raw).hexdigest()}}}}
kernel.validate_checkpoint_decision(decision, "cp-cold", witness.parents[1])
witness.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
decision["falsification"]["sha256"] = hashlib.sha256(witness.read_bytes()).hexdigest()
try:
 kernel.validate_checkpoint_decision(decision, "cp-oversize", witness.parents[1])
except ValueError as exc:
 assert "integrity" in str(exc)
else:
 raise AssertionError("oversize CAS witness was accepted")
for name in ("rds_guard", "rds_project", "rds_advisor"):
 assert name not in sys.modules, name
""".format(scripts=str(SCRIPTS), witness=str(witness))
            env = os.environ.copy()
            env["RDS_USAGE_DB"] = str(root / "usage.sqlite3")
            result = subprocess.run([sys.executable, "-X", "utf8", "-B", "-c", code],
                                    cwd=root, env=env, capture_output=True, text=True,
                                    encoding="utf-8", timeout=8)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
