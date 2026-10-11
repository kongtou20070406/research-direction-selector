"""PR301: terminal paid QUICK identity survives supported Advisor activation.

Two finite CPU fixtures; damage controls use raw SQLite only in temporary
owners, restoring their original database after every read-only observation.
"""
from contextlib import closing, contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from rds_method_revision import contract_history
from rds_project import ProjectStore, canonical, digest
from rds_project_lifecycle import enable_advisor
from rds_quick import _parent_controls
from rds_workspace import bind, check_admission, coverage, pointer_path, read_pointer
import test_rds_project_lifecycle as fixture_module


class PaidChildHistoryTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixture_module.ProjectLifecycleTests(methodName='runTest')
        self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.addCleanup(self.export_evidence)
        self.observed = {}
        helper = self.helper
        helper.recipe['budget'] = {'wall_seconds': 30}
        helper.recipe['routes'] = [
            {**route, 'run': {**route['run'], 'resource_estimates': {
                'wall_seconds': route['run']['resource_estimates']['wall_seconds']}}}
            for route in helper.recipe['routes']]
        helper.prepare_contract()
        helper.contract['execution_policy'] = {'schema': 1, 'max_attempts': 3}
        helper.write_json('contract.json', helper.contract)
        helper.output('project', 'init', '--contract', str(helper.contract_path), '--mode', 'quick')
        bind(helper.root)
        (helper.root / 'probe.py').write_text('print("bounded paid child")\n', encoding='utf-8')
        self.result = helper.output('exec', '--name', 'paid-history', '--timeout', '5',
                                    '--', sys.executable, '-B', 'probe.py')
        self.owner = helper.store
        self.child = ProjectStore(helper.root / '.rds/exec/paid-history')
        self.before_owner = self.owner.snapshot()
        self.before_child = self.child.snapshot()
        self.frozen = self.frozen_bytes()
        self.assertEqual(self.result['run_status'], 'SUCCEEDED')
        self.assertEqual(len(self.before_child['runs']), 1)
        self.assertEqual(len(self.before_child['receipts']), 1)
        self.assertIsNotNone(self.before_child['runs'][0]['attempt_id'])
        self.assertEqual(self.before_child['receipts'][0]['attempt_id'],
                         self.before_child['runs'][0]['attempt_id'])
        self.assertEqual(coverage(self.child.root)['status'], 'BOUND')
        self.assertEqual(read_pointer(self.child.root), json.loads(self.frozen['pointer']))
        with self.owner._db(True) as db:
            row = db.execute("SELECT id,body FROM events WHERE json_extract(body,'$.kind')="
                             "'EXTERNAL_RUN_ALLOWANCE'").fetchone()
            self.allowance_id, self.allowance_raw = row['id'], row['body']
        self.allowance = json.loads(self.allowance_raw)
        self.assertEqual(self.allowance['amount'], 5)
        self.assertEqual(self.allowance['request_sha256'], digest(json.loads(self.frozen['request'])))
        self.assertEqual(self.allowance['contract_sha256'], self.before_owner['contract_sha256'])
        self.assertEqual(self.before_owner['budget']['wall_seconds']['charged_estimate'], 5)
        preview = enable_advisor(self.owner, helper.policy)
        self.activation = enable_advisor(self.owner, helper.policy, apply=True,
                                         expected_snapshot=preview['snapshot_sha256'])
        self.assertEqual(self.activation['status'], 'ADVISOR_ENABLED')
        self.observed['activation'] = self.activation

    def frozen_bytes(self):
        return {'pointer': pointer_path(self.child.root).read_bytes(),
                'ledger': self.child.path.read_bytes(),
                'request': (self.child.root / 'rds-exec-request.json').read_bytes(),
                'bindings': {binding['path']: (self.child.root / binding['path']).read_bytes()
                             for binding in self.child.snapshot()['contract']['bindings']}}

    def export_evidence(self):
        destination = os.environ.get('PR301_TEST_EVIDENCE_ROOT')
        if destination:
            target = Path(destination) / self._testMethodName
            target.mkdir(parents=True, exist_ok=False)
            (target / 'observations.json').write_text(
                json.dumps(self.observed, indent=2), encoding='utf-8')
            (target / 'cli-transcript.json').write_text(
                json.dumps(self.helper.trace, indent=2), encoding='utf-8')
            shutil.copytree(self.helper.root, target / 'original-workspace')

    def assert_originals_preserved(self):
        after = self.owner.snapshot()
        self.assertEqual(after['budget'], self.before_owner['budget'])
        self.assertEqual(after['runs'], self.before_owner['runs'])
        self.assertEqual(after['receipts'], self.before_owner['receipts'])
        self.assert_child_records_preserved()
        self.assertEqual(self.frozen_bytes(), self.frozen)
        with self.owner._db(True) as db:
            self.assertEqual(db.execute('SELECT body FROM events WHERE id=?',
                                        (self.allowance_id,)).fetchone()['body'], self.allowance_raw)

    def assert_child_records_preserved(self):
        # workspace_binding is a live view, tested separately from frozen records.
        self.assertEqual({key: value for key, value in self.child.snapshot().items()
                          if key != 'workspace_binding'},
                         {key: value for key, value in self.before_child.items()
                          if key != 'workspace_binding'})

    def test_validated_historical_scope_keeps_terminal_child_identity(self):
        with self.owner._db(True) as db:
            history = contract_history(db)
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]['sha256'], self.allowance['contract_sha256'])
        self.assertEqual(history[0]['contract'], self.before_owner['contract'])
        self.assertEqual(history[1]['contract'],
                         {**self.before_owner['contract'], 'advisor_policy': self.helper.policy})
        self.assertNotEqual(history[-1]['sha256'], self.allowance['contract_sha256'])
        effective, steering = _parent_controls(self.owner.root)
        self.assertEqual(effective, history[-1]['contract'])
        self.assertIsNone(steering)
        self.assertEqual(coverage(self.owner.root)['status'], 'BOUND')
        actual_pointer = read_pointer(self.child.root)
        actual_coverage = coverage(self.child.root)
        self.observed.update(history=history, allowance=self.allowance,
                             actual_pointer=actual_pointer, coverage=actual_coverage,
                             expected_pointer=json.loads(self.frozen['pointer']))
        print('PR301_HISTORICAL_SCOPE=' + canonical({
            'allowance_contract_sha256': self.allowance['contract_sha256'],
            'effective_contract_sha256': history[-1]['sha256'],
            'actual_pointer': actual_pointer, 'expected_pointer': json.loads(self.frozen['pointer']),
            'coverage': actual_coverage}))
        self.assert_originals_preserved()
        with self.subTest(boundary='pointer'):
            self.assertEqual(actual_pointer, json.loads(self.frozen['pointer']))
        with self.subTest(boundary='coverage'):
            self.assertEqual(actual_coverage['status'], 'BOUND', canonical(actual_coverage))

    @contextmanager
    def owner_damage(self, *, event_id=None, event=None, sql=None):
        # Damage is deliberately outside supported writers, never a policy transition.
        saved = sqlite3.connect(':memory:')
        try:
            with closing(sqlite3.connect(self.owner.path)) as db, db:
                db.backup(saved)
                if event is not None:
                    db.execute('DROP TRIGGER events_no_update')
                    db.execute('UPDATE events SET body=? WHERE id=?', (canonical(event), event_id))
                if sql is not None:
                    db.execute(sql)
            yield
        finally:
            with closing(sqlite3.connect(self.owner.path)) as db, db:
                saved.backup(db)
            saved.close()

    def assert_refused(self, label):
        report = coverage(self.child.root)
        self.observed[label] = report
        print('PR301_REFUSAL_' + label + '=' + canonical(report))
        self.assertEqual(report['status'], 'MISMATCH')
        with self.assertRaises(ValueError):
            check_admission(self.child.root)
        self.assert_child_records_preserved()
        self.assertEqual(self.frozen_bytes(), self.frozen)

    def test_foreign_forged_history_and_current_owner_damage_are_refused(self):
        foreign = deepcopy(self.before_owner['contract'])
        foreign['description'] = 'foreign contract outside this validated owner history'
        foreign_sha = digest(foreign)
        with self.owner._db(True) as db:
            self.assertNotIn(foreign_sha, {entry['sha256'] for entry in contract_history(db)})
            identity = db.execute("SELECT id,body FROM events WHERE json_extract(body,'$.kind')="
                                  "'WORKSPACE_BOUND'").fetchone()
            activation = db.execute("SELECT id,body FROM events WHERE json_extract(body,'$.kind')="
                                    "'ADVISOR_POLICY_ENABLED'").fetchone()
        with self.subTest(damage='foreign'), self.owner_damage(
                event_id=self.allowance_id, event={**self.allowance, 'contract_sha256': foreign_sha}):
            self.assert_refused('foreign_contract')
        with self.subTest(damage='forged'), self.owner_damage(
                event_id=self.allowance_id, event={**self.allowance, 'contract_sha256': 'f' * 64}):
            self.assert_refused('forged_contract')
        with self.subTest(damage='current_charge'), self.owner_damage(
                sql="UPDATE budget SET charged=0 WHERE resource='wall_seconds'"):
            self.assert_refused('current_charge')
        with self.subTest(damage='canonical_owner'), self.owner_damage(
                event_id=identity['id'], event={**json.loads(identity['body']), 'event_digest': 'f' * 64}):
            self.assert_refused('canonical_owner')
        with self.subTest(damage='history_integrity'), self.owner_damage(
                event_id=activation['id'], event={**json.loads(activation['body']), 'sha256': 'f' * 64}):
            with self.owner._db(True) as db:
                with self.assertRaisesRegex(ValueError, 'event integrity failure'):
                    contract_history(db)
            self.assert_refused('history_integrity')
        self.assert_originals_preserved()


if __name__ == '__main__':
    unittest.main()
