"""#282 acceptance through the real CLI: binding, refusals, continuation.

Fixtures reuse the boring synthetic project; no scientific workload runs. The
covered cases: paid failed run before an attempted abandonment, missing and
replaced ledgers, concurrent binding, stale identity, root aliases, and normal
canonical continuation through create/execute.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import test_rds_project  # noqa: E402
from rds_project import ProjectStore, canonical, digest, file_sha  # noqa: E402
from rds_workspace import bind, pointer_path, read_pointer  # noqa: E402


class WorkspaceBindingShellTests(unittest.TestCase):
    def setUp(self):
        helper = test_rds_project.ProjectTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        self.root, self.store, self.contract, self.spec = helper.root, helper.store, helper.contract, helper.spec

    def cli(self, *args, ok=True):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"),
                                 "--root", str(self.root), *args], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", timeout=60)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result

    def paid_failed_run(self):
        spec = self.spec("r1", "nonzero")
        self.store.register(spec)
        receipt = self.store.execute(spec["id"])
        self.assertEqual(receipt["run_status"], "FAILED")

    def test_workspace_bind_cli_is_idempotent_and_leads_to_status_coverage(self):
        result = json.loads(self.cli("workspace", "bind").stdout)
        self.assertEqual(result["status"], "BOUND")
        again = json.loads(self.cli("workspace", "bind").stdout)
        self.assertEqual(again["status"], "ALREADY_BOUND")
        status = json.loads(self.cli("project", "status").stdout)
        self.assertEqual(status["workspace_binding"]["status"], "BOUND")
        self.assertIn("next_command", status["workspace_binding"])
        coverage = json.loads(self.cli("workspace", "coverage").stdout)
        self.assertEqual(coverage["status"], "BOUND")

    def test_bind_refused_without_ledger(self):
        empty = self.root / "empty-unbound"
        empty.mkdir()
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"),
                                 "--root", str(empty), "workspace", "bind"],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("No project ledger to bind", result.stderr)

    def test_paid_failed_run_then_separate_project_refusal_via_cli(self):
        self.paid_failed_run()
        self.cli("workspace", "bind")
        contract_path = self.root / "contract-copy.json"
        contract_path.write_text(canonical(self.contract), encoding="utf-8")
        before = (self.store.path.read_bytes(), pointer_path(self.root).read_text(encoding="utf-8"))
        refused = self.cli("project", "init", "--contract", str(contract_path),
                           "--separate-project", ok=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("--separate-project", refused.stderr + refused.stdout)
        self.assertEqual((self.store.path.read_bytes(), pointer_path(self.root).read_text(encoding="utf-8")),
                         before)

    def test_sibling_root_inside_bound_tree_refused_via_cli(self):
        self.paid_failed_run()
        self.cli("workspace", "bind")
        sibling = self.root / "sibling-reset"
        sibling.mkdir()
        for binding in self.contract["bindings"]:
            shutil.copyfile(self.root / binding["path"], sibling / binding["path"])
        contract_path = sibling / "contract.json"
        contract_path.write_text(canonical(self.contract), encoding="utf-8")
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"),
                                 "--root", str(sibling), "project", "init",
                                 "--contract", str(contract_path), "--supersedes", str(self.root)],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--supersedes", result.stderr + result.stdout)
        self.assertFalse((sibling / ".rds" / "project.sqlite3").exists())

    def test_replaced_same_contract_ledger_refused_via_cli_status_and_coverage(self):
        self.paid_failed_run()
        self.cli("workspace", "bind")
        outside = Path(tempfile.mkdtemp(prefix="rds fresh same-contract "))
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        fresh = outside / "same-contract-reset"
        fresh.mkdir()
        for binding in self.contract["bindings"]:
            shutil.copyfile(self.root / binding["path"], fresh / binding["path"])
        ProjectStore(fresh).initialize(self.contract)
        self.store.path.unlink()
        shutil.copyfile(fresh / ".rds" / "project.sqlite3", self.store.path)
        status = self.cli("project", "status")
        payload = json.loads(status.stdout)
        self.assertEqual(payload["workspace_binding"]["status"], "MISMATCH")
        cov = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"),
                              "--root", str(self.root), "workspace", "coverage"],
                             cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(cov.returncode, 2)
        self.assertEqual(json.loads(cov.stdout)["status"], "MISMATCH")

    def test_stale_identity_refused_via_cli(self):
        self.cli("workspace", "bind")
        pointer = read_pointer(self.root)
        forged = {**pointer, "event_digest": "0" * 64}
        pointer_path(self.root).write_text(canonical(forged), encoding="utf-8")
        status = json.loads(self.cli("project", "status").stdout)
        self.assertEqual(status["workspace_binding"]["status"], "MISMATCH")

    def test_concurrent_bind_cli_yields_one_identity(self):
        def try_bind(_):
            return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"),
                                   "--root", str(self.root), "workspace", "bind"],
                                  cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(try_bind, range(3)))
        bound = [r for r in results if r.returncode == 0]
        self.assertTrue(bound)
        payloads = [json.loads(r.stdout) for r in bound]
        pointers = {p["pointer"]["event_digest"] for p in payloads}
        self.assertEqual(len(pointers), 1)
        statuses = sorted(p["status"] for p in payloads)
        self.assertEqual(statuses.count("BOUND"), 1)
        self.assertTrue(all(s in ("ALREADY_BOUND", "BOUND") for s in statuses))
        for r in results:
            if r.returncode != 0:
                self.assertIn("already records a different workspace identity", r.stderr)

    def test_alias_roots_resolve_to_the_same_binding(self):
        self.cli("workspace", "bind")
        alias = self.root / "." / "nested-deeper" / ".."
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"),
                                 "--root", str(alias), "workspace", "bind"],
                                cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "ALREADY_BOUND")

    def test_bound_workspace_continues_create_execute_via_cli(self):
        self.cli("workspace", "bind")
        spec = self.spec("r2", "ok")
        manifest = self.root / "manifest-ok.json"
        manifest.write_text(canonical(spec), encoding="utf-8")
        self.cli("project", "create", "--manifest", str(manifest))
        result = json.loads(self.cli("project", "execute", "--id", spec["id"]).stdout)
        self.assertEqual(result["run_status"], "SUCCEEDED")
        status = json.loads(self.cli("project", "status").stdout)
        self.assertEqual(status["workspace_binding"]["status"], "BOUND")


if __name__ == "__main__":
    unittest.main()
