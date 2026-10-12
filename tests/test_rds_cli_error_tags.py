"""Gate rejections and internal/state faults stay distinguishable on stderr (#59)."""
import contextlib
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import rds_cli


def run_cli(root, *argv):
    environment = dict(os.environ, RDS_USAGE_LOG="0")
    return subprocess.run([sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"), "--root", str(root), *argv],
                          cwd=ROOT, capture_output=True, encoding="utf-8", timeout=30, env=environment)


class CLIErrorTagTests(unittest.TestCase):
    def test_observation_classification_cannot_be_reached_by_spelling(self):
        # Section 6.3: commands and aliases are normalized first, so no accepted
        # spelling of an observation command can drift into the logged path,
        # and no writing command can be classified as observation by accident.
        observation = (['--version'], ['--help'], ['-h'], ['--root', '.', '--version'],
                       ['status'], ['status', '--brief'], ['status', '--digest'],
                       ['--root', '.', 'status'], ['stat'])
        for argv in observation:
            with self.subTest(argv=argv):
                self.assertTrue(rds_cli.observation_argv(argv), argv)
        logged = (['--root'], ['status', '--json'], ['status', '--days', '2'],
                  ['usage', '--days', '2'], ['formal', 'rules'], ['advise', '--help'],
                  ['project', 'discover'],
                  [], ['--version', '--help'], ['status', '--brief', '--json'])
        for argv in logged:
            with self.subTest(argv=argv):
                self.assertFalse(rds_cli.observation_argv(argv), argv)

    def test_gate_rejection_keeps_reject_tag_and_exit_code(self):
        with tempfile.TemporaryDirectory() as raw:
            result = run_cli(Path(raw), "status", "--brief")
            self.assertEqual(result.returncode, 1)
            self.assertIn("[RDS-REJECT] RDS is not initialized", result.stderr)
            self.assertNotIn("[RDS-ERROR]", result.stderr)
            self.assertFalse((Path(raw) / ".rds").exists())

    def test_unique_observation_option_prefixes_match_the_real_parser(self):
        for option in ('--version', '--help'):
            for length in range(3, len(option) + 1):
                argv = [option[:length]]
                with self.subTest(argv=argv):
                    with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as caught:
                        rds_cli.parser().parse_args(argv)
                    self.assertEqual(caught.exception.code, 0)
                    self.assertTrue(rds_cli.observation_argv(argv), argv)
        for option in ('--brief', '--digest'):
            for length in range(3, len(option) + 1):
                argv = ['--roo', '.', 'stat', option[:length]]
                with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                    parsed = rds_cli.parser().parse_args(argv)
                    self.assertEqual(parsed.command, 'status')
                    self.assertTrue(parsed.brief)
                    self.assertTrue(rds_cli.observation_argv(argv), argv)

    def test_invalid_prefixes_and_non_observation_help_stay_logged(self):
        for argv in (['--versx'], ['--ver=bad'], ['--ver', '--help'],
                     ['status', '--brieful'], ['status', '--b=true'],
                     ['status', '--h'], ['project', 'status', '--b'],
                     ['project', 'discov'], ['s', '--b']):
            with self.subTest(argv=argv):
                self.assertFalse(rds_cli.observation_argv(argv), argv)

    def test_an_ambiguous_option_prefix_uses_the_logged_dispatcher(self):
        entry = rds_cli.parser()
        commands = next(a for a in entry._actions if isinstance(a, rds_cli.argparse._SubParsersAction))
        commands.choices['status'].add_argument('--budget')
        argv = ['status', '--b']
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            entry.parse_args(argv)
        self.assertEqual(caught.exception.code, 2)
        with mock.patch.object(rds_cli, 'parser', return_value=entry), \
                mock.patch.object(sys, 'argv', ['rds_cli.py', *argv]), \
                mock.patch('rds_usage.run_logged', return_value=17) as logged:
            self.assertFalse(rds_cli.observation_argv(argv))
            self.assertEqual(rds_cli.main(), 17)
            logged.assert_called_once()
            self.assertEqual(logged.call_args.args[1], argv)

    def test_malformed_user_specs_stay_rejections(self):
        # Unvalidated user JSON reaches _main as KeyError/TypeError; the caller must repair it.
        for content in ("5", "null", '{"budget": {}}'):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as raw:
                contract = Path(raw) / "contract.json"
                contract.write_text(content, encoding="utf-8")
                result = run_cli(Path(raw) / "project", "init", "--contract", str(contract))
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stderr.startswith("[RDS-REJECT] "), result.stderr)
                self.assertNotIn("[RDS-ERROR]", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_corrupt_state_database_is_reported_as_error_not_rejection(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / ".rds").mkdir()
            database = root / ".rds/state.sqlite3"
            corrupt = b"synthetic corruption: this is not a sqlite database\n"
            database.write_bytes(corrupt)
            result = run_cli(root, "status", "--brief")
            self.assertEqual(result.returncode, 1)
            self.assertNotIn("[RDS-REJECT]", result.stderr)
            self.assertIn("[RDS-ERROR] DatabaseError: file is not a database", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertEqual(result.stderr.count("\n"), 1)
            self.assertEqual(database.read_bytes(), corrupt)

    def test_handler_maps_each_caught_exception_class(self):
        cases = [
            (ValueError("synthetic gate refusal"), "[RDS-REJECT] synthetic gate refusal"),
            (FileNotFoundError("synthetic missing input"), "[RDS-REJECT] synthetic missing input"),
            (KeyError("missing_field"), "[RDS-REJECT] 'missing_field'"),
            (TypeError("synthetic shape"), "[RDS-REJECT] synthetic shape"),
            (RecursionError("synthetic depth"), "[RDS-REJECT] synthetic depth"),
            (SyntaxError("synthetic syntax"), "[RDS-REJECT] synthetic syntax"),
            (ImportError("synthetic dependency"), "[RDS-ERROR] ImportError: synthetic dependency"),
            (sqlite3.OperationalError("synthetic lock"), "[RDS-ERROR] OperationalError: synthetic lock"),
            (subprocess.SubprocessError("synthetic spawn"), "[RDS-ERROR] SubprocessError: synthetic spawn"),
        ]
        for exc, expected in cases:
            with self.subTest(exc=type(exc).__name__), tempfile.TemporaryDirectory() as raw:
                stderr = io.StringIO()
                with mock.patch.object(sys, "argv", ["rds_cli.py", "--root", raw, "status", "--brief"]), \
                        mock.patch.object(rds_cli, "cmd_status", side_effect=exc), \
                        contextlib.redirect_stderr(stderr):
                    code = rds_cli._main()
                self.assertEqual(code, 1)
                self.assertEqual(stderr.getvalue(), expected + "\n")

    def test_uncaught_exception_classes_are_not_swallowed(self):
        with tempfile.TemporaryDirectory() as raw:
            with mock.patch.object(sys, "argv", ["rds_cli.py", "--root", raw, "status", "--brief"]), \
                    mock.patch.object(rds_cli, "cmd_status", side_effect=AttributeError("synthetic")):
                with self.assertRaises(AttributeError):
                    rds_cli._main()


if __name__ == "__main__":
    unittest.main()
