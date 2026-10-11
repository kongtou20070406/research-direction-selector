"""Canonical workspace admission covers private QUICK and reference writers."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))

from benchmark.run import Project
import rds_cli as cli
from rds_project import ProjectStore
from rds_workspace import bind
from rds_quick import _charge_ledger
import test_rds_project


class WorkspaceAdmissionTests(unittest.TestCase):
    def setUp(self):
        helper = test_rds_project.ProjectTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        self.helper = helper
        self.root, self.store, self.contract = helper.root, helper.store, helper.contract

    def reference(self, name):
        fixture = Project()
        self.addCleanup(fixture.close)
        child = self.root / name
        child.mkdir()
        for source in fixture.root.iterdir():
            shutil.copyfile(source, child / source.name)
        contract_path = child / 'reference-contract.json'
        contract_path.write_text(json.dumps(fixture.contract), encoding='utf-8')
        return child, contract_path, cli.RDSState(child)

    def charged_child(self):
        owner = self.helper.new_store(budget={'wall_seconds': 20})
        bind(owner.root)
        child = owner.root / '.rds' / 'exec' / 'paid'
        child.mkdir(parents=True)
        for binding in self.contract['bindings']:
            shutil.copyfile(owner.root / binding['path'], child / binding['path'])
        request = {'operation': 'declared QUICK request'}
        (child / 'rds-exec-request.json').write_text(json.dumps(request), encoding='utf-8')
        contract = deepcopy(self.contract)
        contract['budget'] = {'wall_seconds': 5}
        return owner, ProjectStore(child), request, contract

    def test_owner_object_cannot_initialize_an_unfunded_quick_child(self):
        bind(self.root)
        before = self.store.snapshot()['budget']
        child = self.root / '.rds' / 'exec' / 'unfunded'
        child.mkdir(parents=True)
        for binding in self.contract['bindings']:
            shutil.copyfile(self.root / binding['path'], child / binding['path'])
        candidate = ProjectStore(child)
        with self.assertRaisesRegex(ValueError, 'charged allowance'):
            candidate.initialize(deepcopy(self.contract), fresh_workspace=self.store)
        self.assertFalse(candidate.path.exists())
        self.assertEqual(self.store.snapshot()['budget'], before)

    def test_matching_allowance_cannot_grant_a_larger_child_budget(self):
        owner, candidate, request, contract = self.charged_child()
        contract['budget']['wall_seconds'] = 6
        with owner._db() as db:
            db.execute('BEGIN IMMEDIATE')
            _charge_ledger(owner.root, candidate.root, request, 5, _parent_db=db)
            before = tuple(db.execute("SELECT * FROM budget WHERE resource='wall_seconds'").fetchone())
            with self.assertRaisesRegex(ValueError, 'budget must match its charged allowance'):
                candidate.initialize(contract, fresh_workspace=owner,
                                     _fresh_workspace_admission=(db, request))
            self.assertEqual(tuple(db.execute("SELECT * FROM budget WHERE resource='wall_seconds'").fetchone()),
                             before)
        self.assertFalse(candidate.path.exists())

    def test_charged_allowance_cannot_admit_a_different_request(self):
        owner, candidate, request, contract = self.charged_child()
        with owner._db() as db:
            db.execute('BEGIN IMMEDIATE')
            _charge_ledger(owner.root, candidate.root, request, 5, _parent_db=db)
            with self.assertRaisesRegex(ValueError, 'does not match its frozen request'):
                candidate.initialize(contract, fresh_workspace=owner,
                                     _fresh_workspace_admission=(db, {'operation': 'different request'}))
        self.assertFalse(candidate.path.exists())

    def test_reference_cli_init_refuses_a_bound_descendant_before_creating_state(self):
        child, contract_path, state = self.reference('legacy-reset')
        bind(self.root)
        before = self.store.snapshot()['budget']
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(child),
             'init', '--contract', str(contract_path)], capture_output=True, text=True,
            encoding='utf-8', timeout=30)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('workspace', result.stderr.lower())
        self.assertFalse(state.directory.exists())
        self.assertEqual(self.store.snapshot()['budget'], before)

    def test_existing_reference_writer_is_refused_after_ancestor_binding(self):
        child, contract_path, state = self.reference('existing-reference')
        cli.cmd_init(argparse.Namespace(contract=str(contract_path)), state)
        before = state.db_path.read_bytes()
        bind(self.root)
        with self.assertRaisesRegex(ValueError, 'workspace'):
            with state.transaction() as (db, _):
                state.event(db, 'UNAUTHORIZED_SECOND_BUDGET')
        self.assertEqual(state.db_path.read_bytes(), before)
        with state.snapshot() as (_, snapshot):
            self.assertEqual(snapshot['budget']['spent']['runs'], 0)


if __name__ == '__main__':
    unittest.main()
