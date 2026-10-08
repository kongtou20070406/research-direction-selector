"""Real same-ledger feedback loop; scripted provider is explicitly not an LLM."""
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_project import ProjectStore
from rds_math import blob
from rds_autonomy import drive
from rds_method_revision import contract_history

spec = importlib.util.spec_from_file_location('jump_loop_example', REPO / 'examples/jump-loop/run.py')
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


class JumpLoopTests(unittest.TestCase):
    def test_generated_jump_and_failed_method_return_to_second_input(self):
        with tempfile.TemporaryDirectory(prefix='rds-jump-loop-') as parent:
            root, contract = example.build(Path(parent) / 'project')
            store = ProjectStore(root)
            store.initialize(contract)
            first = drive(store, max_steps=10, prepare_only=True)
            self.assertEqual(first['status'], 'MODEL_REQUEST_READY', first.get('reason'))
            self.assertEqual({r['id'] for r in store.snapshot()['runs']},
                             {'baseline', 'jump-probe', 'jump-refresh', 'jump-synthesize'})
            first_input = json.loads(blob(root, first['request']).decode('utf-8'))
            self.assertEqual(first_input['jump_packet']['status'], 'CURRENT')
            self.assertEqual(set(first_input['jump_packet']['generation_results']), {'probe', 'refresh', 'synthesize'})
            result = drive(store, max_steps=10)
            self.assertEqual(result['status'], 'GOAL_CONFIRMED', result.get('reason'))
            with store._db(True) as db:
                events = [json.loads(r['body']) for r in db.execute('SELECT body FROM events ORDER BY id')]
                history = contract_history(db)
            requests = [e for e in events if e.get('kind') == 'AUTONOMY_MODEL_REQUESTED']
            self.assertEqual([e['run_id'] for e in requests], ['repair1', 'repair2'])
            second_input = json.loads(blob(root, requests[1]['request']).decode('utf-8'))
            context = second_input['jump_packet']
            self.assertEqual(context['evidence_scope'], 'HISTORICAL')
            self.assertFalse(context['admission_authorized'])
            self.assertNotEqual(context['origin_contract_sha256'], context['current_contract_sha256'])
            self.assertEqual(context['id'], first_input['jump_packet']['id'])
            self.assertEqual(len(context['method_context']['verified_contract_lineage']), 2)
            feedback = next(e for e in second_input['evidence_excerpts']['original_outputs']
                            if e['path'] == 'out/check1.json')
            rejection = json.loads(feedback['text'])
            self.assertEqual(rejection['verdict'], 'FAIL')
            self.assertEqual(rejection['replay']['counterexample']['expected'], '12')
            self.assertEqual(rejection['replay']['counterexample']['actual'], '13')
            self.assertEqual(json.loads((root / 'out/check2.json').read_text())['verdict'], 'PASS')
            self.assertEqual(len(history), 3)
            self.assertEqual([e['outcome'] for e in events if e.get('kind') == 'AUTONOMY_MODEL_PROCESSED'],
                             ['ADOPTED', 'ADOPTED'])
            uses = [e for e in events if e.get('kind') == 'AUTONOMY_JUMP_REFERENCED']
            self.assertEqual([e['usage']['packet_sha256'] for e in uses],
                             [first_input['jump_packet']['sha256'], context['sha256']])
            receipts = [r['sha256'] for r in store.snapshot()['receipts']]
            self.assertEqual(len(receipts), 10)
            again = drive(store, max_steps=10)
            self.assertEqual(again['status'], 'GOAL_CONFIRMED')
            self.assertEqual(again['executed'], [])
            self.assertEqual([r['sha256'] for r in store.snapshot()['receipts']], receipts)
            self.assertEqual(store.snapshot()['budget']['wall_seconds']['reserved'], 0)


if __name__ == '__main__':
    unittest.main()
