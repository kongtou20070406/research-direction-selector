"""Composition root (B4) wiring and downward-dependency tests."""
from dataclasses import FrozenInstanceError
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_app
from rds_project import ProjectStore
from rds_project_read_services import ProjectReadServices


class CompositionRootTests(unittest.TestCase):
    def test_create_app_binds_store_and_read_services_immutably(self):
        with tempfile.TemporaryDirectory(prefix="rds-app-") as tmp:
            app = rds_app.create_app(tmp)
            self.assertIsInstance(app.store, ProjectStore)
            self.assertEqual(app.root, Path(tmp).resolve())
            with self.assertRaises(FrozenInstanceError):
                app.root = Path(".")
            with sqlite3.connect(":memory:") as db:
                self.assertIsInstance(app.read_services(db), ProjectReadServices)
                self.assertIsInstance(rds_app.read_services(db), ProjectReadServices)

    def test_bootstrap_hooks_registers_providers_and_rejects_unknown(self):
        with tempfile.TemporaryDirectory(prefix="rds-app-hooks-") as tmp:
            app = rds_app.create_app(tmp, hooks=("advisor", "quick"))
            bound = app.bootstrap_hooks("advisor", "quick")
            self.assertEqual(set(bound), {"advisor", "quick"})
            self.assertIs(app.provider("advisor"), bound["advisor"])
            self.assertIs(app.provider("quick"), bound["quick"])
            with self.assertRaisesRegex(ValueError, "Unknown project hook provider"):
                rds_app.bootstrap_hooks("unknown")

    def test_import_isolation_until_hooks_are_requested(self):
        code = """import sys
sys.path.insert(0, sys.argv[1])
import rds_app
assert 'rds_owned_advisor' not in sys.modules
assert 'rds_quick_ledger' not in sys.modules
bound = rds_app.bootstrap_hooks('quick')
assert 'rds_quick_ledger' in sys.modules
assert 'rds_owned_advisor' not in sys.modules
"""
        proc = subprocess.run([sys.executable, "-B", "-c", code, str(ROOT / "scripts")],
                              capture_output=True, text=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_f1_to_f9_packet_advise_and_code_diff_wiring(self):
        with tempfile.TemporaryDirectory(prefix="rds-app-f9-") as tmp:
            app = rds_app.create_app(tmp)
            packet = app.import_packet(
                project="demo",
                goal="verify structural diff",
                texts={"note": "baseline"},
                candidates={
                    "cand_a": {
                        "claim": "treatment improves margin",
                        "actor": "ai",
                        "premises": ["text:note"],
                        "goal": "statement",
                    }
                },
            )
            proposal = app.advise(packet, language="zh")
            self.assertEqual(proposal["schema"], "rds-advisor-proposal-v1")
            diff = app.encode_code_diff("def control(x): return x\n", "def control(x): return x\ndef treatment(x): return x + 1\n")
            self.assertEqual(diff["schema"], "rds-code-diff-v1")
            self.assertEqual(diff["ast_delta"]["functions_added"], ["treatment"])


if __name__ == "__main__":
    unittest.main()

