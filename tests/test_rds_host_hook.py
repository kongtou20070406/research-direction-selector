"""The host command hook must bind dispatch to real ledger admission identities."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_rds_project import SCRIPT, digest, file_sha

ROOT = Path(__file__).resolve().parents[1]


class HostHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="rds host hook ")
        self.root = Path(self.tmp.name)
        (self.root / "code.py").write_text(SCRIPT.replace('mode, output = sys.argv[1:]', 'pass'), encoding="utf-8")
        for name in ("config.json", "evaluator.json"):
            (self.root / name).write_text("{}", encoding="utf-8")
        (self.root / "data.json").write_text("[1,2,3]", encoding="utf-8")
        (self.root / "code.py").write_text(SCRIPT, encoding="utf-8")
        protocol = {"code_sha256": file_sha(self.root / "code.py"), "config_sha256": file_sha(self.root / "config.json"),
                    "data_sha256": file_sha(self.root / "data.json"), "data_split": "development-only",
                    "init": "none", "seed": 0, "checkpoint": "none", "schedule": "one calculation",
                    "sample_work": {"rows": 3}, "numeric_protocol": "Python float"}
        (self.root / "protocol.json").write_text(json.dumps(protocol), encoding="utf-8")
        self.contract = {"schema": 1, "bindings": [{"role": role, "path": name, "sha256": file_sha(self.root / name)}
                                                   for role, name in (("code", "code.py"), ("config", "config.json"),
                                                                      ("data", "data.json"), ("evaluator", "evaluator.json"),
                                                                      ("protocol", "protocol.json"))],
                         "allowed_commands": [[sys.executable, "-B", "code.py", "ok", "outputs/r1.json"]],
                         "output_roots": ["outputs"],
                         "budget": {"wall_seconds": 20, "cpu_seconds": 10, "gpu_seconds": 0}}
        self.manifest = {"schema": 1, "id": "r1", "arm": "control", "control_id": None,
                         "protocol": {"path": "protocol.json", "sha256": file_sha(self.root / "protocol.json")},
                         "argv": [sys.executable, "-B", "code.py", "ok", "outputs/r1.json"],
                         "outpaths": ["outputs/r1.json"],
                         "resource_estimates": {"wall_seconds": 2, "cpu_seconds": 1, "gpu_seconds": 0},
                         "timeout_seconds": 2}

    def tearDown(self):
        self.tmp.cleanup()

    def invoke(self, *arguments):
        return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"),
                               "--root", str(self.root), *arguments], cwd=ROOT,
                              capture_output=True, encoding="utf-8", timeout=30)

    def admit(self, attempt_id="attempt-1"):
        """Issue a real admission identity through the controller ledger."""
        from rds_project import ProjectStore
        store = ProjectStore(self.root)
        with store._db() as db:
            run = store._run(db, "r1")
            run.update(status="RUNNING", attempt_id=attempt_id, worker_pid=None, started_at=None)
            store._save(db, run)
        return attempt_id

    def init_project(self):
        (self.root / "contract.json").write_text(json.dumps(self.contract), encoding="utf-8")
        (self.root / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        self.assertEqual(self.invoke("project", "init", "--mode", "quick", "--contract", str(self.root / "contract.json")).returncode, 0)
        self.assertEqual(self.invoke("project", "create", "--manifest", str(self.root / "manifest.json")).returncode, 0)

    def request(self, attempt_id=None, argv=None, executor_sha256=None, host="windows-task-scheduler"):
        run = json.loads(subprocess.run(
            [sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"), "--root", str(self.root),
             "project", "status"], cwd=ROOT, capture_output=True, encoding="utf-8").stdout)["runs"][0]
        return {"host": host, "run_id": "r1", "attempt_id": attempt_id if attempt_id is not None else run["attempt_id"],
                "argv": argv if argv is not None else self.manifest["argv"],
                "executor_sha256": executor_sha256 or run["executor_sha256"]}

    def test_coverage_without_install_reports_missing_and_claims_nothing(self):
        self.init_project()
        result = self.invoke("host-hook", "coverage")
        self.assertEqual(result.returncode, 2, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["status"], "HOST_GUARD_MISSING")
        self.assertFalse(report["claims_protection_against_direct_shell"])
        self.assertIn("direct shell execution", report["uncovered"])

    def test_install_requires_ledger_and_records_supported_hosts(self):
        result = self.invoke("host-hook", "install")
        self.assertEqual(result.returncode, 1)
        self.assertIn("after the project ledger exists", result.stderr)
        self.init_project()
        result = self.invoke("host-hook", "install")
        self.assertEqual(result.returncode, 0, result.stderr)
        guard = json.loads((self.root / ".rds" / "host-guard.json").read_text(encoding="utf-8"))
        self.assertEqual(guard["schema"], 1)
        self.assertIn("windows-task-scheduler", guard["hosts"])
        report = json.loads(self.invoke("host-hook", "coverage").stdout)
        self.assertEqual(report["status"], "INSTALLED")
        self.assertTrue(report["strict"])
        self.assertFalse(report["claims_protection_against_direct_shell"])

    def test_validate_admits_the_real_bound_request(self):
        self.init_project()
        self.invoke("host-hook", "install")
        attempt = self.admit()
        run = json.loads(self.invoke("project", "status").stdout)["runs"][0]
        (self.root / "request.json").write_text(json.dumps(self.request()), encoding="utf-8")
        result = self.invoke("host-hook", "validate", "--request", str(self.root / "request.json"))
        self.assertEqual(result.returncode, 0, result.stderr)
        verdict = json.loads(result.stdout)
        self.assertEqual(verdict["status"], "ADMITTED")
        self.assertEqual(verdict["attempt_id"], attempt)
        self.assertEqual(verdict["bound_manifest_sha256"], digest(self.manifest))

    def test_forged_or_stale_requests_are_refused_before_dispatch(self):
        self.init_project()
        self.invoke("host-hook", "install")
        self.admit()
        cases = [
            (self.request(attempt_id="forged-attempt"), "does not match the ledger-issued admission"),
            (self.request(argv=[sys.executable, "-B", "evil.py"]), "argv does not match"),
            (self.request(executor_sha256="0" * 64), "Executor digest does not match"),
            (self.request(host="direct-shell"), "not covered by the installed hook"),
            ({"run_id": "r1", "attempt_id": "x"}, "Request must declare nonempty"),
            ({"host": "windows-task-scheduler", "run_id": "missing", "attempt_id": "x",
              "argv": self.manifest["argv"], "executor_sha256": "0" * 64}, "Unknown admission identity"),
        ]
        for request, expected in cases:
            with self.subTest(expected=expected):
                (self.root / "request.json").write_text(json.dumps(request), encoding="utf-8")
                result = self.invoke("host-hook", "validate", "--request", str(self.root / "request.json"))
                self.assertEqual(result.returncode, 1)
                self.assertIn("Host hook refusal", result.stderr)
                self.assertIn(expected, result.stderr)

    def test_terminal_admission_is_refused_after_completion(self):
        self.init_project()
        self.invoke("host-hook", "install")
        self.admit()
        # Move the admitted run to its terminal COMPLETED state, as the controller would.
        from rds_project import ProjectStore
        store = ProjectStore(self.root)
        with store._db() as db:
            run = store._run(db, "r1")
            run.update(status="COMPLETED")
            store._save(db, run)
        (self.root / "request.json").write_text(json.dumps(self.request()), encoding="utf-8")
        result = self.invoke("host-hook", "validate", "--request", str(self.root / "request.json"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("Admission is terminal", result.stderr)

    def test_missing_guard_refuses_validation_even_with_ledger(self):
        self.init_project()
        self.admit()
        (self.root / "request.json").write_text(json.dumps(self.request()), encoding="utf-8")
        result = self.invoke("host-hook", "validate", "--request", str(self.root / "request.json"))
        self.assertEqual(result.returncode, 1)
        self.assertIn("HOST_GUARD_MISSING", result.stderr)
