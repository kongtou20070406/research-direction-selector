"""Real Store reads: retained validators, immutable rows and no new writes."""
import ast
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import test_rds_project as fixtures
import rds_project as project
import rds_owned_advisor as advisor
import rds_autonomy as autonomy
import rds_steering as steering
from rds_project_read_services import ProjectReadServices


class ReadServicesTests(unittest.TestCase):
    def setUp(self):
        self.fx = fixtures.ProjectTests('test_execution_policy_is_opt_in_and_frozen')
        self.fx.setUp()
        self.addCleanup(self.fx.tearDown)
        self.store = self.fx.store

    def legacy_state(self, db):
        # The retained original implementation is the equivalence oracle, not
        # a reimplementation of the new service. It is copied at test creation.
        tree = ast.parse(LEGACY_STATE)
        env = dict(advisor.__dict__)
        exec(compile(tree, '<original-owned-state>', 'exec'), env)
        return env['_state'](self.store, db)

    def test_owned_state_uses_borrowed_port_and_matches_original_without_writes(self):
        self.store.register(self.fx.spec('r1', timeout=8))
        with self.store._db(True) as db:
            db.execute('BEGIN')
            before = db.total_changes
            expected = self.legacy_state(db)
            with patch.object(ProjectReadServices, 'bounded_port', autospec=True,
                              side_effect=ProjectReadServices.bounded_port) as port:
                actual = advisor._state(self.store, db)
            self.assertTrue(port.called)
            self.assertEqual(actual, expected)
            self.assertEqual(advisor._fingerprint(actual), advisor._fingerprint(expected))
            self.assertEqual(db.total_changes, before)
            self.assertTrue(db.in_transaction)

    def test_budget_preserves_more_than_64_dimensions_and_immutable_rows(self):
        with self.store._db() as db:
            db.executemany('INSERT INTO budget(resource,cap) VALUES (?,?)',
                           [(f'custom.{n}', 10.0) for n in range(70)])
        with self.store._db(True) as db:
            db.execute('BEGIN')
            reader = self.store._read_services(db)
            expected = [dict(row) for row in db.execute('SELECT * FROM budget ORDER BY resource')]
            rows = reader.budget(integrity_error=project.ReceiptIntegrityError, digest=project.digest)
            self.assertEqual([dict(row) for row in rows], expected)
            self.assertEqual(len(rows), 73)
            with self.assertRaises(TypeError):
                rows[0]['spent'] = 0.0
            with self.assertRaises(FrozenInstanceError):
                reader._connection = None

    def test_run_corruption_keeps_original_error_before_history(self):
        self.store.register(self.fx.spec('r1', timeout=8))
        with self.store._db() as db:
            db.execute("UPDATE runs SET body='{}' WHERE id='r1'")
        with self.store._db(True) as db:
            db.execute('BEGIN')
            errors = []
            for read in (self.legacy_state, lambda db: advisor._state(self.store, db)):
                with self.assertRaises(project.RunIntegrityError) as caught:
                    read(db)
                errors.append((type(caught.exception), str(caught.exception)))
            self.assertEqual(errors[0], errors[1])
            self.assertEqual(db.total_changes, 0)

    def test_controller_records_preserve_mac_validation_and_selector_bounds(self):
        body = {'kind': 'AUTONOMY_DRIVE_CLAIMED', 'owner': 'fixture', 'pid': 123,
                'started_at': 1.0, 'controller_reservation': 2.0}
        body['sha256'] = project.digest(body)
        with self.store._db() as db:
            db.execute('INSERT INTO events(body) VALUES (?)', (project.canonical(body),))
        with self.store._db(True) as db:
            db.execute('BEGIN')
            reader = ProjectReadServices(db)
            self.assertEqual(autonomy._events(db, (body['kind'],)), [body])
            self.assertEqual(reader.events(("x' OR 1=1 --",), maximum=1, limit_message='bound'), ())
            with self.assertRaisesRegex(ValueError, '^bound$'):
                reader.events((body['kind'],), maximum=0, limit_message='bound')
            self.assertEqual(db.total_changes, 0)
        with self.store._db() as db:
            wrong = {**body, 'sha256': '0' * 64}
            db.execute('INSERT INTO events(body) VALUES (?)', (project.canonical(wrong),))
        with self.store._db(True) as db:
            with self.assertRaisesRegex(ValueError, 'Autonomy event integrity failure'):
                autonomy._events(db, (body['kind'],))

    def test_steering_latest_record_and_checkpoint_table_absence(self):
        body = {'kind': 'PROJECT_STEERING', 'request': {'id': 'fixture'}}
        body['sha256'] = project.digest(body)
        with self.store._db() as db:
            db.execute('INSERT INTO events(body) VALUES (?)', (project.canonical(body),))
        with self.store._db(True) as db:
            db.execute('BEGIN')
            self.assertEqual(steering.current(db), body)
            self.assertIsNone(ProjectReadServices(db).checkpoint_ids(maximum=512))
            self.assertEqual(db.total_changes, 0)

    def test_uninitialized_contract_retains_old_exception_and_selects_do_not_commit(self):
        with sqlite3.connect(':memory:') as db:
            with self.assertRaisesRegex(ValueError, '^Project contract has not been initialized$'):
                project.ProjectStore._contract(db)
            self.assertFalse(db.in_transaction)


