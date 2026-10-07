"""Original-evidence search allocation; synthetic software acceptance only."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_project import ProjectStore, digest
from rds_tms_store import current, maintain
import rds_structure as structure
import rds_search_allocation as allocation

spec = importlib.util.spec_from_file_location('adaptive_fixture', REPO / 'examples/adaptive-search/run.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class SearchAllocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = fixture.prepare(Path(self.tmp.name) / 'project')
        self.store = ProjectStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def request(self):
        return structure.request(self.root)['tasks'][0]

    def execute(self, ident='baseline', mode='zero', kind='explore', **kwargs):
        task = self.request()
        proposal = fixture.proposal(self.root, task, ident, mode, kind, **kwargs)
        row = structure.propose(self.root, proposal)
        return row, structure.advance(self.root, ident)

    def improved(self):
        self.execute()
        self.execute('p1', 'linear')

    def refute_linear_with_another_computation(self, ident, task=None, kind='evidence'):
        task = task or self.request()
        proposal = fixture.proposal(self.root, task, ident, 'zero', kind)
        proposal['discriminator']['hypothesis_id'] = 'linear'
        proposal['topology']['hyperedges'][0]['conclusion'] = task['goal']
        structure.propose(self.root, proposal)
        observed = structure.advance(self.root, ident)
        self.assertEqual(observed['observation'], 'REFUTE')
        return observed

    def test_real_feedback_changes_request_but_single_winner_cannot_buy_refinement(self):
        initial = self.request()['search_allocation']
        self.assertEqual([s['kind'] for s in initial['slots']], ['explore', 'explore'])
        self.improved()
        first = self.request()['search_allocation']
        self.assertIsNone(first['selected_parent'])
        self.assertEqual([s['kind'] for s in first['slots']], ['explore', 'explore', 'evidence'])
        self.assertEqual(first['slots'][-1]['parent_proposal_id'], 'p1')
        self.execute('p2', 'linear', 'evidence')
        repeated = self.request()['search_allocation']
        self.assertEqual(repeated['selected_parent'], 'p2')
        self.assertEqual([s['kind'] for s in repeated['slots']].count('refine'), 2)
        self.assertEqual(repeated['qualified'][0]['distinct_runs'], 2)
        self.assertEqual(repeated['qualified'][0]['worst_improvement'], '45/2')
        self.assertEqual(repeated['limits'], initial['limits'])
        self.assertFalse(repeated['resources_reserved'])
        self.assertEqual(len(self.store.snapshot()['receipts']), 6)

    def test_slot_cannot_be_reused_under_renamed_proposal(self):
        task = self.request()
        proposal = fixture.proposal(self.root, task)
        row = structure.propose(self.root, proposal)
        again = fixture.proposal(self.root, task, 'alias')
        with self.assertRaisesRegex(ValueError, 'slot already consumed'):
            structure.propose(self.root, again)
        self.assertEqual(structure.propose(self.root, proposal), row)
        self.assertIsNone(structure._find(self.store, 'PROPOSAL', 'alias'))
        self.assertEqual(self.store.snapshot()['receipts'], [])

    def test_policy_is_opt_in_and_default_request_is_unchanged(self):
        root = fixture.prepare(Path(self.tmp.name) / 'legacy', enabled=False)
        task = structure.request(root)['tasks'][0]
        self.assertNotIn('search_allocation', task)
        proposal = fixture.proposal(root, task)
        self.assertNotIn('search_allocation', structure.propose(root, proposal))
        self.assertEqual(structure.advance(root, 'baseline')['observation'], 'REFUTE')

    def test_changed_policy_cannot_reallocate(self):
        self.request()
        (self.root / allocation.POLICY_PATH).write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'binding changed'):
            self.request()

    def test_wrong_roles_and_malformed_policy_fail_before_generation(self):
        policy = fixture.policy()
        for key, value in [('min_repeats', 1), ('min_repeats', True), ('slots', {'explore': 0, 'evidence': 1, 'refine': 4}),
                           ('slots', {'explore': 8, 'evidence': 8, 'refine': 8}), ('strategy', 'always-exploit')]:
            with self.subTest(key=key, value=value):
                bad = {**policy, key: value}
                with self.assertRaises(ValueError):
                    allocation.validate(bad)
        for delta in ('0', '-1', '1/0', 'NaN', '1e1000000'):
            bad = deepcopy(policy)
            bad['metric']['min_improvement'] = delta
            with self.assertRaises(ValueError):
                allocation.validate(bad)

    def test_stale_generation_reply_cannot_ignore_new_feedback(self):
        task = self.request()
        proposal = fixture.proposal(self.root, task, 'alias', 'linear')
        proposal['search']['slot'] = 1
        self.execute()
        with self.assertRaisesRegex(ValueError, 'Stale search evidence'):
            structure.propose(self.root, proposal)
        self.assertIsNone(structure._find(self.store, 'PROPOSAL', 'alias'))

    def test_missing_forged_and_withheld_slots_are_rejected(self):
        task = self.request()
        for search in (None, {'slot': True}, {'slot': 7}, {'slot': 0, 'gain': 999}):
            proposal = fixture.proposal(self.root, task)
            proposal['search'] = search
            with self.subTest(search=search), self.assertRaises(ValueError):
                structure.propose(self.root, proposal)
        self.assertEqual(self.store.snapshot()['receipts'], [])

    def test_parent_binding_is_required_and_evidence_cannot_change_claim(self):
        self.improved()
        task = self.request()
        proposal = fixture.proposal(self.root, task, 'p2', 'linear', 'evidence')
        proposal['trigger']['feedback_sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'exact allocated parent'):
            structure.propose(self.root, proposal)
        proposal = fixture.proposal(self.root, task, 'p2', 'quadratic', 'evidence')
        with self.assertRaisesRegex(ValueError, 'same scoped hypothesis'):
            structure.propose(self.root, proposal)

    def test_unknown_generates_diagnosis_never_refinement(self):
        self.execute(verifier_mode='missing')
        task = self.request()
        plan = task['search_allocation']
        self.assertIsNone(plan['selected_parent'])
        self.assertEqual(plan['comparisons'][0]['observation'], 'UNKNOWN')
        slot = next(s for s in plan['slots'] if s['kind'] == 'evidence')
        self.assertEqual(slot['trigger']['purpose'], 'EVIDENCE')
        self.assertFalse(any(s['kind'] == 'refine' for s in plan['slots']))
        self.assertEqual(len(self.store.snapshot()['receipts']), 2)

    def test_corrupt_original_cannot_be_reused_for_new_generation(self):
        self.improved()
        path = self.root / 'out/p1-verdict.json'
        doc = json.loads(path.read_text())
        doc['mse'] = 0
        path.write_text(json.dumps(doc))
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            self.request()

    def test_refuted_hypothesis_cannot_reenter_through_exploration_slot(self):
        self.execute()
        task = self.request()
        proposal = fixture.proposal(self.root, task, 'alias', 'zero')
        with self.assertRaisesRegex(ValueError, 'HYPOTHESIS_REFUTED'):
            structure.propose(self.root, proposal)

    def test_recovery_keeps_original_attempts_and_allocation(self):
        task = self.request()
        proposal = fixture.proposal(self.root, task)
        row = structure.propose(self.root, proposal)
        candidate, verifier = proposal['experiment']['runs']
        self.store.register(candidate)
        original = self.store.execute(candidate['id'])
        self.store.register(verifier)
        observed = structure.advance(self.root, 'baseline')
        before = self.store.snapshot()
        self.assertEqual(structure.advance(self.root, 'baseline'), observed)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(len(before['receipts']), 2)
        self.assertIn(original, before['receipts'])
        self.assertEqual(structure._find(self.store, 'PROPOSAL', 'baseline')['search_allocation'], row['search_allocation'])
        self.assertEqual(before['budget']['wall_seconds']['reserved'], 0)

    def test_request_reuse_does_not_mint_new_slots(self):
        task = self.request()
        structure.propose(self.root, fixture.proposal(self.root, task))
        repeated = self.request()
        self.assertEqual(repeated, task)
        with self.assertRaisesRegex(ValueError, 'slot already consumed'):
            structure.propose(self.root, fixture.proposal(self.root, repeated, 'alias'))

    def test_noise_incomparable_results_and_aliases_do_not_establish_repeats(self):
        self.improved()
        self.execute('p2', 'linear', 'evidence')
        state = self.store.snapshot()
        feedback = structure._verified_feedback(self.store, self.request()['scope_sha256'])
        rows = {r['id']: structure._find(self.store, 'PROPOSAL', r['id']) for r in feedback}
        policy = fixture.policy()
        for change in ('noise', 'definition', 'evaluator-argument', 'shared-run', 'operational-unknown'):
            obs, props = deepcopy(feedback), deepcopy(rows)
            last = next(f for f in obs if f['id'] == 'p2')
            if change == 'noise':
                last['discrimination']['measured']['value'] = 24.5
            elif change == 'definition':
                props['p2']['discriminator']['measurement']['name'] = 'different-metric'
            elif change == 'evaluator-argument':
                props['p2']['proposal']['experiment']['runs'][1]['argv'].append('--different-split')
            elif change == 'shared-run':
                props['p2']['proposal']['experiment']['runs'][0]['id'] = 'p1'
            else:
                last['observation'] = 'UNKNOWN'
            with self.subTest(change=change):
                plan = allocation.build(policy, props, obs, state)
                self.assertIsNone(plan['selected_parent'])

    def test_refutation_withholds_both_promising_and_repeated_interventions(self):
        self.improved()
        self.execute('p2', 'linear', 'evidence')
        self.execute('p3', 'linear', 'evidence')
        state = self.store.snapshot()
        original = structure._verified_feedback(self.store, self.request()['scope_sha256'])
        rows = {r['id']: structure._find(self.store, 'PROPOSAL', r['id']) for r in original}
        by_id = {r['id']: r for r in original}
        # Controlled scoring inputs; the ledger's original observations stay intact.
        for supported in (['p1'], ['p1', 'p2']):
            obs = deepcopy([by_id[ident] for ident in ['baseline', *supported, 'p3']])
            obs[-1]['observation'] = 'REFUTE'
            obs[-1]['discrimination']['measured']['value'] = 22
            with self.subTest(supported=supported):
                plan = allocation.build(fixture.policy(), rows, obs, state)
                self.assertIsNone(plan['selected_parent'])
                self.assertEqual({s['kind'] for s in plan['slots']}, {'explore'})
                self.assertEqual(plan['comparisons'][-1]['reason'], 'SCOPED_HYPOTHESIS_REFUTED')
                self.assertEqual(plan['comparisons'][-1]['improvement'], '5/2')
        self.assertEqual(structure._verified_feedback(self.store, self.request()['scope_sha256']), original)

    def test_search_requires_a_discriminator_before_consuming_a_slot(self):
        task = self.request()
        proposal = fixture.proposal(self.root, task)
        del proposal['discriminator']
        with self.assertRaisesRegex(ValueError, 'New structure experiments require an executable discriminator'):
            structure.propose(self.root, proposal)
        self.assertIsNone(structure._find(self.store, 'PROPOSAL', 'baseline'))
        structure.propose(self.root, fixture.proposal(self.root, task))
        self.assertEqual(self.store.snapshot()['receipts'], [])

    def test_legacy_unknown_without_a_hypothesis_does_not_allocate_a_broken_parent(self):
        self.execute()
        original = structure._verified_feedback(self.store, self.request()['scope_sha256'])
        rows = {'baseline': structure._find(self.store, 'PROPOSAL', 'baseline')}
        rows['baseline']['discriminator'] = None
        original[0]['observation'] = 'UNKNOWN'
        original[0]['discrimination']['measured'] = None
        plan = allocation.build(fixture.policy(), rows, original, self.store.snapshot())
        self.assertEqual({s['kind'] for s in plan['slots']}, {'explore'})
        self.assertEqual(plan['comparisons'][0]['observation'], 'UNKNOWN')

    def test_exhausted_budget_cannot_mint_work_or_reset_cap(self):
        root = fixture.prepare(Path(self.tmp.name) / 'exhausted', budget=1)
        store = ProjectStore(root)
        before = store.snapshot()
        with self.assertRaisesRegex(ValueError, 'Insufficient'):
            structure.request(root)
        self.assertEqual(store.snapshot(), before)

    def test_atomic_slot_claim_has_one_winner(self):
        task = self.request()
        row = structure.propose(self.root, fixture.proposal(self.root, task))
        def insert(ident):
            candidate = deepcopy(row)
            candidate['id'] = candidate['proposal']['id'] = ident
            candidate['proposal']['search']['slot'] = candidate['search_allocation']['slot'] = 1
            candidate['proposal_sha256'] = digest(candidate['proposal'])
            try:
                structure._put(ProjectStore(self.root), 'PROPOSAL', ident, candidate)
                return 'ADMITTED'
            except ValueError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(insert, ('p1', 'alias')))
        self.assertEqual(outcomes.count('ADMITTED'), 1)
        self.assertTrue(any('slot already consumed' in value for value in outcomes))
        self.assertEqual(self.store.snapshot()['receipts'], [])

    def test_later_unknown_suspends_previously_repeated_winner(self):
        self.improved()
        self.execute('p2', 'linear', 'evidence')
        self.assertEqual(self.request()['search_allocation']['selected_parent'], 'p2')
        self.execute('p3', 'linear', 'evidence', verifier_mode='missing')
        plan = self.request()['search_allocation']
        self.assertIsNone(plan['selected_parent'])
        self.assertEqual(next(s for s in plan['slots'] if s['kind'] == 'evidence')['parent_proposal_id'], 'p3')

    def test_public_cli_feedback_to_generation_to_independent_goal(self):
        root = Path(self.tmp.name) / 'cli'
        result = fixture.run(root)
        self.assertEqual(result['attempts'], 8)
        self.assertEqual(result['goal_status'], 'PASS')
        trace = result['trajectory']
        self.assertIsNone(trace[2]['allocation']['selected_parent'])
        self.assertEqual(trace[3]['allocation']['selected_parent'], 'p2')
        self.assertEqual(trace[3]['decision']['search_allocation']['kind'], 'refine')
        self.assertEqual(trace[3]['decision']['search_allocation']['parent_proposal_id'], 'p2')
        self.assertEqual(result['scientific_benefit'], 'UNMEASURED')
        self.assertEqual(ProjectStore(root).snapshot()['budget']['wall_seconds']['reserved'], 0)

    def test_other_goal_cannot_inherit_a_winning_parent(self):
        model = current(self.root)['dependency_map']
        model['nodes'].append({'id': 'other-goal', 'status': 'UNKNOWN', 'source': 'different obligation'})
        model['goals'].append('other-goal')
        maintain(self.root, initial=model)
        self.improved()
        self.execute('p2', 'linear', 'evidence')
        tasks = structure.request(self.root)['tasks']
        by_goal = {task['goal']: task['search_allocation'] for task in tasks}
        self.assertEqual(by_goal['goal']['selected_parent'], 'p2')
        self.assertIsNone(by_goal['other-goal']['selected_parent'])
        self.assertEqual(by_goal['other-goal']['comparisons'], [])

    def test_scoped_refutation_revokes_a_winner_from_another_computation(self):
        self.improved()
        self.execute('p2', 'linear', 'evidence')
        self.assertEqual(self.request()['search_allocation']['selected_parent'], 'p2')
        observed = self.refute_linear_with_another_computation('p3')
        plan = self.request()['search_allocation']
        self.assertIsNone(plan['selected_parent'])
        self.assertEqual({s['kind'] for s in plan['slots']}, {'explore'})
        refuted = next(r for r in plan['scoped_refutations'] if r['proposal_id'] == 'p3')
        self.assertEqual(refuted['feedback_sha256'], digest(observed))
        self.assertEqual(refuted['receipts'], observed['receipts'])
        self.assertEqual(len(self.store.snapshot()['receipts']), 8)

    def test_scoped_refutation_removes_an_unknown_diagnosis_parent(self):
        self.improved()
        self.execute('p2', 'linear', 'evidence', verifier_mode='missing')
        self.assertEqual(next(s for s in self.request()['search_allocation']['slots']
                              if s['kind'] == 'evidence')['parent_proposal_id'], 'p2')
        self.refute_linear_with_another_computation('p3')
        plan = self.request()['search_allocation']
        self.assertEqual({s['kind'] for s in plan['slots']}, {'explore'})

    def test_scoped_refutation_removes_the_supported_baseline_fallback(self):
        self.execute('baseline', 'linear')
        self.assertEqual(next(s for s in self.request()['search_allocation']['slots']
                              if s['kind'] == 'evidence')['parent_proposal_id'], 'baseline')
        self.refute_linear_with_another_computation('p1')
        plan = self.request()['search_allocation']
        self.assertEqual({s['kind'] for s in plan['slots']}, {'explore'})

    def test_scoped_refutation_from_another_goal_matches_the_original_gate(self):
        model = current(self.root)['dependency_map']
        model['nodes'].append({'id': 'other-goal', 'status': 'UNKNOWN', 'source': 'different obligation'})
        model['goals'].append('other-goal')
        maintain(self.root, initial=model)
        self.improved()
        self.execute('p2', 'linear', 'evidence')
        other = next(t for t in structure.request(self.root)['tasks'] if t['goal'] == 'other-goal')
        observed = self.refute_linear_with_another_computation('p3', other, 'explore')
        plan = self.request()['search_allocation']
        self.assertIsNone(plan['selected_parent'])
        self.assertEqual({s['kind'] for s in plan['slots']}, {'explore'})
        self.assertNotIn('p3', [r['proposal_id'] for r in plan['comparisons']])
        self.assertIn(digest(observed), [r['feedback_sha256'] for r in plan['scoped_refutations']])


if __name__ == '__main__':
    unittest.main()
