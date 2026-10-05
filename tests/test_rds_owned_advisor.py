"""Real CLI acceptance for program-owned evidence, choice and dispatch.

The workloads are labelled synthetic CPU scripts. Their actual launch markers,
receipts, artifacts and budget charges establish software behavior, not a
scientific success rate. No helper substitutes for the advertised CLI boundary.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import ProjectStore, digest, _alive
from rds_tms_store import current


SCRIPT = '''import json, pathlib, sys, time
run_id, mode, output = sys.argv[1:]
with pathlib.Path("outputs/launches.txt").open("a", encoding="utf-8") as handle:
    handle.write(run_id + "\\n")
    handle.flush()
print("owned fixture started " + run_id, flush=True)
if mode == "timeout":
    time.sleep(4)
if mode == "slownegative":
    time.sleep(1)
if mode == "nonzero":
    print("intentional fixture failure", file=sys.stderr, flush=True)
    raise SystemExit(7)
if mode == "missing":
    raise SystemExit(0)
path = pathlib.Path(output)
if mode == "badjson":
    path.write_text("{broken", encoding="utf-8")
elif mode == "nan":
    path.write_text('{"score": NaN}', encoding="utf-8")
else:
    score = -1 if mode in ("negative", "slownegative", "large", "missing-extra") else 1
    path.write_text(json.dumps({"score": score, "run_id": run_id}), encoding="utf-8")
if mode == "large":
    pathlib.Path("outputs/weights.bin").write_bytes(b"synthetic unparsed weights\\n" * 130000)
'''


class OwnedAdvisorCLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-owned-advisor-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {**os.environ, 'RDS_USAGE_DB': str(self.root / 'usage.sqlite3')}

    def call(self, *args, ok=True, root=None):
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(root or self.root), *args],
            capture_output=True, text=True, encoding='utf-8', env=self.env, timeout=35)
        if ok:
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-6000:])
        return result

    def output(self, *args, status_codes=(0,)):
        result = self.call(*args, ok=False)
        self.assertIn(result.returncode, status_codes, (result.stdout + result.stderr)[-6000:])
        try:
            return json.loads(result.stdout)
        except ValueError:
            self.fail('CLI did not return JSON: ' + result.stdout + result.stderr)

    def write_json(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, allow_nan=False), encoding='utf-8')
        return str(path)

    def initialize(self, mode='negative', *, timeout=3, include_policy=True, mutate_policy=None, mutate_protocol=None, ok=True):
        files = {'code': ('code.py', SCRIPT), 'config': ('config.json', '{}'),
                 'data': ('data.json', '[1]'), 'evaluator': ('evaluator.json', '{}')}
        for name, text in files.values():
            (self.root / name).write_text(text, encoding='utf-8')
        sha = lambda name: hashlib.sha256((self.root / name).read_bytes()).hexdigest()
        protocol = {'code_sha256': sha('code.py'), 'config_sha256': sha('config.json'),
                    'data_sha256': sha('data.json'), 'data_split': 'synthetic-development',
                    'init': 'none', 'seed': 0, 'checkpoint': 'none',
                    'schedule': 'one scalar observation', 'sample_work': {'rows': 1},
                    'numeric_protocol': 'Python integer'}
        if mutate_protocol is not None:
            mutate_protocol(protocol)
        self.write_json('protocol.json', protocol)
        files['protocol'] = ('protocol.json', '')
        self.manifests = {}
        for run_id, run_mode in (('baseline', mode), ('repair', 'positive')):
            run_timeout = timeout if run_id == 'baseline' else 3
            self.manifests[run_id] = {
                'schema': 1, 'id': run_id, 'arm': 'control', 'control_id': None,
                'protocol': {'path': 'protocol.json', 'sha256': sha('protocol.json')},
                'argv': [sys.executable, '-B', 'code.py', run_id, run_mode, f'outputs/{run_id}.json'],
                'outpaths': [f'outputs/{run_id}.json'],
                'resource_estimates': {'wall_seconds': run_timeout + .2, 'cpu_seconds': 1, 'gpu_seconds': 0},
                'timeout_seconds': run_timeout}
        if mode in ('large', 'missing-extra'):
            self.manifests['baseline']['outpaths'].append('outputs/weights.bin')
        def node(run_id, preconditions):
            return {'id': run_id, 'sources': ['synthetic-owned-fixture'], 'executable': {
                'decisions': ['next'], 'preconditions': preconditions,
                'action': {'id': run_id, 'kind': 'PAIRED_TEST', 'target': 'baseline.score',
                           'operation': 'measure-' + run_id,
                           'description': 'Read the synthetic scalar from ' + run_id,
                           'competing_explanations': ['positive response', 'negative response'],
                           'required_observables': [run_id + '.score'],
                           'outcomes': [{'observation': 'positive', 'next_decision': 'stop at the scoped goal'},
                                        {'observation': 'negative', 'next_decision': 'review the bounded repair'}]}}}
        self.policy = {
            'schema': 1,
            'context': {'decision': {'id': 'next', 'goal_revision': 'synthetic-v1',
                                     'scope': {'domain': 'software-acceptance'},
                                     'goal_conditions': [{'fact': 'baseline.score', 'op': 'gte', 'value': .5}]}},
            'graph': {'nodes': [node('baseline', []), node('repair', [{'fact': 'baseline.score', 'op': 'lt', 'value': 0}])],
                      'edges': []},
            'routes': [{'candidate': run_id, 'manifest': deepcopy(spec)} for run_id, spec in self.manifests.items()],
            'observations': [{'fact': run_id + '.score', 'run_id': run_id, 'path': f'outputs/{run_id}.json',
                              'selector': {'pointer': '/score'}, 'format': 'json'} for run_id in self.manifests]}
        self.contract = {
            'schema': 1,
            'bindings': [{'role': role, 'path': name, 'sha256': sha(name)} for role, (name, _) in files.items()],
            'allowed_commands': [spec['argv'] for spec in self.manifests.values()],
            'output_roots': ['outputs'], 'budget': {'wall_seconds': 30, 'cpu_seconds': 10, 'gpu_seconds': 0}}
        if mutate_policy is not None:
            mutate_policy(self.policy)
        if include_policy:
            self.contract['advisor_policy'] = self.policy
        return self.call('project', 'init', '--contract', self.write_json('contract.json', self.contract), ok=ok)

    def create(self, run_id='baseline', *, spec=None, ok=True):
        return self.call('project', 'create', '--manifest',
                         self.write_json('create-' + run_id + '.json', spec or self.manifests[run_id]), ok=ok)

    def execute(self, run_id='baseline', *, ok=True):
        result = self.call('project', 'execute', '--id', run_id, ok=ok)
        return json.loads(result.stdout) if result.returncode == 0 else result

    def starts(self):
        path = self.root / 'outputs/launches.txt'
        return path.read_text(encoding='utf-8').splitlines() if path.exists() else []

    def snapshot(self):
        # Read-only independent evidence; mutations always traverse the real CLI.
        return ProjectStore(self.root).snapshot()

    def assert_uncharged(self, before):
        self.assertEqual(self.starts(), [])
        self.assertEqual(self.snapshot()['budget'], before['budget'])
        self.assertEqual(self.snapshot()['runs'], before['runs'])

    def assert_owned_receipt(self, receipt, run_id, status):
        self.assertEqual(receipt['run_id'], run_id)
        self.assertEqual(receipt['run_status'], status)
        # CLI wraps advice; it must not mutate the signed receipt payload.
        self.assertEqual(receipt['sha256'], digest({k: v for k, v in receipt.items() if k != 'sha256'}))
        saved = [row for row in self.snapshot()['receipts'] if row['run_id'] == run_id]
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]['sha256'], receipt['sha256'])

    def test_negative_result_is_collected_without_model_context_and_changes_route(self):
        self.initialize()
        self.assertEqual(self.output('advise')['selected_run'], 'baseline')
        self.create()
        completed = self.execute()
        self.assert_owned_receipt(completed['receipt'], 'baseline', 'SUCCEEDED')
        self.assertEqual(completed['advisor']['selected_run'], 'repair')
        self.assertEqual(self.starts(), ['baseline'])
        self.assertEqual(json.loads((self.root / 'outputs/baseline.json').read_text())['score'], -1)
        state = self.snapshot()
        self.assertEqual(state['budget']['cpu_seconds']['charged_estimate'], 1)
        self.assertEqual(state['budget']['cpu_seconds']['reserved'], 0)
        # Completion itself must have persisted evidence, before a follow-up advise call.
        saved = current(self.root)
        self.assertIsNotNone(saved)
        fact = next(node['owned_fact'] for node in saved['dependency_map']['nodes']
                    if node['id'] == 'owned:fact:baseline.score')
        self.assertEqual(fact['value'], -1)
        self.assertTrue(fact['reliable'])
        self.assertEqual(fact['source']['sha256'], hashlib.sha256((self.root / 'outputs/baseline.json').read_bytes()).hexdigest())
        observed = self.output('advise')
        self.assertEqual(observed['selected_run'], 'repair')
        self.assertEqual(observed['context']['facts']['baseline.score']['value'], -1)
        self.assertEqual(observed['coverage']['runs'], 1)
        self.assertEqual(observed['coverage']['receipts'], 1)
        self.assertEqual(observed['coverage']['parsed_observations'], 1)
        self.assertEqual(observed['coverage']['artifacts'], len(completed['receipt']['artifacts']))
        self.assertEqual(self.snapshot()['budget'], state['budget'])
        self.assertEqual(self.starts(), ['baseline'])

    def test_candidate_limit_counts_pending_routes_without_erasing_completed_dependency(self):
        def policy(value):
            value['context']['max_candidates'] = 1
            value['graph']['nodes'][0]['executable']['satisfied_when'] = [
                {'fact': 'baseline.score', 'op': 'lt', 'value': 0}]
            value['graph']['edges'] = [{'from': 'baseline', 'to': 'repair', 'relation': 'prerequisite_for'}]
        self.initialize(mutate_policy=policy)
        frozen = deepcopy(self.snapshot()['contract']['advisor_policy'])
        first = self.output('project', 'next')
        search = next(row['search'] for row in first['recommendations'] if 'search' in row)
        self.assertTrue(search['truncation']['truncated'])
        self.assertIn('candidate limit', search['truncation']['reasons'])
        completed = self.output('project', 'advance')
        self.assertEqual(completed['advisor']['selected_run'], 'repair')
        search = next(row['search'] for row in completed['advisor']['recommendations'] if 'search' in row)
        self.assertEqual(search['truncation']['limits']['max_candidates'], 1)
        self.assertFalse(search['truncation']['truncated'])
        self.assertEqual([row['action']['id'] for row in search['candidates']], ['repair'])
        self.assertTrue(any(step.get('step') == 'dependency' and step.get('from') == 'baseline'
                            for step in search['candidates'][0]['derivation']))
        self.assertEqual(completed['advisor']['context']['facts']['baseline.score']['value'], -1)
        self.assertEqual(completed['advisor']['coverage']['receipts'], 1)
        before = self.snapshot()
        self.create('repair')
        reserved = self.snapshot()
        self.assertEqual(reserved['budget']['cpu_seconds']['reserved'], 1)
        self.assertEqual(self.output('project', 'next')['selected_run'], 'repair')
        self.assertEqual(self.snapshot()['budget'], reserved['budget'])
        finished = self.output('project', 'advance')
        self.assert_owned_receipt(finished['receipt'], 'repair', 'SUCCEEDED')
        self.assertEqual(self.starts(), ['baseline', 'repair'])
        final = self.snapshot()
        self.assertEqual(final['receipts'][0], before['receipts'][0])
        self.assertEqual(final['contract']['advisor_policy'], frozen)
        self.assertEqual(final['budget']['cpu_seconds']['charged_estimate'], 2)
        self.assertEqual(final['budget']['cpu_seconds']['reserved'], 0)
        self.assertIsNone(self.output('project', 'advance')['selected_run'])
        self.assertEqual(self.snapshot()['budget'], final['budget'])

    def test_active_reservation_precedes_newly_ready_earlier_route_under_candidate_limit(self):
        def policy(value):
            value['context']['max_candidates'] = 1
            value['graph']['nodes'][0]['executable']['preconditions'] = [
                {'fact': 'run.repair.status', 'value': 'RESERVED'}]
            value['graph']['nodes'][1]['executable']['preconditions'] = []
        self.initialize(mutate_policy=policy)
        self.assertEqual(self.output('project', 'next')['selected_run'], 'repair')
        self.create('repair')
        reserved = self.snapshot()
        advice = self.output('project', 'next')
        self.assertEqual(advice['selected_run'], 'repair')
        search = next(row['search'] for row in advice['recommendations'] if 'search' in row)
        self.assertEqual([row['action']['id'] for row in search['candidates']], ['repair'])
        self.assertTrue(search['truncation']['truncated'])
        self.assertEqual(self.snapshot()['budget'], reserved['budget'])
        completed = self.output('project', 'advance')
        self.assert_owned_receipt(completed['receipt'], 'repair', 'SUCCEEDED')
        final = self.snapshot()
        self.assertEqual(self.starts(), ['repair'])
        self.assertEqual(len(final['runs']), 1)
        self.assertEqual(final['budget']['cpu_seconds']['charged_estimate'], 1)
        self.assertEqual(final['budget']['cpu_seconds']['reserved'], 0)
        self.assertIsNone(self.output('project', 'advance')['selected_run'])
        self.assertEqual(self.snapshot()['budget'], final['budget'])

    def test_active_fallback_keeps_its_slot_before_a_new_reservation(self):
        def policy(value):
            value['context']['max_candidates'] = 1
            baseline, trigger = value['graph']['nodes']
            spare = deepcopy(trigger)
            spare['id'] = 'spare'
            spare['executable'].update(decisions=[], preconditions=[])
            spare['executable']['action'].update(id='spare', operation='measure-spare')
            baseline['executable']['preconditions'] = [{'fact': 'run.spare.status', 'value': 'RESERVED'}]
            trigger['executable']['preconditions'] = [{'fact': 'run.repair.completed', 'value': True,
                'on_false': deepcopy(spare['executable']['action'])}]
            value['graph']['nodes'].append(spare)
            manifest = deepcopy(self.manifests['repair'])
            manifest.update(id='spare', outpaths=['outputs/spare.json'])
            manifest['argv'][3] = 'spare'
            manifest['argv'][-1] = manifest['outpaths'][0]
            self.manifests['spare'] = manifest
            value['routes'].append({'candidate': 'spare', 'manifest': manifest})
            self.contract['allowed_commands'].append(manifest['argv'])
        self.initialize(mutate_policy=policy)
        self.assertEqual(self.output('project', 'next')['selected_run'], 'spare')
        self.create('spare')
        reserved = self.snapshot()
        advice = self.output('project', 'next')
        self.assertEqual(advice['selected_run'], 'spare')
        search = next(row['search'] for row in advice['recommendations'] if 'search' in row)
        self.assertEqual([row['action']['id'] for row in search['candidates']], ['spare'])
        self.assertTrue(search['truncation']['truncated'])
        self.assertEqual(search['truncation']['limits']['max_candidates'], 1)
        self.assertNotEqual(self.create('baseline', ok=False).returncode, 0)
        self.assertEqual(self.snapshot()['budget'], reserved['budget'])
        self.assertEqual(self.snapshot()['runs'], reserved['runs'])
        completed = self.output('project', 'advance')
        self.assert_owned_receipt(completed['receipt'], 'spare', 'SUCCEEDED')
        final = self.snapshot()
        self.assertEqual(self.starts(), ['spare'])
        self.assertEqual(len(final['runs']), 1)
        self.assertEqual(final['budget']['cpu_seconds']['charged_estimate'], 1)
        self.assertEqual(final['budget']['cpu_seconds']['reserved'], 0)
        self.assertIsNone(self.output('project', 'advance')['selected_run'])
        self.assertEqual(self.snapshot()['budget'], final['budget'])

    def test_terminal_fallback_cannot_consume_pending_candidate_slot(self):
        def policy(value):
            value['context']['max_candidates'] = 1
            baseline, repair = value['graph']['nodes']
            diagnosis = deepcopy(repair)
            diagnosis['id'] = 'diagnosis'
            diagnosis['executable']['action']['id'] = 'diagnosis'
            diagnosis['executable']['action']['operation'] = 'measure-diagnosis'
            repair['executable']['preconditions'] = [{'fact': 'baseline.score', 'op': 'gte', 'value': 0,
                'on_false': deepcopy(baseline['executable']['action'])}]
            value['graph']['nodes'].append(diagnosis)
            manifest = deepcopy(self.manifests['repair'])
            manifest.update(id='diagnosis', outpaths=['outputs/diagnosis.json'])
            manifest['argv'][3] = 'diagnosis'
            manifest['argv'][-1] = manifest['outpaths'][0]
            value['routes'].append({'candidate': 'diagnosis', 'manifest': manifest})
            self.contract['allowed_commands'].append(manifest['argv'])
        self.initialize(mutate_policy=policy)
        completed = self.output('project', 'advance')
        self.assertEqual(completed['advisor']['selected_run'], 'diagnosis')
        search = next(row['search'] for row in completed['advisor']['recommendations'] if 'search' in row)
        self.assertEqual([row['action']['id'] for row in search['candidates']], ['diagnosis'])
        self.assertTrue(any(row.get('rule_id') == 'repair' and row['status'] == 'BLOCKED_PREREQUISITE'
                            for row in search['blocked_candidates']))
        self.assert_owned_receipt(self.output('project', 'advance')['receipt'], 'diagnosis', 'SUCCEEDED')
        self.assertEqual(self.starts(), ['baseline', 'diagnosis'])

    def test_candidate_limit_does_not_reopen_exhausted_resource_budget(self):
        def policy(value):
            value['context']['max_candidates'] = 1
            self.contract['budget']['cpu_seconds'] = 1
        self.initialize(mutate_policy=policy)
        completed = self.output('project', 'advance')
        self.assertIsNone(completed['advisor']['selected_run'])
        before = self.snapshot()
        self.assertEqual(before['budget']['cpu_seconds']['remaining'], 0)
        self.assertIsNone(self.output('project', 'advance')['selected_run'])
        self.assertNotEqual(self.create('repair', ok=False).returncode, 0)
        self.assertEqual(self.snapshot()['budget'], before['budget'])
        self.assertEqual(self.snapshot()['runs'], before['runs'])
        self.assertEqual(self.starts(), ['baseline'])

    def test_positive_result_closes_declared_goal_without_repair_launch(self):
        self.initialize('positive', mutate_policy=lambda value: value['context'].update(max_candidates=1))
        result = self.output('project', 'advance')
        self.assert_owned_receipt(result['receipt'], 'baseline', 'SUCCEEDED')
        self.assertIsNone(result['advisor']['selected_run'])
        state = self.snapshot()
        for _ in range(2):
            self.call('project', 'advance', ok=False)
            self.output('advise')
        self.assertEqual(self.starts(), ['baseline'])
        self.assertEqual(len(self.snapshot()['receipts']), 1)
        self.assertEqual(self.snapshot()['budget'], state['budget'])

    def test_model_cannot_override_owned_inputs_or_choose_a_different_route(self):
        self.initialize()
        before = self.snapshot()
        context = self.write_json('favourable-context.json', {
            **self.policy['context'], 'facts': {'baseline.score': {'value': 1, 'source': 'invented.json'}}})
        graph = self.write_json('favourable-graph.json', self.policy['graph'])
        artifacts = self.write_json('favourable-artifacts.json', {'schema': 'rds-artifact-manifest-v1', 'sources': []})
        for options in (('--context', context), ('--graph', graph), ('--artifacts', artifacts),
                        ('--choose', 'repair'), ('--record', 'model-picked')):
            with self.subTest(options=options):
                result = self.call('advise', *options, ok=False)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assert_uncharged(before)
        rejected = self.create('repair', ok=False)
        self.assertNotEqual(rejected.returncode, 0, rejected.stdout)
        self.assertNotEqual(self.call('project', 'execute', '--id', 'repair', ok=False).returncode, 0)
        self.assert_uncharged(before)

    def test_manifest_substitution_is_refused_before_reservation(self):
        self.initialize()
        before = self.snapshot()
        changed = deepcopy(self.manifests['baseline'])
        changed['argv'] = self.manifests['repair']['argv']
        rejected = self.create(spec=changed, ok=False)
        self.assertNotEqual(rejected.returncode, 0, rejected.stdout)
        self.assert_uncharged(before)
        original = (self.root / 'code.py').read_bytes()
        (self.root / 'code.py').write_bytes(original + b'\n# Changed after contract lock\n')
        self.assertNotEqual(self.create(ok=False).returncode, 0)
        self.assert_uncharged(before)

    def test_quick_entry_cannot_bypass_owned_source_or_parent_ledger(self):
        self.initialize()
        before = self.snapshot()
        rejected = self.call('exec', '--name', 'unreviewed', '--timeout', '3', '--',
                             *self.manifests['baseline']['argv'], ok=False)
        self.assertNotEqual(rejected.returncode, 0, rejected.stdout)
        self.assertFalse((self.root / '.rds/exec/unreviewed').exists())
        self.assert_uncharged(before)
        other = self.root / 'unowned-source'
        other.mkdir()
        (other / 'probe.py').write_text('raise SystemExit("should not launch")\n', encoding='utf-8')
        context = self.write_json('quick-context.json', self.policy['context'])
        graph = self.write_json('quick-graph.json', self.policy['graph'])
        rejected = self.call('exec', '--name', 'borrowed-ledger', '--timeout', '3',
                             '--ledger', str(self.root), '--context', context, '--graph', graph,
                             '--', sys.executable, '-B', 'probe.py', root=other, ok=False)
        self.assertNotEqual(rejected.returncode, 0, rejected.stdout)
        self.assertFalse((other / '.rds/exec/borrowed-ledger').exists())
        self.assert_uncharged(before)

    def test_invalid_collection_contract_is_rejected_before_ledger_or_budget(self):
        variants = {
            'unimplemented-format': lambda policy: policy['observations'][0].update(format='csv'),
            'unknown-run': lambda policy: policy['observations'][0].update(run_id='unregistered'),
            'unbound-output': lambda policy: policy['observations'][0].update(path='outputs/hidden.json'),
            'invalid-pointer': lambda policy: policy['observations'][0].update(selector={'pointer': 'score'}),
            'model-facts': lambda policy: policy['context'].update(facts={'baseline.score': {'value': 1, 'source': 'fake'}}),
        }
        for label, change in variants.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory(prefix='owned-invalid-') as directory:
                original_root, original_env = self.root, self.env
                self.root = Path(directory)
                self.env = {**os.environ, 'RDS_USAGE_DB': str(self.root / 'usage.sqlite3')}
                try:
                    result = self.initialize(mutate_policy=change, ok=False)
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertFalse((self.root / '.rds/project.sqlite3').exists())
                    self.assertEqual(self.starts(), [])
                finally:
                    self.root, self.env = original_root, original_env

    def test_actions_the_advisor_would_discard_are_rejected_before_the_contract_freezes(self):
        def node(policy, run_id):
            return next(n for n in policy['graph']['nodes'] if n['id'] == run_id)['executable']['action']
        variants = {
            'one-next-decision': (lambda policy: [o.update(next_decision='review the bounded repair')
                                                  for o in node(policy, 'repair')['outcomes']],
                                  'no outcome can distinguish next decisions'),
            'one-explanation': (lambda policy: node(policy, 'baseline').update(competing_explanations=['positive response']),
                                'missing competing explanations or action'),
            'no-observables': (lambda policy: node(policy, 'baseline').update(required_observables=[]),
                               'missing required observables'),
        }
        for label, (change, reason) in variants.items():
            with self.subTest(label=label):
                result = self.initialize(mutate_policy=change, ok=False)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn('would never be admitted by the Advisor: ' + reason, result.stderr)
                self.assertFalse((self.root / '.rds/project.sqlite3').exists())
                self.assertEqual(self.starts(), [])
        # Corrected in the same root, the policy freezes and the Advisor selects a route.
        self.initialize()
        self.assertEqual(self.output('advise')['selected_run'], 'baseline')

    def test_route_protocols_that_cannot_register_are_rejected_before_the_contract_freezes(self):
        variants = {
            'missing-field': (lambda protocol: protocol.pop('seed'), 'Protocol identity fields required: seed'),
            'reserved-field': (lambda protocol: protocol.update(path='protocol.json'), 'Protocol identity uses reserved fields'),
        }
        for label, (change, reason) in variants.items():
            with self.subTest(label=label):
                result = self.initialize(mutate_protocol=change, ok=False)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                # The general binding loop rejects the protocol before the owned-route loop names the route.
                self.assertIn(reason, result.stderr)
                self.assertIn("in protocol.json; the protocol is frozen with the contract", result.stderr)
                self.assertFalse((self.root / '.rds/project.sqlite3').exists())
                self.assertEqual(self.starts(), [])
        # Corrected in the same root, the contract freezes and the first route registers and runs.
        self.initialize()
        self.assert_owned_receipt(self.output('project', 'advance')['receipt'], 'baseline', 'SUCCEEDED')
        self.assertEqual(self.starts(), ['baseline'])

    def test_missing_or_changed_negative_artifact_blocks_dispatch_and_can_be_recovered(self):
        self.initialize(mutate_policy=lambda value: value['context'].update(max_candidates=1))
        self.output('project', 'advance')
        path = self.root / 'outputs/baseline.json'
        original = path.read_bytes()
        before = self.snapshot()
        for mutation in ('delete', 'overwrite'):
            with self.subTest(mutation=mutation):
                if mutation == 'delete':
                    path.unlink()
                else:
                    path.write_text('{"score":1}', encoding='utf-8')
                result = self.call('project', 'advance', ok=False)
                if result.returncode == 0:
                    self.assertIsNone(json.loads(result.stdout).get('selected_run'))
                self.assertEqual(self.starts(), ['baseline'])
                self.assertEqual(self.snapshot()['budget'], before['budget'])
                self.assertEqual(len(self.snapshot()['receipts']), 1)
                path.write_bytes(original)
                self.assertEqual(self.output('advise')['selected_run'], 'repair')
        self.output('project', 'advance')
        self.assertEqual(self.starts(), ['baseline', 'repair'])
        self.assertEqual(len(self.snapshot()['receipts']), 2)

    def test_failure_paths_keep_real_receipts_and_never_invent_measurements(self):
        for mode in ('missing', 'badjson', 'nan', 'nonzero', 'timeout'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix='owned-failure-') as directory:
                original_root, original_env = self.root, self.env
                self.root = Path(directory)
                self.env = {**os.environ, 'RDS_USAGE_DB': str(self.root / 'usage.sqlite3')}
                try:
                    self.initialize(mode, timeout=1 if mode == 'timeout' else 3)
                    self.create()
                    self.execute(ok=False)
                    state = self.snapshot()
                    self.assertEqual(self.starts(), ['baseline'])
                    self.assertEqual(len(state['receipts']), 1)
                    receipt = state['receipts'][0]
                    expected = 'SUCCEEDED' if mode in ('badjson', 'nan') else 'FAILED'
                    self.assertEqual(receipt['run_status'], expected)
                    self.assertEqual(state['budget']['cpu_seconds']['charged_estimate'], 1)
                    self.assertEqual(state['budget']['cpu_seconds']['reserved'], 0)
                    self.assertGreater(state['budget']['wall_seconds']['spent_measured'], 0)
                    review = self.output('advise', status_codes=(2,) if mode in ('badjson', 'nan') else (0,))
                    self.assertIsNone(review['selected_run'])
                    self.assertFalse(review['context']['facts']['baseline.score']['reliable'])
                    self.assertEqual(review['context']['facts']['baseline.score']['kind'], 'UNKNOWN')
                    if mode in ('badjson', 'nan'):
                        self.assertTrue(review['coverage']['errors'])
                    else:
                        self.assertTrue(review['coverage']['gaps'])
                    self.call('project', 'advance', ok=False)
                    self.assertEqual(self.starts(), ['baseline'])
                    self.assertEqual(self.snapshot()['budget'], state['budget'])
                finally:
                    self.root, self.env = original_root, original_env

    def test_unparsed_large_output_is_in_inventory_and_tampering_blocks_dispatch(self):
        self.initialize('large')
        complete = self.output('project', 'advance')
        self.assert_owned_receipt(complete['receipt'], 'baseline', 'SUCCEEDED')
        review = complete['advisor']
        self.assertEqual(review['selected_run'], 'repair')
        self.assertIn({'run_id': 'baseline', 'path': 'outputs/weights.bin'}, review['coverage']['unparsed_outputs'])
        artifact = next(item for item in complete['receipt']['artifacts'] if item['path'] == 'outputs/weights.bin')
        self.assertGreater(artifact['size'], 2 * 1024 * 1024)
        path = self.root / artifact['path']
        original = path.read_bytes()
        self.assertEqual(hashlib.sha256(original).hexdigest(), artifact['sha256'])
        node = next(node for node in current(self.root)['dependency_map']['nodes']
                    if isinstance(node['source'], dict) and node['source'].get('file') == artifact['path'])
        self.assertEqual(node['source']['sha256'], artifact['sha256'])
        before = self.snapshot()
        for mutation in ('delete', 'overwrite'):
            with self.subTest(mutation=mutation):
                if mutation == 'delete':
                    path.unlink()
                else:
                    path.write_bytes(b'changed unparsed output')
                broken = self.output('advise', status_codes=(2,))
                self.assertIsNone(broken['selected_run'])
                self.assertTrue(any(row.get('path') == artifact['path'] for row in broken['coverage']['errors']))
                self.call('project', 'advance', ok=False)
                self.assertEqual(self.starts(), ['baseline'])
                self.assertEqual(self.snapshot()['budget'], before['budget'])
                path.write_bytes(original)
        self.assertEqual(self.output('project', 'next', '--brief')['selected_run'], 'repair')
        self.assertEqual(self.snapshot()['budget'], before['budget'])

    def test_missing_non_observation_output_remains_visible_in_coverage(self):
        self.initialize('missing-extra')
        self.create()
        self.execute(ok=False)
        self.assertEqual(self.starts(), ['baseline'])
        self.assertEqual(self.snapshot()['receipts'][0]['run_status'], 'FAILED')
        review = self.output('advise')
        self.assertIsNone(review['selected_run'])
        self.assertFalse(review['context']['facts']['baseline.score']['reliable'])
        missing = next(row for row in review['coverage']['declared_outputs'] if row['path'] == 'outputs/weights.bin')
        self.assertEqual(missing, {'run_id': 'baseline', 'path': 'outputs/weights.bin', 'status': 'MISSING'})
        self.assertTrue(review['coverage']['gaps'])
        before = self.snapshot()['budget']
        self.call('project', 'advance', ok=False)
        self.assertEqual(self.starts(), ['baseline'])
        self.assertEqual(self.snapshot()['budget'], before)

    def test_failed_process_can_select_diagnosis_without_refuting_scientific_goal(self):
        def diagnose_failure(policy):
            repair = policy['graph']['nodes'][1]['executable']
            repair['preconditions'] = [{'fact': 'run.baseline.failed', 'value': True}]
            repair['action']['description'] = 'Diagnose the recorded process failure'
            repair['action']['target'] = 'run.baseline.failed'

        self.initialize('nonzero', mutate_policy=diagnose_failure)
        self.create()
        self.execute(ok=False)
        review = self.output('advise')
        self.assertEqual(review['selected_run'], 'repair')
        self.assertFalse(review['context']['facts']['baseline.score']['reliable'])
        self.assertEqual(review['context']['facts']['baseline.score']['kind'], 'UNKNOWN')
        goal = next(node for node in current(self.root)['dependency_map']['nodes'] if node['id'] == 'owned:goal:0')
        self.assertEqual(goal['status'], 'UNKNOWN')
        self.assertTrue(review['coverage']['gaps'])
        self.assertEqual(review['coverage']['errors'], [])
        brief_result = self.output('project', 'advance', '--brief')
        self.assertEqual(brief_result['run_id'], 'repair')
        state = self.snapshot()
        self.assertEqual(self.starts(), ['baseline', 'repair'])
        self.assertEqual(len(state['receipts']), 2)
        self.assertEqual(state['budget']['cpu_seconds']['charged_estimate'], 2)
        self.assertEqual(state['budget']['cpu_seconds']['reserved'], 0)
        self.assertIsNone(self.output('advise')['selected_run'])

    def test_real_controller_interruption_recovers_original_attempt_without_relaunch(self):
        self.initialize('timeout', timeout=8)
        self.create()
        command = [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.root),
                   'project', 'execute', '--id', 'baseline']
        controller = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                      encoding='utf-8', env=self.env,
                                      creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        run = None
        try:
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                state = self.snapshot()
                run = state['runs'][0]
                if self.starts() == ['baseline'] and run.get('pid'):
                    break
                if controller.poll() is not None:
                    out, err = controller.communicate()
                    self.fail('Controller ended before interruption: ' + out + err)
                time.sleep(.03)
            self.assertEqual(self.starts(), ['baseline'])
            self.assertIsNotNone(run.get('pid'))
            attempt_id, child_pid = run['attempt_id'], run['pid']
            controller.kill()
            controller.communicate(timeout=5)
            # Windows job teardown may be asynchronous. The only child created
            # by this fixture also has a fixed four-second lifetime on POSIX.
            deadline = time.monotonic() + 6
            while _alive(child_pid) is not False and time.monotonic() < deadline:
                time.sleep(.03)
            self.assertIs(_alive(child_pid), False, 'Owned fixture child death could not be verified')
            result = self.call('project', 'recover', '--id', 'baseline', ok=False)
            recovered = json.loads(result.stdout)['receipt']
            self.assert_owned_receipt(recovered, 'baseline', 'INTERRUPTED')
            self.assertEqual(recovered['attempt_id'], attempt_id)
            self.assertEqual(recovered['assessment'], {'task_gain': 'UNKNOWN', 'mechanism': 'UNKNOWN'})
            before = self.snapshot()
            self.assertEqual(before['budget']['cpu_seconds']['charged_estimate'], 1)
            self.assertEqual(before['budget']['cpu_seconds']['reserved'], 0)
            for _ in range(2):
                self.call('project', 'recover', '--id', 'baseline', ok=False)
                self.call('project', 'advance', ok=False)
            self.assertEqual(self.starts(), ['baseline'])
            self.assertEqual(len(self.snapshot()['receipts']), 1)
            self.assertEqual(self.snapshot()['budget'], before['budget'])
        finally:
            if controller.poll() is None:
                controller.kill()
                controller.communicate(timeout=5)
            deadline = time.monotonic() + 6
            while run and run.get('pid') and _alive(run['pid']) is not False and time.monotonic() < deadline:
                time.sleep(.03)

    def test_completed_recovery_and_repeated_loop_do_not_launch_or_charge_twice(self):
        self.initialize()
        self.output('project', 'advance')
        self.output('project', 'advance')
        before = self.snapshot()
        for _ in range(2):
            self.call('project', 'recover', '--id', 'baseline', ok=False)
            self.call('project', 'execute', '--id', 'baseline', ok=False)
            self.call('project', 'advance', ok=False)
        self.assertEqual(self.starts(), ['baseline', 'repair'])
        self.assertEqual(len(self.snapshot()['receipts']), 2)
        self.assertEqual(self.snapshot()['budget'], before['budget'])
        self.assertEqual(before['budget']['cpu_seconds']['charged_estimate'], 2)
        self.assertEqual(before['budget']['cpu_seconds']['reserved'], 0)

    def test_first_valid_checkpoint_keeps_owned_advice_and_advance_usable(self):
        self.initialize()
        before = self.output('advise')
        self.assertEqual(before['selected_run'], 'baseline')
        saved = self.output('checkpoint', 'save', '--id', 'first-owned')
        self.assertEqual(saved['status'], 'SAVED')
        after = self.output('advise')
        self.assertEqual(after['status'], 'REVIEWED')
        self.assertEqual(after['selected_run'], 'baseline')
        search = next(row['search'] for row in after['recommendations']
                      if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
        self.assertEqual(search['loop_review']['flags'], [])
        completed = self.output('project', 'advance')
        self.assert_owned_receipt(completed['receipt'], 'baseline', 'SUCCEEDED')
        self.assertEqual(self.starts(), ['baseline'])
        self.output('checkpoint', 'save', '--id', 'after-baseline')
        self.assertEqual(self.output('advise')['selected_run'], 'repair')
        repaired = self.output('project', 'advance')
        self.assert_owned_receipt(repaired['receipt'], 'repair', 'SUCCEEDED')
        self.assertEqual(self.starts(), ['baseline', 'repair'])
        self.assertEqual(len(self.snapshot()['receipts']), 2)
        self.assertEqual(self.snapshot()['budget']['cpu_seconds']['charged_estimate'], 2)

    def test_matching_structured_checkpoint_is_reviewed_without_blocking_dispatch(self):
        self.initialize()
        advice = self.output('advise')
        context = advice['context']
        search = next(row['search'] for row in advice['recommendations']
                      if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
        decision = {'question_id': context['decision']['id'],
                    'goal_revision': context['decision']['goal_revision'],
                    'scope': context['decision']['scope'], 'candidate': search['candidates'][0],
                    'outcome': 'deferred', 'evidence': context['facts']}
        path = self.write_json('owned-decision.json', decision)
        self.output('checkpoint', 'save', '--id', 'matching-owned', '--decision', path)
        after = self.output('advise')
        search = next(row['search'] for row in after['recommendations']
                      if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
        self.assertEqual(search['loop_review']['flags'], [])
        self.assertEqual(after['selected_run'], 'baseline')
        self.assert_owned_receipt(self.output('project', 'advance')['receipt'], 'baseline', 'SUCCEEDED')
        self.assertEqual(self.starts(), ['baseline'])

    def assert_owned_checkpoint_rejected(self, corruption, expected_reason):
        from contextlib import closing
        import sqlite3
        import rds_owned_history
        from rds_owned_advisor import review
        self.initialize()
        self.output('advise')
        self.output('checkpoint', 'save', '--id', 'bad-owned')
        before = self.snapshot()
        with closing(sqlite3.connect(self.root / '.rds/project.sqlite3')) as db, db:
            db.execute('DROP TRIGGER checkpoint_no_update')
            if corruption == 'body':
                db.execute("UPDATE checkpoints SET body='{}' WHERE id='bad-owned'")
            else:
                record = json.loads(db.execute("SELECT body FROM checkpoints WHERE id='bad-owned'").fetchone()[0])
                record['contract_sha256'] = '0' * 64
                # A correctly hashed body with a foreign contract is still invalid.
                raw = json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(',', ':'))
                db.execute("UPDATE checkpoints SET body=?,sha=? WHERE id='bad-owned'",
                           (raw, hashlib.sha256(raw.encode('utf-8')).hexdigest()))
        observed = []
        original = rds_owned_history.history_cut

        def observe_history(*args, **kwargs):
            try:
                return original(*args, **kwargs)
            except ValueError as exc:
                observed.append(str(exc))
                raise

        # The owned state check now rejects before Advisor loop review. Observe
        # that original checker without replacing its result or exception.
        with patch.object(rds_owned_history, 'history_cut', new=observe_history):
            with self.assertRaisesRegex(ValueError, 'Repair checkpoint integrity'):
                review(ProjectStore(self.root))
        self.assertEqual(len(observed), 1)
        self.assertIn(expected_reason, observed[0])
        for args in (('advise',), ('project', 'advance')):
            rejected = self.call(*args, ok=False)
            self.assertEqual(rejected.returncode, 1, rejected.stdout + rejected.stderr)
            self.assertIn('Repair checkpoint integrity', rejected.stderr)
            self.assertIn(expected_reason, rejected.stderr)
        self.assert_uncharged(before)
        self.assertEqual(self.snapshot()['receipts'], [])

    def test_tampered_owned_checkpoint_still_rejects_without_launch_or_charge(self):
        self.assert_owned_checkpoint_rejected('body', 'Checkpoint integrity failure')

    def test_nonmatching_owned_checkpoint_still_rejects_without_launch_or_charge(self):
        self.assert_owned_checkpoint_rejected('contract', 'Checkpoint contract mismatch')

    def test_concurrent_advance_cannot_launch_the_same_attempt_twice(self):
        self.initialize('slownegative', mutate_policy=lambda value: value['context'].update(max_candidates=1))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.call('project', 'advance', ok=False), range(2)))
        self.assertTrue(any(result.returncode == 0 for result in results),
                        '\n'.join(result.stdout + result.stderr for result in results))
        self.assertEqual(self.starts(), ['baseline'])
        state = self.snapshot()
        self.assertEqual(len(state['receipts']), 1)
        self.assertEqual(state['budget']['cpu_seconds']['charged_estimate'], 1)
        self.assertEqual(state['budget']['cpu_seconds']['reserved'], 0)
        self.assertEqual(self.output('advise')['selected_run'], 'repair')

    def test_late_duplicate_registration_cannot_block_identical_reserved_map_execution(self):
        self.initialize('slownegative', mutate_policy=lambda value: value['context'].update(max_candidates=1))
        gates = self.root / 'gates'
        gates.mkdir()
        wrapper = gates / 'advance.py'
        # Only control timing at real method boundaries. Original save, locked
        # callback, register/execute/admission and the public CLI remain in use.
        wrapper.write_text(r'''
import json, pathlib, sys, time
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')
source, role, root = pathlib.Path(sys.argv[1]).resolve(), sys.argv[2], pathlib.Path(sys.argv[3]).resolve()
gates = root / 'gates'
sys.path.insert(0, str(source / 'scripts'))
import rds_cli, rds_project, rds_tms_store
phase = False
def wait(name):
    deadline = time.monotonic() + 20
    while not (gates / name).exists():
        if time.monotonic() >= deadline:
            raise RuntimeError('Synthetic gate timeout: ' + name)
        time.sleep(.01)
original_register = rds_project.ProjectStore.register
def register(self, *args, **kwargs):
    if role == 'B' and self.root == root:
        (gates / 'b-waiting').touch()
        wait('a-waiting')
    return original_register(self, *args, **kwargs)
rds_project.ProjectStore.register = register
original_execute = rds_project.ProjectStore.execute
def execute(self, *args, **kwargs):
    global phase
    phase = self.root == root
    return original_execute(self, *args, **kwargs)
rds_project.ProjectStore.execute = execute
original_save = rds_tms_store.save
def save(target, spec, **kwargs):
    global phase
    info = {'expected': kwargs['expected'], 'map': rds_project.digest(spec)}
    if role == 'A' and phase and pathlib.Path(target) == root:
        phase = False
        (gates / 'a-waiting').touch()
        wait('b-ended')
        actual = rds_tms_store.current(root)
        info.update(current=actual['sha256'], current_map=rds_project.digest(actual['dependency_map']))
        (gates / 'a.json').write_text(json.dumps(info), encoding='utf-8')
    result = original_save(target, spec, **kwargs)
    if role == 'B' and pathlib.Path(target) == root and (gates / 'a-waiting').exists():
        (gates / 'b.json').write_text(json.dumps({**info, 'published': result}), encoding='utf-8')
    return result
rds_tms_store.save = save
sys.argv = [str(source / 'scripts/rds_cli.py'), '--root', str(root), 'project', 'advance']
try:
    sys.exit(rds_cli.main())
finally:
    if role == 'B':
        (gates / 'b-ended').touch()
''', encoding='utf-8')
        children = []

        def launch(role):
            child = subprocess.Popen([sys.executable, '-B', str(wrapper), str(ROOT), role, str(self.root)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding='utf-8', env=self.env)
            children.append(child)
            return child

        def wait(name, child):
            deadline = time.monotonic() + 20
            while not (gates / name).exists():
                self.assertIsNone(child.poll(), 'CLI exited before gate ' + name)
                self.assertLess(time.monotonic(), deadline, 'Synthetic gate timeout: ' + name)
                time.sleep(.01)

        try:
            duplicate = launch('B')
            wait('b-waiting', duplicate)
            executor = launch('A')
            wait('a-waiting', executor)
            duplicate_out, duplicate_err = duplicate.communicate(timeout=30)
            execution_out, execution_err = executor.communicate(timeout=30)
            self.assertEqual(duplicate.returncode, 1, duplicate_out + duplicate_err)
            self.assertIn('Run ID already exists', duplicate_err)
            self.assertEqual(executor.returncode, 0, execution_out + execution_err)
            self.assert_owned_receipt(json.loads(execution_out)['receipt'], 'baseline', 'SUCCEEDED')
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)
        a = json.loads((gates / 'a.json').read_text(encoding='utf-8'))
        b = json.loads((gates / 'b.json').read_text(encoding='utf-8'))
        self.assertEqual(a['expected'], b['expected'])
        self.assertNotEqual(a['expected'], a['current'])
        self.assertEqual(a['current'], b['published'])
        self.assertEqual(a['map'], a['current_map'])
        self.assertEqual(a['map'], b['map'])
        self.assertEqual(self.starts(), ['baseline'])
        state = self.snapshot()
        self.assertEqual(len(state['runs']), 1)
        self.assertIsNotNone(state['runs'][0]['attempt_id'])
        self.assertEqual(len(state['receipts']), 1)
        self.assertEqual(state['budget']['cpu_seconds']['charged_estimate'], 1)
        self.assertEqual(state['budget']['cpu_seconds']['reserved'], 0)
        self.assertEqual(state['budget']['wall_seconds']['reserved'], 0)
        self.assertEqual(self.output('advise')['selected_run'], 'repair')
        print(json.dumps({'case': 'identical-reserved-map-publication',
                          'children': [{'role': 'B', 'returncode': duplicate.returncode,
                                        'stdout': duplicate_out, 'stderr': duplicate_err},
                                       {'role': 'A', 'returncode': executor.returncode,
                                        'stdout': execution_out, 'stderr': execution_err}],
                          'trace': {'A': a, 'B': b}, 'launches': self.starts(),
                          'attempt_id': state['runs'][0]['attempt_id'],
                          'receipt_count': len(state['receipts']), 'budget': state['budget']}))

    def test_new_dependency_revision_invalidates_admission_before_actual_launch(self):
        import rds_owned_advisor as owned
        self.initialize()
        self.create()
        before = self.snapshot()
        original_prepare = owned.prepare_admission
        changed = []

        def prepare_then_change_map(store, spec):
            token = original_prepare(store, spec)
            if not changed:
                previous = current(self.root)['sha256']
                self.call('hypergraph', '--declare', json.dumps({'claims': {
                    'new-proposal': {'status': 'UNKNOWN', 'source': 'synthetic-concurrent-declaration'}}}), '--json')
                self.assertNotEqual(current(self.root)['sha256'], previous)
                changed.append(True)
            return token

        # Only inject the timing of a real map change; actual execute/guard/Popen
        # implementations remain in use. The gate is never copied into a helper.
        with patch.object(owned, 'prepare_admission', side_effect=prepare_then_change_map):
            with self.assertRaisesRegex(ValueError, 'changed|snapshot|revision|stale'):
                ProjectStore(self.root).execute('baseline')
        self.assertEqual(self.starts(), [])
        self.assertEqual(self.snapshot()['budget'], before['budget'])
        self.assertEqual(self.snapshot()['receipts'], [])
        completed = self.execute()
        self.assert_owned_receipt(completed['receipt'], 'baseline', 'SUCCEEDED')
        self.assertEqual(self.starts(), ['baseline'])

    def test_collection_failure_after_commit_recovers_without_rerunning_experiment(self):
        import rds_owned_advisor as owned
        self.initialize()
        self.create()
        store = ProjectStore(self.root)
        with patch.object(owned, 'after_finish', side_effect=OSError('synthetic interruption after receipt commit')):
            receipt = store.execute('baseline')
        self.assert_owned_receipt(receipt, 'baseline', 'SUCCEEDED')
        self.assertEqual(self.starts(), ['baseline'])
        before = self.snapshot()
        self.assertEqual(store.last_advisor_review['status'], 'COLLECTION_FAILED')
        recovered = self.output('advise')
        self.assertEqual(recovered['selected_run'], 'repair')
        self.assertEqual(recovered['context']['facts']['baseline.score']['value'], -1)
        self.assertEqual(len(self.snapshot()['receipts']), 1)
        self.assertEqual(self.snapshot()['budget'], before['budget'])
        self.assertEqual(self.starts(), ['baseline'])

    def test_admission_rechecks_cas_bytes_even_when_snapshot_row_is_unchanged(self):
        import rds_owned_advisor as owned
        self.initialize()
        self.create()
        before = self.snapshot()
        original_prepare = owned.prepare_admission
        changed = []

        def prepare_then_damage_cas(store, spec):
            token = original_prepare(store, spec)
            if not changed:
                saved = current(self.root)
                path = self.root / saved['map']['path']
                raw = path.read_bytes()
                changed.append((path, raw, saved['sha256']))
                # Keep the stored snapshot row untouched. Even valid JSON with
                # different bytes must lose its hash-bound admission authority.
                path.write_bytes(raw + b'\n')
            return token

        try:
            with patch.object(owned, 'prepare_admission', side_effect=prepare_then_damage_cas):
                with self.assertRaises(ValueError) as refused:
                    ProjectStore(self.root).execute('baseline')
            self.assertTrue(any(word in str(refused.exception).lower()
                                for word in ('hash', 'digest', 'size', 'integrity')), str(refused.exception))
            self.assertEqual(len(changed), 1)
            with ProjectStore(self.root)._db(True) as db:
                row = db.execute('SELECT sha256 FROM dependency_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()
            self.assertEqual(row['sha256'], changed[0][2])
            self.assertEqual(self.starts(), [])
            self.assertEqual(self.snapshot()['budget'], before['budget'])
            self.assertEqual(self.snapshot()['receipts'], [])
        finally:
            for path, raw, _ in changed:
                path.write_bytes(raw)
        completed = self.execute()
        self.assert_owned_receipt(completed['receipt'], 'baseline', 'SUCCEEDED')
        self.assertEqual(self.starts(), ['baseline'])

    def test_publication_race_rechecks_ledger_inside_save_and_preserves_new_graph(self):
        import rds_owned_advisor as owned
        import rds_tms_store as tms
        self.initialize()
        first = self.output('advise')
        original_save = tms.save
        concurrent = []

        def publish_after_real_execution(root, spec, **kwargs):
            if not concurrent:
                # This wrapper changes only timing. A separate, real CLI owns
                # registration, launch, receipt settlement and the newer map.
                complete = self.output('project', 'advance')
                self.assert_owned_receipt(complete['receipt'], 'baseline', 'SUCCEEDED')
                saved, state = current(self.root), self.snapshot()
                self.assertNotEqual(saved['sha256'], first['snapshot_sha256'])
                concurrent.append((saved, state))
            return original_save(root, spec, **kwargs)

        with patch.object(tms, 'save', side_effect=publish_after_real_execution):
            # A snapshot-only conflict is insufficient: this exact reason
            # proves the live ledger was checked by save's locked callback.
            with self.assertRaisesRegex(ValueError, 'Owned state changed during evidence collection'):
                owned.review(ProjectStore(self.root))
        self.assertEqual(len(concurrent), 1)
        saved, state = concurrent[0]
        self.assertEqual(current(self.root)['sha256'], saved['sha256'])
        self.assertEqual(current(self.root)['dependency_map'], saved['dependency_map'])
        self.assertEqual(self.snapshot()['budget'], state['budget'])
        self.assertEqual(self.snapshot()['receipts'], state['receipts'])
        self.assertEqual(self.starts(), ['baseline'])
        self.assertEqual(len(state['receipts']), 1)
        self.assertEqual(state['budget']['cpu_seconds']['charged_estimate'], 1)
        self.assertEqual(self.output('advise')['selected_run'], 'repair')

    def test_program_evidence_cannot_be_retracted_through_generic_tms(self):
        self.initialize()
        self.output('project', 'advance')
        before = current(self.root)
        nodes = before['dependency_map']['nodes']
        self.assertIn('owned:fact:baseline.score', [node['id'] for node in nodes])
        replacement = self.write_json('hide-negative-map.json', {'claims': {'favourable': {
            'status': 'SUPPORTED', 'source': 'model-declaration'}}, 'goal': 'favourable'})
        for options in (('--retract-node', 'owned:fact:baseline.score', '--change-source', 'hide-negative'),
                        ('--input', replacement)):
            result = self.call('hypergraph', *options, '--json', ok=False)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual(current(self.root)['sha256'], before['sha256'])
        self.assertEqual(self.output('advise')['selected_run'], 'repair')
        self.assertEqual(self.starts(), ['baseline'])


if __name__ == '__main__':
    unittest.main()