LEGACY_STATE = r'''def _state(store, db):
    """Bounded, coherent read, shared by analysis and the write-lock recheck."""
    contract = store._contract(db)
    require(db.execute('SELECT COUNT(*) FROM runs').fetchone()[0] <= MAX_RUNS, 'Owned run coverage limit exceeded')
    require(db.execute('SELECT COUNT(*) FROM receipts').fetchone()[0] <= MAX_RUNS, 'Owned receipt coverage limit exceeded')
    runs = store._runs(db)
    receipts = [store._receipt(row) for row in db.execute('SELECT run_id,sha256,body FROM receipts ORDER BY run_id')]
    budget = [dict(row) for row in db.execute('SELECT * FROM budget ORDER BY resource')]
    completed = []
    for receipt in receipts:
        require(db.execute("SELECT 1 FROM events WHERE json_extract(body,'$.kind')='ATTEMPT_FINISHED' "
                           "AND json_extract(body,'$.run_id')=? AND json_extract(body,'$.sha256')=? LIMIT 1",
                           (receipt['run_id'], receipt['sha256'])).fetchone() is not None,
                'Owned receipt has no matching completion event')
        completed.append(receipt['sha256'])
    campaign = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='CAMPAIGN_STARTED' ORDER BY id LIMIT 1").fetchone()
    from rds_method_revision import contract_history
    history = contract_history(db)
    from rds_owned_history import history_cut
    try:
        verified_history = history_cut(store, db)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        raise ValueError('Repair checkpoint integrity before executing a candidate: ' + str(exc)) from exc
    state = {'contract': contract, 'runs': sorted(runs, key=lambda r: r['id']), 'receipts': receipts,
            'budget': budget, 'completion_events': completed,
            'history_cut': verified_history,
            'contract_history': history,
            'exposures': [strict_json(row['body']) for row in db.execute('SELECT body FROM exposures ORDER BY id')],
            'campaign_started': strict_json(campaign['body']) if campaign else None}
    from rds_steering import current, view
    steering = current(db)
    if steering is not None:
        state['steering'] = view(steering)
    if 'autonomy' in contract.get('advisor_policy', {}):
        from rds_autonomy import records
        state['autonomy_records'] = records(store, db, contract)
    from rds_postcommit_confirmation import enabled, records as confirmation_records
    if enabled(contract):
        state['confirmation_challenges'] = confirmation_records(store, db, contract)
    return state
'''

if __name__ == '__main__':
    unittest.main()
