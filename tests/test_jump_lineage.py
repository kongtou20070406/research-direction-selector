"""One owning ledger prepares generators and retains verified ancestor evidence."""
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
from rds_project import ProjectStore, canonical, digest, file_sha
import rds_jump as jump
import rds_structure as structure
import rds_method_revision as revision

spec = importlib.util.spec_from_file_location('jump_lineage_example', REPO / 'examples/jump-generation/run.py')
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


class JumpLineageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'project'

    def build(self):
        initialize = ProjectStore.initialize

        def owned(store, contract):
            candidate = store.root / 'candidate.py'
            candidate.write_text("import json, pathlib\npathlib.Path('out/score.json').write_text(json.dumps({'score': 0}))\n", encoding='utf-8')
            contract['bindings'].append({'path': 'candidate.py', 'role': 'code', 'sha256': file_sha(candidate)})
            protocol = json.loads((store.root / 'protocol.json').read_text())
            protocol['code_sha256'] = ProjectStore._role_sha(contract, 'code')
            (store.root / 'protocol.json').write_text(canonical(protocol), encoding='utf-8')
            binding = next(b for b in contract['bindings'] if b['role'] == 'protocol')
            binding['sha256'] = file_sha(store.root / 'protocol.json')
            stages = json.loads((store.root / 'jump-generation.json').read_text())['stages']
            runs = []
            for stage in stages:
                run = deepcopy(stage['run'])
                run.pop('protocol_path')
                run['protocol'] = {'path': binding['path'], 'sha256': binding['sha256']}
                runs.append(run)
            argv = [sys.executable, '-B', 'candidate.py']
            contract['allowed_commands'].append(argv)
            runs.append({'schema': 1, 'id': 'method-candidate', 'arm': 'tool', 'argv': argv,
                         'protocol': {'path': binding['path'], 'sha256': binding['sha256']},
                         'outpaths': ['out/score.json'], 'resource_estimates': {'wall_seconds': 10}, 'timeout_seconds': 10})
            nodes = []
            for index, run in enumerate(runs):
                ident = run['id']
                preconditions = [] if not index else [{'fact': 'run.' + runs[index - 1]['id'] + '.succeeded', 'op': 'eq', 'value': True}]
                nodes.append({'id': ident, 'sources': ['public-synthetic-fixture'], 'executable': {
                    'decisions': ['predict'], 'preconditions': preconditions, 'action': {
                        'id': ident, 'kind': 'PAIRED_TEST', 'target': 'finite-integer-method', 'operation': 'measure-' + ident,
                        'description': 'Retain exact synthetic originals before adapting the method',
                        'competing_explanations': ['interaction', 'additive rival'], 'required_observables': [ident + '.done'],
                        'outcomes': [{'observation': 'positive', 'next_decision': 'inspect next original'},
                                     {'observation': 'negative', 'next_decision': 'retain failure'}]}}})
            contract['advisor_policy'] = {'schema': 1, 'context': {'decision': {
                'id': 'predict', 'goal_revision': 'fixture-v1', 'scope': {'domain': 'finite-integer'},
                'goal_conditions': [{'fact': 'candidate.score', 'op': 'gte', 'value': 1}]}},
                'graph': {'nodes': nodes, 'edges': []},
                'routes': [{'candidate': run['id'], 'manifest': run} for run in runs],
                'observations': [{'fact': 'candidate.score', 'run_id': 'method-candidate',
                                  'path': 'out/score.json', 'selector': {'pointer': '/score'}}]}
            contract['method_evolution'] = {'schema': 1, 'max_revisions': 2, 'code_paths': ['candidate.py']}
            (store.root / 'contract.json').write_text(canonical(contract), encoding='utf-8')
            return initialize(store, contract)

        maintain = example.maintain
        with patch.object(ProjectStore, 'initialize', owned), \
             patch.object(example, 'maintain', side_effect=lambda root, **kwargs: maintain(root)):
            return example.build(self.root)

    def complete(self, store):
        plan = jump.load_plan(store, store.snapshot())
        for stage in plan['stages']:
            prepared = jump.prepare_owned(store, stage['run'])
            self.assertEqual(prepared['status'], 'READY_TO_EXECUTE')
            store.register(stage['run'])
            self.assertEqual(store.execute(stage['run']['id'])['run_status'], 'SUCCEEDED')
        completed = jump.prepare_owned(store, None)
        self.assertEqual(completed['status'], 'JUMP_PROPOSED')
        return completed

    def revise(self, store):
        source = store.root / 'replacement.py'
        source.write_text("import json, pathlib\ndef predict(x, y):\n    return x * y\npathlib.Path('out/score.json').write_text(json.dumps({'score': predict(1, 1)}))\n", encoding='utf-8')
        return revision.apply(store, {'id': 'actual-method', 'parent_sha256': store.snapshot()['contract_sha256'],
            'reason': 'Adopt a callable integer interaction method while preserving original generators',
            'policy': deepcopy(store.snapshot()['contract']['advisor_policy']),
            'code_replacements': [{'path': 'candidate.py', 'source': 'replacement.py', 'sha256': file_sha(source)}]})

    def test_prepare_never_executes_registers_or_repeats_control_work(self):
        _, store = self.build()
        selected = jump.load_plan(store, store.snapshot())['stages'][0]['run']
        self.assertIsNone(jump.prepare_owned(store, None))
        with patch.object(ProjectStore, 'execute', side_effect=AssertionError('owner executes')), \
             patch.object(ProjectStore, 'register', side_effect=AssertionError('owner registers')):
            first = jump.prepare_owned(store, selected)
            self.assertTrue(first['changed'])
            before = store.snapshot()
            again = jump.prepare_owned(store, selected)
            self.assertFalse(again['changed'])
            self.assertEqual(again['status'], 'READY_TO_EXECUTE')
            self.assertEqual(store.snapshot(), before)

    def test_owned_receipts_get_fresh_admission_without_search_policy(self):
        _, store = self.build()
        completed = self.complete(store)
        original = structure._find(store, 'JUMP', completed['id'])
        proposal = structure._find(store, 'PROPOSAL', completed['proposal_ids'][0])
        admission = structure._find(store, 'REQUEST', proposal['request_id'])
        self.assertNotEqual(admission['id'], original['request_id'])
        self.assertEqual(admission['receipts'], [r['sha256'] for r in store.snapshot()['receipts']])
        self.assertEqual(jump.packet(store)['status'], 'CURRENT')
        self.assertIsNone(jump.prepare_owned(store, None))

    def test_adopted_revision_keeps_originals_and_actual_method_receipt(self):
        _, store = self.build()
        completed = self.complete(store)
        original = jump.packet(store)
        self.revise(store)
        run = store.snapshot()['contract']['advisor_policy']['routes'][-1]['manifest']
        store.register(run)
        store.execute(run['id'])
        current = jump.packet(store)
        self.assertEqual(current['status'], 'CURRENT')
        self.assertEqual(current['evidence_scope'], 'HISTORICAL')
        self.assertEqual(current['sources'], original['sources'])
        self.assertEqual(current['origin_contract_sha256'], original['current_contract_sha256'])
        self.assertNotEqual(current['origin_contract_sha256'], current['current_contract_sha256'])
        self.assertEqual(current['items'][0]['observation'], 'UNKNOWN')
        self.assertFalse(current['method_context']['historical_feedback_is_current_support'])
        self.assertEqual(current['method_context']['executions'][-1]['run_id'], 'method-candidate')
        before = store.snapshot()
        self.assertIsNone(jump.prepare_owned(store, None))
        with patch.object(structure, '_meter', side_effect=AssertionError('no rerun/charge')):
            self.assertEqual(jump.generate(store.root, 3)['id'], completed['id'])
        self.assertEqual(store.snapshot(), before)

    def test_missing_revision_original_stops_visibly(self):
        _, store = self.build()
        self.complete(store)
        self.revise(store)
        with store._db(True) as db:
            event = json.loads(db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='METHOD_REVISION_PREPARED'").fetchone()[0])
        (store.state_dir / 'cas' / (event['changes'][0]['before_sha256'] + '.method-bytes')).unlink()
        self.assertEqual(jump.packet(store)['status'], 'UNAVAILABLE')
        self.assertEqual(jump.prepare_owned(store, None)['status'], 'JUMP_UNAVAILABLE')

    def test_incomplete_generation_cannot_migrate_or_invent_another_round(self):
        _, store = self.build()
        selected = jump.load_plan(store, store.snapshot())['stages'][0]['run']
        jump.prepare_owned(store, selected)
        self.revise(store)
        stopped = jump.prepare_owned(store, selected)
        self.assertEqual(stopped['status'], 'JUMP_UNAVAILABLE')
        self.assertIn('Unfinished jump', stopped['diagnostic'])
        self.assertEqual(jump.packet(store)['status'], 'UNAVAILABLE')


if __name__ == '__main__':
    unittest.main()
