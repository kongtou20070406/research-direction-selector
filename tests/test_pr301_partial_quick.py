"""A paid but unbound QUICK child must retain its enclosing admission identity."""
from contextlib import closing
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_cli
import rds_quick as quick
import rds_workspace as workspace
from rds_project import ProjectStore, file_sha
import test_rds_quick as fixture


class PartialQuickTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.QuickTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.initialize_policy_ledger(max_attempts=3)
        self.root = self.f.ledger.resolve()
        self.parent = ProjectStore(self.root)
        workspace.bind(self.root)
        self.args = rds_cli.parser().parse_args([
            '--root', str(self.root), 'exec', '--name', 'partial', '--timeout', '5',
            '--', sys.executable, '-B', 'probe.py'])
        self.child_root = self.root / '.rds/exec/partial'

    @property
    def child(self):
        return ProjectStore(self.child_root)

    def crash(self):
        bind = workspace.bind

        def interrupted(root):
            if Path(root).resolve() == self.child_root:
                raise OSError('crash after initialized child before binding')
            return bind(root)

        with patch.dict(os.environ, self.f.env), patch.object(workspace, 'bind', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'crash after initialized'):
                quick.execute(self.args)
        self.assertTrue(self.child.path.is_file())
        self.assertFalse(workspace.pointer_path(self.child.root).exists())
        with self.child._db(True) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM events WHERE json_extract(body,'$.kind')='WORKSPACE_BOUND'").fetchone()[0], 0)

    def dump(self, store):
        with store._db(True) as db:
            rows = list(db.iterdump())
        pointer = workspace.pointer_path(store.root)
        return rows, pointer.read_bytes() if pointer.exists() else None

    def allowances(self):
        with self.parent._db(True) as db:
            return [tuple(row) for row in db.execute(
                "SELECT id,body FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE'")]

    def manifest(self):
        return {'schema': 1, 'id': 'partial', 'arm': 'tool', 'control_id': None,
                'protocol': {'path': 'rds-exec-protocol.json',
                             'sha256': file_sha(self.child.root / 'rds-exec-protocol.json')},
                'argv': self.child.snapshot()['contract']['allowed_commands'][0],
                'outpaths': [], 'timeout_seconds': 4,
                'resource_estimates': {'wall_seconds': 4}}

    def test_gap_inherits_owner_pointer(self):
        self.crash()
        print('gap pointer:', workspace.read_pointer(self.child.root), flush=True)
        self.assertEqual(workspace.read_pointer(self.child.root), workspace.read_pointer(self.root))

    def test_gap_refuses_ordinary_register_execute_and_initialize_without_writes(self):
        self.crash()
        before = self.dump(self.parent), self.dump(self.child)
        operations = [lambda: self.child.register(self.manifest()),
                      lambda: self.child.execute('partial'),
                      lambda: self.child.initialize(self.child.snapshot()['contract'])]
        for operation in operations:
            with self.subTest(operation=operations.index(operation)):
                with self.assertRaisesRegex(ValueError, 'identity does not match'):
                    result = operation()
                    print('UNEXPECTED ordinary operation accepted:', result, flush=True)
        self.assertEqual((self.dump(self.parent), self.dump(self.child)), before)

    def test_cli_recovers_crash_with_exact_allowance_and_no_second_charge(self):
        self.crash()
        allowance, budget = self.allowances(), self.parent.snapshot()['budget']
        args = ['exec', '--name', 'partial', '--timeout', '5', '--', sys.executable, '-B', 'probe.py']
        first = json.loads(self.f.call(*args, root=self.root).stdout)
        receipt = self.child.snapshot()['receipts'][0]
        self.assertEqual(first['run_status'], 'SUCCEEDED')
        self.assertEqual(self.allowances(), allowance)
        self.assertEqual(self.parent.snapshot()['budget'], budget)
        self.f.call(*args, root=self.root)
        self.assertEqual(self.child.snapshot()['receipts'][0]['sha256'], receipt['sha256'])
        self.assertEqual(self.allowances(), allowance)
        self.assertEqual(self.parent.snapshot()['budget'], budget)
        self.assertEqual(len(list((self.root / '.rds/quick-partials').iterdir())), 1)

    def test_bootstrap_refuses_changed_request(self):
        self.crash()
        request = self.child.root / 'rds-exec-request.json'
        body = json.loads(request.read_text(encoding='utf-8'))
        request.write_text(json.dumps({**body, 'changed': True}), encoding='utf-8')
        before = self.dump(self.parent), self.dump(self.child)
        with self.assertRaises(ValueError):
            workspace.bind(self.child.root)
        self.assertEqual((self.dump(self.parent), self.dump(self.child)), before)

    def test_bootstrap_refuses_mismatched_child_budget(self):
        self.crash()
        # A damaged contract is not authority to bootstrap a larger allowance.
        import sqlite3
        with closing(sqlite3.connect(self.child.path)) as db, db:
            contract = json.loads(db.execute('SELECT body FROM contract WHERE id=1').fetchone()[0])
            contract['budget']['wall_seconds'] += 1
            db.execute('DROP TRIGGER contract_no_update')
            db.execute('UPDATE contract SET body=?', (json.dumps(contract),))
        with self.assertRaises(ValueError):
            workspace.bind(self.child.root)

    def test_bootstrap_does_not_admit_reentrant_ordinary_writer(self):
        self.crash()
        before = self.allowances(), self.parent.snapshot()['budget']
        publish = workspace._write_pointer
        checked = []

        def publish_after_check(root, pointer):
            if Path(root).resolve() == self.child.root:
                with self.assertRaisesRegex(ValueError, 'identity does not match'):
                    self.child.register(self.manifest())
                checked.append(True)
            return publish(root, pointer)

        with patch.object(workspace, '_write_pointer', side_effect=publish_after_check):
            self.assertEqual(workspace.bind(self.child.root)['status'], 'BOUND')
        self.assertEqual(checked, [True])
        self.assertEqual((self.allowances(), self.parent.snapshot()['budget']), before)
        self.assertEqual(self.child.snapshot()['runs'], [])

    def test_failed_bootstrap_publication_keeps_owner_and_rolls_back_event(self):
        self.crash()
        before = self.dump(self.parent), self.dump(self.child)
        with patch.object(workspace, '_write_pointer', side_effect=OSError('publication failed')):
            with self.assertRaisesRegex(OSError, 'publication failed'):
                workspace.bind(self.child.root)
        self.assertEqual((self.dump(self.parent), self.dump(self.child)), before)
        self.assertEqual(workspace.read_pointer(self.child.root), workspace.read_pointer(self.root))
        self.assertEqual(workspace.bind(self.child.root)['status'], 'BOUND')

    def test_paid_nested_scopes_keep_their_own_identity(self):
        self.f.call('exec', '--name', 'partial', '--timeout', '5', '--', sys.executable, '-B', 'probe.py', root=self.root)
        (self.child.root / 'nested.py').write_text('print("nested paid")\n', encoding='utf-8')
        self.f.call('exec', '--name', 'nested', '--timeout', '2', '--', sys.executable, '-B', 'nested.py', root=self.child.root)
        nested = self.child.root / '.rds/exec/nested'
        for root in (self.root, self.child.root, nested):
            self.assertEqual(workspace.read_pointer(root), workspace._read_pointer_at(root))


if __name__ == '__main__':
    unittest.main()
