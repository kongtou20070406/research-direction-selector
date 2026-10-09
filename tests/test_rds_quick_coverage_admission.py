"""Real QUICK admission retains a charged job when the parent graph changes."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import rds_cli
import rds_quick
from rds_advisor_coverage import project_context
from rds_project import ProjectStore
from rds_tms_store import current, save
import test_rds_quick as fixture_module


class QuickCoverageAdmissionTests(unittest.TestCase):
    def setUp(self):
        # Composition avoids collecting or rerunning QuickTests' old suite.
        self.fixture = fixture_module.QuickTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.initialize_ledger()

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
