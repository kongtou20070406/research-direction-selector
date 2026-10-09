"""Explicit nested owner accounting and retained interruption boundaries."""
import importlib.util
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_project import canonical
import rds_autonomy as autonomy
import rds_structure as structure

spec = importlib.util.spec_from_file_location('control_budget_fixture', REPO / 'tests/test_rds_autonomy.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class JumpControlBudgetTests(unittest.TestCase):
    def build(self):
        case = fixture.AutonomyTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.build(budget=4, control_wall=4)
        return case.store

    def claim(self, store):
        owner, allowance = autonomy._claim(store)
        with store._db(True) as db:
            claim = autonomy._events(db, ('AUTONOMY_DRIVE_CLAIMED',))[-1]
        return owner, allowance, claim

    def budget(self, store):
        return store.snapshot()['budget']['wall_seconds']

    def test_existing_owner_allowance_accounts_child_once(self):
        store = self.build()
        owner, allowance, claim = self.claim(store)
        before = self.budget(store)
        self.assertEqual(before['remaining'], 0.)
        with self.assertRaisesRegex(ValueError, 'Insufficient wall_seconds'):
            with structure._meter(store, 'independent-call'):
                self.fail('Same PID cannot inherit a drive allowance')
        started = time.monotonic()
        with structure.owned_control(store, owner, lambda: allowance - (time.monotonic() - started)):
            with structure._meter(store, 'owned-child'):
                self.assertEqual(self.budget(store), before)
        self.assertEqual(self.budget(store), before)
        events = structure._events(store)
        self.assertEqual(events[-2]['budget_owner'], owner)
        self.assertEqual(events[-2]['owner_claim_sha256'], claim['sha256'])
        self.assertEqual(events[-1]['accounting'], 'OWNING_DRIVE_ALLOWANCE')
        elapsed = time.monotonic() - started
        with store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE budget SET reserved=reserved-?,spent=spent+? WHERE resource='wall_seconds'", (allowance, elapsed))
            autonomy._append(db, {'kind': 'AUTONOMY_DRIVE_RELEASED', 'owner': owner, 'reason': 'TEST_OWNER_FINISHED'})
        after = self.budget(store)
        self.assertAlmostEqual(after['spent_measured'], elapsed)
        self.assertEqual(after['reserved'], 0.)
        self.assertEqual(after['charged_estimate'], 0.)

    def test_wrong_owner_root_and_remaining_are_rejected(self):
        store, other = self.build(), self.build()
        owner, allowance, _ = self.claim(store)
        before = self.budget(store)
        with self.assertRaisesRegex(ValueError, 'active drive'):
            with structure.owned_control(store, 'invented', lambda: allowance):
                self.fail('Bad owner entered')
        with structure.owned_control(store, owner, lambda: allowance):
            with self.assertRaisesRegex(ValueError, 'another project'):
                with structure._meter(other, 'wrong-root'):
                    self.fail('Cross-project accounting')
            with self.assertRaisesRegex(ValueError, 'Nested'):
                with structure.owned_control(store, owner, lambda: allowance):
                    self.fail('Nested owner scope')
        for remaining in (True, float('nan'), allowance + 1, -1):
            with self.subTest(remaining=remaining), self.assertRaises(ValueError):
                with structure.owned_control(store, owner, lambda: remaining):
                    self.fail('Invalid remaining allowance')
        with structure.owned_control(store, owner, lambda: 1.):
            with self.assertRaisesRegex(ValueError, 'Insufficient owned'):
                with structure._meter(store, 'short-allowance'):
                    self.fail('Original two-second control cap was bypassed')
        self.assertEqual(self.budget(store), before)

    def test_deadline_and_released_owner_are_checked_at_each_meter(self):
        store = self.build()
        owner, allowance, _ = self.claim(store)
        with structure.owned_control(store, owner, lambda: allowance):
            with patch.object(store, '_campaign_deadline', side_effect=ValueError('CAMPAIGN_DEADLINE')):
                with self.assertRaisesRegex(ValueError, 'CAMPAIGN_DEADLINE'):
                    with structure._meter(store, 'expired'):
                        self.fail('Expired campaign admitted')
            with store._db() as db:
                db.execute('BEGIN IMMEDIATE')
                autonomy._append(db, {'kind': 'AUTONOMY_DRIVE_RELEASED', 'owner': owner, 'reason': 'TEST_RELEASE'})
            with self.assertRaisesRegex(ValueError, 'active drive'):
                with structure._meter(store, 'lost-owner'):
                    self.fail('Released owner admitted')

    def interrupted_child(self, store, owner, claim):
        with store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO events(body) VALUES (?)', (canonical({
                'kind': 'STRUCTURE_CONTROL_STARTED', 'id': 'interrupted-child', 'operation': 'jump-start',
                'cap': 2., 'pid': os.getpid(), 'budget_owner': owner, 'owner_claim_sha256': claim['sha256']}),))

    def test_interrupted_child_and_dead_owner_recover_without_double_charge(self):
        for parent_first in (False, True):
            with self.subTest(parent_first=parent_first):
                store = self.build()
                owner, allowance, claim = self.claim(store)
                self.interrupted_child(store, owner, claim)
                before = self.budget(store)
                with patch.object(autonomy, '_alive', return_value=False), patch.object(structure, '_alive', return_value=False):
                    if parent_first:
                        with self.assertRaisesRegex(ValueError, 'no remaining wall budget'):
                            autonomy._claim(store)
                    result = structure.recover_control(store.root)
                    self.assertEqual(result['reconciled_controls'], 1)
                    if not parent_first:
                        self.assertEqual(self.budget(store), before)
                        with self.assertRaisesRegex(ValueError, 'no remaining wall budget'):
                            autonomy._claim(store)
                after = self.budget(store)
                self.assertEqual(after['reserved'], 0.)
                self.assertEqual(after['charged_estimate'], allowance)
                self.assertEqual(after['spent_measured'], 0.)
                event = structure._events(store)[-1]
                self.assertEqual(event['status'], 'UNKNOWN')
                self.assertEqual(event['charged_estimate'], 0.)
                self.assertEqual(structure.recover_control(store.root)['reconciled_controls'], 0)

    def test_independent_meter_keeps_original_reservation_and_settlement(self):
        for elapsed in (0.25, 0.):
            with self.subTest(elapsed=elapsed):
                store = self.build()
                before = self.budget(store)
                clock = Mock(side_effect=(10., 10. + elapsed))
                # Replace only this adapter's clock binding. The real project
                # ledger and its deadlines keep their original time module.
                with patch.object(structure, 'time', SimpleNamespace(monotonic=clock)):
                    with structure._meter(store, 'direct-cli'):
                        inside = self.budget(store)
                        self.assertEqual(inside['reserved'], 2.)
                        self.assertEqual(inside['spent_measured'], before['spent_measured'])
                self.assertEqual(clock.call_count, 2)
                self.assertIs(structure.time, time)
                after = self.budget(store)
                self.assertEqual(after['reserved'], 0.)
                self.assertEqual(after['spent_measured'] - before['spent_measured'], elapsed)
                self.assertEqual(after['charged_estimate'], before['charged_estimate'])
                self.assertEqual(after['remaining'], before['remaining'] - elapsed)
                started, finished = structure._events(store)[-2:]
                self.assertEqual(started['kind'], 'STRUCTURE_CONTROL_STARTED')
                self.assertEqual(started['operation'], 'direct-cli')
                self.assertEqual(started['cap'], 2.)
                self.assertEqual(finished['kind'], 'STRUCTURE_CONTROL_FINISHED')
                self.assertEqual(finished['id'], started['id'])
                self.assertEqual(finished['wall_seconds'], elapsed)
                self.assertFalse(finished['over_cap'])
                for event in (started, finished):
                    self.assertNotIn('budget_owner', event)

    def test_independent_interrupted_meter_keeps_original_unknown_charge(self):
        store = self.build()
        with store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE budget SET reserved=reserved+2 WHERE resource='wall_seconds'")
            db.execute('INSERT INTO events(body) VALUES (?)', (canonical({
                'kind': 'STRUCTURE_CONTROL_STARTED', 'id': 'direct-interruption',
                'operation': 'direct-cli', 'cap': 2., 'pid': os.getpid()}),))
        with patch.object(structure, '_alive', return_value=False):
            structure.recover_control(store.root)
        after = self.budget(store)
        self.assertEqual(after['reserved'], 0.)
        self.assertEqual(after['charged_estimate'], 2.)
        self.assertEqual(after['spent_measured'], 0.)
        self.assertNotIn('budget_owner', structure._events(store)[-1])


if __name__ == '__main__':
    unittest.main()
