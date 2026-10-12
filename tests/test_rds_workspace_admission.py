"""Canonical workspace admission covers private QUICK and reference writers."""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))

from benchmark.run import Project
import rds_cli as cli
from rds_project import ProjectStore
from rds_workspace import _arg, bind, check_admission, coverage, pointer_path
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

    def nested_project(self):
        child = self.root / 'preexisting'
        child.mkdir()
        for binding in self.contract['bindings']:
            shutil.copyfile(self.root / binding['path'], child / binding['path'])
        store = ProjectStore(child)
        store.initialize(deepcopy(self.contract))
        return store

    def test_preexisting_nested_ledger_cannot_shadow_new_ancestor_binding(self):
        child = self.nested_project()
        bind(self.root)
        before = child.path.read_bytes()
        with self.assertRaisesRegex(ValueError, 'identity does not match'):
            child.register(self.helper.spec('r1', 'nonzero'))
        self.assertEqual(child.path.read_bytes(), before)

        self.assertEqual(coverage(child.root)['status'], 'MISMATCH')

    def test_generic_writer_refuses_inherited_binding_before_budget_mutation(self):
        child = self.nested_project()
        bind(self.root)
        before = child.snapshot()
        with self.assertRaisesRegex(ValueError, 'identity does not match'):
            with child._db() as db:
                db.execute("UPDATE budget SET charged=charged+1 WHERE resource='wall_seconds'")
        self.assertEqual(child.snapshot(), before)

    def test_existing_writer_rechecks_new_ancestor_binding_at_commit(self):
        child = self.nested_project()
        before = child.snapshot()
        with self.assertRaisesRegex(ValueError, 'identity does not match'):
            with child._db() as db:
                bind(self.root)
                db.execute("UPDATE budget SET charged=charged+1 WHERE resource='wall_seconds'")
        after = child.snapshot()
        after.pop('workspace_binding', None)
        before.pop('workspace_binding', None)
        self.assertEqual(after, before)

    def test_structure_request_cannot_charge_a_preexisting_nested_ledger(self):
        import rds_structure
        from test_rds_structure import fixture
        root = fixture.prepare(self.root / 'structure-child')
        child = ProjectStore(root)
        bind(self.root)
        before = child.snapshot()
        with self.assertRaisesRegex(ValueError, 'identity does not match'):
            rds_structure.request(root)
        self.assertEqual(child.snapshot(), before)
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts' / 'rds_cli.py'),
             '--root', str(root), 'structure', 'request'],
            capture_output=True, text=True, encoding='utf-8', timeout=30,
            env={**os.environ, 'RDS_USAGE_LOG': '0'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('identity does not match', result.stdout + result.stderr)
        self.assertEqual(child.snapshot(), before)

    def test_recursive_pointer_coverage_returns_json_mismatch(self):
        bind(self.root)
        pointer_path(self.root).write_text('[' * 5000 + '0' + ']' * 5000, encoding='utf-8')
        report = coverage(self.root)
        self.assertEqual(report['status'], 'MISMATCH')
        self.assertNotIn('ledger_sha256', report)
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts' / 'rds_cli.py'),
             '--root', str(self.root), 'workspace', 'coverage'],
            capture_output=True, text=True, encoding='utf-8', timeout=30,
            env={**os.environ, 'RDS_USAGE_LOG': '0'})
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'MISMATCH')

    def test_oversized_pointer_is_refused_before_json_parsing(self):
        bind(self.root)
        pointer_path(self.root).write_text('[' * 100000 + '0' + ']' * 100000, encoding='utf-8')
        with patch('rds_workspace.json.loads', wraps=json.loads) as parse:
            report = coverage(self.root)
        self.assertEqual(report['status'], 'MISMATCH')
        self.assertNotIn('ledger_sha256', report)
        self.assertIn('size', report['mismatch'])
        parse.assert_not_called()

    def test_preexisting_nested_pointer_cannot_shadow_new_ancestor_binding(self):
        child = self.nested_project()
        bind(child.root)
        original = pointer_path(child.root).read_bytes()
        bind(self.root)
        with self.assertRaisesRegex(ValueError, 'identity does not match'):
            check_admission(child.root)
        self.assertEqual(coverage(child.root)['status'], 'MISMATCH')
        self.assertEqual(pointer_path(child.root).read_bytes(), original)

    def test_grandchild_of_preexisting_ledger_still_inherits_ancestor_binding(self):
        child = self.nested_project()
        bind(self.root)
        grandchild = child.root / 'another-ledger'
        grandchild.mkdir()
        for binding in self.contract['bindings']:
            shutil.copyfile(self.root / binding['path'], grandchild / binding['path'])
        with self.assertRaisesRegex(ValueError, 'bound to a project ledger that is now missing'):
            ProjectStore(grandchild).initialize(deepcopy(self.contract))
        self.assertFalse(ProjectStore(grandchild).path.exists())

    def test_existing_exec_directory_with_frozen_request_is_not_a_paid_scope(self):
        child = self.root / '.rds' / 'exec' / 'preexisting'
        child.mkdir(parents=True)
        for binding in self.contract['bindings']:
            shutil.copyfile(self.root / binding['path'], child / binding['path'])
        contract = deepcopy(self.contract)
        contract['budget'] = {'wall_seconds': 5}
        candidate = ProjectStore(child)
        candidate.initialize(contract)
        (child / 'rds-exec-request.json').write_text('{"operation":"unfunded"}', encoding='utf-8')
        bind(self.root)
        with self.assertRaisesRegex(ValueError, 'identity does not match'):
            check_admission(child)
        self.assertEqual(coverage(child)['status'], 'MISMATCH')

    def test_malformed_pointer_coverage_is_json_mismatch_without_invented_hashes(self):
        bind(self.root)
        before = self.store.path.read_bytes()
        pointer_path(self.root).write_text('{broken', encoding='utf-8')
        report = coverage(self.root)
        self.assertEqual(report['status'], 'MISMATCH')
        self.assertIn('unreadable', report['mismatch'])
        self.assertNotIn('ledger_sha256', report)
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.root),
             'workspace', 'coverage'], capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'MISMATCH')
        self.assertEqual(self.store.path.read_bytes(), before)

    def test_unreadable_pointer_coverage_retains_diagnostic(self):
        bind(self.root)
        with patch('rds_bounded_io.read_regular_bytes',
                   side_effect=PermissionError('pointer denied')) as reader:
            report = coverage(self.root)
        reader.assert_called_once()
        self.assertEqual(report['status'], 'MISMATCH')
        self.assertIn('pointer denied', report['mismatch'])
        self.assertNotIn('ledger_sha256', report)

    def test_posix_suggested_argument_quotes_all_shell_syntax(self):
        for value in ('/tmp/rds;next', '/tmp/rds$(value)', '/tmp/rds`value`', '/tmp/rds\tvalue', ''):
            with self.subTest(value=value), patch('rds_project.os.name', 'posix'):
                rendered = _arg(value)
                self.assertEqual(rendered, shlex.quote(value))
                self.assertEqual(shlex.split(rendered), [value])

    def test_windows_suggested_argument_uses_powershell_apostrophes(self):
        with patch('rds_project.os.name', 'nt'):
            self.assertEqual(_arg("D:\\O'Brien project"), "'D:\\O''Brien project'")

    @unittest.skipUnless(os.name == 'nt' and shutil.which('pwsh'), 'PowerShell host unavailable')
    def test_windows_suggested_argument_roundtrips_through_powershell(self):
        value = "D:\\O'Brien project"
        result = subprocess.run([shutil.which('pwsh'), '-NoProfile', '-Command',
                                 '[Console]::Out.Write(' + _arg(value) + ')'],
                                capture_output=True, text=True, encoding='utf-8', timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, value)


if __name__ == '__main__':
    unittest.main()
