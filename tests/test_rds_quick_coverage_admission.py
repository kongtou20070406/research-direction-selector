"""Real QUICK admission retains a charged job when the parent graph changes."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import rds_cli
import rds_quick
from rds_advisor_coverage import project_context
from rds_project import ProjectStore, digest
from rds_tms_store import current, save
import test_rds_quick as fixture_module


class QuickCoverageAdmissionTests(unittest.TestCase):
    def setUp(self):
        # Composition avoids collecting or rerunning QuickTests' old suite.
        self.fixture = fixture_module.QuickTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.initialize_ledger()

    def reviewed_job(self, name, source_root=None):
        fixture = self.fixture
        source_root = source_root or fixture.root
        (source_root / 'probe.py').write_text(
            'from pathlib import Path\nPath("launch-marker").write_text("started")\n', encoding='utf-8')
        args = rds_cli.parser().parse_args([
            '--root', str(source_root), 'exec', '--name', name, '--timeout', '5',
            '--context', str(fixture.context_path), '--graph', str(fixture.graph_path),
            '--ledger', str(fixture.ledger), '--', sys.executable, '-B', 'probe.py'])
        review_args = argparse.Namespace(root=str(fixture.ledger),
            research_context=str(fixture.context_path), graph=str(fixture.graph_path), saved_dependencies=False)
        advice = rds_cli.cmd_advise(review_args, rds_cli.RDSState(fixture.ledger))
        context = project_context(fixture.ledger, json.loads(fixture.context_path.read_text(encoding='utf-8')))
        return args, (advice, context), source_root / '.rds' / 'exec' / name

    def assert_held_child_and_original_accounting(self, workspace, before, at_admission):
        child = ProjectStore(workspace).snapshot()
        self.assertEqual(len(child['runs']), 1)
        self.assertEqual(child['runs'][0]['status'], 'RESERVED')
        self.assertIsNone(child['runs'][0]['attempt_id'])
        self.assertIsNone(child['runs'][0]['started_at'])
        self.assertEqual(child['receipts'], [])
        self.assertFalse((workspace / 'launch-marker').exists())
        after = ProjectStore(self.fixture.ledger).snapshot()
        self.assertEqual(after['budget'], at_admission)
        for key in ('runs', 'receipts', 'exposures', 'contract_sha256'):
            self.assertEqual(after[key], before[key])
        original, charged = before['budget']['wall_seconds'], after['budget']['wall_seconds']
        self.assertEqual(charged['charged_estimate'], original['charged_estimate'] + 5)
        for key in ('spent_measured', 'reserved', 'cap'):
            self.assertEqual(charged[key], original[key])
        self.assertAlmostEqual(charged['remaining'], original['remaining'] - 5)

    def test_owner_pause_after_preparation_blocks_actual_attempt_without_refunding(self):
        from rds_steering import submit, current as current_steering
        args, reviewed, workspace = self.reviewed_job('pause-race')
        parent = ProjectStore(self.fixture.ledger)
        before, observed = parent.snapshot(), {}
        original_execute = ProjectStore.execute

        def pause_before_admission(store, run_id, *positional, **keywords):
            self.assertEqual(store.root, workspace.resolve())
            self.assertEqual(observed, {})
            self.assertTrue(callable(keywords.get('admission_guard')))
            observed['budget'] = parent.snapshot()['budget']
            observed['pause'] = submit(parent, {'id': 'pause-before-launch', 'kind': 'pause',
                'message': 'Current user stops new dispatch', 'expected_revision': None,
                'contract_sha256': before['contract_sha256']},
                user_directed=True, source='current-user:synthetic-admission-test')
            return original_execute(store, run_id, *positional, **keywords)

        with mock.patch.object(ProjectStore, 'execute', new=pause_before_admission):
            with self.assertRaisesRegex(ValueError, 'Human steering changed before quick admission'):
                rds_quick.execute(args, review=reviewed)
        self.assert_held_child_and_original_accounting(workspace, before, observed['budget'])
        with parent._db(True) as db:
            pause = current_steering(db)
        self.assertEqual(pause['sha256'], observed['pause']['received_revision'])
        self.assertTrue(pause['dispatch']['paused'])

    def test_new_source_quick_policies_after_preparation_block_actual_attempt(self):
        fixture = self.fixture
        parent = ProjectStore(fixture.ledger)
        for key, value in (
                ('execution_policy', {'schema': 1, 'max_attempts': 1}),
                ('stop_policy', {'schema': 1, 'wall_seconds': 10,
                                 'progress': {'window_seconds': 1, 'min_bytes': 1}})):
            with self.subTest(policy=key):
                # Copy valid original role/protocol bytes under distinct source
                # names; the late contract does not relabel the child's inputs.
                source_root = fixture.root / ('source-' + key)
                source_root.mkdir()
                declared = deepcopy(parent.snapshot()['contract'])
                for binding in declared['bindings']:
                    original_path = parent._path(binding['path'])
                    binding['path'] = 'declared-' + binding['path']
                    destination = source_root / binding['path']
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(original_path, destination)
                declared['allowed_commands'] = [[sys.executable, '-B', 'declared-probe.py']]
                declared[key] = value
                args, reviewed, workspace = self.reviewed_job('new-' + key, source_root)
                before, observed = parent.snapshot(), {}
                source = ProjectStore(source_root)
                original_execute = ProjectStore.execute

                def initialize_before_admission(store, run_id, *positional, **keywords):
                    self.assertEqual(store.root, workspace.resolve())
                    self.assertEqual(observed, {})
                    observed['budget'] = parent.snapshot()['budget']
                    observed['source'] = source.initialize(declared)
                    return original_execute(store, run_id, *positional, **keywords)

                with mock.patch.object(ProjectStore, 'execute', new=initialize_before_admission):
                    with self.assertRaisesRegex(ValueError, 'Quick parent contract changed before admission'):
                        rds_quick.execute(args, review=reviewed)
                self.assert_held_child_and_original_accounting(workspace, before, observed['budget'])
                self.assertEqual(source.snapshot(), observed['source'])
                self.assertEqual(source.snapshot()['contract_sha256'], digest(declared))

    def test_new_source_maintenance_contract_blocks_actual_attempt(self):
        import test_rds_project as project_fixture
        helper = project_fixture.StopPolicyAndMaintenanceTests(methodName='runTest')
        helper.setUp()
        self.addCleanup(helper.tearDown)
        args, reviewed, workspace = self.reviewed_job('maintenance-race', helper.root)
        parent = ProjectStore(self.fixture.ledger)
        before, observed = parent.snapshot(), {}
        original_execute = ProjectStore.execute

        def initialize_before_admission(store, run_id, *positional, **keywords):
            self.assertEqual(store.root, workspace.resolve())
            self.assertEqual(observed, {})
            observed['budget'] = parent.snapshot()['budget']
            # Reuse the established original-goal/config-bound maintenance
            # declaration; do not invent a bypassing maintenance policy shape.
            source = helper.contract_with(maintenance_allowance={
                'schema': 1, 'wall_seconds': 1, 'max_uses': 1})
            observed['source'] = source.snapshot()
            return original_execute(store, run_id, *positional, **keywords)

        with mock.patch.object(ProjectStore, 'execute', new=initialize_before_admission):
            with self.assertRaisesRegex(ValueError, 'Quick parent contract changed before admission'):
                rds_quick.execute(args, review=reviewed)
        self.assert_held_child_and_original_accounting(workspace, before, observed['budget'])
        self.assertEqual(helper.store.snapshot(), observed['source'])
        self.assertIn('maintenance_allowance', observed['source']['contract'])

    def test_source_advisor_activation_after_preparation_invalidates_effective_contract(self):
        import test_rds_project_lifecycle as lifecycle_fixture
        from rds_project_lifecycle import enable_advisor
        helper = lifecycle_fixture.ProjectLifecycleTests(methodName='runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.init_quick()
        source = helper.store
        source_before = source.snapshot()
        args, reviewed, workspace = self.reviewed_job('activation-race', helper.root)
        parent = ProjectStore(self.fixture.ledger)
        before, observed = parent.snapshot(), {}
        original_execute = ProjectStore.execute

        def activate_before_admission(store, run_id, *positional, **keywords):
            self.assertEqual(store.root, workspace.resolve())
            self.assertEqual(observed, {})
            observed['budget'] = parent.snapshot()['budget']
            preview = enable_advisor(source, helper.policy)
            observed['activation'] = enable_advisor(source, helper.policy, apply=True,
                                                    expected_snapshot=preview['snapshot_sha256'])
            return original_execute(store, run_id, *positional, **keywords)

        with mock.patch.object(ProjectStore, 'execute', new=activate_before_admission):
            with self.assertRaisesRegex(ValueError, 'Quick parent contract changed before admission'):
                rds_quick.execute(args, review=reviewed)
        self.assert_held_child_and_original_accounting(workspace, before, observed['budget'])
        after = source.snapshot()
        self.assertNotEqual(after['contract_sha256'], source_before['contract_sha256'])
        self.assertEqual(after['contract_sha256'], observed['activation']['contract_sha256'])
        for key in ('budget', 'runs', 'receipts', 'exposures'):
            self.assertEqual(after[key], source_before[key])

    def test_tms_change_after_final_choice_rejects_actual_attempt_admission(self):
        fixture = self.fixture
        fixture.script('from pathlib import Path\nPath("launch-marker").write_text("started")\n')
        initial = {'schema': 1, 'nodes': [
            {'id': 'declared', 'status': 'SUPPORTED', 'source': {'locator': 'synthetic declared premise'}}],
            'hyperedges': [], 'goals': []}
        initial_sha = save(fixture.ledger, initial, expected=None, source_base=fixture.ledger)
        args = rds_cli.parser().parse_args([
            '--root', str(fixture.root), 'exec', '--name', 'coverage-race', '--timeout', '5',
            '--context', str(fixture.context_path), '--graph', str(fixture.graph_path),
            '--ledger', str(fixture.ledger), '--', sys.executable, '-B', 'probe.py'])
        review_args = argparse.Namespace(root=str(fixture.ledger),
            research_context=str(fixture.context_path), graph=str(fixture.graph_path), saved_dependencies=False)
        advice = rds_cli.cmd_advise(review_args, rds_cli.RDSState(fixture.ledger))
        context = project_context(fixture.ledger, json.loads(fixture.context_path.read_text(encoding='utf-8')))
        search = next(row['search'] for row in advice['recommendations']
                      if row['type'] == 'EXECUTABLE_DIRECTION_SEARCH')
        self.assertTrue(search['analysis_coverage']['full'])
        self.assertEqual(context['dependency_snapshot_sha256'], initial_sha)

        parent = ProjectStore(fixture.ledger)
        before = parent.snapshot()
        workspace = fixture.root / '.rds' / 'exec' / 'coverage-race'
        original_execute = ProjectStore.execute
        observed = {}

        def update_before_real_admission(store, run_id, *positional, **keywords):
            # This is after quick.execute's final outer choice, but before the
            # original execute method invokes its actual admission callback.
            self.assertEqual(store.root, workspace.resolve())
            self.assertEqual(run_id, 'coverage-race')
            self.assertTrue(callable(keywords.get('admission_guard')))
            self.assertEqual(observed, {})
            at_entry = store.snapshot()['runs'][0]
            self.assertEqual(at_entry['status'], 'RESERVED')
            self.assertIsNone(at_entry['attempt_id'])
            observed['budget'] = parent.snapshot()['budget']
            changed = deepcopy(initial)
            changed['nodes'].append({'id': 'new-premise', 'status': 'UNKNOWN',
                                     'source': {'locator': 'concurrent synthetic declaration'}})
            observed['map'] = changed
            observed['sha256'] = save(fixture.ledger, changed, expected=initial_sha,
                                     source_base=fixture.ledger)
            return original_execute(store, run_id, *positional, **keywords)

        with mock.patch.object(ProjectStore, 'execute', new=update_before_real_admission):
            with self.assertRaisesRegex(ValueError, 'Dependency snapshot changed after analysis'):
                rds_quick.execute(args, review=(advice, context))

        self.assertNotEqual(observed['sha256'], initial_sha)
        retained = ProjectStore(workspace).snapshot()
        self.assertEqual(len(retained['runs']), 1)
        self.assertEqual(retained['runs'][0]['status'], 'RESERVED')
        self.assertIsNone(retained['runs'][0]['attempt_id'])
        self.assertIsNone(retained['runs'][0]['started_at'])
        self.assertEqual(retained['receipts'], [])
        self.assertFalse((workspace / 'launch-marker').exists())
        after = parent.snapshot()
        self.assertEqual(after['budget'], observed['budget'])
        self.assertEqual(after['runs'], before['runs'])
        self.assertEqual(after['receipts'], before['receipts'])
        self.assertEqual(after['contract_sha256'], before['contract_sha256'])
        previous = before['budget']['wall_seconds']
        charged = after['budget']['wall_seconds']
        self.assertEqual(charged['charged_estimate'], previous['charged_estimate'] + 5)
        self.assertEqual(charged['spent_measured'], previous['spent_measured'])
        self.assertEqual(charged['reserved'], previous['reserved'])
        self.assertEqual(charged['cap'], previous['cap'])
        self.assertAlmostEqual(charged['remaining'], previous['remaining'] - 5)
        latest = current(fixture.ledger)
        self.assertEqual(latest['sha256'], observed['sha256'])
        self.assertEqual(latest['dependency_map'], observed['map'])


if __name__ == '__main__':
    unittest.main()
