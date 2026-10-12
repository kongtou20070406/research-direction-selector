"""Workspace binding preserves controller holds and unresolved reference work."""
import argparse
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_workspace as workspace
import test_pr301_descendant_binding as fixture


class ControllerBindingTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.DescendantBindingTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def assert_refusal(self, child):
        before = self.f.dump(self.f.store), self.f.dump(child)
        with self.assertRaisesRegex(ValueError, 'active descendant'):
            workspace.bind(self.f.root)
        self.assertEqual((self.f.dump(self.f.store), self.f.dump(child)), before)

    def test_structure_reservation_settles_before_binding(self):
        from rds_structure import reserved_control
        child = self.f.child()
        with reserved_control(child, 'binding-regression', 2):
            self.assertEqual(child.snapshot()['runs'], [])
            self.assertEqual(child.snapshot()['budget']['wall_seconds']['reserved'], 2)
            self.assert_refusal(child)
        self.assertEqual(child.snapshot()['budget']['wall_seconds']['reserved'], 0)
        with child._db(True) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE "
                                        "json_extract(body,'$.kind')='STRUCTURE_CONTROL_FINISHED'").fetchone()[0], 1)
        self.assertEqual(workspace.bind(self.f.root)['status'], 'BOUND')

    def test_autonomy_claim_releases_before_binding(self):
        import rds_autonomy as autonomy
        from rds_project import ProjectStore
        import test_rds_autonomy as autonomy_fixture
        helper = autonomy_fixture.AutonomyTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.root = self.f.root / 'autonomy-child'
        helper.root.mkdir()
        helper.store = child = ProjectStore(helper.root)
        helper.build(control_wall=2)
        claim = autonomy._claim
        owners = []

        def claimed(store, *args, **kwargs):
            owner, allowance = claim(store, *args, **kwargs)
            self.assertEqual(store.snapshot()['runs'], [])
            self.assertGreater(allowance, 0)
            self.assert_refusal(store)
            owners.append(owner)
            return owner, allowance

        with patch.object(autonomy, '_claim', side_effect=claimed):
            autonomy.drive(child, max_steps=1, prepare_only=True)
        self.assertEqual(len(owners), 1)
        self.assertEqual(child.snapshot()['budget']['wall_seconds']['reserved'], 0)
        with child._db(True) as db:
            released = [json.loads(row['body']) for row in db.execute(
                "SELECT body FROM events WHERE json_extract(body,'$.kind')='AUTONOMY_DRIVE_RELEASED'")]
        self.assertEqual([event['owner'] for event in released], owners)
        self.assertEqual(workspace.bind(self.f.root)['status'], 'BOUND')

    def test_recovery_required_reference_remains_unresolved(self):
        import rds_cli as cli
        import test_pr301_reference_checkpoint_admission as reference_fixture
        helper = reference_fixture.ReferenceCheckpointAdmissionTests()
        state, _ = helper.reference(self.f.root / 'reference-child')
        run_process = cli.subprocess.run
        observed = []

        def run(argv, *args, **kwargs):
            if Path(argv[-1]).name == 'rds_probe.py':
                with state.snapshot() as (_, snapshot):
                    lease = snapshot['plans']['P1']['lease_expires_ns']
                with patch.object(cli.time, 'time_ns', return_value=lease + 1):
                    recovered = cli.cmd_recover(argparse.Namespace(id='P1'), state)
                self.assertFalse(recovered['budget_refunded'])
                before = self.f.dump(self.f.store), state.db_path.read_bytes()
                with self.assertRaisesRegex(ValueError, 'active descendant'):
                    workspace.bind(self.f.root)
                self.assertEqual((self.f.dump(self.f.store), state.db_path.read_bytes()), before)
                observed.append(recovered['run_status'])
            return run_process(argv, *args, **kwargs)

        with patch.object(cli.subprocess, 'run', side_effect=run):
            result = cli.cmd_run(argparse.Namespace(id='P1'), state)
        self.assertEqual(observed, ['RECOVERY_REQUIRED'])
        self.assertEqual(result['run_status'], 'RECOVERY_REQUIRED')
        self.assertTrue(result['late_result_discarded'])
        with self.assertRaisesRegex(ValueError, 'active descendant'):
            workspace.bind(self.f.root)
