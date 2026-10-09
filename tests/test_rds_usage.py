"""Local-day counts, concurrent processes, privacy and transparent log failures."""
from concurrent.futures import ThreadPoolExecutor
import argparse
from contextlib import closing, redirect_stderr
from datetime import datetime
import json
import io
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from usage_cli_fixture import attach_usage_diagnostics, caller_commit_reader, cold_commit_handoff, count_diagnostics, dual_sqlite_wait, ledger_snapshot, paused_schema, phase_diagnostics, run_cli, slow_schema_commits, wait_marker

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import rds_usage as usage


class SandboxUsageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.root = self.folder / 'project with spaces'
        self.root.mkdir()
        self.state = self.folder / 'state'
        self.default = self.state / 'ResearchDirectionSelector' / 'cli-usage.sqlite3'
        self.default.parent.mkdir(parents=True)
        self.fallback = self.root / '.rds/usage/cli-usage.sqlite3'
        env = {k: v for k, v in os.environ.items() if k not in {'RDS_USAGE_DB', 'XDG_STATE_HOME', 'LOCALAPPDATA'}}
        env.update(LOCALAPPDATA=str(self.state))
        environment = patch.dict(os.environ, env, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        self.addCleanup(setattr, usage, '_last_error', None)

    def cli(self, *argv, cwd=None):
        return subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), *argv],
            cwd=cwd or self.root, capture_output=True, text=True, encoding='utf-8', timeout=30)

    def readonly_default(self):
        # Actual filesystem/SQLite denial, also on Windows where chmod sets
        # the read-only file attribute. No fake success or SQLite result.
        usage.run_logged(lambda: 0, ['status'], 'test')
        self.default.chmod(0o444)
        self.addCleanup(self.default.chmod, 0o666)
        try:
            with closing(sqlite3.connect(self.default)) as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute("INSERT INTO calls (command,mode,version,started,day) VALUES ('status','command','test',0,'2000-01-01')")
        except sqlite3.OperationalError as exc:
            self.assertEqual(exc.sqlite_errorcode & 255, sqlite3.SQLITE_READONLY)
        else:
            self.skipTest('This account bypasses filesystem read-only protection')

    def rows(self, path):
        with closing(sqlite3.connect(path)) as db:
            return db.execute('SELECT command,mode,exit_code FROM calls ORDER BY id').fetchall()

    def test_real_readonly_default_help_and_report_share_project_log(self):
        self.readonly_default()
        help_result = self.cli('--root', str(self.root), 'advise', '--help')
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn('usage:', help_result.stdout)
        self.assertNotIn('RDS-USAGE-DEGRADED', help_result.stderr)
        self.assertEqual(self.rows(self.fallback), [('advise', 'help', 0)])
        report_result = self.cli('usage', '--root=' + str(self.root), '--days', '1', '--json')
        self.assertEqual(report_result.returncode, 0, report_result.stderr)
        report = json.loads(report_result.stdout)
        self.assertEqual(Path(report['log_path']).resolve(), self.fallback.resolve())
        self.assertEqual(report['logging'], 'ENABLED')
        self.assertEqual(report['total_calls'], 2)
        self.assertEqual(self.rows(self.fallback), [('advise', 'help', 0), ('usage', 'command', 0)])
        self.assertEqual(self.rows(self.default), [('status', 'command', 0)])
        self.assertFalse((self.root / '.rds/ledger.sqlite3').exists())

    def test_accepted_root_spellings_share_selected_log_when_cwd_differs(self):
        import rds_cli
        self.readonly_default()
        cwd = self.folder / 'different working directory'
        cwd.mkdir()
        prefixes = (['--root', str(self.root)], ['--roo', str(self.root)],
                    ['-w' + str(self.root)], ['--workspace=' + str(self.root)],
                    ['--roo=' + str(self.root)], ['-d' + str(self.root)])
        help_stdout = None
        for index, prefix in enumerate(prefixes):
            with self.subTest(prefix=prefix):
                namespace = rds_cli.parser().parse_args([*prefix, 'usage', '--json'])
                self.assertEqual(Path(namespace.root).resolve(), self.root.resolve())
                help_result = self.cli(*prefix, 'advise', '--help', cwd=cwd)
                self.assertEqual(help_result.returncode, 0, help_result.stderr)
                self.assertEqual(help_result.stderr, '')
                if help_stdout is None:
                    help_stdout = help_result.stdout
                self.assertEqual(help_result.stdout, help_stdout)
                result = self.cli(*prefix, 'usage', '--days', '1', '--json', cwd=cwd)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, '')
                report = json.loads(result.stdout)
                self.assertEqual(Path(report['log_path']).resolve(), self.fallback.resolve())
                self.assertEqual(report['total_calls'], (index + 1) * 2)
                self.assertEqual(report['logging'], 'ENABLED')
                self.assertFalse((cwd / '.rds').exists())
        self.assertEqual(len(self.rows(self.fallback)), len(prefixes) * 2)
        self.assertTrue(all(row[2] == 0 for row in self.rows(self.fallback)))

    def test_root_spellings_normalize_without_consuming_child_arguments(self):
        import rds_cli
        entry = rds_cli.parser()
        for prefix in (['--roo', str(self.root)], ['-w' + str(self.root)],
                       ['--workspace=' + str(self.root)]):
            for child in (['--', 'python', '--roo', 'child'], ['python', '-wchild']):
                with self.subTest(prefix=prefix, child=child):
                    normalized = entry.normalize_args([*prefix, 'exec', *child], quiet=True)
                    self.assertEqual(normalized[:2], ['--root', str(self.root)])
                    expected = child[1:] if child[:1] == ['--'] else child
                    self.assertEqual(normalized[normalized.index('--') + 1:], expected)
        # The same abbreviation can belong to a child command's own option.
        parsed = entry.parse_args(['reject', '--rout', 'candidate', '--reason', 'reason', '--evidence', 'file'])
        self.assertEqual(parsed.route, 'candidate')

    def test_real_readonly_default_uses_aliases_cwd_and_preserves_errors(self):
        self.readonly_default()
        for alias in ('--workspace', '--project-root', '-w', '-d', '--dir'):
            with self.subTest(alias=alias):
                result = self.cli('advise', alias, str(self.root), '--help')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('RDS-USAGE-DEGRADED', result.stderr)
        result = self.cli('usage', '--days', '0')
        self.assertEqual(result.returncode, 1)
        self.assertIn('--days', result.stderr)
        self.assertNotIn('RDS-USAGE-DEGRADED', result.stderr)
        rows = self.rows(self.fallback)
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[-1], ('usage', 'command', 1))
        self.assertTrue(all(row[2] is not None for row in rows))

    def test_root_inspection_respects_explicit_and_implicit_child_boundaries(self):
        import rds_cli
        other = str(self.folder / 'child-root')
        parser = rds_cli.parser()
        for tail in (['--', 'python', '--root', other], ['python', '--root', other]):
            with self.subTest(tail=tail):
                tokens = parser.normalize_args(['exec', '-w', str(self.root), *tail], quiet=True)
                self.assertEqual(tokens[:2], ['--root', str(self.root)])
                self.assertEqual(tokens[tokens.index('--') + 1:], ['python', '--root', other])
        # A root-looking option value is data, even before the child boundary.
        tokens = parser.normalize_args(['exec', '--name', '--root', '--', 'python'], quiet=True)
        self.assertNotEqual(tokens[:1], ['--root'])
        self.assertFalse((self.folder / 'child-root').exists())

    def test_permission_denied_home_falls_back_and_keeps_exception(self):
        environment = dict(os.environ)
        environment.pop('LOCALAPPDATA')
        mkdir = Path.mkdir
        denied = self.folder / 'home/.local/state/ResearchDirectionSelector'
        def restricted(path, *args, **kwargs):
            if path == denied:
                raise PermissionError('synthetic workspace-write HOME denial')
            return mkdir(path, *args, **kwargs)
        stderr = io.StringIO()
        with patch.dict(os.environ, environment, clear=True), patch.object(Path, 'home', return_value=self.folder / 'home'), \
                patch.object(Path, 'mkdir', restricted), redirect_stderr(stderr):
            self.assertEqual(usage.run_logged(lambda: 7, ['status'], 'test', root=self.root), 7)
            with self.assertRaisesRegex(RuntimeError, 'original failure'):
                usage.run_logged(lambda: (_ for _ in ()).throw(RuntimeError('original failure')),
                    ['status'], 'test', root=self.root)
        self.assertEqual(stderr.getvalue(), '')
        self.assertEqual(self.rows(self.fallback), [('status', 'command', 7), ('status', 'command', 1)])
        self.assertEqual(usage.log_path(), self.default)  # Invocation context restored.

    def test_explicit_locations_and_nonpermission_failures_do_not_fallback(self):
        self.readonly_default()
        for settings in ({'RDS_USAGE_DB': str(self.default)},):
            with self.subTest(settings=settings), patch.dict(os.environ, settings):
                result = self.cli('-w', str(self.root), 'advise', '--help')
                self.assertEqual(result.returncode, 0)
                self.assertIn('RDS-USAGE-DEGRADED', result.stderr)
                self.assertFalse(self.fallback.exists())
        self.default.chmod(0o666)
        self.default.write_bytes(b'not a sqlite database')
        result = self.cli('-w', str(self.root), 'advise', '--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('SQLITE_NOTADB', result.stderr)
        self.assertFalse(self.fallback.exists())

    def test_unrecognized_xdg_setting_keeps_existing_default_semantics(self):
        with patch.dict(os.environ, {'XDG_STATE_HOME': str(self.folder / 'xdg')}):
            self.assertEqual(usage.log_path(), self.default)
            self.readonly_default()
            result = self.cli('-w', str(self.root), 'advise', '--help')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('RDS-USAGE-DEGRADED', result.stderr)
            self.assertEqual(self.rows(self.fallback), [('advise', 'help', 0)])
            self.assertFalse((self.folder / 'xdg').exists())

    def test_invalid_or_duplicate_root_does_not_create_unintended_directories(self):
        self.readonly_default()
        other = self.folder / 'unintended root'
        for argv in (['--root', str(other), '-w', str(self.root), 'advise', '--help'],
                     ['--roo', str(other), '-w' + str(self.root), 'advise', '--help'],
                     ['-w' + str(other), '--workspace=' + str(self.root), 'advise', '--help'],
                     ['--roo'],
                     ['advise', '--root']):
            with self.subTest(argv=argv):
                result = self.cli(*argv)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertIn('RDS-USAGE-DEGRADED', result.stderr)
                self.assertFalse(other.exists())
                self.assertFalse(self.fallback.exists())

    def test_option_looking_root_value_does_not_create_usage_files(self):
        self.readonly_default()
        result = self.cli('--root', '--help', 'advise', '--help')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('expected one argument', result.stderr)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_option_looking_root_aliases_and_placements_leave_cwd_untouched(self):
        self.readonly_default()
        for index, argv in enumerate((
                ['advise', '--workspace', '--help', '--help'],
                ['-w', '--help', 'advise', '--help'],
                ['--project-root=--help', 'advise', '--help'],
                ['--dir', '--version', 'advise', '--help'],
                ['--roo', '--help', 'advise', '--help'],
                ['--roo=--help', 'advise', '--help'],
                ['-w--help', 'advise', '--help'],
                ['--root', '--', 'advise', '--help'])):
            with self.subTest(argv=argv):
                cwd = self.folder / ('invalid root case ' + str(index))
                cwd.mkdir()
                result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), *argv],
                    cwd=cwd, capture_output=True, text=True, encoding='utf-8', timeout=30)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(list(cwd.iterdir()), [])

    def test_explicit_path_named_like_an_option_remains_a_valid_root(self):
        self.readonly_default()
        target = self.root / '--help'
        result = self.cli('--root', str(target), 'advise', '--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('RDS-USAGE-DEGRADED', result.stderr)
        self.assertEqual(self.rows(target / '.rds/usage/cli-usage.sqlite3'), [('advise', 'help', 0)])

    def test_real_exec_help_does_not_use_child_root_options(self):
        self.readonly_default()
        other = str(self.folder / 'child root')
        for child in (['--', 'python', '--root', other], ['python', '--root', other]):
            with self.subTest(child=child):
                result = self.cli('exec', '-w', str(self.root), '--help', *child)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('RDS-USAGE-DEGRADED', result.stderr)
                self.assertFalse(Path(other).exists())
        self.assertEqual(self.rows(self.fallback), [('exec', 'help', 0), ('exec', 'help', 0)])

    def test_unavailable_fallback_keeps_degraded_result(self):
        self.readonly_default()
        (self.root / '.rds').write_text('synthetic unavailable root output')
        result = self.cli('-w', str(self.root), 'advise', '--help')
        self.assertEqual(result.returncode, 0)
        self.assertIn('RDS-USAGE-DEGRADED', result.stderr)
        self.assertIn('start record not confirmed', result.stderr)
        self.assertIn('usage:', result.stdout)

    def test_writable_default_stays_shared_and_explicit_file_wins(self):
        result = self.cli('-w', str(self.root), 'advise', '--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.rows(self.default), [('advise', 'help', 0)])
        self.assertFalse(self.fallback.exists())
        override = self.folder / 'explicit.sqlite3'
        with patch.dict(os.environ, {'RDS_USAGE_DB': str(override), 'XDG_STATE_HOME': str(self.folder / 'xdg')}):
            result = self.cli('-w', str(self.root), 'advise', '--help')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(self.rows(override), [('advise', 'help', 0)])
        self.assertFalse((self.folder / 'xdg').exists())

    def test_invocation_pins_database_even_if_command_changes_environment(self):
        other = self.folder / 'changed.sqlite3'
        def command():
            os.environ['RDS_USAGE_DB'] = str(other)
            report = usage.summarize(days=1)
            self.assertEqual(report['log_path'], str(self.default))
            self.assertEqual(report['total_calls'], 1)
            return 7
        self.assertEqual(usage.run_logged(command, ['usage'], 'test', root=self.root), 7)
        self.assertEqual(self.rows(self.default), [('usage', 'command', 7)])
        self.assertFalse(other.exists())
        self.assertEqual(usage.log_path().resolve(), other.resolve())

    def test_finish_denial_never_updates_matching_row_in_fallback_database(self):
        with patch.dict(os.environ, {'RDS_USAGE_DB': str(self.fallback)}):
            usage.run_logged(lambda: 7, ['status'], 'test')
        warning = io.StringIO()
        def command():
            self.default.chmod(0o444)
            self.addCleanup(self.default.chmod, 0o666)
            return 9
        with redirect_stderr(warning):
            self.assertEqual(usage.run_logged(command, ['status'], 'test', root=self.root), 9)
        if 'finish logging failed' not in warning.getvalue():
            self.skipTest('This account bypasses filesystem read-only protection')
        self.assertEqual(self.rows(self.default), [('status', 'command', None)])
        self.assertEqual(self.rows(self.fallback), [('status', 'command', 7)])

    def test_busy_default_does_not_split_history_into_project_fallback(self):
        usage.run_logged(lambda: 0, ['status'], 'test')
        warning = io.StringIO()
        with closing(sqlite3.connect(self.default)) as db:
            db.execute('BEGIN IMMEDIATE')
            with redirect_stderr(warning):
                self.assertEqual(usage.run_logged(lambda: 7, ['status'], 'test', root=self.root), 7)
            db.rollback()
        self.assertIn('SQLITE_BUSY', warning.getvalue())
        self.assertFalse(self.fallback.exists())
        self.assertEqual(self.rows(self.default), [('status', 'command', 0)])

    def test_concurrent_fallback_cli_calls_keep_all_finished_records(self):
        self.readonly_default()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.cli('-w', str(self.root), 'advise', '--help'), range(12)))
        self.assertTrue(all(result.returncode == 0 for result in results), results)
        self.assertTrue(all('RDS-USAGE-DEGRADED' not in result.stderr for result in results), results)
        self.assertEqual(self.rows(self.fallback), [('advise', 'help', 0)] * 12)


class UsageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.path = self.folder / "calls.sqlite3"
        environment = patch.dict(os.environ, {"RDS_USAGE_DB": str(self.path)})
        environment.start()
        self.addCleanup(environment.stop)
        state = patch.object(usage, "_last_error", None)
        state.start()
        self.addCleanup(state.stop)

    def cli(self, *args):
        return run_cli([sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"), *args],
                       self.folder, self.path)

    def test_cli_fixture_preserves_both_real_logging_waits(self):
        warmup = self.cli("--version")
        self.assertEqual(warmup.returncode, 0)
        began = time.monotonic()
        with dual_sqlite_wait(self.folder, self.path) as probe:
            with patch.dict(os.environ, probe["environment"]):
                result = self.cli("--version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, warmup.stdout)
        self.assertEqual(len(probe["intervals"]), 2)
        self.assertTrue(all(value >= 8.2 for value in probe["intervals"]), probe)
        self.assertGreater(time.monotonic() - began, 15)
        report = usage.summarize(days=1)
        self.assertEqual(report["total_calls"], 2)
        self.assertEqual(report["modes"], {"version": 2})
        self.assertEqual(report["daily"][0]["successful"], 2)
        self.assertEqual(report["daily"][0]["unfinished"], 0)
        self.assertTrue(all(row["exit_code"] == 0 for row in ledger_snapshot(self.path)["calls"]))

    def test_cli_fixture_watchdog_keeps_original_error_outputs_and_ledger(self):
        usage.run_logged(lambda: 0, ["status"], "test")
        before = ledger_snapshot(self.path)
        ready = self.folder / "hung-child-ready"
        script = ("import pathlib,sys,time; "
                  "print('synthetic stdout',flush=True); "
                  "print('synthetic stderr',file=sys.stderr,flush=True); "
                  "pathlib.Path(sys.argv[1]).write_text('ready'); "
                  "time.sleep(120)")
        command = [sys.executable, "-B", "-c", script, str(ready)]
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            run_cli(command, self.folder, self.path, watchdog=.2,
                    ready=lambda child: wait_marker(ready, child=child))
        error = caught.exception
        self.assertEqual(error.cmd, command)
        self.assertEqual(error.timeout, .2)
        self.assertIn("synthetic stdout", error.stdout)
        self.assertIn("synthetic stderr", error.stderr)
        diagnostics = json.loads(error.__notes__[0])
        self.assertTrue(diagnostics["fixture_watchdog"])
        self.assertNotEqual(diagnostics["returncode"], 0)
        self.assertEqual(diagnostics["ledger"], before)
        self.assertEqual(ledger_snapshot(self.path), before)
        from rds_project import _alive
        self.assertFalse(_alive(diagnostics["pid"]))

    def test_cli_fixture_does_not_convert_nonzero_to_success(self):
        command = [sys.executable, "-B", "-c",
                   "import sys; print('synthetic failure',file=sys.stderr); sys.exit(7)"]
        result = run_cli(command, self.folder, self.path)
        self.assertEqual(result.returncode, 7)
        self.assertIn("synthetic failure", result.stderr)

    def test_midnight_daily_counts_and_untracked_history(self):
        for moment, command, exit_code in ((datetime(2026, 9, 30, 23, 59), "formal", 0),
                                           (datetime(2026, 10, 1, 0, 1), "status", 1)):
            with patch.object(usage.time, "time", return_value=moment.astimezone().timestamp()):
                self.assertEqual(usage.run_logged(lambda: exit_code, [command], "test"), exit_code)
        result = usage.summarize(since="2026-09-29", until="2026-10-01")
        self.assertEqual([row["calls"] for row in result["daily"]], [None, 1, 1])
        self.assertEqual(result["total_calls"], 2)
        self.assertEqual(result["daily"][1]["successful"], 1)
        self.assertEqual(result["daily"][2]["failed"], 1)

    def test_help_and_argument_errors_are_counted_without_saving_payloads(self):
        secret = "private-prompt-and-token-never-log"
        def help_exit():
            raise SystemExit(0)
        def error_exit():
            raise SystemExit(2)
        with self.assertRaises(SystemExit):
            usage.run_logged(help_exit, ["--root", secret, "formal", "--help"], "test")
        with self.assertRaises(SystemExit):
            usage.run_logged(error_exit, [secret], "test")
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute("SELECT command,mode,exit_code FROM calls ORDER BY id").fetchall()
        self.assertEqual(rows, [("formal", "help", 0), ("other", "command", 2)])
        self.assertNotIn(secret, str(rows))

    def test_usage_labels_cover_every_public_top_level_command(self):
        import rds_cli
        groups = [action for action in rds_cli.parser()._actions
                  if isinstance(action, argparse._SubParsersAction)]
        self.assertEqual(len(groups), 1)
        self.assertEqual(set(groups[0].choices), usage.COMMANDS)
        for command in groups[0].choices:
            with self.subTest(command=command):
                self.assertEqual(usage._label([command]), (command, "command"))
                self.assertEqual(usage._label([command, "--help"]), (command, "help"))

    def test_real_host_hook_help_is_recorded_under_its_command(self):
        result = self.cli("host-hook", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("coverage", result.stdout)
        self.assertFalse((self.folder / ".rds").exists())
        with closing(sqlite3.connect(self.path)) as connection:
            rows = connection.execute(
                "SELECT command,mode,exit_code FROM calls ORDER BY id").fetchall()
        self.assertEqual(rows, [("host-hook", "help", 0)])

    def test_storage_failure_does_not_change_original_return_or_exception(self):
        with patch.dict(os.environ, {"RDS_USAGE_DB": str(self.folder)}):
            self.assertEqual(usage.run_logged(lambda: 7, ["status"], "test"), 7)
            with self.assertRaisesRegex(RuntimeError, "original error"):
                usage.run_logged(lambda: (_ for _ in ()).throw(RuntimeError("original error")), ["status"], "test")
        self.assertIsNotNone(usage._last_error)

    def test_rejected_start_rolls_back_tracking_with_call_and_recovers(self):
        with usage._database() as db:
            db.execute("CREATE TRIGGER reject_call BEFORE INSERT ON calls "
                       "BEGIN SELECT RAISE(ABORT, 'synthetic rejection'); END")
        warning = io.StringIO()
        with redirect_stderr(warning):
            self.assertEqual(usage.run_logged(lambda: 7, ["status"], "test"), 7)
        self.assertIn("start record not confirmed", warning.getvalue())
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM tracking").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM calls").fetchone()[0], 0)
            db.execute("DROP TRIGGER reject_call")
        self.assertEqual(usage.run_logged(lambda: 0, ["status"], "test"), 0)
        report = usage.summarize(days=2)
        self.assertEqual(report["total_calls"], 1)
        self.assertEqual(sum(r["unfinished"] for r in report["daily"]), 0)

    def test_concurrent_cli_processes_retain_all_calls(self):
        warmup = self.cli("--version")
        self.assertEqual(warmup.returncode, 0, count_diagnostics(self.path, [warmup]))
        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(lambda _: self.cli("--version"), range(12)))
        children = [warmup, *results]
        self.assertTrue(all(result.returncode == 0 for result in results), count_diagnostics(self.path, children))
        try:
            result = usage.summarize(days=1)
        except Exception as exc:
            exc.add_note(count_diagnostics(self.path, children))
            raise
        evidence = count_diagnostics(self.path, children, result)
        self.assertEqual(result["total_calls"], 13, evidence)
        self.assertEqual(result["modes"], {"version": 13}, evidence)
        self.assertEqual(result["daily"][0]["successful"], 13, evidence)
        self.assertEqual(result["daily"][0]["unfinished"], 0, evidence)
        self.assertTrue(all(row["exit_code"] == 0 for row in ledger_snapshot(self.path)["calls"]), evidence)

    def test_first_concurrent_invocations_retain_all_calls(self):
        self.assertFalse(self.path.exists())
        with ThreadPoolExecutor(max_workers=8) as workers:
            results = list(workers.map(lambda _: self.cli("--version"), range(12)))
        self.assertTrue(all(r.returncode == 0 for r in results), count_diagnostics(self.path, results))
        try:
            report = usage.summarize(days=1)
        except Exception as exc:
            exc.add_note(count_diagnostics(self.path, results))
            raise
        evidence = count_diagnostics(self.path, results, report)
        self.assertEqual(report['total_calls'], 12, evidence)
        self.assertEqual(report['daily'][0]['successful'], 12, evidence)
        self.assertEqual(report['daily'][0]['unfinished'], 0, evidence)
        self.assertTrue(all(row["exit_code"] == 0 for row in ledger_snapshot(self.path)["calls"]), evidence)

    def test_failed_schema_initialization_leaves_no_partial_tables_and_recovers(self):
        original = sqlite3.connect
        def reject_index(*args, **kwargs):
            connection = original(*args, **kwargs)
            connection.set_authorizer(lambda action, *_:
                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_INDEX else sqlite3.SQLITE_OK)
            return connection
        warning = io.StringIO()
        with patch("rds_usage.sqlite3.connect", side_effect=reject_index), redirect_stderr(warning):
            self.assertEqual(usage.run_logged(lambda: 7, ["status"], "test"), 7)
        self.assertIn("[RDS-USAGE-DEGRADED] start logging failed", warning.getvalue())
        with closing(original(self.path)) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])
        self.assertEqual(usage.run_logged(lambda: 0, ["status"], "test"), 0)
        report = usage.summarize(days=1)
        self.assertEqual(report["total_calls"], 1)
        self.assertEqual(report["daily"][0]["successful"], 1)

    def test_partial_existing_schema_preserves_rows_and_journal_mode(self):
        self.assertEqual(self.cli("--version").returncode, 0)
        with closing(sqlite3.connect(self.path)) as db:
            journal = db.execute("PRAGMA journal_mode").fetchone()[0]
            db.execute("DROP INDEX calls_day")
            db.commit()
        before = ledger_snapshot(self.path)["calls"]
        self.assertEqual(usage.run_logged(lambda: 7, ["status"], "test"), 7)
        after = ledger_snapshot(self.path)["calls"]
        self.assertEqual(after[:1], before)
        self.assertEqual(after[1]["exit_code"], 7)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("PRAGMA journal_mode").fetchone()[0], journal)
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall(), [("calls_day",)])

    def test_slow_schema_initialization_retains_first_concurrent_calls(self):
        # Three separate schema commits consume the ten-second start budget
        # under this declared slow-storage fixture. The cold schema/start
        # transaction keeps twelve calls within the same bounded budget.
        self.assertFalse(self.path.exists())
        with slow_schema_commits(self.folder, self.path) as probe:
            environment = {**os.environ, **probe["environment"]}
            completed, gate = {}, threading.Lock()

            def invoke(index):
                child = self.cli("--version")
                with gate:
                    completed[index] = child
                return child

            try:
                with patch.dict(os.environ, environment), ThreadPoolExecutor(max_workers=8) as workers:
                    children = list(workers.map(invoke, range(12)))
                delays = [json.loads(line) for path in probe["work"].glob("*.jsonl")
                          for line in path.read_text(encoding="utf-8").splitlines()]
                report = usage.summarize(days=1)
            except BaseException as exc:
                attach_usage_diagnostics(exc, self.path,
                                         [completed[index] for index in sorted(completed)], probe["work"])
                raise
            phases = phase_diagnostics(probe["work"], self.path,
                                       expected_pids=[child.fixture_timing["pid"] for child in children])
        evidence = (count_diagnostics(self.path, children, report)
                    + " schema=" + json.dumps(delays) + " phases=" + json.dumps(phases))
        self.assertTrue(all(child.returncode == 0 for child in children), evidence)
        self.assertEqual(report["total_calls"], 12, evidence)
        self.assertEqual(report["daily"][0]["successful"], 12, evidence)
        self.assertEqual(report["daily"][0]["unfinished"], 0, evidence)
        self.assertTrue(delays and all(row["writer_held"] for row in delays), evidence)
        self.assertEqual(phases["status"], "OBSERVED", evidence)

    def test_slow_schema_trace_preserves_real_busy_and_cli_result(self):
        # An external real writer is deliberately untraced: retain the exact
        # failing statement/PID, but never guess its holder from absent data.
        warmup = self.cli("--version")
        self.assertEqual(warmup.returncode, 0)
        before = ledger_snapshot(self.path)
        with slow_schema_commits(self.folder, self.path) as probe:
            with closing(sqlite3.connect(self.path, isolation_level=None)) as writer:
                writer.execute("BEGIN IMMEDIATE")
                with patch.dict(os.environ, {**os.environ, **probe["environment"]}):
                    child = self.cli("--version")
            phases = phase_diagnostics(probe["work"], self.path,
                                       expected_pids=[child.fixture_timing["pid"]])
        evidence = count_diagnostics(self.path, [child]) + " phases=" + json.dumps(phases)
        self.assertEqual(child.returncode, 0, evidence)
        self.assertEqual(child.stdout, warmup.stdout, evidence)
        self.assertIn("start logging failed (OperationalError/SQLITE_BUSY)", child.stderr, evidence)
        self.assertEqual(ledger_snapshot(self.path), before, evidence)
        self.assertEqual(phases["status"], "OBSERVED", evidence)
        self.assertEqual(len(phases["errors"]), 1, evidence)
        error = phases["errors"][0]
        self.assertEqual(error["pid"], child.fixture_timing["pid"], evidence)
        self.assertEqual(error["operation"], "BEGIN IMMEDIATE", evidence)
        self.assertEqual(error["sqlite_errorname"], "SQLITE_BUSY", evidence)
        self.assertEqual(error["observed_writers_at_error"], "UNKNOWN", evidence)
        self.assertEqual(error["overlapping_observed_writers"], [], evidence)
        self.assertGreaterEqual(error["seconds"], 9, evidence)
        self.assertEqual(phases["physical_database"]["bytes"], self.path.stat().st_size, evidence)

    def test_cold_start_does_not_reacquire_writer_after_recordable_commit(self):
        with cold_commit_handoff(self.folder, self.path) as probe:
            with patch.dict(os.environ, {**os.environ, **probe["environment"]}):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(self.cli, "--version")
                    try:
                        wait_marker(probe["work"] / "handoff-ready")
                        with closing(sqlite3.connect(self.path, isolation_level=None)) as writer:
                            writer.execute("BEGIN IMMEDIATE")
                            (probe["work"] / "handoff-release").touch()
                            time.sleep(1)
                            writer.rollback()
                    finally:
                        (probe["work"] / "handoff-release").touch()
                    child = pending.result(timeout=45)
            phases = phase_diagnostics(probe["work"], self.path,
                                       expected_pids=[child.fixture_timing["pid"]])
        report = usage.summarize(days=1)
        evidence = count_diagnostics(self.path, [child], report) + " phases=" + json.dumps(phases)
        self.assertEqual(child.returncode, 0, evidence)
        self.assertEqual(report["total_calls"], 1, evidence)
        self.assertEqual(report["daily"][0]["successful"], 1, evidence)
        self.assertEqual(report["daily"][0]["unfinished"], 0, evidence)
        self.assertEqual(child.stderr, "", evidence)
        self.assertEqual(phases["status"], "OBSERVED", evidence)
        start_writers = [row for row in phases["writer_intervals"] if row["connection"] == 1]
        self.assertEqual(len(start_writers), 1, evidence)
        commits = [event for event in phases["transaction_events"]
                   if event["operation"] == "COMMIT" and event["connection"] == 1]
        self.assertTrue(commits, evidence)
        self.assertTrue(all(event["deadline"] is not None for event in commits), evidence)

    def caller_commit_control(self, *, expire):
        warmup = self.cli("--version")
        self.assertEqual(warmup.returncode, 0, warmup.stderr)
        before = ledger_snapshot(self.path)
        with caller_commit_reader(self.folder, self.path, expire=expire) as probe:
            with patch.dict(os.environ, {**os.environ, **probe["environment"]}):
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pending = pool.submit(self.cli, "--version")
                    try:
                        wait_marker(probe["work"] / "caller-ready")
                        with closing(sqlite3.connect(self.path, isolation_level=None)) as reader:
                            reader.execute("BEGIN")
                            reader.execute("SELECT * FROM calls").fetchall()
                            (probe["work"] / "caller-release").touch()
                            if expire:
                                child = pending.result(timeout=45)
                            else:
                                time.sleep(.3)
                            reader.rollback()
                    finally:
                        (probe["work"] / "caller-release").touch()
                    child = pending.result(timeout=45)
            phases = phase_diagnostics(probe["work"], self.path,
                                       expected_pids=[child.fixture_timing["pid"]])
        evidence = count_diagnostics(self.path, [child]) + " phases=" + json.dumps(phases)
        self.assertEqual(child.returncode, 0, evidence)
        self.assertEqual(child.stdout, warmup.stdout, evidence)
        self.assertEqual(phases["status"], "OBSERVED", evidence)
        if expire:
            self.assertEqual(ledger_snapshot(self.path), before, evidence)
            self.assertIn("start logging failed (OperationalError/SQLITE_BUSY)", child.stderr, evidence)
            self.assertEqual(len(phases["errors"]), 1, evidence)
            error = phases["errors"][0]
            self.assertEqual(error["operation"], "COMMIT", evidence)
            self.assertEqual(error["busy_timeout_ms"], 0, evidence)
            self.assertLessEqual(error["remaining_seconds"], 0, evidence)
            self.assertTrue(error["in_transaction"], evidence)
            self.assertEqual(error["transaction_work"], ["caller"], evidence)
            # The known fixture's parent reader is intentionally not traced;
            # the observer cannot invent its identity from writer intervals.
            self.assertEqual(error["observed_writers_at_error"][0]["pid"], child.fixture_timing["pid"], evidence)
        else:
            report = usage.summarize(days=1)
            self.assertEqual(report["total_calls"], 2, evidence)
            self.assertEqual(report["daily"][0]["successful"], 2, evidence)
            self.assertEqual(report["daily"][0]["unfinished"], 0, evidence)
            self.assertEqual(child.stderr, "", evidence)
            self.assertEqual(phases["errors"], [], evidence)
            committed = [event for event in phases["transaction_events"]
                         if event["operation"] == "COMMIT" and event["stage"] == "after"]
            self.assertTrue(any(event["seconds"] >= .2 and event["remaining_seconds"] > 0 for event in committed), evidence)

    def test_transient_reader_commit_retains_start_with_original_deadline(self):
        self.caller_commit_control(expire=False)

    def test_expired_commit_reader_preserves_loss_without_new_deadline(self):
        self.caller_commit_control(expire=True)

    def phase_reader_fixture(self):
        work = self.folder / "reader"
        (work / "phases").mkdir(parents=True)
        self.path.touch()
        records = [{"pid": 99, "connection": 1, "operation": operation,
                    "stage": stage, "monotonic": clock, "in_transaction": False}
                   for operation, stage, clock in [("CONNECT", "after", 1),
                       ("CLOSE", "before", 2), ("CLOSE", "after", 3)]]
        source = work / "phases/99.jsonl"
        source.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        return work, source, records

    def test_phase_reader_retains_valid_records_on_parse_and_shape_errors(self):
        work, source, records = self.phase_reader_fixture()
        original = source.read_text(encoding="utf-8")
        self.assertEqual(phase_diagnostics(work, self.path, expected_pids=[99])["status"], "OBSERVED")
        for damaged in ['{"pid":', 'null', '{}',
                        json.dumps({**records[0], "monotonic": float("nan")}),
                        json.dumps({**records[0], "stage": "error", "seconds": -1})]:
            with self.subTest(damaged=damaged):
                source.write_text(original + damaged + "\n", encoding="utf-8")
                evidence = phase_diagnostics(work, self.path, expected_pids=[99])
                self.assertEqual(evidence["status"], "UNKNOWN")
                self.assertEqual(evidence["event_count"], 3)
                self.assertEqual(evidence["missing_pids"], [])
                self.assertEqual(len(evidence["read_errors"]), 1)
                self.assertEqual(evidence["read_errors"][0]["line"], 4)
                self.assertEqual(evidence["read_errors"][0]["path"], str(source))

    def test_phase_reader_retains_valid_records_with_unreadable_and_missing_trace(self):
        work, source, _ = self.phase_reader_fixture()
        (work / "phases/100.jsonl").write_bytes(b"\xff")
        evidence = phase_diagnostics(work, self.path, expected_pids=[99, 100])
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertEqual(evidence["event_count"], 3)
        self.assertEqual(evidence["missing_pids"], [100])
        self.assertEqual(evidence["read_errors"][0]["error"], "UnicodeDecodeError")
        with patch.object(Path, "read_text", side_effect=PermissionError("trace unreadable")):
            evidence = phase_diagnostics(work, self.path, expected_pids=[99])
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertEqual(evidence["missing_pids"], [99])
        self.assertTrue(all(error["error"] == "PermissionError" for error in evidence["read_errors"]))
        evidence = phase_diagnostics(work / "missing", self.path, expected_pids=[99])
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertEqual(evidence["read_errors"][0]["error"], "FileNotFoundError")

    def test_phase_reader_incomplete_connection_is_unknown(self):
        work, source, records = self.phase_reader_fixture()
        records = [records[0], {**records[0], "operation": "BEGIN IMMEDIATE", "stage": "before", "monotonic": 2},
                   {**records[0], "operation": "BEGIN IMMEDIATE", "monotonic": 3, "in_transaction": True}]
        source.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
        evidence = phase_diagnostics(work, self.path, expected_pids=[99])
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertEqual(len(evidence["incomplete_connections"]), 1)
        self.assertEqual(evidence["unclosed_writers"], [{"pid": 99, "connection": 1, "acquired": 3}])

    def test_slow_schema_autocommit_records_native_writer_acquisition(self):
        # Exercise the old one-statement autocommit route, not the current
        # production explicit schema transaction; both need honest coverage.
        with slow_schema_commits(self.folder, self.path) as probe:
            environment = {**os.environ, **probe["environment"]}
            command = [sys.executable, "-B", "-c",
                "import os,sqlite3; db=sqlite3.connect(os.environ['RDS_USAGE_DB'],isolation_level=None); "
                "db.execute('CREATE TABLE observed (id INTEGER)'); db.close()"]
            with patch.dict(os.environ, environment):
                child = run_cli(command, self.folder, self.path)
            phases = phase_diagnostics(probe["work"], self.path, expected_pids=[child.fixture_timing["pid"]])
        self.assertEqual(child.returncode, 0, child.stderr)
        self.assertEqual(phases["status"], "OBSERVED", phases)
        self.assertEqual(len(phases["writer_intervals"]), 1, phases)
        writer = phases["writer_intervals"][0]
        self.assertEqual(writer["pid"], child.fixture_timing["pid"], phases)
        self.assertGreaterEqual(writer["released"] - writer["acquired"], 3.6, phases)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM observed").fetchone()[0], 0)

    def test_slow_schema_map_exception_retains_original_identity(self):
        original = subprocess.TimeoutExpired(["synthetic-child"], 60)
        original.add_note("original watchdog evidence")
        with patch.object(self, "cli", side_effect=original):
            with self.assertRaises(subprocess.TimeoutExpired) as raised:
                self.test_slow_schema_initialization_retains_first_concurrent_calls()
        self.assertIs(raised.exception, original)
        self.assertEqual(original.__notes__[0], "original watchdog evidence")
        self.assertIn('"status": "UNKNOWN"', original.__notes__[-1])

    def test_usage_failure_annotation_cannot_replace_original_exception(self):
        original = sqlite3.OperationalError("primary reader failed")
        original.add_note("original reader evidence")
        with patch("usage_cli_fixture.phase_diagnostics", side_effect=PermissionError("observer failed")):
            attach_usage_diagnostics(original, self.path, [], self.folder)
        self.assertEqual(original.__notes__[0], "original reader evidence")
        evidence = json.loads(original.__notes__[-1])
        self.assertEqual(evidence["status"], "UNKNOWN")
        self.assertEqual(evidence["diagnostic_error"], "PermissionError")
        self.assertEqual(str(original), "primary reader failed")

    def test_paused_schema_preparation_does_not_drop_concurrent_cli_starts(self):
        # One real warmup pauses after the cold schema/start commit. The other
        # twelve real invocations must complete while it is still paused;
        # Python work after that commit must not reserve their writer.
        with paused_schema(self.folder, self.path) as probe:
            environment = {**os.environ, **probe["environment"]}
            with ThreadPoolExecutor(max_workers=1) as warmup_pool:
                warmup_future = warmup_pool.submit(
                    subprocess.run,
                    [sys.executable, "-B", str(ROOT / "scripts/rds_cli.py"), "--version"],
                    cwd=self.folder, env=environment, capture_output=True, text=True, timeout=60)
                try:
                    wait_marker(probe["work"] / "ready")
                    with ThreadPoolExecutor(max_workers=8) as workers:
                        results = list(workers.map(lambda _: self.cli("--version"), range(12)))
                    paused = json.loads((probe["work"] / "ready").read_text())
                finally:
                    (probe["work"] / "release").touch()
                warmup = warmup_future.result(timeout=60)
        children = [warmup, *results]
        report = usage.summarize(days=2)
        evidence = count_diagnostics(self.path, children, report)
        self.assertTrue(all(r.returncode == 0 for r in children), evidence)
        self.assertEqual(report["total_calls"], 13, evidence)
        self.assertEqual(sum(r["successful"] for r in report["daily"]), 13, evidence)
        self.assertEqual(sum(r["unfinished"] for r in report["daily"]), 0, evidence)
        self.assertTrue(all(r["exit_code"] == 0 for r in ledger_snapshot(self.path)["calls"]), evidence)
        self.assertFalse(paused["in_transaction"])

    def test_persistent_start_lock_reports_loss_but_preserves_real_cli_result(self):
        warmup = self.cli("--version")
        self.assertEqual(warmup.returncode, 0, warmup.stderr)
        before = ledger_snapshot(self.path)
        with closing(sqlite3.connect(self.path)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            with ThreadPoolExecutor(max_workers=1) as pool:
                # Keep the real writer until the child reaches the existing bounded storage failure.
                result = pool.submit(self.cli, "--version").result(timeout=45)
            blocker.rollback()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, warmup.stdout)
        self.assertIn("[RDS-USAGE-DEGRADED] start logging failed (OperationalError/SQLITE_BUSY)", result.stderr)
        self.assertIn("start record not confirmed", result.stderr)
        self.assertEqual(ledger_snapshot(self.path), before)
        diagnostics = count_diagnostics(self.path, [warmup, result], usage.summarize(days=2))
        evidence = json.loads(diagnostics)
        self.assertEqual(evidence["children"][1]["stderr"], result.stderr)
        self.assertGreaterEqual(evidence["children"][1]["elapsed_seconds"], 10)
        self.assertEqual(evidence["ledger"], before)
        # A missing start must still fail complete-count acceptance. The original
        # child result, phase notice and raw dated row survive in that assertion.
        with self.assertRaises(AssertionError) as incomplete:
            self.assertEqual(evidence["report"]["total_calls"], 2, diagnostics)
        self.assertIn("[RDS-USAGE-DEGRADED] start", str(incomplete.exception))
        self.assertIn('"returncode": 0', str(incomplete.exception))
        self.assertIn('"day":', str(incomplete.exception))
        restored = self.cli("--version")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual(restored.stderr, "")
        report = usage.summarize(days=2)
        self.assertEqual(report["total_calls"], 2)
        self.assertEqual(sum(row["successful"] for row in report["daily"]), 2)

    def test_finish_lock_keeps_unfinished_row_and_original_exception_or_exit(self):
        secret = "private-error-payload-never-print"
        for raises in (False, True):
            with self.subTest(raises=raises), closing(sqlite3.connect(self.path)) as blocker:
                def command():
                    blocker.execute("BEGIN IMMEDIATE")
                    if raises:
                        raise RuntimeError(secret)
                    return 7
                warning = io.StringIO()
                with redirect_stderr(warning):
                    if raises:
                        with self.assertRaisesRegex(RuntimeError, secret):
                            usage.run_logged(command, ["status", secret], "test")
                    else:
                        self.assertEqual(usage.run_logged(command, ["status", secret], "test"), 7)
                blocker.rollback()
            self.assertIn("[RDS-USAGE-DEGRADED] finish logging failed (OperationalError/SQLITE_BUSY)", warning.getvalue())
            self.assertIn("exit record not confirmed", warning.getvalue())
            self.assertNotIn(secret, warning.getvalue())
            retained = ledger_snapshot(self.path)["calls"]
            self.assertEqual(len(retained), 2 if raises else 1)
            self.assertTrue(all(row["exit_code"] is None for row in retained))
            self.assertNotIn(secret, json.dumps(retained))

    def test_unavailable_warning_stream_never_replaces_original_result(self):
        with patch.dict(os.environ, {"RDS_USAGE_DB": str(self.folder)}):
            with patch("rds_usage.sys.stderr") as stream:
                stream.write.side_effect = OSError("synthetic closed stderr")
                self.assertEqual(usage.run_logged(lambda: 7, ["status"], "test"), 7)
                with self.assertRaisesRegex(RuntimeError, "original"):
                    usage.run_logged(lambda: (_ for _ in ()).throw(RuntimeError("original")), ["status"], "test")
        self.assertIsNotNone(usage._last_error)

    def test_brief_lock_wait_preserves_call_and_existing_wal_mode(self):
        usage.run_logged(lambda: 0, ['status'], 'test')
        with closing(sqlite3.connect(self.path)) as blocker:
            self.assertEqual(blocker.execute('PRAGMA journal_mode=WAL').fetchone()[0], 'wal')
            blocker.execute('BEGIN IMMEDIATE')
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(usage.run_logged, lambda: 7, ['status'], 'test')
                time.sleep(0.6)  # Exceeds the old 250 ms limit, within the bounded wait.
                blocker.commit()
                self.assertEqual(future.result(timeout=4), 7)
            self.assertEqual(blocker.execute('PRAGMA journal_mode').fetchone()[0], 'wal')
        report = usage.summarize(days=1)
        self.assertEqual(report['total_calls'], 2)
        self.assertEqual(report['daily'][0]['failed'], 1)
        self.assertIsNone(usage._last_error)

    def test_actual_cli_queries_are_counted_and_do_not_create_project_state(self):
        first = self.cli("usage", "--days", "2", "--json")
        self.assertEqual(first.returncode, 0, first.stderr)
        report = json.loads(first.stdout)
        self.assertEqual(report["total_calls"], 1)
        self.assertEqual(report["logging"], "ENABLED")
        self.assertFalse((self.folder / ".rds").exists())
        second = self.cli("usage", "--days", "2")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("Recorded calls: 2", second.stdout)

    def test_lock_contention_longer_than_old_timeout_retains_start_and_exit(self):
        usage.run_logged(lambda: 0, ['status'], 'test')
        with closing(sqlite3.connect(self.path)) as blocker:
            blocker.execute('BEGIN IMMEDIATE')
            with ThreadPoolExecutor(max_workers=1) as pool:
                entering = threading.Event()
                def invoke():
                    entering.set()
                    return usage.run_logged(lambda: 7, ['status'], 'test')
                pending = pool.submit(invoke)
                self.assertTrue(entering.wait(timeout=2))
                time.sleep(2.4)  # Deterministic contention beyond the original 2-second wait.
                blocker.commit()
                self.assertEqual(pending.result(timeout=12), 7)
        report = usage.summarize(days=1)
        self.assertEqual(report['total_calls'], 2)
        self.assertEqual(report['daily'][0]['failed'], 1)
        self.assertIsNone(usage._last_error)

    def test_invalid_windows_fail_and_unfinished_starts_remain_visible(self):
        usage._start(["project"], "test")
        self.assertEqual(usage.summarize(days=1)["daily"][0]["unfinished"], 1)
        for options in ({"days": 0}, {"days": 3661}, {"since": ""},
                        {"since": "2026-10-02", "until": "2026-10-01"}):
            with self.assertRaises(ValueError):
                usage.summarize(**options)
        invalid = self.cli("usage", "--days", "0")
        self.assertEqual(invalid.returncode, 1)
        self.assertIn("--days", invalid.stderr)


if __name__ == "__main__":
    unittest.main()
