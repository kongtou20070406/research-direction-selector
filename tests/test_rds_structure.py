"""Engineering integration cases, never evidence of autonomous discovery."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import rds_structure as structure
from rds_project import ProjectStore, canonical, digest
from rds_tms_store import current, maintain
from rds_hypergraph import review_hypergraph
from rds_frontier_proposals import review_proposals

spec = importlib.util.spec_from_file_location('structure_fixture', REPO / 'examples/problem-structure/run.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class StructureTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = fixture.prepare(Path(self.tmp.name) / 'project')
        self.store = ProjectStore(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def proposal(self, **kwargs):
        task = structure.request(self.root)['tasks'][0]
        return fixture.proposal(self.root, task, **kwargs)

    def test_missing_concept_is_absent_initially_then_retained_without_goal_path(self):
        original = current(self.root)['dependency_map']
        p = self.proposal()
        self.assertNotIn('candidate-concept', {n['id'] for n in original['nodes']})
        row = structure.propose(self.root, p)
        self.assertFalse(row['review']['complete_goal_path_required'])
        self.assertFalse(row['execution_authorized'])
        self.assertEqual(current(self.root)['dependency_map'], original)
        self.assertEqual(structure.advance(self.root, p['id'])['goal_status'], 'PASS')
        structure.activate(self.root, p['id'])
        graph = current(self.root)['dependency_map']
        self.assertEqual(next(n for n in graph['nodes'] if n['id'] == 'candidate-concept')['status'], 'UNKNOWN')
        self.assertEqual(next(e for e in graph['hyperedges'] if e['id'] == 'candidate-alternative')['status'], 'PROPOSED')
        self.assertEqual(review_hypergraph(graph)['goals']['goal']['status'], 'UNKNOWN')
        structure.rollback(self.root, p['id'])
        self.assertEqual(current(self.root)['dependency_map'], original)

    def test_reconstruction_retires_bad_decomposition_in_reversible_branch(self):
        other = fixture.prepare(Path(self.tmp.name) / 'decomposition', 'decomposition')
        task = structure.request(other)['tasks'][0]
        p = fixture.proposal(other, task, strategy='simultaneous', action='structural_reconstruction')
        old = current(other)['dependency_map']
        row = structure.propose(other, p)
        self.assertNotIn('old-decomposition', {e['id'] for e in row['candidate_map']['hyperedges']})
        self.assertEqual(current(other)['dependency_map'], old)
        self.assertEqual(structure.advance(other, 'candidate')['status'], 'TEST_SUPPORTED')
        structure.activate(other, 'candidate')
        structure.rollback(other, 'candidate')
        self.assertEqual(current(other)['dependency_map'], old)
        records = structure.inspect(other)['records']
        self.assertTrue(any(e['kind'] == 'STRUCTURE_PROPOSAL' for e in records))

    def test_refuted_reconstruction_is_consumed_and_other_route_selected(self):
        other = fixture.prepare(Path(self.tmp.name) / 'negative', 'negative')
        task = structure.request(other)['tasks'][0]
        p = fixture.proposal(other, task, strategy='linear', action='structural_reconstruction')
        structure.propose(other, p)
        result = structure.advance(other, 'candidate')
        self.assertEqual(result['status'], 'TEST_REFUTED')
        with self.assertRaisesRegex(ValueError, 'supporting'):
            structure.activate(other, 'candidate')
        rival = fixture.proposal(other, task, ident='rival', strategy='periodic', action='structural_reconstruction')
        structure.propose(other, rival)
        selected = structure.next_step(other)
        self.assertEqual(selected['selected'], 'rival')
        self.assertEqual(selected['feedback_consumed'], ['candidate'])
        self.assertEqual(structure.advance(other, 'rival')['goal_status'], 'PASS')

    def test_duplicate_feedback_and_completed_resume_preserve_attempts_and_budget(self):
        p = self.proposal()
        structure.propose(self.root, p)
        result = structure.advance(self.root, p['id'])
        before = self.store.snapshot()
        self.assertEqual(structure.feedback(self.root, p['id']), result)
        self.assertEqual(structure.advance(self.root, p['id']), result)
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(structure.propose(self.root, p)['proposal_sha256'], digest(p))
        p['exploration']['change'] = 'Different meaning under same ID'
        with self.assertRaisesRegex(ValueError, 'reused'):
            structure.propose(self.root, p)

    def test_partial_experiment_recovery_does_not_repeat_candidate(self):
        p = self.proposal()
        structure.propose(self.root, p)
        candidate = p['experiment']['runs'][0]
        self.store.register(candidate)
        original = self.store.execute(candidate['id'])
        result = structure.advance(self.root, p['id'])
        self.assertEqual(result['status'], 'TEST_SUPPORTED')
        state = self.store.snapshot()
        self.assertEqual(len(state['receipts']), 2)
        self.assertEqual(next(r for r in state['receipts'] if r['run_id'] == candidate['id']), original)

    def test_unknown_and_timeout_preserve_evidence_and_no_auto_retry(self):
        for mode, strategy, timeout in [('unknown', 'interaction', 2), ('ok', 'timeout', .1)]:
            other = fixture.prepare(Path(self.tmp.name) / mode, 'knowledge')
            task = structure.request(other)['tasks'][0]
            p = fixture.proposal(other, task, mode=mode, strategy=strategy, timeout=timeout)
            structure.propose(other, p)
            result = structure.advance(other, 'candidate')
            self.assertEqual(result['status'], 'UNKNOWN')
            state = ProjectStore(other).snapshot()
            self.assertEqual(structure.advance(other, 'candidate'), result)
            self.assertEqual(ProjectStore(other).snapshot(), state)
            self.assertGreater(state['budget']['wall_seconds']['spent_measured'], 0)

    def test_wrong_scope_and_tampered_output_do_not_create_feedback(self):
        p = self.proposal(mode='wrong_scope')
        structure.propose(self.root, p)
        with self.assertRaisesRegex(ValueError, 'scope/proposal/run/hash'):
            structure.advance(self.root, 'candidate')
        self.assertIsNone(structure._find(self.store, 'FEEDBACK', 'candidate'))
        (self.root / 'out/candidate.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            structure.feedback(self.root, 'candidate')
        self.assertIsNone(structure._find(self.store, 'FEEDBACK', 'candidate'))

    def test_stale_snapshot_rejects_proposal_and_execution(self):
        p = self.proposal()
        structure.propose(self.root, p)
        maintain(self.root, updates=[{'nodes': [{'id': 'unrelated', 'status': 'UNKNOWN', 'source': 'changed evidence'}]}])
        with self.assertRaisesRegex(ValueError, 'Stale'):
            structure.advance(self.root, 'candidate')
        p['id'] = 'rival'
        with self.assertRaisesRegex(ValueError, 'Stale'):
            structure.propose(self.root, p)
        self.assertEqual(self.store.snapshot()['runs'], [])

    def test_and_missing_premise_and_healthy_or_alternative(self):
        graph = current(self.root)['dependency_map']
        self.assertEqual(review_hypergraph(graph)['goals']['goal']['status'], 'UNKNOWN')
        graph['hyperedges'].append({'id': 'healthy-or', 'premises': ['observations'], 'conclusion': 'goal',
                                   'status': 'SUPPORTED', 'source': 'independently supported alternative'})
        maintain(self.root, initial=graph)
        self.assertEqual(structure.request(self.root)['tasks'], [])

    def test_self_support_and_goal_replacement_do_not_admit(self):
        p = self.proposal()
        p['topology']['hyperedges'][0]['status'] = 'SUPPORTED'
        with self.assertRaisesRegex(ValueError, 'support flag'):
            structure.propose(self.root, p)
        p['topology']['hyperedges'][0].pop('status')
        p['experiment']['runs'][0]['argv'] = p['experiment']['runs'][1]['argv']
        with self.assertRaisesRegex(ValueError, 'own evaluator'):
            structure.propose(self.root, p)

    def test_budget_exhaustion_is_original_ledger_not_new_branch_allowance(self):
        p = self.proposal()
        structure.propose(self.root, p)
        with self.store._db() as db:
            # Leave exactly the intended control allowance after ALL prior
            # measured/reserved work; elapsed request time is not assumed.
            db.execute("UPDATE budget SET charged=cap-spent-reserved-2.1")
        self.assertEqual(structure.next_step(self.root)['blocked'][0]['reason'], 'BUDGET_EXHAUSTED')
        with self.assertRaisesRegex(ValueError, 'Insufficient'):
            structure.advance(self.root, 'candidate')
        self.assertEqual(self.store.snapshot()['runs'], [])

    def test_controller_recovery_charges_once_and_refuses_live_controller(self):
        with self.store._db() as db:
            db.execute("UPDATE budget SET reserved=reserved+2")
            db.execute('INSERT INTO events(body) VALUES (?)', (canonical({'kind': 'STRUCTURE_CONTROL_STARTED',
                       'id': 'interrupted', 'cap': 2, 'pid': 999999999}),))
        with patch.object(structure, '_alive', return_value=True):
            with self.assertRaisesRegex(ValueError, 'active'):
                structure.recover_control(self.root)
        with patch.object(structure, '_alive', return_value=False):
            self.assertEqual(structure.recover_control(self.root)['reconciled_controls'], 1)
            state = self.store.snapshot()
            self.assertEqual(structure.recover_control(self.root)['reconciled_controls'], 0)
            self.assertEqual(self.store.snapshot(), state)
        self.assertEqual(state['budget']['wall_seconds']['charged_estimate'], 2)

    def test_feedback_goal_is_consumed_without_asserting_logical_support(self):
        p = self.proposal()
        structure.propose(self.root, p)
        result = structure.drive(self.root, steps=2)
        self.assertEqual(result['status'], 'ORIGINAL_EVALUATOR_PASSED')
        self.assertEqual(result['scientific_support'], 'UNKNOWN')
        self.assertEqual(review_hypergraph(current(self.root)['dependency_map'])['goals']['goal']['status'], 'UNKNOWN')

    def test_owned_collection_changes_survive_activation_and_rollback(self):
        p = self.proposal()
        structure.propose(self.root, p)
        structure.advance(self.root, 'candidate')
        from rds_tms_store import save
        saved = current(self.root)
        collected = deepcopy(saved['dependency_map'])
        collected['nodes'].append({'id': 'owned:receipt-later', 'status': 'SUPPORTED', 'source': 'new original receipt'})
        save(self.root, collected, expected=saved['sha256'], source_base=self.root)
        structure.activate(self.root, 'candidate')
        active = current(self.root)
        collected = deepcopy(active['dependency_map'])
        collected['nodes'].append({'id': 'owned:new-observation', 'status': 'SUPPORTED', 'source': 'latest checked original'})
        save(self.root, collected, expected=active['sha256'], source_base=self.root)
        structure.rollback(self.root, 'candidate')
        ids = {n['id'] for n in current(self.root)['dependency_map']['nodes']}
        self.assertIn('owned:receipt-later', ids)
        self.assertIn('owned:new-observation', ids)
        self.assertNotIn('candidate-concept', ids)

    def test_imported_proposal_ids_cannot_inject_printed_commands(self):
        p = self.proposal()
        for ident in ('candidate; echo injected', 'candidate\nstructure activate', '--help', "candidate'payload"):
            p['id'] = ident
            with self.assertRaisesRegex(ValueError, 'Invalid structure proposal ID'):
                structure.propose(self.root, p)
        self.assertFalse(any(e['kind'] == 'STRUCTURE_PROPOSAL' for e in structure._events(self.store)))

    def test_rollback_refuses_intervening_goal_change(self):
        p = self.proposal()
        structure.propose(self.root, p)
        structure.advance(self.root, p['id'])
        structure.activate(self.root, p['id'])
        from rds_tms_store import save
        saved = current(self.root)
        changed = deepcopy(saved['dependency_map'])
        changed['goals'].append('observations')
        save(self.root, changed, expected=saved['sha256'], source_base=self.root)
        with self.assertRaisesRegex(ValueError, 'goal/scope'):
            structure.rollback(self.root, p['id'])
        self.assertEqual(current(self.root)['dependency_map'], changed)

    def test_result_reuse_and_activation_reject_changed_originals(self):
        p = self.proposal()
        structure.propose(self.root, p)
        structure.advance(self.root, 'candidate')
        (self.root / 'out/candidate.json').write_text('{}')
        for action in (structure.feedback, structure.advance, structure.activate):
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                action(self.root, 'candidate')

    def test_owned_admission_collection_and_goal_consumption_are_reused(self):
        other = fixture.prepare(Path(self.tmp.name) / 'owned', owned=True)
        task = structure.request(other)['tasks'][0]
        p = fixture.proposal(other, task)
        structure.propose(other, p)
        self.assertEqual(structure.advance(other, 'candidate')['goal_status'], 'PASS')
        state = ProjectStore(other).snapshot()
        self.assertEqual(len(state['receipts']), 2)
        self.assertTrue(all(r.get('owned_history_recorded') for r in state['runs']))
        from rds_owned_advisor import review
        reported = review(ProjectStore(other))
        self.assertEqual(reported['context']['facts']['result.correct']['value'], True)
        self.assertEqual(structure.next_step(other)['status'], 'ORIGINAL_EVALUATOR_PASSED')
        structure.activate(other, 'candidate')
        review(ProjectStore(other))
        structure.rollback(other, 'candidate')
        self.assertIn('owned:goal:0', current(other)['dependency_map']['goals'])

    def test_open_proposal_respects_as_of_and_excluded_nodes(self):
        p = self.proposal()
        task = structure._find(self.store, 'REQUEST', p['request_id'])
        task['frontier']['as_of'] = '2000-01-01'
        result = review_proposals(task['frontier'], task['frontier_spec'], {'schema_version': 1, 'proposals': [p]})
        self.assertIn('UNKNOWN_AVAILABILITY', ';'.join(result['proposals'][0]['definition_errors']))
        p['new_nodes'][0]['available_on'] = '2001-01-01'
        result = review_proposals(task['frontier'], task['frontier_spec'], {'schema_version': 1, 'proposals': [p]})
        self.assertIn('FUTURE_RECORD', ';'.join(result['proposals'][0]['definition_errors']))

    def test_replanning_packet_exposes_actual_negative_results(self):
        other = fixture.prepare(Path(self.tmp.name) / 'negative', 'negative')
        task = structure.request(other)['tasks'][0]
        p = fixture.proposal(other, task, strategy='linear', action='structural_reconstruction')
        structure.propose(other, p)
        observed = structure.advance(other, p['id'])
        self.assertEqual(observed['status'], 'TEST_REFUTED')
        packet = structure.request(other)['tasks'][0]
        receipts = ProjectStore(other).snapshot()['receipts']
        self.assertEqual(packet['receipts'], [r['sha256'] for r in receipts])
        self.assertEqual([r['run_id'] for r in packet['settled_evidence']], [r['run_id'] for r in receipts])
        self.assertTrue(all(r['original_results'] for r in packet['settled_evidence']))
        self.assertEqual(packet['prior_feedback'][0]['receipts'], observed['receipts'])
        self.assertEqual(packet['prior_feedback'][0]['scope_sha256'], observed['scope_sha256'])
        self.assertEqual(packet['prior_feedback'][0]['proposal_sha256'], observed['proposal_sha256'])
        self.assertEqual(packet['prior_feedback'][0]['next_decision'], observed['next_decision'])
        self.assertEqual(packet['prior_feedback'][0]['status'], 'TEST_REFUTED')
        self.assertIn('wall_seconds', packet['budget'])


if __name__ == '__main__':
    unittest.main()
