"""Exercise original receipts and actual worker commands, not hand-labelled success."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_project import ProjectStore
import rds_jump
import rds_structure

spec = importlib.util.spec_from_file_location('jump_example', REPO / 'examples/jump-generation/run.py')
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


class JumpGenerationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / 'project'

    def tearDown(self):
        self.tmp.cleanup()

    def test_cli_three_sources_generate_and_independently_verify(self):
        root, store = example.build(self.root)
        process = subprocess.run([sys.executable, '-B', str(REPO / 'scripts/rds_cli.py'), '--root', str(root),
                                  'structure', 'jump', '--steps', '3'], capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
        generated = json.loads(process.stdout)
        self.assertEqual(generated['status'], 'JUMP_PROPOSED')
        self.assertEqual(len(generated['sources']), 3)
        packet = json.loads((root / 'out/synthesize.json').read_text())
        self.assertEqual(packet['inputs'], generated['sources'][:2])
        search = packet['result']['search']
        self.assertEqual(search['candidate']['op'], 'mul')
        self.assertNotEqual(search['probe_predictions']['candidate'], search['probe_predictions']['rival'])
        row = rds_structure._find(store, 'PROPOSAL', generated['proposal_ids'][0])
        self.assertEqual(row['scientific_support'], 'UNKNOWN')
        observed = rds_structure.advance(root, row['id'])
        self.assertEqual((observed['observation'], observed['goal_status']), ('SUPPORT', 'PASS'))
        self.assertEqual(rds_structure.next_step(root)['status'], 'ORIGINAL_EVALUATOR_PASSED')
        original = [(r['run_id'], r['sha256']) for r in store.snapshot()['receipts']]
        original_budget = store.snapshot()['budget']
        self.assertEqual(rds_jump.generate(root, 3)['proposal_ids'], generated['proposal_ids'])
        self.assertEqual(store.snapshot()['budget'], original_budget)
        self.assertEqual(rds_structure.advance(root, row['id']), observed)
        self.assertEqual([(r['run_id'], r['sha256']) for r in store.snapshot()['receipts']], original)

    def test_drive_progresses_one_stage_per_step_and_resumes_reservation(self):
        root, store = example.build(self.root)
        plan = rds_jump.load_plan(store, store.snapshot())
        store.register(plan['stages'][0]['run'])
        first = rds_structure.drive(root)
        self.assertEqual(first['status'], 'JUMP_STEP_LIMIT')
        self.assertEqual(len(store.snapshot()['receipts']), 1)
        self.assertEqual(rds_structure.drive(root)['status'], 'JUMP_STEP_LIMIT')
        self.assertEqual(rds_structure.drive(root)['status'], 'JUMP_PROPOSED')
        self.assertEqual(len(store.snapshot()['receipts']), 3)

    def test_missing_retrieval_primitive_changes_candidate_availability(self):
        root, store = example.build(self.root, corpus=[])
        result = rds_jump.generate(root, 3)
        self.assertEqual(result['status'], 'NO_CANDIDATE')
        self.assertFalse(result['proposal_ids'])
        receipts = [r['sha256'] for r in store.snapshot()['receipts']]
        self.assertEqual(rds_jump.generate(root, 3)['status'], 'NO_CANDIDATE')
        self.assertEqual(receipts, [r['sha256'] for r in store.snapshot()['receipts']])

    def test_acquired_counterexample_changes_fit(self):
        root, _ = example.build(self.root, measurement_offset=1)
        self.assertEqual(rds_jump.generate(root, 3)['status'], 'NO_CANDIDATE')

    def test_tampered_original_stops_before_next_dispatch(self):
        root, store = example.build(self.root)
        rds_jump.generate(root)
        (root / 'out/probe.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'output hash mismatch'):
            rds_jump.generate(root, 3)
        self.assertEqual(len(store.snapshot()['receipts']), 1)

    def test_worker_failure_never_retries_and_cost_is_retained(self):
        root, store = example.build(self.root, corpus=[{'text': 'interaction', 'branch_seeds': [{'evil': True}]}])
        result = rds_jump.generate(root, 3)
        self.assertEqual(result['status'], 'JUMP_STOPPED')
        self.assertEqual(result['reason'], 'FAILED')
        before = store.snapshot()
        again = rds_jump.generate(root, 3)
        self.assertEqual(again['status'], 'JUMP_STOPPED')
        self.assertEqual(before['runs'], store.snapshot()['runs'])
        self.assertGreaterEqual(store.snapshot()['budget']['wall_seconds']['spent_measured'], before['budget']['wall_seconds']['spent_measured'])

    def test_active_attempt_requires_recovery_without_dispatch(self):
        root, store = example.build(self.root)
        rds_jump.generate(root)
        plan = rds_jump.load_plan(store, store.snapshot())
        store.register(plan['stages'][1]['run'])
        state = store.snapshot()
        second = next(r for r in state['runs'] if r['id'] == 'jump-refresh')
        second.update(status='RUNNING', attempt_id='active-original-attempt')
        with patch.object(ProjectStore, 'snapshot', return_value=state), \
             patch.object(ProjectStore, 'recover', return_value={'run_status': 'RUNNING'}) as recover, \
             patch.object(ProjectStore, 'execute', side_effect=AssertionError('must not redispatch')):
            self.assertEqual(rds_jump.generate(root, 3)['status'], 'RECOVERY_REQUIRED')
            recover.assert_called_once_with('jump-refresh')

    def test_retained_branch_changes_start_under_same_small_budget(self):
        good = {'op': 'mul', 'args': [{'var': 'x'}, {'var': 'y'}]}
        root, _ = example.build(self.root, corpus=[{'text': 'interaction', 'operators': ['mul'], 'branch_seeds': [good]}], max_candidates=1)
        self.assertEqual(rds_jump.generate(root, 3)['status'], 'JUMP_PROPOSED')
        search = json.loads((root / 'out/synthesize.json').read_text())['result']['search']
        self.assertEqual(search['candidate_origin'], 'retained_seed')
        self.assertEqual(search['seed_index'], 0)
        other, _ = example.build(Path(self.tmp.name) / 'no-branch', corpus=[{'text': 'interaction', 'operators': ['mul']}], max_candidates=1)
        self.assertEqual(rds_jump.generate(other, 3)['status'], 'NO_CANDIDATE')

    def test_changed_policy_rejected_before_work(self):
        root, store = example.build(self.root)
        (root / 'jump-generation.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'binding changed'):
            rds_jump.generate(root, 3)
        self.assertEqual(store.snapshot()['runs'], [])

    def test_adaptive_allocation_adopts_new_receipts_without_stale_request(self):
        root, store = example.build(self.root, search_policy=True)
        generated = rds_jump.generate(root, 3)
        row = rds_structure._find(store, 'PROPOSAL', generated['proposal_ids'][0])
        self.assertEqual(row['search_allocation']['kind'], 'explore')
        request = rds_structure._find(store, 'REQUEST', row['request_id'])
        self.assertEqual(request['receipts'], [r['sha256'] for r in store.snapshot()['receipts']])
        self.assertEqual(rds_structure.advance(root, row['id'])['goal_status'], 'PASS')
        self.assertEqual(rds_jump.generate(root, 3)['status'], 'JUMP_PROPOSED')

    def test_finished_reuse_does_not_reserve_new_control_budget(self):
        root, store = example.build(self.root)
        generated = rds_jump.generate(root, 3)
        with patch.object(rds_structure, '_meter', side_effect=AssertionError('No fresh reservation')):
            self.assertEqual(rds_jump.generate(root, 3), generated)

    def test_unconfigured_project_keeps_existing_open_handoff(self):
        root, store = example.build(self.root)
        state = store.snapshot()
        state['contract']['bindings'] = [b for b in state['contract']['bindings'] if b['path'] != 'jump-generation.json']
        with patch.object(ProjectStore, 'snapshot', return_value=state):
            self.assertEqual(rds_jump.generate(root)['status'], 'JUMP_NOT_CONFIGURED')
        self.assertEqual(store.snapshot()['runs'], [])


if __name__ == '__main__':
    unittest.main()
