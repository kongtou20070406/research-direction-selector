"""Ancestor binding cannot revoke recovery of an already charged QUICK job."""
from copy import deepcopy
import os
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_workspace as workspace
from rds_project import ProjectStore
from rds_quick import _charge_ledger
import test_pr301_descendant_binding as fixture


class PendingQuickAllowanceTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.DescendantBindingTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        root = self.f.root / 'paid-owner'
        root.mkdir()
        contract = deepcopy(self.f.helper.contract)
        contract['budget'] = {'wall_seconds': 20}
        for binding in contract['bindings']:
            shutil.copyfile(self.f.root / binding['path'], root / binding['path'])
        self.owner = ProjectStore(root)
        self.owner.initialize(contract)
        self.job = root / '.rds/exec/not-materialized'
        self.request = {'original': 'paid QUICK request'}

    def test_pending_allowance_retains_original_recovery_without_second_charge(self):
        _charge_ledger(self.owner.root, self.job, self.request, 5)
        budget = self.owner.snapshot()['budget']
        self.assertEqual(budget['wall_seconds']['reserved'], 0)
        self.assertEqual(budget['wall_seconds']['charged_estimate'], 5)
        before = self.f.dump(self.f.store), self.f.dump(self.owner)
        with self.assertRaisesRegex(ValueError, 'active descendant'):
            workspace.bind(self.f.root)
        self.assertEqual((self.f.dump(self.f.store), self.f.dump(self.owner)), before)
        result = _charge_ledger(self.owner.root, self.job, self.request, 5)
        self.assertEqual(result['status'], 'PENDING_MATERIALIZATION_RECOVERY')
        self.assertEqual(self.owner.snapshot()['budget'], budget)
        self.assertFalse(self.job.exists())

    def test_non_dispatch_charge_is_not_a_pending_materialization(self):
        _charge_ledger(self.owner.root, self.owner.root, self.request, 1, dispatch=False)
        self.assertEqual(workspace.bind(self.f.root)['status'], 'BOUND')

    def test_materialized_completed_allowance_permits_ancestor_binding(self):
        import rds_cli
        import rds_quick
        import test_rds_quick as quick_fixture
        helper = quick_fixture.QuickTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.initialize_policy_ledger()
        args = rds_cli.parser().parse_args([
            '--root', str(helper.ledger), 'exec', '--name', 'settled', '--timeout', '5',
            '--', sys.executable, '-B', 'probe.py'])
        with patch.dict(os.environ, helper.env):
            result = rds_quick.execute(args)
        self.assertEqual(result['receipt']['run_status'], 'SUCCEEDED')
        for binding in self.f.helper.contract['bindings']:
            shutil.copyfile(self.f.root / binding['path'], helper.root / binding['path'])
        ProjectStore(helper.root).initialize(self.f.helper.contract)
        self.assertEqual(workspace.bind(helper.root)['status'], 'BOUND')


if __name__ == '__main__':
    unittest.main()
