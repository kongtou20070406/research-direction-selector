"""Read-side autonomy history preserves integrity without controller imports."""
import ast
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_autonomy_read as reads
from rds_project_values import canonical


class AutonomyReadTests(unittest.TestCase):
    def test_import_does_not_load_controller_or_store(self):
        code = ('import sys; sys.path.insert(0, sys.argv[1]); '
                'import rds_autonomy_read; '
                'assert not {"rds_autonomy", "rds_owned_advisor", "rds_project", '
                '"rds_quick", "rds_cli"}.intersection(sys.modules)')
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code,
                                 str(ROOT / 'scripts')], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_absent_configuration_does_not_read(self):
        self.assertEqual(reads.records(None, None, {}), [])

    def test_event_reader_is_readonly_and_checks_original_hash(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY, body TEXT)')
        event = {'kind': reads.REQUESTED, 'run_id': 'repair'}
        event['sha256'] = reads._hash_event(event)
        db.execute('INSERT INTO events(body) VALUES (?)', (canonical(event),))
        db.commit()
        db.execute('PRAGMA query_only=ON')
        before = db.total_changes
        self.assertEqual(reads._events(db, (reads.REQUESTED,)), [event])
        self.assertEqual(db.total_changes, before)
        self.assertFalse(db.in_transaction)
        db.execute('PRAGMA query_only=OFF')
        event['run_id'] = 'tampered'
        db.execute('UPDATE events SET body=?', (json.dumps(event),))
        with self.assertRaisesRegex(ValueError, 'Autonomy event integrity failure'):
            reads._events(db, (reads.REQUESTED,))

    def test_event_bound_remains_enforced(self):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY, body TEXT)')
        event = {'kind': reads.REQUESTED, 'run_id': 'repair'}
        event['sha256'] = reads._hash_event(event)
        db.executemany('INSERT INTO events(body) VALUES (?)', [(canonical(event),)] * 257)
        with self.assertRaisesRegex(ValueError, 'Autonomy event bound exceeded'):
            reads._events(db, (reads.REQUESTED,))

    def test_legacy_entrypoints_are_same_functions(self):
        import rds_autonomy as controller
        for name in ('records', '_events', '_hash_event'):
            self.assertIs(getattr(controller, name), getattr(reads, name))

    def test_read_consumers_do_not_import_controller(self):
        for name in ('rds_campaign.py', 'rds_owned_history.py'):
            imports = [node.module for node in ast.walk(ast.parse(
                (ROOT / 'scripts' / name).read_text(encoding='utf-8')))
                if isinstance(node, ast.ImportFrom)]
            self.assertIn('rds_autonomy_read', imports)
            self.assertNotIn('rds_autonomy', imports)


if __name__ == '__main__':
    unittest.main()
