"""Finite engineering trajectories; no model or scientific efficacy claim."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_project import ProjectStore
import rds_search_allocation as allocation
import rds_structure as structure

spec = importlib.util.spec_from_file_location('neighborhood_fixture', REPO / 'examples/adaptive-search/run.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class NeighborhoodTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.policy = {**fixture.policy(), 'strategy': allocation.NEIGHBORHOOD, 'stagnation_trials': 2}
        self.root = fixture.prepare(Path(self.tmp.name) / 'project', search_policy=self.policy)
        self.store = ProjectStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def request(self, public=False):
        return (fixture.cli(self.root, 'structure', 'request') if public else structure.request(self.root))['tasks'][0]

    def proposal(self, task, ident, mode, kind, anchor=None):
        value = fixture.proposal(self.root, task, ident, mode, kind)
        # The independent numeric oracle remains unchanged. These hypotheses ask
        # whether error is below 100, separately from the original exact goal.
        value['discriminator']['proposal']['value'] = 100
        value['discriminator']['rival']['value'] = 100
        value['prediction'].update(if_proposal='finite MSE below 100', if_rival='finite MSE at least 100')
        if anchor is not None:
            value['search']['anchor_proposal_id'] = anchor
            observed = next(e for e in task['search_allocation']['comparisons'] if e['proposal_id'] == anchor)
            value['trigger'] = {'proposal_id': anchor, 'feedback_sha256': observed['feedback_sha256'],
                                'observation': observed['observation'], 'purpose': 'ALTERNATIVE'}
        return value

    def execute(self, ident='baseline', mode='zero', kind='explore', anchor=None, public=False):
        task = self.request(public)
        value = self.proposal(task, ident, mode, kind, anchor)
        if public:
            path = self.root / (ident + '-proposal.json')
            path.write_text(json.dumps(value), encoding='utf-8')
            fixture.cli(self.root, 'structure', 'propose', '--proposal', str(path))
            chosen = fixture.cli(self.root, 'structure', 'next')
            self.assertEqual(chosen['selected'], ident)
            return fixture.cli(self.root, 'structure', 'advance', '--id', ident)
        structure.propose(self.root, value)
        return structure.advance(self.root, ident)

    def incumbent(self):
        self.execute()
        self.execute('p1', 'linear')
        self.execute('p2', 'linear', 'evidence')

    def inputs(self):
        task = self.request()
        feedback = structure._verified_feedback(self.store, task['scope_sha256'])
        rows = {f['id']: structure._find(self.store, 'PROPOSAL', f['id']) for f in feedback}
        return rows, feedback, self.store.snapshot()

    def test_good_point_bad_direction_and_repeat_anchor(self):
        self.incumbent()
        self.execute('p3', 'overshoot', 'refine')
        pending = self.request()['search_allocation']
        self.assertEqual(pending['neighborhood']['evidence_parent'], 'p3')
        self.execute('p4', 'overshoot', 'evidence')
        plan = self.request()['search_allocation']
        for item in plan['neighborhood']['comparisons'][-2:]:
            self.assertEqual(item['baseline_improvement'], '16')
            self.assertEqual(item['parent_improvement'], '-13/2')
            self.assertEqual(item['parent_proposal_id'], 'p2')
        self.assertEqual(plan['selected_parent'], 'p2')
        self.assertEqual(plan['neighborhood']['trials'][0]['outcome'], 'STAGNANT')

    def test_bad_point_good_direction_via_public_cli(self):
        self.execute(public=True)
        self.execute('p1', 'steeper-negative', public=True)
        self.execute('p2', 'negative', anchor='p1', public=True)
        self.execute('p3', 'negative', 'evidence', public=True)
        plan = self.request(True)['search_allocation']
        self.assertEqual(plan['selected_parent'], 'p3')
        self.assertEqual(plan['qualified'][0]['worst_improvement'], '57/2')
        for item in plan['neighborhood']['comparisons'][-2:]:
            self.assertEqual(item['parent_proposal_id'], 'p1')
            self.assertEqual(item['baseline_improvement'], '-43/2')
            self.assertEqual(item['parent_improvement'], '57/2')
        self.assertEqual(len(self.store.snapshot()['receipts']), 8)
        self.assertEqual(self.store.snapshot()['budget']['wall_seconds']['reserved'], 0)

    def test_repeated_stagnation_redirects_slots_without_resetting_history(self):
        self.incumbent()
        self.execute('p3', 'overshoot', 'refine')
        self.execute('p4', 'overshoot', 'evidence')
        self.execute('alternative', 'near-linear', 'refine')
        self.execute('alias', 'near-linear', 'evidence')
        task = self.request()
        plan = task['search_allocation']
        self.assertIsNone(plan['selected_parent'])
        self.assertEqual(len(plan['neighborhood']['paused_parents']), 1)
        self.assertEqual([s['kind'] for s in plan['slots']].count('explore'), 4)
        self.assertEqual(len(plan['slots']), sum(self.policy['slots'].values()))
        self.assertEqual(len([s for s in plan['slots'] if s.get('redirected_from') == 'refine']), 2)
        self.assertEqual(self.request(), task)
        self.assertEqual(len(self.store.snapshot()['receipts']), 14)
        self.assertEqual(plan['limits'], self.policy['slots'])

    def test_unknown_noise_incomparability_shared_runs_and_refutation(self):
        self.execute()
        self.execute('p1', 'weak')
        self.execute('p2', 'linear', anchor='p1')
        self.execute('p3', 'linear', 'evidence')
        rows, feedback, state = self.inputs()
        for change in ('unknown', 'noise', 'incomparable', 'shared-run', 'refute', 'parent-noise', 'other-anchor'):
            props, observed = deepcopy(rows), deepcopy(feedback)
            if change == 'unknown':
                observed[-1]['observation'] = 'UNKNOWN'
            elif change == 'noise':
                observed[-1]['discrimination']['measured']['value'] = 11
            elif change == 'incomparable':
                props['p3']['proposal']['experiment']['runs'][1]['argv'].append('--different-split')
            elif change == 'shared-run':
                props['p3']['proposal']['experiment']['runs'][0]['id'] = 'p2'
            elif change == 'refute':
                observed[-1]['observation'] = 'REFUTE'
            elif change == 'parent-noise':
                # Parent noise that reverses the conservative observed envelope.
                observed[1]['discrimination']['measured']['value'] = 1
            else:
                # An adverse repeat cannot disappear by using another anchor.
                props['alias'] = deepcopy(props['p3'])
                props['alias']['id'] = 'alias'
                props['alias']['proposal']['experiment']['runs'][0]['id'] = 'alias'
                props['alias']['proposal']['experiment']['runs'][1]['argv'][3] = 'alias'
                props['alias']['search_allocation'] = {'kind': 'explore', 'parent_proposal_id': None}
                observed.append(deepcopy(observed[-1]))
                observed[-1]['id'] = 'alias'
                observed[-1]['discrimination']['measured']['value'] = 11
            plan = allocation.build(self.policy, props, observed, state)
            with self.subTest(change=change):
                self.assertNotIn(plan['selected_parent'], ('p2', 'p3'))
                self.assertEqual(plan['neighborhood']['paused_parents'], [])

    def test_explore_trigger_is_not_an_implicit_neighborhood(self):
        self.execute()
        self.execute('p1', 'linear')
        self.execute('p2', 'weak')
        item = self.request()['search_allocation']['neighborhood']['comparisons'][-1]
        self.assertEqual(item['parent_proposal_id'], 'baseline')
        self.assertEqual(item['basis'], 'PREDECLARED_BASELINE')

    def test_explicit_anchor_needs_same_goal_original_feedback_and_exact_trigger(self):
        self.execute()
        task = self.request()
        value = self.proposal(task, 'p1', 'weak', 'explore', 'baseline')
        for change in ('absent', 'forged-trigger', 'type'):
            bad = deepcopy(value)
            if change == 'absent':
                bad['search']['anchor_proposal_id'] = 'other-goal'
            elif change == 'forged-trigger':
                bad['trigger']['feedback_sha256'] = '0' * 64
            else:
                bad['search']['anchor_proposal_id'] = []
            with self.subTest(change=change), self.assertRaises(ValueError):
                structure.propose(self.root, bad)
        self.assertIsNone(structure._find(self.store, 'PROPOSAL', 'p1'))

    def test_unknown_cannot_be_an_explicit_comparison_anchor(self):
        task = self.request()
        value = self.proposal(task, 'baseline', 'zero', 'explore')
        value['experiment']['runs'][1]['argv'][-1] = 'missing'
        structure.propose(self.root, value)
        structure.advance(self.root, 'baseline')
        value = self.proposal(self.request(), 'p1', 'weak', 'explore', 'baseline')
        with self.assertRaisesRegex(ValueError, 'original numeric feedback'):
            structure.propose(self.root, value)

    def test_frozen_stagnation_limit_is_required_and_strict(self):
        for value in (None, True, 0, 1, 9, '2'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                allocation.validate({**self.policy, 'stagnation_trials': value})
        with self.assertRaises(ValueError):
            allocation.validate({**fixture.policy(), 'stagnation_trials': 2})
        self.assertEqual(allocation.validate(self.policy), self.policy)


if __name__ == '__main__':
    unittest.main()
