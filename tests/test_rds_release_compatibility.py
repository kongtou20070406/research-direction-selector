"""Public reference-ledger reads preserve versions without admitting a changed engine."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]
from benchmark.run import Project
import rds_cli as cli


class ReleaseCompatibilityTests(unittest.TestCase):
    def project(self):
        project = Project().init()
        self.addCleanup(project.close)
        return project

    def set_fields(self, project, **fields):
        path = project.root / ".rds/state.sqlite3"
        with closing(sqlite3.connect(path)) as db, db:
            state = json.loads(db.execute("SELECT body FROM state WHERE id=1").fetchone()[0])
            state.update(fields)
            db.execute("UPDATE state SET body=? WHERE id=1", (json.dumps(state),))
        return path

    def test_public_status_reads_stable_previous_preview_and_current_without_rewriting(self):
        project = self.project()
        for version in ("5.8.0", "5.9.0-rc.1", cli.VERSION):
            with self.subTest(version=version):
                path = self.set_fields(project, version=version)
                before = path.read_bytes()
                status = project.call("status")
                self.assertEqual(status["version"], version)
                self.assertEqual(path.read_bytes(), before)

    def test_public_status_rejects_unlisted_versions_without_rewriting(self):
        project = self.project()
        for version in ("5.9.0-rc.999", "5.9.0", "6.0.0"):
            with self.subTest(version=version):
                path = self.set_fields(project, version=version)
                before = path.read_bytes()
                self.assertIn("Incompatible state version", project.call("status", ok=False))
                self.assertEqual(path.read_bytes(), before)

    def test_read_compatibility_preserves_contract_digest_check(self):
        project = self.project()
        for version in ("5.8.0", "5.9.0-rc.1", cli.VERSION):
            with self.subTest(version=version):
                path = self.set_fields(project, version=version, contract_sha256="0" * 64)
                before = path.read_bytes()
                self.assertIn("Contract integrity failure", project.call("status", ok=False))
                self.assertEqual(path.read_bytes(), before)

    def test_changed_engine_remains_readable_but_blocks_new_admission(self):
        project = self.project()
        for version in ("5.8.0", "5.9.0-rc.1", cli.VERSION):
            with self.subTest(version=version):
                path = self.set_fields(project, version=version, engine_sha256="0" * 64)
                before = path.read_bytes()
                status = project.call("status")
                self.assertEqual(status["version"], version)
                self.assertIn("a new contract is required", project.call("plan", "create", spec=project.plan(), ok=False))
                self.assertEqual(path.read_bytes(), before)
                after = project.call("status")
                self.assertEqual(after["plans"], {})
                self.assertEqual(after["budget"]["reserved"]["runs"], 0)
                self.assertEqual(after["budget"]["reserved"]["runtime_ms"], 0)


if __name__ == "__main__":
    unittest.main()
