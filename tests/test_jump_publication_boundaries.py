"""Publication and final projection boundaries over native local executions."""
from copy import deepcopy
import importlib.util
import json
import sqlite3
from pathlib import Path
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import rds_autonomy as autonomy
import rds_jump as jump
import rds_structure as structure
from rds_project import ProjectStore, canonical, file_sha
from test_jump_ai_packet import build_multi_proposal_example


def load_example(name, path):
    spec = importlib.util.spec_from_file_location(name, REPO / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


example = load_example('publication_generation_example', 'examples/jump-generation/run.py')
loop = load_example('publication_loop_example', 'examples/jump-loop/run.py')


class JumpPublicationTests(unittest.TestCase):
    def build(self, root, variant):
        initialize = ProjectStore.initialize
        def frozen(store, contract):
            contract = deepcopy(contract)
            worker = store.root / 'worker.py'
            raw = worker.read_text(encoding='utf-8')
            marker = "    Path(output).write_text(canonical({'schema': 1, 'kind': kind,"
            extension = """    if kind == 'synthesize' and result['proposals']:
        from copy import deepcopy
        first = result['proposals'][0]
        second = deepcopy(first)
"""
            if variant == 'duplicate_changed':
                extension += "        second['exploration']['changed_fixture_content'] = True\n"
            elif variant != 'duplicate_equal':
                extension += "        second['id'] += '-second'\n"
                extension += "        for run in second['experiment']['runs']:\n            run['id'] += '-second'\n"
            if variant == 'late_invalid':
                extension += "        second['experiment']['verdict_output'] = 'out/undeclared-verdict.json'\n"
            extension += "        result['proposals'] = [first, second]\n"
            self.assertEqual(raw.count(marker), 1)
            worker.write_text(raw.replace(marker, extension + marker), encoding='utf-8')
            next(b for b in contract['bindings'] if b['path'] == 'worker.py')['sha256'] = file_sha(worker)
            protocol_path = store.root / 'protocol.json'
            protocol = json.loads(protocol_path.read_text(encoding='utf-8'))
            protocol['code_sha256'] = ProjectStore._role_sha(contract, 'code')
            protocol_path.write_text(canonical(protocol), encoding='utf-8')
            next(b for b in contract['bindings'] if b['role'] == 'protocol')['sha256'] = file_sha(protocol_path)
            (store.root / 'contract.json').write_text(canonical(contract), encoding='utf-8')
            return initialize(store, contract)
        with patch.object(ProjectStore, 'initialize', frozen):
            return example.build(root)

    def assert_no_publication(self, store):
        events = structure._events(store)
        self.assertFalse(any(e['kind'] in {'STRUCTURE_PROPOSAL', 'STRUCTURE_JUMP_FINISHED'} for e in events))
        state = store.snapshot()
        self.assertEqual(len(state['receipts']), 3)
        self.assertTrue(all(r['run_status'] == 'SUCCEEDED' for r in state['receipts']))
        self.assertEqual(state['budget']['wall_seconds']['reserved'], 0)
        self.assertIsNone(structure.next_step(store.root)['selected'])
        return state

    def test_duplicate_generated_ids_reject_before_any_proposal_publication(self):
        for variant in ('duplicate_equal', 'duplicate_changed'):
            with self.subTest(variant=variant), tempfile.TemporaryDirectory() as directory:
                root, store = self.build(Path(directory) / 'project', variant)
                with self.assertRaisesRegex(ValueError, 'IDs must be distinct'):
                    jump.generate(root, 3)
                before = self.assert_no_publication(store)
                with self.assertRaisesRegex(ValueError, 'IDs must be distinct'):
                    jump.generate(root, 3)
                after = self.assert_no_publication(store)
                self.assertEqual(before['runs'], after['runs'])
                self.assertEqual(before['receipts'], after['receipts'])

    def test_invalid_later_proposal_keeps_originals_without_partial_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root, store = self.build(Path(directory) / 'project', 'late_invalid')
            with self.assertRaisesRegex(ValueError, 'independent verifier output|Result files must be declared'):
                jump.generate(root, 3)
            before = self.assert_no_publication(store)
            with self.assertRaisesRegex(ValueError, 'independent verifier output|Result files must be declared'):
                jump.generate(root, 3)
            after = self.assert_no_publication(store)
            self.assertEqual(before['runs'], after['runs'])
            self.assertEqual(before['receipts'], after['receipts'])

    def test_late_insert_failure_rolls_back_batch_then_reuses_original_workers(self):
        with tempfile.TemporaryDirectory() as directory:
            root, store = self.build(Path(directory) / 'project', 'valid_pair')
            put = structure._put
            inserted = []
            def interrupted(store, kind, ident, value, **kwargs):
                if kind == 'PROPOSAL':
                    # A separate native reader must not see the first uncommitted proposal.
                    self.assertFalse(any(e['kind'] == 'STRUCTURE_PROPOSAL' for e in structure._events(store)))
                    inserted.append(ident)
                    if len(inserted) == 2:
                        raise ValueError('injected second publication failure')
                return put(store, kind, ident, value, **kwargs)
            with patch.object(structure, '_put', interrupted):
                with self.assertRaisesRegex(ValueError, 'second publication failure'):
                    jump.generate(root, 3)
            self.assertEqual(len(inserted), 2)
            before = self.assert_no_publication(store)
            result = jump.generate(root, 3)
            self.assertEqual(result['status'], 'JUMP_PROPOSED')
            self.assertEqual(len(set(result['proposal_ids'])), 2)
            events = structure._events(store)
            self.assertEqual(len([e for e in events if e['kind'] == 'STRUCTURE_PROPOSAL']), 2)
            self.assertEqual(len([e for e in events if e['kind'] == 'STRUCTURE_JUMP_FINISHED']), 1)
            self.assertEqual(before['runs'], store.snapshot()['runs'])
            self.assertEqual(before['receipts'], store.snapshot()['receipts'])
            self.assertEqual(store.snapshot()['budget']['wall_seconds']['reserved'], 0)

    def test_completed_no_candidate_returns_new_exploration_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project', corpus=[])
            result = jump.generate(root, 3)
            self.assertEqual(result['status'], 'NO_CANDIDATE')
            before = store.snapshot()
            driven = structure.drive(root)
            self.assertEqual(driven['status'], 'REQUEST_OPEN_EXPLORATION')
            self.assertTrue(driven['agent_tasks'])
            self.assertNotIn('generation', driven)
            self.assertEqual(jump.generate(root, 3), result)
            self.assertEqual(before['runs'], store.snapshot()['runs'])
            self.assertEqual(before['receipts'], store.snapshot()['receipts'])
            self.assertEqual(store.snapshot()['budget']['wall_seconds']['reserved'], 0)

    def test_new_scoped_refutation_after_preparation_prevents_atomic_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            write = example.write
            def additive_oracle(path, value):
                if path.name == 'oracle.json':
                    value = [{'inputs': row['inputs'], 'value': row['inputs']['x'] + row['inputs']['y']}
                             for row in value]
                return write(path, value)
            with patch.object(example, 'write', additive_oracle):
                # The shared fixture freezes the same independent table
                # evaluator with exact multi-proposal identity selection.
                from test_jump_ai_packet import example as packet_example
                with patch.object(packet_example, 'write', additive_oracle):
                    root, store = build_multi_proposal_example(Path(directory) / 'project')
            propose, route = structure.propose, structure._route_constraint
            prepared, originals, locked_checks = [], [], []
            def arriving(project, value, **kwargs):
                result = propose(project, value, **kwargs)
                if kwargs.get('_prepare_only'):
                    prepared.append(result['record']['id'])
                    originals.extend(store.snapshot()['receipts'])
                    alias = deepcopy(value)
                    alias['id'] = 'concurrent-refutation'
                    for run in alias['experiment']['runs']:
                        run['id'] += '-concurrent'
                    alias['experiment']['runs'][1]['argv'].append(alias['id'])
                    propose(project, alias)
                    observed = structure.advance(project, alias['id'])
                    self.assertEqual(observed['observation'], 'REFUTE')
                return result
            def locked(store, row):
                constraint = route(store, row)
                if constraint and constraint['kind'] == 'HYPOTHESIS_REFUTED' and row['id'] in prepared:
                    # A separate real SQLite writer cannot publish more
                    # feedback between this recheck and proposal insertion.
                    with sqlite3.connect(store.path, timeout=0) as writer:
                        with self.assertRaisesRegex(sqlite3.OperationalError, 'locked'):
                            writer.execute('BEGIN IMMEDIATE')
                    locked_checks.append(row['id'])
                return constraint
            with patch.object(structure, 'propose', arriving), patch.object(structure, '_route_constraint', locked):
                with self.assertRaisesRegex(ValueError, 'feedback changed before publication.*HYPOTHESIS_REFUTED'):
                    jump.generate(root, 3)
            self.assertEqual(locked_checks, prepared)
            self.assertEqual(len(prepared), 1)
            self.assertTrue(all(structure._find(store, 'PROPOSAL', ident) is None for ident in prepared))
            self.assertFalse(any(e['kind'] == 'STRUCTURE_JUMP_FINISHED' for e in structure._events(store)))
            before = store.snapshot()
            self.assertEqual(len(originals), 3)
            self.assertTrue(all(r in before['receipts'] for r in originals))
            self.assertEqual(len(before['receipts']), 5)
            self.assertEqual(before['budget']['wall_seconds']['reserved'], 0)
            with self.assertRaisesRegex(ValueError, 'HYPOTHESIS_REFUTED'):
                jump.generate(root, 3)
            after = store.snapshot()
            self.assertEqual(before['runs'], after['runs'])
            self.assertEqual(before['receipts'], after['receipts'])
            self.assertEqual(after['budget']['wall_seconds']['reserved'], 0)

    def test_completed_refuted_proposals_return_new_exploration_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            write = example.write
            def additive_oracle(path, value):
                if path.name == 'oracle.json':
                    value = [{'inputs': row['inputs'], 'value': row['inputs']['x'] + row['inputs']['y']} for row in value]
                return write(path, value)
            with patch.object(example, 'write', additive_oracle):
                root, store = example.build(Path(directory) / 'project')
            generated = jump.generate(root, 3)
            observed = structure.advance(root, generated['proposal_ids'][0])
            self.assertEqual(observed['observation'], 'REFUTE')
            before = store.snapshot()
            driven = structure.drive(root)
            self.assertEqual(driven['status'], 'REQUEST_OPEN_EXPLORATION')
            self.assertTrue(driven['agent_tasks'])
            self.assertNotIn('generation', driven)
            self.assertEqual(before['runs'], store.snapshot()['runs'])
            self.assertEqual(before['receipts'], store.snapshot()['receipts'])
            self.assertTrue(driven['agent_tasks'][0]['feedback_sha256'])


class FinalProjectionAccountingTests(unittest.TestCase):
    def project(self, fail):
        with tempfile.TemporaryDirectory() as directory:
            root, contract = loop.build(Path(directory) / 'project')
            store = ProjectStore(root)
            store.initialize(contract)
            before = store.snapshot()['budget']['wall_seconds']
            clock = [0.]
            calls = []
            def projection(project):
                with project._db(True) as db:
                    events = autonomy._events(db, ('AUTONOMY_DRIVE_CLAIMED', 'AUTONOMY_DRIVE_RELEASED'))
                self.assertEqual(events[-1]['kind'], 'AUTONOMY_DRIVE_CLAIMED')
                self.assertGreater(project.snapshot()['budget']['wall_seconds']['reserved'], 0)
                calls.append(True)
                clock[0] += .625
                if fail:
                    raise ValueError('injected final projection failure')
                return {'status': 'TEST_PROJECTION', 'scientific_support': 'UNKNOWN'}
            # Only the projection cost is injected. Admission, baseline execution,
            # receipt, allowance and release use the original native project.
            with patch.object(autonomy, 'time', SimpleNamespace(monotonic=lambda: clock[0], time=time.time)), \
                 patch.object(jump, 'prepare_owned', return_value=None), patch.object(jump, 'packet', projection):
                result = autonomy.drive(store, max_steps=1)
            self.assertEqual(calls, [True])
            self.assertEqual(result['controller_wall_seconds'], .625)
            state = store.snapshot()
            self.assertEqual(len(state['receipts']), 1)
            self.assertEqual(state['receipts'][0]['run_status'], 'SUCCEEDED')
            budget = state['budget']['wall_seconds']
            self.assertEqual(budget['reserved'], 0)
            measured_worker = state['receipts'][0]['resources']['wall_seconds']['measured']
            self.assertAlmostEqual(budget['spent_measured'] - before['spent_measured'], measured_worker + .625)
            self.assertEqual(budget['charged_estimate'], before['charged_estimate'])
            with store._db(True) as db:
                releases = autonomy._events(db, ('AUTONOMY_DRIVE_RELEASED',))
            self.assertEqual(len(releases), 1)
            self.assertEqual(releases[0]['controller_wall_seconds'], .625)
            if fail:
                self.assertEqual(result['status'], 'HANDOFF_REQUIRED')
                self.assertIn('projection failure', result['reason'])
            else:
                self.assertEqual(result['status'], 'STEP_LIMIT')
                self.assertEqual(result['jump_packet']['status'], 'TEST_PROJECTION')

    def test_final_projection_is_charged_before_controller_release(self):
        self.project(False)

    def test_failed_final_projection_retains_receipt_and_releases_allowance(self):
        self.project(True)


if __name__ == '__main__':
    unittest.main()
