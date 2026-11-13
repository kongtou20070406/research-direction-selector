"""Notes must coexist with the native research ledger, never replace it."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from rds_project import canonical
from rds_math import bind_objective, objective
from rds_notes import append
from rds_research import start


class NativeNoteTests(unittest.TestCase):
    def test_native_objective_remains_identical_when_research_loop_starts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = {'schema': 'rds-objective-v1', 'question_id': 'q', 'goal_revision': '1', 'scope': {},
                    'statement': 'Find a fixed point', 'domain': 'QQ', 'quantifier_order': [],
                    'assumptions': [], 'evidence_standard': 'exact check', 'completion_standard': 'witness'}
            old = bind_objective(root, canonical(spec).encode('utf-8'))
            result = start(root, spec['statement'])
            self.assertEqual(objective(root), old)
            self.assertEqual(result['context']['retained_context']['native_objective'], old)
            with closing(sqlite3.connect(root / '.rds' / 'project.sqlite3')) as db:
                self.assertEqual({r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                                 {'research_records', 'events'})

    def test_unrelated_database_is_still_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.rds').mkdir()
            with closing(sqlite3.connect(root / '.rds' / 'project.sqlite3')) as db:
                db.execute('CREATE TABLE foreign_data(value TEXT)')
                db.commit()
            with self.assertRaisesRegex(ValueError, 'not an RDS'):
                append(root, 'goal', 'Do not adopt this database')
