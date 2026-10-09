"""Real bounded controller/worker executions using an explicit fixture provider.

The provider is deterministic local test code, never a model or scientific
effectiveness trial. Counts, receipts and budget assertions inspect originals.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import ProjectStore, canonical, digest, file_sha
from rds_owned_advisor import review
import rds_autonomy as autonomy
import rds_method_revision as revision
from rds_autonomy_worker import output_paths

OLD = "import json,pathlib,sys\nvalue=0\npathlib.Path(sys.argv[1]).write_text(json.dumps({'score':value}),encoding='utf-8')\n"
NEW = "import json,pathlib,sys\nvalue=sum([1,2,3])\npathlib.Path(sys.argv[1]).write_text(json.dumps({'score':value}),encoding='utf-8')\n"
PROVIDER = '''import json,pathlib,sys,time
request=json.loads(sys.stdin.read().split('\\n',1)[1])
rid=request['run_id']
with pathlib.Path('provider-calls.jsonl').open('a',encoding='utf-8') as log:
    log.write(json.dumps({'run_id':rid,'parent':request['parent_sha256']})+'\\n')
mode=MODES.get(rid,'good')
if mode=='exit': raise SystemExit(9)
if mode=='delayed': time.sleep(3)
if mode=='timeout': time.sleep(5)
if mode=='malformed': print('not JSON'); raise SystemExit(0)
policy=request['policy']
next(n for n in policy['graph']['nodes'] if n['id']=='solve')['executable']['preconditions']=[
    {'fact':'autonomy.'+rid+'.adopted','op':'eq','value':True}]
source=request['base_source'] if mode=='bad' else NEW_SOURCE
reply={'status':'needs_authorization' if mode=='authority' else 'proposed',
       'source':'' if mode=='authority' else source,
       'policy_json':'' if mode=='authority' else json.dumps(policy),
       'reason':'Explicit deterministic fixture response, not a model'}
if mode in ('envelope_bound','raw_bound'):
    reply['source'] += '#' * (2*1024*1024-len(json.dumps(reply,separators=(',',':')).encode('utf-8'))-1)
    if mode=='raw_bound': reply['source'] += 'x'*1024
sys.stdout.buffer.write(json.dumps(reply,separators=(',',':')).encode('utf-8')+b'\\n')
'''


class AutonomyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-autonomy-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.store = ProjectStore(self.root)
        self.trace = []

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, allow_nan=False), encoding='utf-8')

    def build(self, *, modes=None, slots=1, max_steps=4, timeout=4, budget=60, baseline=False, control_wall=None,
              legacy_worker=False):
        self.write('code.py', OLD)
        self.write('provider.py', PROVIDER.replace('MODES', repr(modes or {})).replace('NEW_SOURCE', repr(NEW)))
        shutil.copyfile(ROOT / 'scripts/rds_autonomy_worker.py', self.root / 'worker.py')
        if legacy_worker:
            # Freeze the pre-fix publication behavior as a legacy producer.
            # Admission, paid provider process and original receipts remain real.
            worker = (self.root / 'worker.py').read_text(encoding='utf-8')
            start = worker.index('    encoded = canonical(result).encode(\'utf-8\')')
            end = worker.index('    return 0  # Process completion', start)
            worker = worker[:start] + "    paths[0].write_text(canonical(result), encoding='utf-8')\n" + worker[end:]
            self.write('worker.py', worker)
        self.write('config.json', {})
        self.write('data.json', [1])
        self.write('evaluator.json', {'expected': 6})
        paths = [('code', 'code.py'), ('code', 'worker.py'), ('code', 'provider.py'),
                 ('config', 'config.json'), ('data', 'data.json'), ('evaluator', 'evaluator.json')]
        bindings = [{'role': role, 'path': path, 'sha256': file_sha(self.root / path)} for role, path in paths]
        protocol = {role + '_sha256': ProjectStore._role_sha({'bindings': bindings}, role)
                    for role in ('code', 'config', 'data')}
        protocol.update(data_split='public-controller-fixture', init='none', seed=0, checkpoint='none',
                        schedule='bounded fixture calls', sample_work={'cases': 1}, numeric_protocol='Python integer')
        self.write('protocol.json', protocol)
        ref = {'path': 'protocol.json', 'sha256': file_sha(self.root / 'protocol.json')}
        bindings.append({'role': 'protocol', **ref})
        routes, nodes, repair_slots = [], [], []
        for rid in (['baseline'] if baseline else []) + ['solve'] + ['repair' + str(i + 1) for i in range(slots)]:
            is_repair = rid.startswith('repair')
            response = 'outputs/' + rid + '.json'
            argv = [sys.executable, '-B', 'worker.py', '--run', rid] if is_repair else [sys.executable, '-B', 'code.py', response]
            outputs = output_paths(response) if is_repair else [response]
            duration = timeout if is_repair else 2
            manifest = {'schema': 1, 'id': rid, 'arm': 'tool', 'control_id': None, 'protocol': ref,
                        'argv': argv, 'outpaths': outputs, 'timeout_seconds': duration,
                        'resource_estimates': {'wall_seconds': duration}}
            routes.append({'candidate': rid, 'manifest': manifest})
            pre = [{'fact': 'autonomy.' + (rid if is_repair else 'repair1') + ('.ready' if is_repair else '.adopted'),
                    'op': 'eq', 'value': True}]
            if rid == 'baseline':
                pre = []
            action = {'id': rid, 'kind': 'OBLIGATION_CHECK', 'target': 'score',
                      'description': 'Execute a bounded local controller fixture', 'claim': 'Finite software regression only',
                      'required_observables': ['score'],
                      'outcomes': [{'observation': 'verified', 'next_decision': 'review original result'},
                                   {'observation': 'counterexample', 'next_decision': 'repair from original failure'},
                                   {'observation': 'unresolved', 'next_decision': 'retain unknown'}]}
            nodes.append({'id': rid, 'sources': ['explicit local fixture'],
                          'executable': {'decisions': ['next'], 'preconditions': pre, 'action': action}})
            if is_repair:
                repair_slots.append({'run_id': rid, 'code_path': 'code.py', 'worker_path': 'worker.py', 'response_path': response})
        config = {'schema': 1, 'max_steps': max_steps, 'repair_slots': repair_slots,
                  'provider': {'kind': 'fixture', 'argv': [sys.executable, '-B', 'provider.py'],
                               'executable_sha256': file_sha(sys.executable),
                               'model': None, 'effort': None, 'service_tier': None}}
        if control_wall is not None:
            config['controller_wall_seconds'] = control_wall
        policy = {'schema': 1, 'context': {'decision': {'id': 'next', 'goal_revision': 'controller-fixture-v1',
                    'scope': {'domain': 'finite software fixture'}, 'goal_conditions': [{'fact': 'score', 'op': 'eq', 'value': 6}]}},
                  'graph': {'nodes': nodes, 'edges': []}, 'routes': routes,
                  'observations': [{'fact': 'score', 'run_id': 'solve', 'path': 'outputs/solve.json', 'selector': {'pointer': '/score'}}],
                  'autonomy': config}
        self.contract = {'schema': 1, 'bindings': bindings, 'allowed_commands': [r['manifest']['argv'] for r in routes],
                         'output_roots': ['outputs'], 'budget': {'wall_seconds': budget}, 'advisor_policy': policy,
                         'method_evolution': {'schema': 1, 'max_revisions': 4, 'code_paths': ['code.py']},
                         'stop_policy': {'schema': 1, 'wall_seconds': 120, 'progress': {'window_seconds': 20, 'min_bytes': 0}}}
        self.write('contract.json', self.contract)
        self.store.initialize(self.contract)

    def cli(self, *args, program=None):
        p = subprocess.run([sys.executable, '-B', str(program or ROOT / 'scripts/rds_cli.py'), '--root', str(self.root), *args],
                           capture_output=True, text=True, encoding='utf-8', timeout=35,
                           env={**os.environ, 'RDS_USAGE_DB': str(self.root / 'usage.sqlite3')})
        self.trace.append({'argv': list(args), 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
        evidence = os.environ.get('RDS_AUTONOMY_EVIDENCE')
        if evidence:
            directory = Path(evidence) / self._testMethodName
            directory.mkdir(parents=True, exist_ok=True)
            (directory / 'cli-transcript.json').write_text(json.dumps(self.trace), encoding='utf-8')
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads(p.stdout)

    def events(self, kind=None):
        with self.store._db(True) as db:
            values = [json.loads(row['body']) for row in db.execute('SELECT body FROM events ORDER BY id')]
        return [v for v in values if kind is None or v['kind'] == kind]

    def calls(self):
        path = self.root / 'provider-calls.jsonl'
        return [json.loads(line)['run_id'] for line in path.read_text().splitlines()] if path.exists() else []

    def request(self):
        report = review(self.store)
        self.assertIsNone(report['selected_run'])
        self.assertEqual(autonomy.request_repair(self.store, report), 'REQUESTED')
        return self.events(autonomy.REQUESTED)[0]

    def run_repair(self):
        event = self.request()
        route = next(r['manifest'] for r in self.contract['advisor_policy']['routes'] if r['manifest']['id'] == event['run_id'])
        self.store.register(route)
        receipt = self.store.execute(event['run_id'])
        self.assertEqual(receipt['run_status'], 'SUCCEEDED', receipt)
        return event, receipt

    def assert_single_model_cost(self, before, rid='repair1'):
        after = self.store.snapshot()
        self.assertEqual([r['sha256'] for r in after['receipts']], [r['sha256'] for r in before['receipts']])
        # Repeated foreground passes have a real controller cost. The original
        # worker cost stays in the same receipt; only measured control work grows.
        worker_wall = sum(r['resources']['wall_seconds']['measured'] or 0 for r in after['receipts'])
        controller_wall = sum(e['controller_wall_seconds'] for e in self.events('AUTONOMY_DRIVE_RELEASED'))
        self.assertAlmostEqual(after['budget']['wall_seconds']['spent_measured'], worker_wall + controller_wall, places=6)
        self.assertGreaterEqual(after['budget']['wall_seconds']['spent_measured'], before['budget']['wall_seconds']['spent_measured'])
        self.assertEqual(after['budget']['wall_seconds']['charged_estimate'], 0)
        self.assertEqual(after['budget']['wall_seconds']['reserved'], 0)
        self.assertEqual(len([r for r in after['runs'] if r['id'] == rid]), 1)
        self.assertEqual(len(self.events('AUTONOMY_MODEL_DISPATCH_INTENT')), 1)

    def test_actual_cli_repair_adoption_and_application_one_ledger(self):
        self.build()
        result = self.cli('project', 'drive', '--max-steps', '4')
        self.assertEqual(self.calls(), ['repair1'])
        self.assertEqual([r['id'] for r in self.store.snapshot()['runs']], ['repair1', 'solve'])
        self.assertEqual(json.loads((self.root / 'outputs/solve.json').read_text())['score'], 6)
        self.assertEqual(len(self.events('METHOD_REVISION_ADOPTED')), 1)
        event = self.events(autonomy.REQUESTED)[0]
        receipt = next(r for r in self.store.snapshot()['receipts'] if r['run_id'] == 'repair1')
        self.assertEqual(receipt['autonomy_request'], event['request'])
        self.assertEqual(receipt['effective_contract_sha256'], event['parent_sha256'])
        self.assertEqual(result['scientific_support'], 'UNKNOWN')
        self.assertNotEqual(result['status'], 'GOAL_CONFIRMED')  # No domain confirmation was declared.
        self.assertEqual(self.events('AUTONOMY_DRIVE_CLAIMED')[0]['controller_reservation'], 30)
        self.assert_single_model_cost(self.store.snapshot())

    def test_reserved_route_does_not_start_after_control_allowance(self):
        self.build(baseline=True, control_wall=.001)
        route = next(r['manifest'] for r in self.contract['advisor_policy']['routes'] if r['candidate'] == 'baseline')
        self.store.register(route)
        before = self.store.snapshot()
        result = self.cli('project', 'drive', '--max-steps', '1')
        after = self.store.snapshot()
        self.assertEqual(result['status'], 'HANDOFF_REQUIRED', result)
        self.assertIn('CONTROLLER_WALL_ALLOWANCE_EXHAUSTED', result['reason'])
        self.assertEqual(after['runs'][0]['status'], 'RESERVED')
        self.assertIsNone(after['runs'][0]['attempt_id'])
        self.assertEqual(after['receipts'], before['receipts'])
        self.assertEqual(after['budget']['wall_seconds']['reserved'], before['budget']['wall_seconds']['reserved'])
        self.assertGreater(after['budget']['wall_seconds']['spent_measured'] + after['budget']['wall_seconds']['charged_estimate'], 0)
        self.assertEqual(self.calls(), [])

    def test_registration_and_execute_review_cannot_bypass_admission_allowance(self):
        import time
        for slow_boundary in ('register', '_advisor_prepare_run'):
            with self.subTest(boundary=slow_boundary):
                # The real review/registration remains intact; only its elapsed
                # work is delayed, so no verdict or worker result is mocked.
                root = self.root / slow_boundary
                root.mkdir()
                self.root, self.store = root, ProjectStore(root)
                self.build(baseline=True, control_wall=1.)
                original = getattr(self.store, slow_boundary)
                def delayed(*args, **kwargs):
                    result = original(*args, **kwargs)
                    time.sleep(1.05)
                    return result
                with patch.object(self.store, slow_boundary, side_effect=delayed):
                    result = autonomy.drive(self.store, max_steps=1)
                state = self.store.snapshot()
                self.assertEqual(result['status'], 'HANDOFF_REQUIRED', result)
                self.assertIn('CONTROLLER_WALL_ALLOWANCE_EXHAUSTED', result['reason'])
                self.assertEqual(len(state['runs']), 1)
                self.assertIsNone(state['runs'][0]['attempt_id'])
                self.assertEqual(state['runs'][0]['status'], 'RESERVED')
                self.assertEqual(state['receipts'], [])
                self.assertGreaterEqual(result['controller_wall_seconds'], 1.05)
                self.assertEqual(self.calls(), [])

    def test_oversize_provider_originals_stay_unknown_without_a_second_paid_slot(self):
        for mode, reason in (('envelope_bound', 'MODEL_RESPONSE_ENVELOPE_BYTE_LIMIT'),
                             ('raw_bound', 'PROVIDER_RESPONSE_BYTE_LIMIT')):
            with self.subTest(mode=mode):
                root = self.root / mode
                root.mkdir()
                self.root, self.store = root, ProjectStore(root)
                self.build(modes={'repair1': mode}, slots=2, timeout=5)
                result = self.cli('project', 'drive', '--max-steps', '4')
                self.assertEqual(result['status'], 'RECONCILE_MODEL_DELIVERY_REQUIRED', result)
                response = (root / 'outputs/repair1.json').read_bytes()
                self.assertLessEqual(len(response), autonomy.MAX_BYTES)
                self.assertEqual(json.loads(response)['reason'], reason)
                self.assertGreaterEqual((root / 'outputs/repair1.json.trace.jsonl').stat().st_size, autonomy.MAX_BYTES)
                original = self.store.snapshot()
                processed = self.events(autonomy.PROCESSED)
                self.assertEqual(len(processed), 1)
                self.assertIn(processed[0]['outcome'], {'UNKNOWN', 'MODEL_OUTCOME_UNKNOWN'})
                event = self.events(autonomy.REQUESTED)[0]
                self.assertEqual(autonomy.process_result(self.store, event, original['receipts'][0]), processed[0]['outcome'])
                self.cli('project', 'drive', '--max-steps', '4')
                self.assertEqual(self.calls(), ['repair1'])
                self.assertEqual(self.events(autonomy.PROCESSED), processed)
                self.assert_single_model_cost(original)

    def test_model_original_tamper_is_not_a_rejected_method_or_fresh_purchase(self):
        self.build(slots=2)
        event, receipt = self.run_repair()
        response = self.root / 'outputs/repair1.json'
        original = response.read_bytes()
        tampered = original.replace(b'proposed', b'xroposed', 1)
        self.assertEqual(len(original), len(tampered))
        response.write_bytes(tampered)
        with self.assertRaises(autonomy.ModelResultIntegrityError):
            autonomy.process_result(self.store, event, receipt)
        self.assertEqual(self.events(autonomy.PROCESSED), [])
        blocked = self.cli('project', 'drive', '--max-steps', '4')
        self.assertEqual(blocked['status'], 'HANDOFF_REQUIRED', blocked)
        self.assertEqual(self.events(autonomy.PROCESSED), [])
        self.assertEqual(self.calls(), ['repair1'])
        response.write_bytes(original)
        recovered = self.cli('project', 'drive', '--max-steps', '4')
        self.assertEqual(self.calls(), ['repair1'])
        self.assertEqual(len(self.events('METHOD_REVISION_ADOPTED')), 1)
        self.assertEqual(self.events(autonomy.PROCESSED)[0]['outcome'], 'ADOPTED')
        self.assertNotEqual(recovered['status'], 'HANDOFF_REQUIRED', recovered)

    def test_legacy_paid_oversize_envelope_is_processed_unknown_once(self):
        self.build(modes={'repair1': 'envelope_bound'}, slots=2, timeout=5, legacy_worker=True)
        event = self.request()
        # A frozen producer distribution supports its own original adapter. The
        # updated consumer never admits that old adapter as a fresh attempt.
        producer = self.root / 'legacy-producer'
        producer.mkdir()
        for original in (ROOT / 'scripts').glob('*.py'):
            shutil.copyfile(original, producer / original.name)
        shutil.copyfile(self.root / 'worker.py', producer / 'rds_autonomy_worker.py')
        route = next(r['manifest'] for r in self.contract['advisor_policy']['routes'] if r['candidate'] == 'repair1')
        self.write('legacy-manifest.json', route)
        self.cli('project', 'create', '--manifest', str(self.root / 'legacy-manifest.json'), program=producer / 'rds_cli.py')
        receipt = self.cli('project', 'execute', '--id', 'repair1', program=producer / 'rds_cli.py')['receipt']
        self.assertEqual(receipt['run_status'], 'SUCCEEDED', receipt)
        response = self.root / 'outputs/repair1.json'
        self.assertGreater(response.stat().st_size, autonomy.MAX_BYTES)
        original = self.store.snapshot()
        self.assertEqual(autonomy.process_result(self.store, event, receipt), 'MODEL_OUTCOME_UNKNOWN')
        processed = self.events(autonomy.PROCESSED)
        self.assertEqual(processed[0]['reason'], 'ORIGINAL_MODEL_RESPONSE_JSON_BYTE_LIMIT')
        self.assertEqual(autonomy.process_result(self.store, event, receipt), 'MODEL_OUTCOME_UNKNOWN')
        self.assertEqual(self.events(autonomy.PROCESSED), processed)
        recovered = self.cli('project', 'drive', '--max-steps', '4')
        self.assertEqual(recovered['status'], 'RECONCILE_MODEL_DELIVERY_REQUIRED', recovered)
        self.assertEqual(self.calls(), ['repair1'])
        self.assert_single_model_cost(original)

    def test_old_adapter_cannot_authorize_a_new_model_attempt(self):
        self.build(legacy_worker=True)
        result = self.cli('project', 'drive', '--max-steps', '4')
        self.assertEqual(result['status'], 'HANDOFF_REQUIRED', result)
        self.assertIn('Unsupported adapter', result['reason'])
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.store.snapshot()['runs'], [])
        self.assertEqual(self.store.snapshot()['receipts'], [])

    def test_corrupt_validated_proposal_is_evidence_failure_not_method_feedback(self):
        self.build(slots=2)
        event, receipt = self.run_repair()
        with patch.object(revision, 'apply', side_effect=OSError('Interrupted before method prepare')):
            with self.assertRaisesRegex(OSError, 'Interrupted before method prepare'):
                autonomy.process_result(self.store, event, receipt)
        with self.store._db() as db:
            row = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='AUTONOMY_PROPOSAL_VALIDATED'").fetchone()
            original = row['body']
            damaged = json.loads(original)
            damaged['receipt_sha256'] = 'f' * 64
            # Preserve append-only originals. A forged retained record cannot
            # become method feedback even if it is appended after validation.
            db.execute('INSERT INTO events(body) VALUES (?)', (json.dumps(damaged),))
        blocked = self.cli('project', 'drive', '--max-steps', '4')
        self.assertEqual(blocked['status'], 'HANDOFF_REQUIRED', blocked)
        self.assertEqual(self.events(autonomy.PROCESSED), [])
        self.assertEqual(self.calls(), ['repair1'])
        self.assertEqual(self.events('METHOD_REVISION_ADOPTED'), [])

    def test_prepare_only_retains_request_after_baseline_without_provider_dispatch(self):
        self.build(baseline=True)
        self.cli('project', 'drive', '--max-steps', '1')
        before = self.store.snapshot()
        self.assertEqual([r['id'] for r in before['runs']], ['baseline'])
        start = self.events('CAMPAIGN_STARTED')[0]
        prepared = self.cli('project', 'drive', '--prepare-only', '--max-steps', '4')
        state = self.store.snapshot()
        self.assertEqual(prepared['status'], 'MODEL_REQUEST_READY')
        self.assertEqual(prepared['run_id'], 'repair1')
        event = self.events(autonomy.REQUESTED)[0]
        self.assertEqual(prepared['request'], event['request'])
        self.assertEqual(prepared['parent_sha256'], event['parent_sha256'])
        self.assertEqual(prepared['provider'], self.contract['advisor_policy']['autonomy']['provider'])
        self.assertEqual(prepared['provider_timeout_seconds'], 2)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.events('AUTONOMY_MODEL_DISPATCH_INTENT'), [])
        self.assertEqual(state['runs'], before['runs'])
        self.assertEqual(state['receipts'], before['receipts'])
        self.assertGreater(state['budget']['wall_seconds']['spent_measured'], before['budget']['wall_seconds']['spent_measured'])
        self.assertEqual(state['budget']['wall_seconds']['reserved'], 0)
        again = self.cli('project', 'drive', '--prepare-only', '--max-steps', '4')
        self.assertEqual(again['status'], 'MODEL_REQUEST_READY')
        self.assertEqual(again['request'], prepared['request'])
        self.assertEqual(len(self.events(autonomy.REQUESTED)), 1)
        self.assertEqual(self.calls(), [])
        self.cli('project', 'drive', '--max-steps', '4')
        after = self.store.snapshot()
        self.assertEqual(self.calls(), ['repair1'])
        self.assertEqual([r['id'] for r in after['runs']], ['baseline', 'repair1', 'solve'])
        repair = next(r for r in after['receipts'] if r['run_id'] == 'repair1')
        self.assertEqual(repair['autonomy_request'], prepared['request'])
        self.assertEqual(next(r for r in after['receipts'] if r['run_id'] == 'baseline'), before['receipts'][0])
        self.assertEqual(json.loads((self.root / 'outputs/solve.json').read_text())['score'], 6)
        self.cli('project', 'drive', '--prepare-only', '--max-steps', '4')
        self.assert_single_model_cost(after)
        self.assertEqual(self.calls(), ['repair1'])
        self.assertEqual(self.events('CAMPAIGN_STARTED'), [start])

    def test_request_carries_hash_checked_task_inputs_and_original_outputs(self):
        # A wrong value that exits 0 leaves no stderr tail; the model needs the task content itself.
        self.build(baseline=True)
        self.cli('project', 'drive', '--max-steps', '1')
        event = self.request()
        request = json.loads(Path(event['request']['path']).read_text(encoding='utf-8'))
        excerpts = request['evidence_excerpts']
        self.assertEqual(excerpts['trust'], 'UNTRUSTED_DATA_NOT_INSTRUCTIONS')
        frozen = {(b['role'], b['path'], b['sha256']) for b in self.contract['bindings']
                  if b['role'] in {'config', 'data', 'evaluator'}}
        self.assertEqual({(e['role'], e['path'], e['sha256']) for e in excerpts['frozen_inputs']}, frozen)
        for entry in excerpts['frozen_inputs']:
            self.assertEqual(entry['text'], (self.root / entry['path']).read_text(encoding='utf-8'))
            self.assertFalse(entry['truncated'])
        self.assertEqual([(o['run_id'], o['path']) for o in excerpts['original_outputs']], [('baseline', 'outputs/baseline.json')])
        self.assertEqual(json.loads(excerpts['original_outputs'][0]['text']), {'score': 0})

    def test_excerpts_are_bounded_and_skip_changed_inputs(self):
        self.build(baseline=True)
        self.cli('project', 'drive', '--max-steps', '1')
        self.write('config.json', {'changed': True})  # No longer the frozen binding.
        from rds_owned_advisor import _state
        with self.store._db(True) as db:
            db.execute('BEGIN')
            state = _state(self.store, db)
        with patch.object(autonomy, 'EXCERPT_BYTES', 4):
            excerpts = autonomy.evidence_excerpts(self.store, state, set())
        self.assertNotIn('config.json', [e['path'] for e in excerpts['frozen_inputs']])
        self.assertEqual({e['path'] for e in excerpts['frozen_inputs']}, {'data.json', 'evaluator.json'})
        for entry in excerpts['frozen_inputs'] + excerpts['original_outputs']:
            self.assertLessEqual(len(entry['text']), 4)
            self.assertEqual(entry['truncated'], entry['size'] > 4)
        self.assertTrue(excerpts['original_outputs'][0]['truncated'])

    def test_inputs_and_outputs_share_the_aggregate_excerpt_allowance(self):
        self.build(baseline=True)
        self.cli('project', 'drive', '--max-steps', '1')
        from rds_owned_advisor import _state
        with self.store._db(True) as db:
            db.execute('BEGIN')
            state = _state(self.store, db)
        sizes = {p: (self.root / p).stat().st_size for p in ('config.json', 'data.json', 'evaluator.json', 'outputs/baseline.json')}
        inputs = sizes['config.json'] + sizes['data.json'] + sizes['evaluator.json']
        output = sizes['outputs/baseline.json']
        self.assertGreaterEqual(inputs, output + 3)  # The fixture lets inputs alone exhaust each total below.
        per_file = max(sizes.values()) + 1  # Every file would fit whole under the per-file limit alone.
        # (total, retained input bytes, retained output bytes)
        cases = ((inputs + output + 5, inputs, output),  # Everything fits whole.
                 (inputs + output - 3, inputs - 3, output),  # Outputs need less than half: inputs get the rest.
                 (output + 1, output + 1 - (output + 1) // 2, (output + 1) // 2))  # Inputs cannot crowd outputs out.
        for total, kept_inputs, kept_output in cases:
            with patch.object(autonomy, 'EXCERPT_BYTES', per_file), patch.object(autonomy, 'EXCERPT_TOTAL', total):
                excerpts = autonomy.evidence_excerpts(self.store, state, set())
            retained = lambda entries: sum(len(e['text'].encode('utf-8')) for e in entries)
            self.assertEqual(retained(excerpts['frozen_inputs']), kept_inputs)
            self.assertEqual(retained(excerpts['original_outputs']), kept_output)
            self.assertEqual([o['path'] for o in excerpts['original_outputs']], ['outputs/baseline.json'])
            for entry in excerpts['frozen_inputs'] + excerpts['original_outputs']:
                raw = (self.root / entry['path']).read_bytes()
                self.assertEqual(entry['text'].encode('utf-8'), raw[:len(entry['text'].encode('utf-8'))])
                self.assertEqual(entry['truncated'], len(entry['text'].encode('utf-8')) < len(raw))

    def test_excerpt_text_bytes_stay_bounded_for_undecodable_and_split_characters(self):
        # U+FFFD replacement is 3 UTF-8 bytes per undecodable byte; the bound is on retained text.
        # A head cut inside a 4-byte character must not invent a U+FFFD that is not in the file.
        payloads = {'binary.bin': b'\xff' * 64, 'accents.txt': 'é'.encode('utf-8') * 8, 'exact.txt': b'abcd',
                    'emoji.txt': 'ab😀'.encode('utf-8')}
        bindings = []
        for name, raw in payloads.items():
            (self.root / name).write_bytes(raw)
            bindings.append({'role': 'data', 'path': name, 'sha256': hashlib.sha256(raw).hexdigest()})
        state = {'contract': {'bindings': bindings}, 'receipts': []}
        with patch.object(autonomy, 'EXCERPT_BYTES', 5), patch.object(autonomy, 'EXCERPT_TOTAL', 64):
            excerpts = autonomy.evidence_excerpts(self.store, state, set())['frozen_inputs']
        texts = {e['path']: e['text'] for e in excerpts}
        self.assertEqual(texts, {'binary.bin': '�', 'accents.txt': 'éé', 'exact.txt': 'abcd', 'emoji.txt': 'ab'})
        self.assertEqual({e['path']: e['truncated'] for e in excerpts},
                         {'binary.bin': True, 'accents.txt': True, 'exact.txt': False, 'emoji.txt': True})
        with patch.object(autonomy, 'EXCERPT_BYTES', 5), patch.object(autonomy, 'EXCERPT_TOTAL', 6):
            excerpts = autonomy.evidence_excerpts(self.store, state, set())['frozen_inputs']
        self.assertEqual([(e['path'], e['text'], e['truncated']) for e in excerpts],
                         [('binary.bin', '�', True), ('accents.txt', 'é', True), ('exact.txt', 'a', True)])

    def test_repair_cannot_start_without_a_program_owned_request(self):
        self.build()
        manifest = self.contract['advisor_policy']['routes'][1]['manifest']
        with self.assertRaisesRegex(ValueError, 'did not select|request'):
            self.store.register(manifest)
        self.assertEqual(self.store.snapshot()['runs'], [])
        self.assertEqual(self.calls(), [])

    def test_request_cas_tamper_rejects_reserved_launch(self):
        self.build()
        event = self.request()
        self.store.register(self.contract['advisor_policy']['routes'][1]['manifest'])
        before = self.store.snapshot()
        path = Path(event['request']['path'])
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaises(ValueError):
            self.store.execute('repair1')
        self.assertEqual(self.calls(), [])
        after = self.store.snapshot()
        self.assertEqual(after['budget'], before['budget'])
        self.assertEqual(after['runs'][0]['attempt_id'], None)
        self.assertEqual(after['receipts'], [])

    def test_changed_provider_code_rejects_before_model_dispatch(self):
        self.build()
        self.request()
        self.store.register(self.contract['advisor_policy']['routes'][1]['manifest'])
        self.write('provider.py', 'raise SystemExit(0)\n')
        receipt = self.store.execute('repair1')
        self.assertEqual(receipt['run_status'], 'FAILED')
        self.assertIs(receipt['process_started'], False)
        self.assertIsNone(receipt['pid'])
        self.assertTrue(any('provider.py' in error for error in receipt['errors']), receipt['errors'])
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.events('AUTONOMY_MODEL_DISPATCH_INTENT'), [])
        self.assertEqual(len(self.store.snapshot()['receipts']), 1)

    def test_provider_executable_identity_is_rechecked_before_launch(self):
        self.build()
        self.request()
        self.store.register(self.contract['advisor_policy']['routes'][1]['manifest'])
        with self.store._db(True) as db:
            run = self.store._run(db, 'repair1')
            contract = self.store._contract(db)
            provider=Path(contract['advisor_policy']['autonomy']['provider']['argv'][0]).resolve()
            original_sha=autonomy.file_sha
            def changed_provider(path):
                return '0'*64 if Path(path).resolve()==provider else original_sha(path)
            with patch.object(autonomy, 'file_sha', side_effect=changed_provider):
                with self.assertRaisesRegex(ValueError, 'executable changed'):
                    autonomy.check_run(self.store, db, contract, run)
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.events('AUTONOMY_MODEL_DISPATCH_INTENT'), [])
        self.assertIsNone(next(r for r in self.store.snapshot()['runs'] if r['id']=='repair1')['attempt_id'])

    def test_failed_provider_is_not_reexecuted_or_recharged(self):
        self.build(modes={'repair1': 'exit'})
        self.cli('project', 'drive', '--max-steps', '4')
        before = self.store.snapshot()
        self.cli('project', 'drive', '--max-steps', '4')
        self.assertEqual(self.calls(), ['repair1'])
        self.assert_single_model_cost(before)
        response = json.loads((self.root / 'outputs/repair1.json').read_text())
        self.assertEqual(response['returncode'], 9)
        self.assertEqual(response['usage'], 'UNKNOWN')
        self.assertFalse((self.root / 'outputs/solve.json').exists())

    def test_malformed_provider_reply_stays_unknown_without_another_call(self):
        self.build(modes={'repair1': 'malformed'})
        self.cli('project', 'drive', '--max-steps', '4')
        before = self.store.snapshot()
        self.cli('project', 'drive', '--max-steps', '4')
        self.assertEqual(self.calls(), ['repair1'])
        self.assert_single_model_cost(before)
        self.assertEqual(len(self.events('METHOD_REVISION_ADOPTED')), 0)
        self.assertFalse((self.root / 'outputs/solve.json').exists())

    def test_timed_out_provider_retains_original_attempt_and_fee(self):
        self.build(modes={'repair1': 'timeout'}, timeout=3)
        self.cli('project', 'drive', '--max-steps', '4')
        before = self.store.snapshot()
        self.cli('project', 'drive', '--max-steps', '4')
        self.assertEqual(self.calls(), ['repair1'])
        self.assert_single_model_cost(before)
        self.assertTrue(json.loads((self.root / 'outputs/repair1.json').read_text())['timed_out'])
        self.assertGreater(before['budget']['wall_seconds']['spent_measured'], 0)

    def test_adopted_then_controller_crash_resumes_exact_proposal(self):
        self.build()
        original = autonomy._append
        def crash(db, event):
            if event['kind'] == autonomy.PROCESSED and event.get('outcome') == 'ADOPTED':
                raise RuntimeError('synthetic crash after method adoption')
            return original(db, event)
        with patch.object(autonomy, '_append', side_effect=crash):
            with self.assertRaisesRegex(RuntimeError, 'after method adoption'):
                autonomy.drive(self.store, 4)
        before = self.store.snapshot()
        self.assertEqual(len(before['contract_history']), 2)
        self.assertEqual(self.calls(), ['repair1'])
        self.cli('project', 'drive', '--prepare-only', '--max-steps', '4')
        after = self.store.snapshot()
        self.assertEqual(len(after['contract_history']), 2)
        self.assertEqual(self.calls(), ['repair1'])
        self.assertEqual(next(r for r in after['receipts'] if r['run_id']=='repair1'), before['receipts'][0])
        self.assertEqual(len(self.events(autonomy.PROCESSED)), 1)
        self.assertEqual(json.loads((self.root / 'outputs/solve.json').read_text())['score'], 6)

    def test_prepared_partial_copy_resumes_without_another_model_call(self):
        self.build()
        original, copies = revision._replace, []
        def interrupt(path, raw):
            copies.append(str(path))
            if len(copies) == 2:
                raise OSError('synthetic partial copy')
            return original(path, raw)
        with patch.object(revision, '_replace', side_effect=interrupt):
            autonomy.drive(self.store, 4)
        before = self.store.snapshot()
        self.assertIsNotNone(before['method_revision_pending'])
        self.assertEqual(self.calls(), ['repair1'])
        self.cli('project', 'drive', '--prepare-only', '--max-steps', '4')
        after = self.store.snapshot()
        self.assertIsNone(after['method_revision_pending'])
        self.assertEqual(self.calls(), ['repair1'])
        self.assertEqual(len(after['contract_history']), 2)
        self.assertEqual(next(r for r in after['receipts'] if r['run_id']=='repair1'), before['receipts'][0])

    def test_total_step_cap_survives_multiple_drive_invocations(self):
        self.build(max_steps=1)
        self.cli('project', 'drive', '--max-steps', '1')
        before = self.store.snapshot()
        start = self.events('CAMPAIGN_STARTED')[0]
        result = self.cli('project', 'drive', '--max-steps', '4')
        self.assertIn('TOTAL_STEP_LIMIT', result.get('reason', ''))
        self.assertEqual(len(self.store.snapshot()['runs']), 1)
        self.assertEqual(self.calls(), ['repair1'])
        self.assert_single_model_cost(before)
        self.assertEqual(self.events('CAMPAIGN_STARTED'), [start])
        self.assertFalse((self.root / 'outputs/solve.json').exists())

    def test_short_drive_passes_preserve_budget_deadline_and_receipts(self):
        self.build()
        self.cli('project', 'drive', '--max-steps', '1')
        before = self.store.snapshot()
        start = self.events('CAMPAIGN_STARTED')[0]
        self.cli('project', 'drive', '--max-steps', '1')
        self.cli('project', 'drive', '--max-steps', '1')
        after = self.store.snapshot()
        self.assertEqual(self.calls(), ['repair1'])
        self.assertEqual(len(after['runs']), 2)
        self.assertEqual(len(after['receipts']), 2)
        self.assertEqual(self.events('CAMPAIGN_STARTED'), [start])
        self.assertEqual(after['budget']['wall_seconds']['cap'], before['budget']['wall_seconds']['cap'])
        self.assertGreater(after['budget']['wall_seconds']['spent_measured'], before['budget']['wall_seconds']['spent_measured'])
        self.assertEqual(after['budget']['wall_seconds']['reserved'], 0)
        self.assertEqual(next(r for r in after['receipts'] if r['run_id']=='repair1'), before['receipts'][0])

    def test_controller_reservation_prevents_spending_its_headroom_on_a_worker(self):
        self.build(budget=30)
        result = self.cli('project', 'drive', '--max-steps', '4')
        state = self.store.snapshot()
        self.assertEqual(result['status'], 'BUDGET_EXHAUSTED')
        self.assertEqual(self.calls(), [])
        self.assertEqual(state['runs'], [])
        self.assertEqual(state['receipts'], [])
        self.assertEqual(state['budget']['wall_seconds']['cap'], 30)
        self.assertEqual(state['budget']['wall_seconds']['reserved'], 0)
        self.assertGreater(state['budget']['wall_seconds']['spent_measured'], 0)
        self.assertEqual(self.events('AUTONOMY_DRIVE_CLAIMED')[0]['controller_reservation'], 30)

    def test_dead_controller_settlement_survives_exhausted_new_admission(self):
        self.build(budget=30)
        owner, allowance = autonomy._claim(self.store)
        before = self.store.snapshot()
        self.assertEqual(allowance, 30)
        self.assertEqual(before['budget']['wall_seconds']['reserved'], 30)
        self.assertEqual(before['budget']['wall_seconds']['charged_estimate'], 0)
        with patch.object(autonomy, '_alive', return_value=False):
            first = autonomy.drive(self.store, 4)
            settled = self.store.snapshot()
            second = autonomy.drive(self.store, 4)
        self.assertEqual(first['status'], 'CONTROLLER_ADMISSION_BLOCKED')
        self.assertEqual(second['status'], 'CONTROLLER_ADMISSION_BLOCKED')
        self.assertIn('no remaining wall budget', first['reason'])
        self.assertIn('no remaining wall budget', second['reason'])
        after = self.store.snapshot()
        self.assertEqual(after['budget'], settled['budget'])
        self.assertEqual(after['budget']['wall_seconds']['reserved'], 0)
        self.assertEqual(after['budget']['wall_seconds']['charged_estimate'], 30)
        self.assertEqual(after['budget']['wall_seconds']['spent_measured'], 0)
        releases = self.events('AUTONOMY_DRIVE_RELEASED')
        self.assertEqual(len(releases), 1)
        self.assertEqual(releases[0]['owner'], owner)
        self.assertEqual(releases[0]['reason'], 'OWNER_DEAD_NO_WORKER_RELAUNCH')
        self.assertEqual(len(self.events('AUTONOMY_DRIVE_CLAIMED')), 1)
        self.assertEqual(after['runs'], [])
        self.assertEqual(after['receipts'], [])
        self.assertEqual(self.calls(), [])
        self.assertEqual(self.events('AUTONOMY_MODEL_DISPATCH_INTENT'), [])
        with self.store._db(True) as db:
            revision._idle(self.store, db)

    def test_rejected_first_proposal_uses_next_slot_with_original_cost_retained(self):
        self.build(modes={'repair1': 'bad'}, slots=2)
        self.cli('project', 'drive', '--max-steps', '4')
        if not (self.root / 'outputs/solve.json').exists():
            self.cli('project', 'drive', '--max-steps', '4')
        state = self.store.snapshot()
        self.assertEqual(self.calls(), ['repair1', 'repair2'])
        self.assertEqual(len(state['runs']), 3)
        self.assertEqual(len(state['receipts']), 3)
        self.assertEqual(len(self.events('METHOD_REVISION_ADOPTED')), 1)
        self.assertGreater(state['budget']['wall_seconds']['spent_measured'], 0)
        self.assertEqual(json.loads((self.root / 'outputs/solve.json').read_text())['score'], 6)

    def test_self_signed_domain_pass_cannot_complete_controller_goal(self):
        spec = importlib.util.spec_from_file_location('_autonomy_domain_fixture', ROOT / 'tests/test_rds_domain_confirmation.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        helper = module.DomainConfirmationTests('test_candidate_self_signed_pass_has_no_certificate')
        helper.root, helper.store = self.root, self.store
        self.write('provider.py', 'raise SystemExit(99)\n')
        original = ProjectStore.initialize
        def initialize(store, contract, **kwargs):
            contract = deepcopy(contract)
            contract['bindings'].append({'role':'code','path':'provider.py','sha256':file_sha(self.root / 'provider.py')})
            protocol = json.loads((self.root / 'protocol.json').read_text())
            protocol['code_sha256'] = ProjectStore._role_sha(contract, 'code')
            self.write('protocol.json', protocol)
            sha = file_sha(self.root / 'protocol.json')
            next(b for b in contract['bindings'] if b['role']=='protocol')['sha256'] = sha
            for route in contract['advisor_policy']['routes']:
                route['manifest']['protocol']['sha256'] = sha
            contract['advisor_policy']['autonomy'] = {'schema':1,'max_steps':4,'repair_slots':[],
                'provider':{'kind':'fixture','argv':[sys.executable,'-B','provider.py'],
                            'executable_sha256':file_sha(sys.executable),'model':None,'effort':None,'service_tier':None}}
            return original(store, contract, **kwargs)
        with patch.object(ProjectStore, 'initialize', new=initialize):
            helper.build(unsigned=True)
        result = self.cli('project', 'drive', '--max-steps', '4')
        self.assertNotEqual(result['status'], 'GOAL_CONFIRMED')
        self.assertEqual(result['advisor']['confirmation']['task_confirmation'], 'UNKNOWN')
        self.assertEqual(len(self.store.snapshot()['receipts']), 2)
        self.assertTrue(all(r['run_status']=='SUCCEEDED' for r in self.store.snapshot()['receipts']))
        self.assertEqual(self.calls(), [])


if __name__ == '__main__':
    unittest.main()
