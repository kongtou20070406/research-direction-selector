"""Project-only roots make L3 rejections name the real split (#76)."""
import os
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples" / "project-runner"))
from prepare import prepare  # noqa: E402


def run_cli(root, *argv):
    environment = dict(os.environ, RDS_USAGE_LOG="0")
    return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"), "--root", str(root), *argv],
                          cwd=ROOT, capture_output=True, encoding="utf-8", timeout=60, env=environment)


class ProjectRootGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.project_root = Path(prepare(Path(self.temp.name) / "proj")["root"])
        run_cli(self.project_root, "project", "init", "--mode", "quick", "--contract", str(self.project_root / "contract.json"))

    def tearDown(self):
        self.temp.cleanup()

    def test_l3_commands_name_the_project_ledger_and_the_missing_l3_init(self):
        for argv in (["decide", "--run", "control"], ["hypothesis", "add", "--spec", "spec.json"],
                     ["meta", "auto-repair", "--dry-run"],
                     ["checkpoint", "save", "--kind", "reference", "--id", "x"]):
            with self.subTest(command=" ".join(argv)):
                result = run_cli(self.project_root, *argv)
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stderr.startswith("[RDS-REJECT] L3 kernel not initialized in this root"),
                                result.stderr)
                self.assertIn("project ledger found", result.stderr)
                self.assertIn("init --contract", result.stderr)
                self.assertIn("[RDS-HINT] python -B scripts/rds_cli.py --root", result.stderr)
                self.assertIn(" project next", result.stderr)
                self.assertFalse((self.project_root / '.rds/state.sqlite3').exists())

    def test_project_artifact_import_reads_originals_without_creating_l3(self):
        directory = self.project_root / 'import-fixture'
        shutil.copytree(ROOT / 'examples/artifact-import', directory)
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        for source in manifest['sources']:
            source['path'] = 'import-fixture/' + source['path']
        (directory / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
        result = run_cli(self.project_root, 'artifacts', 'import', '--manifest', str(directory / 'manifest.json'))
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'IMPORTED')
        self.assertEqual(report['facts']['steps']['kind'], 'OBSERVED')
        self.assertEqual(report['facts']['steps']['value'], 4)
        self.assertFalse((self.project_root / '.rds/state.sqlite3').exists())

    def test_project_reflect_reads_project_receipts_without_creating_l3(self):
        # The public runner provides genuine ledger receipts; reflect must use
        # those records through its ProjectStore branch, without an L3 mirror.
        for argv in (('project', 'create', '--manifest', str(self.project_root / 'control.json')),
                     ('project', 'execute', '--id', 'control')):
            result = run_cli(self.project_root, *argv)
            self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(self.project_root, 'meta', 'reflect')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report['auto_apply'])
        self.assertEqual(report['proposed_rules_count'], 0)
        self.assertFalse((self.project_root / '.rds/state.sqlite3').exists())

    def test_project_rule_replay_adoption_and_rollback_do_not_create_l3(self):
        directory = self.project_root / 'rsi-fixture'
        shutil.copytree(ROOT / 'examples/rsi', directory)
        graph, rule, cases = (str(directory / name) for name in ('base-graph.json', 'candidate-rule.json', 'cases.json'))
        evaluation = str(directory / 'evaluation.json')
        original = Path(graph).read_bytes()
        result = run_cli(self.project_root, 'meta', 'evaluate-rule', '--rule', rule,
                         '--graph', graph, '--cases', cases, '--output', evaluation)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'ACCEPTABLE_REGRESSION_CHANGE')
        self.assertEqual(report['cases_evaluated'], 4)
        self.assertTrue(report['adoption_eligible'])
        self.assertFalse((self.project_root / '.rds/state.sqlite3').exists())
        result = run_cli(self.project_root, 'meta', 'apply-rule', '--rule', rule, '--graph', graph,
                         '--cases', cases, '--evaluation', evaluation, '--force')
        self.assertEqual(result.returncode, 0, result.stderr)
        adopted = json.loads(result.stdout)
        self.assertEqual(adopted['status'], 'APPLIED')
        self.assertNotEqual(Path(graph).read_bytes(), original)
        self.assertFalse((self.project_root / '.rds/state.sqlite3').exists())
        result = run_cli(self.project_root, 'meta', 'rollback-rule', '--graph', graph, '--record', adopted['record_path'])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(Path(graph).read_bytes(), original)
        self.assertFalse((self.project_root / '.rds/state.sqlite3').exists())

    def test_hint_command_is_runnable_and_empty_root_message_unchanged(self):
        hint = run_cli(self.project_root, "decide", "--run", "control").stderr.splitlines()
        runnable = [line for line in hint if line.startswith("[RDS-HINT] ")][0].replace("[RDS-HINT] ", "")
        tokens = shlex.split(runnable)
        self.assertEqual(tokens[0], "python")
        self.assertEqual(tokens[1], "-B")
        result = subprocess.run([sys.executable, "-B", *tokens[2:]], cwd=ROOT, capture_output=True,
                                encoding="utf-8", timeout=60, env=dict(os.environ, RDS_USAGE_LOG="0"))
        self.assertEqual(result.returncode, 0, result.stderr)
        with tempfile.TemporaryDirectory() as empty:
            result = run_cli(Path(empty), "decide", "--run", "x")
            self.assertEqual(result.returncode, 1)
            self.assertIn("[RDS-REJECT] RDS is not initialized", result.stderr)
            self.assertNotIn("project ledger", result.stderr)

    def test_project_kind_checkpoint_stays_reachable(self):
        result = run_cli(self.project_root, "checkpoint", "save", "--kind", "project", "--id", "x")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('"status": "SAVED"', result.stdout)
        result = run_cli(self.project_root, "checkpoint", "restore", "--kind", "auto", "--id", "missing")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("project ledger", result.stderr)

    def test_l3_initialized_root_is_unaffected(self):
        import shutil
        reference = Path(self.temp.name) / "ref"
        shutil.copytree(ROOT / "examples" / "reference-run", reference)
        result = run_cli(reference, "init", "--contract", str(reference / "contract.json"))
        self.assertEqual(result.returncode, 0, result.stderr)
        result = run_cli(reference, "decide", "--run", "x")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("project ledger", result.stderr)


if __name__ == "__main__":
    unittest.main()
