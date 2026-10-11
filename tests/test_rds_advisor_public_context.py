"""Public Advisor roots and choice records retain their effective input identity."""
from copy import deepcopy
from pathlib import Path
import shutil
import sys
import unittest

import test_rds_owned_advisor as owned_fixture
import test_rds_quick as quick_fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_advisor import RDSAdvisor
from rds_cli import RDSState, RESOURCES, VERSION
from rds_project import ProjectStore, digest
from rds_project_lifecycle import initialize
from rds_quick import record_choice
from rds_tms_store import current, save


class PublicAdvisorRootTests(unittest.TestCase):
    def setUp(self):
        self.f = owned_fixture.OwnedAdvisorCLITests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.initialize()

    def test_full_ancestor_cannot_be_hidden_by_local_reference_or_dependencies(self):
        before = self.f.snapshot()
        for kind in ('empty', 'reference', 'dependencies', 'missing'):
            with self.subTest(child=kind):
                child = self.f.root / kind
                if kind != 'missing':
                    child.mkdir()
                if kind == 'reference':
                    with RDSState(child).transaction(create=True) as (_, state):
                        state.update(version=VERSION, contract={}, contract_sha256=digest({}), plans={},
                                     budget={field: {name: 0 for name in RESOURCES}
                                             for field in ('limits', 'spent', 'reserved')})
                if kind == 'dependencies':
                    save(child, {'schema': 1, 'nodes': [], 'hyperedges': [], 'goals': []}, expected=None)
                token = current(child) if child.is_dir() else None
                local_before = sorted(str(p.relative_to(child)) for p in child.rglob('*')) if child.is_dir() else None
                advisor = RDSAdvisor(child)
                with self.assertRaisesRegex(ValueError, 'Advisor must use the existing FULL project root'):
                    advisor.recommend_next_directions({'advisor_context': {'decision': 'fake'}},
                                                      deepcopy(self.f.policy['graph']))
                with self.assertRaisesRegex(ValueError, 'Advisor must use the existing FULL project root'):
                    advisor.effective_context({'decision': 'fake'})
                if child.is_dir():
                    self.assertEqual(current(child), token)
                else:
                    self.assertFalse(child.exists())
                if local_before is not None:
                    self.assertEqual(sorted(str(p.relative_to(child)) for p in child.rglob('*')), local_before)
                self.assertEqual(self.f.snapshot(), before)

    def test_current_full_uses_owned_collection_and_independent_child_uses_its_contract(self):
        advisor = RDSAdvisor(self.f.root)
        recommendations = advisor.recommend_next_directions(
            {'advisor_context': {'decision': 'fake', 'facts': {'baseline.score': {'value': 99}}}},
            deepcopy(self.f.policy['graph']))
        self.assertIsInstance(recommendations, list)
        search = next(row['search'] for row in recommendations if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
        self.assertTrue(search['analysis_coverage']['full'])
        self.assertEqual(self.f.starts(), [])
        with self.assertRaisesRegex(ValueError, 'complete frozen direction graph'):
            advisor.recommend_next_directions({}, {'nodes': [], 'edges': []})
        child = self.f.root / 'independent'
        child.mkdir()
        contract = deepcopy(self.f.contract)
        del contract['advisor_policy']
        for binding in contract['bindings']:
            shutil.copyfile(self.f.root / binding['path'], child / binding['path'])
        initialize(ProjectStore(child), contract, mode='quick',
                   separate_reason='Independent synthetic API fixture with its own declared contract')
        context = {'decision': 'next'}
        result = RDSAdvisor(child).recommend_next_directions(
            {**ProjectStore(child).snapshot(), 'advisor_context': context}, deepcopy(self.f.policy['graph']))
        self.assertIsInstance(result, list)
        self.assertTrue(any(row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH' for row in result))
        self.assertEqual(ProjectStore(child).snapshot()['runs'], [])


class PublicAdvisorContextTests(unittest.TestCase):
    def setUp(self):
        self.f = quick_fixture.QuickTests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.initialize_ledger()

    def test_saved_dependencies_are_available_to_raw_context_choice_without_new_execution(self):
        root = self.f.ledger
        spec = {'schema': 1, 'nodes': [], 'hyperedges': [], 'goals': []}
        token = save(root, spec, expected=None)
        advisor = RDSAdvisor(root)
        raw = deepcopy(self.f.context)
        original = deepcopy(raw)
        context = advisor.effective_context(raw)
        self.assertEqual(context['dependency_snapshot_sha256'], token)
        self.assertIn('dependency_map', context)
        self.assertNotIn('dependency_map', raw)
        context['dependency_map']['nodes'].append({'id': 'caller-mutation'})
        self.assertEqual(advisor.effective_context(raw)['dependency_map']['nodes'], [])
        snapshot = ProjectStore(root).snapshot()
        recommendations = advisor.recommend_next_directions(
            {**snapshot, 'advisor_context': raw}, deepcopy(self.f.graph))
        search = next(row['search'] for row in recommendations if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
        self.assertEqual(search['context_sha256'], digest(advisor.effective_context(raw)))
        advice = {'recommendations': recommendations}
        selected = record_choice(root, advice, raw, 'inspect-x', 'public-choice')
        self.assertEqual(selected['candidate_id'], search['candidates'][0]['id'])
        self.assertEqual(raw, original)
        changed = {'schema': 1, 'nodes': [{'id': 'later', 'status': 'UNKNOWN', 'source': 'synthetic'}],
                   'hyperedges': [], 'goals': []}
        next_token = save(root, changed, expected=token)
        with self.assertRaisesRegex(ValueError, 'changed after'):
            record_choice(root, advice, raw, 'inspect-x', 'stale-public-choice')
        self.assertEqual(current(root)['sha256'], next_token)
        after = ProjectStore(root).snapshot()
        for key in ('runs', 'receipts', 'budget'):
            self.assertEqual(after[key], snapshot[key])
        with ProjectStore(root)._db(True) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM checkpoints').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
