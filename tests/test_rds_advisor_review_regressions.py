"""Actual entry-point regressions for complete and nonpersistent Advisor review."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from rds_advisor_search import search_directions
from rds_owned_advisor import review
from rds_project import ProjectStore
from rds_quick import choice
from rds_tms_store import current
import test_rds_advisor_search as search_fixture
import test_rds_cli_input_errors as cli_fixture
import test_rds_owned_advisor as owned_fixture


class AdvisorReviewRegressionTests(unittest.TestCase):
    def test_nonobject_context_is_a_controlled_cli_rejection(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for value in ([], None, 'not-an-object'):
                with self.subTest(value=value):
                    context = root / 'context.json'
                    context.write_text(json.dumps(value), encoding='utf-8')
                    result = cli_fixture.run_cli(root, 'advise', '--research-context', str(context))
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('[RDS-REJECT]', result.stderr)
                    self.assertNotIn('Traceback', result.stderr)
                    self.assertFalse((root / '.rds/project.sqlite3').exists())

    def fallback_search(self, fallback, role):
        disconnected = search_fixture.node('disconnected')
        disconnected['executable']['decisions'] = []
        disconnected['executable'][role] = [
            {'fact': 'condition', 'value': True, 'on_false': fallback}]
        context = {'decision': {'id': 'choose', 'goal_revision': 'review-regression',
                               'scope': {'domain': 'synthetic-software'}},
                   'facts': {'condition': search_fixture.fact(False)}}
        graph = {'nodes': [search_fixture.node('root'), disconnected], 'edges': []}
        return context, search_directions(graph, context)

    def test_invalid_active_disconnected_fallback_blocks_complete_choice(self):
        for role in ('preconditions', 'satisfied_when'):
            with self.subTest(role=role):
                context, search = self.fallback_search({'id': 'malformed'}, role)
                self.assertFalse(search['analysis_coverage']['full'])
                with self.assertRaisesRegex(ValueError, 'Complete graph analysis required'):
                    choice({'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH',
                                                 'search': search}]}, context, 'root:root-test')

    def test_valid_disconnected_fallback_is_analyzed_without_becoming_a_candidate(self):
        fallback = deepcopy(search_fixture.node('repair')['executable']['action'])
        context, search = self.fallback_search(fallback, 'preconditions')
        self.assertTrue(search['analysis_coverage']['full'])
        row = next(row for row in search['graph_coverage']['analyzed_nodes']
                   if row['id'] == 'disconnected')
        self.assertTrue(row['fallback_actions'][0]['action_validation']['valid'])
        self.assertNotIn('repair-test', [row['action']['id'] for row in search['candidates']])
        self.assertEqual(choice({'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH',
                                                      'search': search}]}, context)['candidate']['id'],
                         'root:root-test')

    def test_nonpersistent_owned_review_uses_fresh_collected_map_without_saving(self):
        fixture = owned_fixture.OwnedAdvisorCLITests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.initialize()
        store = ProjectStore(fixture.root)
        review(store)
        fixture.create()
        saved = current(fixture.root)
        before = store.snapshot()
        result = review(store, persist=False)
        self.assertTrue(result['analysis_coverage']['full'])
        self.assertNotEqual(result['context']['dependency_map'], saved['dependency_map'])
        self.assertEqual(current(fixture.root), saved)
        self.assertEqual(store.snapshot(), before)
        self.assertEqual(fixture.starts(), [])

    def test_legacy_init_does_not_advertise_a_quick_project(self):
        fixture = cli_fixture.CLIInputErrorTests(methodName='runTest')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        result = cli_fixture.run_cli(fixture.root, 'init', '--contract',
                                     str(fixture.root / 'contract.json'))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('workflow', json.loads(result.stdout))
        self.assertNotIn('mode=QUICK', result.stderr)
        self.assertTrue((fixture.root / '.rds/state.sqlite3').is_file())
        self.assertFalse((fixture.root / '.rds/project.sqlite3').exists())


if __name__ == '__main__':
    unittest.main()
