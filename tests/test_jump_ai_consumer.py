"""Real fixture-provider stdin/receipt/adoption; projection is injected separately.

Original jump provenance is exercised by test_jump_ai_packet. This boundary test
does not claim that a fixture is a model or measures scientific improvement.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import test_rds_autonomy as fixture
import rds_autonomy as autonomy
import rds_jump as jump
from rds_autonomy_worker import model_prompt, reply_schema
from rds_math import blob
from rds_project import canonical, digest

PROVIDER = fixture.PROVIDER.replace(
    "source=request['base_source'] if mode=='bad' else NEW_SOURCE",
    """context=request['jump_packet']
item=context['items'][0]
value=item['discriminator']['proposal']['value']
x,y=item['discriminator']['proposal']['input']
source=request['base_source'].replace('value=0', 'value='+str(x)+'*'+str(y))""").replace(
    "if mode in ('envelope_bound','raw_bound'):",
    """usage={'schema':1,'packet_sha256':context['sha256'],'decisions':[
    {'id':item['id'],'disposition':'adapt',
     'reason':'The interaction predicts '+str(value)+' on the separating sample.',
     'next_step':'Return source using that prediction; execute solve under the frozen evaluator.'}]}
if mode=='ack': usage['decisions'][0]['reason']='ACK ACK ACK'
if mode=='mismatch': usage['packet_sha256']='0'*64
if mode!='missing': reply['jump_use_json']=json.dumps(usage)
if mode=='oversize_use': reply['jump_use_json']='x'*(2*1024*1024-400)
if mode=='deep_use': reply['jump_use_json']='['*4000+'0'+']'*4000
if mode in ('envelope_bound','raw_bound'):""")


class JumpAiConsumerTests(unittest.TestCase):
    def setUp(self):
        self.case = fixture.AutonomyTests(methodName='runTest')
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.context = {'schema': jump.PACKET_SCHEMA, 'status': 'CURRENT',
            'omissions': {'items': 0, 'generation_results': False},
            'items': [{'id': 'generated-interaction', 'kind': 'hypothesis',
                'explanation': 'A multiplicative interaction explains the measured residual.',
                'assumptions': ['Integer inputs in the declared finite domain'],
                'discriminator': {'proposal': {'value': 6, 'input': [2, 3]}, 'rival': {'value': 5}}}],
            'scientific_support': 'UNKNOWN', 'use_assurance': 'NOT_RECORDED'}
        self.context['sha256'] = digest(self.context)

    def build(self, mode='good'):
        with patch.object(fixture, 'PROVIDER', PROVIDER):
            self.case.build(modes={'repair1': mode}, timeout=12, budget=100)

    def run_repair(self):
        with patch.object(jump, 'packet', return_value=deepcopy(self.context)):
            return self.case.run_repair()

    def test_actual_provider_input_changes_source_and_retains_response_links(self):
        self.build()
        event, receipt = self.run_repair()
        request = json.loads(blob(self.case.root, event['request']).decode('utf-8'))
        self.assertEqual(request['jump_packet'], self.context)
        dispatch = self.case.events('AUTONOMY_MODEL_DISPATCH_INTENT')[0]
        self.assertEqual(dispatch['prompt_sha256'], hashlib.sha256(model_prompt(request).encode('utf-8')).hexdigest())
        response = json.loads((self.case.root / 'outputs/repair1.json').read_text(encoding='utf-8'))
        self.assertEqual(response['status'], 'proposed')
        self.assertIn('value=2*3', response['source'])
        self.assertIn('predicts 6', json.loads(response['jump_use_json'])['decisions'][0]['reason'])
        self.assertEqual(autonomy.process_result(self.case.store, event, receipt), 'ADOPTED')
        references = self.case.events('AUTONOMY_JUMP_REFERENCED')
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0]['request_sha256'], event['request']['sha256'])
        self.assertEqual(references[0]['receipt_sha256'], receipt['sha256'])
        self.assertEqual(references[0]['response_source_sha256'], digest(response['source']))
        self.assertEqual(references[0]['usage']['packet_sha256'], self.context['sha256'])
        self.assertEqual(references[0]['scientific_support'], 'UNKNOWN')
        self.assertEqual((self.case.root / 'code.py').read_text(encoding='utf-8'), response['source'])
        before = self.case.store.snapshot()
        self.assertEqual(autonomy.process_result(self.case.store, event, receipt), 'ADOPTED')
        self.assertEqual(self.case.events('AUTONOMY_JUMP_REFERENCED'), references)
        self.assertEqual(self.case.store.snapshot(), before)
        self.assertEqual(self.case.calls(), ['repair1'])

    def test_deep_jump_use_rejects_paid_result_once_without_new_dispatch(self):
        self.build('deep_use')
        event, receipt = self.run_repair()
        before = self.case.store.snapshot()
        response_bytes = (self.case.root / 'outputs/repair1.json').read_bytes()
        response = json.loads(response_bytes.decode('utf-8'))
        self.assertEqual(response['status'], 'proposed')
        self.assertLessEqual(len(response_bytes), autonomy.MAX_BYTES)
        self.assertEqual(response['jump_use_json'], '['*4000+'0'+']'*4000)
        original_artifacts = {item['path']: (self.case.root / item['path']).read_bytes()
                              for item in receipt['artifacts']}
        original_finished = self.case.events('ATTEMPT_FINISHED')
        original_code = (self.case.root / 'code.py').read_bytes()
        for _ in range(2):
            with patch.object(jump, 'packet', return_value=deepcopy(self.context)):
                result = autonomy.drive(self.case.store, max_steps=1, prepare_only=True)
            self.assertEqual(result['executed'], [])
            self.assertEqual(self.case.calls(), ['repair1'])
            processed = self.case.events(autonomy.PROCESSED)
            self.assertEqual(len(processed), 1)
            self.assertEqual(processed[0]['outcome'], 'PROPOSAL_REJECTED')
            self.assertIn('Jump-use JSON nesting', processed[0]['reason'])
            self.assertEqual(processed[0]['receipt_sha256'], receipt['sha256'])
            self.assertEqual(autonomy.process_result(self.case.store, event, receipt), 'PROPOSAL_REJECTED')
            after = self.case.store.snapshot()
            self.assertEqual(after['runs'], before['runs'])
            self.assertEqual(after['receipts'], before['receipts'])
            self.case.assert_single_model_cost(before)
        claims = self.case.events('AUTONOMY_DRIVE_CLAIMED')
        releases = self.case.events('AUTONOMY_DRIVE_RELEASED')
        self.assertEqual(len(claims), 2)
        self.assertEqual(len(releases), 2)
        self.assertEqual([row['owner'] for row in releases], [row['owner'] for row in claims])
        self.assertEqual(self.case.events('AUTONOMY_JUMP_REFERENCED'), [])
        self.assertEqual(self.case.events('AUTONOMY_PROPOSAL_VALIDATED'), [])
        self.assertEqual(self.case.events('METHOD_REVISION_ADOPTED'), [])
        self.assertEqual(self.case.events('ATTEMPT_FINISHED'), original_finished)
        self.assertEqual((self.case.root / 'code.py').read_bytes(), original_code)
        self.assertEqual({path: (self.case.root / path).read_bytes() for path in original_artifacts}, original_artifacts)

    def test_ack_cannot_become_an_adopted_method(self):
        self.build('ack')
        event, receipt = self.run_repair()
        with self.assertRaisesRegex(ValueError, 'Acknowledgement'):
            autonomy.process_result(self.case.store, event, receipt)
        self.assertEqual(self.case.events('AUTONOMY_JUMP_REFERENCED'), [])
        self.assertEqual(self.case.events('METHOD_REVISION_ADOPTED'), [])
        self.assertEqual((self.case.root / 'code.py').read_text(encoding='utf-8'), fixture.OLD)

    def test_missing_use_retains_unknown_provider_response(self):
        self.build('missing')
        event, receipt = self.run_repair()
        self.assertEqual(autonomy.process_result(self.case.store, event, receipt), 'UNKNOWN')
        self.assertEqual(self.case.events('AUTONOMY_JUMP_REFERENCED'), [])
        self.assertEqual(self.case.events('METHOD_REVISION_ADOPTED'), [])
        self.assertEqual(self.case.calls(), ['repair1'])

    def test_unavailable_or_omitted_context_blocks_fresh_inference(self):
        self.build()
        for status in ('UNAVAILABLE', 'NEEDS_ORIGINAL'):
            with self.subTest(status=status), patch.object(jump, 'packet', return_value={'status': status}):
                self.assertEqual(autonomy.request_repair(self.case.store, fixture.review(self.case.store)),
                                 'JUMP_CONTEXT_REQUIRES_ORIGINALS')
        self.assertEqual(self.case.events(autonomy.REQUESTED), [])
        self.assertEqual(self.case.calls(), [])

    def test_concurrent_jump_evidence_change_blocks_request_commit(self):
        from rds_tms_store import current, maintain, save
        import rds_tool_workbench as workbench
        for change, context in (('structure', self.context), ('snapshot', self.context),
                                ('structure', None), ('snapshot', None)):
            with self.subTest(change=change, context=context is not None):
                case = fixture.AutonomyTests(methodName='runTest')
                case.setUp()
                self.addCleanup(case.doCleanups)
                case.build(timeout=12, budget=100)
                maintain(case.root)
                report = fixture.review(case.store)
                before = case.store.snapshot()
                prepare = workbench.prepare
                def concurrent_prepare(*args, **kwargs):
                    result = prepare(*args, **kwargs)
                    if change == 'structure':
                        with case.store._db() as db:
                            db.execute('BEGIN IMMEDIATE')
                            db.execute('INSERT INTO events(body) VALUES (?)', (canonical({
                                'kind': 'STRUCTURE_EVIDENCE_CHANGED',
                                'source': 'Concurrent native event; no support or outcome claim'}),))
                    else:
                        saved = current(case.root)
                        graph = deepcopy(saved['dependency_map'])
                        graph['nodes'].append({'id': 'new-evidence', 'status': 'UNKNOWN',
                                               'source': 'Concurrent native snapshot; no support claim'})
                        save(case.root, graph, expected=saved['sha256'])
                    return result
                with patch.object(jump, 'packet', return_value=deepcopy(context)), \
                        patch.object(workbench, 'prepare', side_effect=concurrent_prepare):
                    with self.assertRaisesRegex(ValueError, 'Jump evidence changed'):
                        autonomy.request_repair(case.store, report)
                self.assertEqual(case.events(autonomy.REQUESTED), [])
                self.assertEqual(case.calls(), [])
                after = case.store.snapshot()
                self.assertEqual(after['runs'], before['runs'])
                self.assertEqual(after['receipts'], before['receipts'])
                self.assertEqual(after['budget'], before['budget'])

    def test_without_jump_keeps_existing_provider_contract(self):
        self.case.build(timeout=12, budget=100)
        event, receipt = self.case.run_repair()
        request = json.loads(blob(self.case.root, event['request']).decode('utf-8'))
        self.assertNotIn('jump_packet', request)
        self.assertNotIn('jump_use_json', reply_schema()['required'])
        self.assertEqual(autonomy.process_result(self.case.store, event, receipt), 'ADOPTED')
        self.assertEqual(self.case.events('AUTONOMY_JUMP_REFERENCED'), [])

    def test_working_set_delivers_the_same_packet_without_execution(self):
        from rds_advisor_workset import build
        self.build()
        report = fixture.review(self.case.store)
        before = self.case.store.snapshot()
        with patch.object(jump, 'packet', return_value=deepcopy(self.context)):
            view = build(self.case.store, report)
        self.assertEqual(view['status'], 'CURRENT', view)
        self.assertEqual(view['jump_packet'], self.context)
        self.assertEqual(self.case.store.snapshot(), before)
        self.assertEqual(self.case.calls(), [])


if __name__ == '__main__':
    unittest.main()
