"""Cold-start repairs are batched and every hint runs as printed (#72)."""
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rds_cli


def run_cli(*argv):
    environment = dict(os.environ, RDS_USAGE_LOG="0")
    return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"), *argv],
                          cwd=ROOT, capture_output=True, encoding="utf-8", timeout=60, env=environment)


class BatchValidationTests(unittest.TestCase):
    def test_missing_fields_rejected_once_naming_all_of_them(self):
        # Five sequential REJECTs measured in #72; one repair round trip now lists every field.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "proj").mkdir()
            contract = root / "contract.json"
            contract.write_text(json.dumps({"schema": 1, "project_id": "demo"}), encoding="utf-8")
            result = run_cli("--root", str(root / "proj"), "init", "--contract", str(contract))
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stderr.splitlines()[0],
                             "[RDS-REJECT] Missing contract fields: claim, primary_metric, budget, splits, "
                             "baseline_source")
            self.assertEqual(len(result.stderr.splitlines()), 2)
            self.assertIn("[RDS-HINT] Inspect the existing project first: python -B scripts/rds_cli.py --root ", result.stderr)
            self.assertIn(str(root / "proj"), result.stderr)
            self.assertIn(" project discover; see docs/project-lifecycle.md", result.stderr)
            self.assertFalse((root / "proj" / ".rds").exists())

    def test_single_missing_field_keeps_the_batch_message_shape(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "proj").mkdir()
            full = json.loads((ROOT / "examples/reference-run/contract.json").read_text(encoding="utf-8"))
            contract = root / "contract.json"
            contract.write_text(json.dumps({k: v for k, v in full.items() if k != "budget"}), encoding="utf-8")
            result = run_cli("--root", str(root / "proj"), "init", "--contract", str(contract))
            self.assertEqual(result.returncode, 1)
            self.assertTrue(result.stderr.startswith("[RDS-REJECT] Missing contract fields: budget\n"),
                            result.stderr)

    def test_type_error_detail_stays_per_field(self):
        # The batch covers presence only; the existing per-field detail for values is retained.
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "proj").mkdir()
            full = json.loads((ROOT / "examples/reference-run/contract.json").read_text(encoding="utf-8"))
            contract = root / "contract.json"
            contract.write_text(json.dumps({**full, "primary_metric": "mse"}), encoding="utf-8")
            result = run_cli("--root", str(root / "proj"), "init", "--contract", str(contract))
            self.assertEqual(result.returncode, 1)
            self.assertTrue(result.stderr.startswith("[RDS-REJECT] Contract primary_metric must be a JSON object"),
                            result.stderr)


class InitExampleHintTests(unittest.TestCase):
    def test_every_l3_init_rejection_names_root_discovery(self):
        with tempfile.TemporaryDirectory() as raw:
            (Path(raw) / "proj").mkdir()
            contract = Path(raw) / "contract.json"
            contract.write_text("5", encoding="utf-8")
            result = run_cli("--root", str(Path(raw) / "proj"), "init", "--contract", str(contract))
            self.assertEqual(result.returncode, 1)
            self.assertIn("[RDS-HINT] Inspect the existing project first: python -B scripts/rds_cli.py --root ", result.stderr)
            self.assertIn(str(Path(raw) / "proj"), result.stderr)
            self.assertIn(" project discover; see docs/project-lifecycle.md", result.stderr)

    def test_project_init_rejection_names_root_discovery(self):
        with tempfile.TemporaryDirectory() as raw:
            (Path(raw) / "proj").mkdir()
            contract = Path(raw) / "contract.json"
            contract.write_text("5", encoding="utf-8")
            result = run_cli("--root", str(Path(raw) / "proj"), "project", "init", "--contract", str(contract))
            self.assertEqual(result.returncode, 1)
            self.assertIn("[RDS-HINT] Inspect the existing project first: python -B scripts/rds_cli.py --root ", result.stderr)
            self.assertIn(str(Path(raw) / "proj"), result.stderr)
            self.assertIn(" project discover; see docs/project-lifecycle.md", result.stderr)

    def test_non_init_rejections_stay_unhinted(self):
        with tempfile.TemporaryDirectory() as raw:
            result = run_cli("--root", raw, "status", "--brief")
            self.assertEqual(result.returncode, 1)
            self.assertIn("[RDS-REJECT]", result.stderr)
            self.assertNotIn("[RDS-HINT]", result.stderr)

    def test_hint_command_runs_on_a_clean_directory(self):
        # Acceptance: the named command executes successfully on a clean directory.
        with tempfile.TemporaryDirectory() as raw:
            demo = Path(raw) / "my-project"
            environment = dict(os.environ, RDS_USAGE_LOG="0")
            prepared = subprocess.run([sys.executable, "-B", str(ROOT / "examples/project-runner/prepare.py"),
                                       "--root", str(demo)], cwd=ROOT, capture_output=True,
                                      encoding="utf-8", timeout=60, env=environment)
            self.assertEqual(prepared.returncode, 0, prepared.stderr)
            self.assertTrue((demo / "contract.json").exists())


class RunnableHintTests(unittest.TestCase):
    def assertHintRuns(self, stderr):
        self.assertIn("[RDS-HINT] python scripts/rds_cli.py", stderr)
        hint = [line for line in stderr.splitlines() if line.startswith("[RDS-HINT] ")][0]
        self.assertTrue(hint.endswith(" --help"))
        # The hint must run as printed: hand the whole line to the shell.
        result = subprocess.run(hint[len("[RDS-HINT] "):], cwd=ROOT, shell=True, capture_output=True,
                                encoding="utf-8", timeout=60, env=dict(os.environ, RDS_USAGE_LOG="0"))
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_argument_hint_names_subcommand_and_runs(self):
        with tempfile.TemporaryDirectory() as raw:
            result = run_cli("--root", raw, "project", "execute", "--id")
            self.assertEqual(result.returncode, 2)
            self.assertIn("usage: rds_cli.py project execute", result.stderr)
            self.assertHintRuns(result.stderr)

    def test_unknown_option_hint_names_subcommand_and_runs(self):
        with tempfile.TemporaryDirectory() as raw:
            result = run_cli("--root", raw, "project", "init", "--badflag")
            self.assertEqual(result.returncode, 2)
            self.assertHintRuns(result.stderr)

    def test_root_level_error_hint_runs(self):
        with tempfile.TemporaryDirectory() as raw:
            result = run_cli("--root", raw)
            self.assertEqual(result.returncode, 2)
            self.assertHintRuns(result.stderr)


if __name__ == "__main__":
    unittest.main()
