"""Regressions for research reminders and their host entry point."""
from contextlib import closing
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import build_plugins
import rds_notes
import rds_nudge
import rds_conversation_hook as hook


class Regressions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def run_hook(self, event='PostToolUseFailure', command='python work/train.py', **extra):
        payload = {'hook_event_name': event, 'cwd': str(self.root), 'tool_name': 'Bash',
                   'tool_input': {'command': command}, **extra}
        return subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_conversation_hook.py')],
                              input=json.dumps(payload), text=True, encoding='utf-8', capture_output=True, timeout=3)

    def test_failure_records_attempt_and_delivers_reminder(self):
        rds_notes.append(self.root, 'goal', 'goal')
        results = [self.run_hook() for _ in range(3)]
        result = json.loads(results[-1].stdout)['hookSpecificOutput']
        self.assertEqual(result['hookEventName'], 'PostToolUseFailure')
        self.assertIn('train.py', result['additionalContext'])
        attempts = [n for n in rds_notes.read(self.root) if n['type'] == 'attempt']
        self.assertEqual([n['outcome'] for n in attempts], ['fail'] * 3)

    def test_packaged_runpy_hook_with_claude_failure_payload(self):
        rds_notes.append(self.root, 'goal', 'goal')
        plugin = self.root / 'plugin'
        scripts = plugin / 'skills' / build_plugins.NAME / 'scripts'
        shutil.copytree(ROOT / 'scripts', scripts, ignore=shutil.ignore_patterns('__pycache__', 'assets'))
        command = build_plugins.hook_config('claude')['hooks']['PostToolUseFailure'][0]['hooks'][0]['command']
        bootstrap = command[len('python -B -c "'):-1]
        env = os.environ.copy()
        env.pop('PYTHONPATH', None)
        env['CLAUDE_PLUGIN_ROOT'] = str(plugin)
        payload = {'session_id': 'redacted', 'transcript_path': 'redacted', 'cwd': str(self.root),
                   'scratchpad_dir': 'redacted', 'prompt_id': 'redacted', 'permission_mode': 'default',
                   'effort': {'level': 'medium'}, 'hook_event_name': 'PostToolUseFailure', 'tool_name': 'Bash',
                   'tool_input': {'command': 'python train.py', 'description': 'Run training script (run 1 of 3)'},
                   'tool_use_id': 'redacted', 'error': 'Exit code 1\nloss nan', 'is_interrupt': False,
                   'duration_ms': 1078}
        for _ in range(3):
            result = subprocess.run([sys.executable, '-B', '-c', bootstrap], cwd=self.root, env=env,
                                    input=json.dumps(payload), text=True, encoding='utf-8',
                                    capture_output=True, timeout=3)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('train.py', json.loads(result.stdout)['hookSpecificOutput']['additionalContext'])
        notes = rds_notes.read(self.root)
        self.assertEqual([(n['route'], n['outcome'], n['auto']) for n in notes if n['type'] == 'attempt'],
                         [('train.py', 'fail', True)] * 3)
        self.assertTrue(any(n['type'] == 'shown' for n in notes))

    def test_nonledger_is_untouched(self):
        dbpath = self.root / '.rds' / 'project.sqlite3'
        dbpath.parent.mkdir()
        with closing(sqlite3.connect(dbpath)) as db, db:
            db.execute('CREATE TABLE foreign_data (x)')
        self.assertEqual(self.run_hook().stdout, '')
        self.assertEqual(self.run_hook(command='python "{}" status'.format(ROOT / 'scripts/rds_cli.py')).stdout, '')
        with self.assertRaises(ValueError):
            rds_notes.append(self.root, 'claim', 'x')
        with closing(sqlite3.connect(dbpath)) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [('foreign_data',)])

    def test_empty_database_hook_does_not_initialize(self):
        dbpath = self.root / '.rds' / 'project.sqlite3'
        dbpath.parent.mkdir()
        sqlite3.connect(dbpath).close()
        self.assertEqual(self.run_hook().stdout, '')
        self.assertEqual(self.run_hook(command='python "{}" status'.format(ROOT / 'scripts/rds_cli.py')).stdout, '')
        with closing(sqlite3.connect(dbpath)) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])

    def test_busy_timeout_and_locked_hook(self):
        rds_notes.append(self.root, 'goal', 'goal')
        dbpath = self.root / '.rds' / 'project.sqlite3'
        with closing(hook._hook_connect(':memory:')) as db:
            self.assertEqual(db.execute('PRAGMA busy_timeout').fetchone()[0], 1000)
        db = sqlite3.connect(dbpath)
        try:
            db.execute('BEGIN EXCLUSIVE')
            started = time.monotonic()
            result = self.run_hook()
            self.assertLess(time.monotonic() - started, 3)
            self.assertEqual(result.stdout, '')
        finally:
            db.rollback()
            db.close()

    def test_map_rejects_ledger_and_sqlite_and_writes_html(self):
        rds_notes.append(self.root, 'claim', 'story')
        ledger = self.root / '.rds' / 'project.sqlite3'
        other = self.root / 'other.db'
        with closing(sqlite3.connect(other)) as db, db:
            db.execute('CREATE TABLE data(x)')
        for target in (ledger, self.root / '.rds' / 'map.html', other):
            before = target.read_bytes() if target.exists() else None
            p = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.root), 'map', '--output', str(target)], capture_output=True)
            self.assertNotEqual(p.returncode, 0)
            self.assertEqual(target.read_bytes() if target.exists() else None, before)
        target = self.root / 'map.html'
        p = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.root), 'map', '--output', str(target)], capture_output=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('story', target.read_text(encoding='utf-8'))

    def test_limit_and_large_mixed_evaluation(self):
        for i in range(8):
            rds_notes.append(self.root, 'claim', str(i))
        self.assertEqual([n['text'] for n in rds_notes.read(self.root, limit=3)], ['5', '6', '7'])
        kinds = ('goal', 'expect', 'observe', 'claim', 'attempt')
        notes = [{'id': 'n'+str(i), 'type': kinds[i % 5], 'text': 'x', 'metric': 'm',
                  'value': 1, 'tolerance': 0, 'scale': 'a', 'route': 'r', 'outcome': 'fail'} for i in range(20000)]
        start = time.monotonic()
        result = rds_nudge.evaluate(notes)
        self.assertLess(time.monotonic() - start, 2)
        self.assertTrue(any(n['rule'] == 'unbound_claim' for n in result))

    def test_interpreter_options_and_script_names(self):
        for command, name in [('python -O -X utf8 -W ignore -B -u dir/a.py', 'a.py'),
                              ('powershell -File dir/a.ps1', 'a.ps1'), ('pwsh -File dir/b.ps1', 'b.ps1'),
                              ('bash dir/a.sh', 'a.sh'), ('node dir/a.js', 'a.js')]:
            with self.subTest(command=command):
                payload = {'tool_name': 'Bash', 'tool_input': {'command': command}, 'hook_event_name': 'PostToolUseFailure'}
                self.assertEqual(hook._attempt(payload), (name, 'fail'))
        for command in ('python -m module', 'python -c code', 'python -X utf8 -m module'):
            self.assertIsNone(hook._attempt({'tool_name': 'Bash', 'tool_input': {'command': command}, 'hook_event_name': 'PostToolUseFailure'}))


if __name__ == '__main__':
    unittest.main()
