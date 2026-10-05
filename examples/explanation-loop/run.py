"""One public real-Agent failure -> rival predictions -> probe -> route trace.

Run only with explicit native provider selection. No scripted model answer,
provider retry, automatic model campaign, or second scientific ledger exists.
"""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_project import ProjectStore, canonical, digest, file_sha, require
from rds_tms_store import maintain
import rds_structure as structure
from candidate import calculate
from worker import review_trace


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(value), encoding='utf-8')


def build(root, native):
    root = Path(root).resolve()
    require(not root.exists(), 'Use a new empty sibling workspace; never replace attempts')
    require(not root.is_relative_to(REPO), 'Workspace must be outside this checkout')
    root.mkdir(parents=True)
    (root / 'out').mkdir()
    for name in ('worker.py', 'candidate.py', 'evaluator.py'):
        shutil.copyfile(Path(__file__).parent / name, root / name)
    shutil.copyfile(REPO / 'scripts/rds_autonomy_worker.py', root / 'adapter.py')
    provider = {'kind': 'codex_exec', 'argv': [str(Path(native).resolve())],
                'executable_sha256': file_sha(native), 'model': 'gpt-6.1-sol', 'effort': 'high',
                'service_tier': 'default', 'timeout_seconds': 180}
    write(root / 'provider.json', provider)
    # Future outcomes are absent from the supplied packet. Oracle remains frozen.
    write(root / 'data.json', {'inputs': list(range(8)), 'observations': [[0, 0], [1, 1], [2, 0], [3, 1]], 'probe_x': 5})
    bindings = [{'path': p, 'role': role, 'sha256': file_sha(root / p)} for p, role in
                [('worker.py', 'code'), ('candidate.py', 'code'), ('adapter.py', 'code'),
                 ('evaluator.py', 'evaluator'), ('provider.json', 'config'), ('data.json', 'data')]]
    commands = [[sys.executable, '-B', 'worker.py']]
    for ident in ('baseline', 'candidate'):
        commands += [[sys.executable, '-B', 'candidate.py', ident, f'out/{ident}.json'],
                     [sys.executable, '-B', 'evaluator.py', ident, f'out/{ident}.json', f'out/{ident}-verdict.json']]
    contract = {'schema': 1, 'description': 'Predict all eight public finite inputs, independently checked; no scientific efficacy claim',
                'bindings': bindings, 'allowed_commands': commands, 'output_roots': ['out'], 'budget': {'wall_seconds': 600}}
    protocol = {role + '_sha256': ProjectStore._role_sha(contract, role) for role in ('code', 'config', 'data')}
    protocol.update(data_split='public-finite-development', init='fresh', seed=0, checkpoint='none',
                    schedule='one failure, one real model call, one precommitted probe', sample_work={'rows': 8},
                    numeric_protocol='bounded exact integer arithmetic')
    write(root / 'protocol.json', protocol)
    contract['bindings'].append({'path': 'protocol.json', 'role': 'protocol', 'sha256': file_sha(root / 'protocol.json')})
    store = ProjectStore(root)
    store.initialize(contract)
    initial = {'schema': 1, 'nodes': [{'id': n, 'status': 'SUPPORTED' if n == 'observations' else 'UNKNOWN',
                'label': n, 'source': 'public observed measurements'} for n in ('observations', 'linear-model', 'goal')],
               'hyperedges': [{'id': 'linear-route', 'premises': ['observations', 'linear-model'],
                              'conclusion': 'goal', 'status': 'PROPOSED', 'source': 'initial linear assumption'}], 'goals': ['goal']}
    maintain(root, initial=initial)
    return root, store, contract


def manifest(root, ident, argv, outputs, cap=5):
    return {'schema': 1, 'id': ident, 'arm': 'tool', 'argv': argv, 'outpaths': outputs,
            'protocol': {'path': 'protocol.json', 'sha256': file_sha(root / 'protocol.json')},
            'resource_estimates': {'wall_seconds': cap}, 'timeout_seconds': cap}


def proposal(root, task, ident, contract, hypothesis, explanation, rival, positive, negative, measurement, a, b, trigger=None):
    node = {'id': ident + '-concept', 'kind': 'model', 'label': explanation,
            'source': {'locator': 'real post-failure model reply' if trigger else 'declared initial assumption'}}
    result = {'id': ident, 'request_id': task['id'], 'gap_id': task['gap_id'], 'action_kind': 'knowledge_expansion',
              'new_nodes': [node], 'relations': [], 'assumptions': ['Frozen finite data and independent acceptance remain unchanged'],
              'prediction': {'observable': node['id'], 'if_proposal': explanation, 'if_rival': rival},
              'test': {'protocol': 'Frozen finite integer inputs; commit before independent observation',
                       'measurement': measurement['name'], 'stop_condition': 'One candidate and one independent verifier'},
              'discriminator': {'schema': 1, 'hypothesis_id': hypothesis,
                                'conditions': [{k: v[k] for k in ('path', 'sha256')} for v in contract['bindings'] if v['role'] in ('config', 'data')],
                                'measurement': measurement, 'proposal': a, 'rival': b},
              'next_if_positive': positive, 'next_if_negative': negative,
              'exploration': {'limitation': 'Linear outputs conflict with observed measurements', 'change': explanation,
                              'rationale': 'Competing predicted values are fixed before the independent probe',
                              'sources': [{'kind': 'local_observation', 'source': 'original failure receipts and measurements'},
                                          {'kind': 'agent_interpretation', 'source': node['source']}],
                              'unknown_premises': ['Whether a proposed law applies beyond measured points'],
                              'search_directions': ['Compare the generated rival under frozen conditions'], 'cost': {'wall_seconds': 10}},
              'topology': {'retire_hyperedges': [], 'hyperedges': [{'id': ident + '-route',
                           'premises': ['observations', node['id']], 'conclusion': 'goal', 'source': node['source']}]},
              'experiment': {'candidate_output': f'out/{ident}.json', 'verdict_output': f'out/{ident}-verdict.json',
                             'runs': [manifest(root, ident, [sys.executable, '-B', 'candidate.py', ident, f'out/{ident}.json'], [f'out/{ident}.json']),
                                      manifest(root, ident + '-verifier', [sys.executable, '-B', 'evaluator.py', ident, f'out/{ident}.json', f'out/{ident}-verdict.json'], [f'out/{ident}-verdict.json'])]}}
    if trigger:
        result['trigger'] = trigger
    return result


def run(root, native, resume=False):
    started = time.monotonic()
    if resume:
        root = Path(root).resolve()
        store = ProjectStore(root)
        contract = store.snapshot()['contract']
        provider = json.loads((root / 'provider.json').read_text())
        require(provider['argv'] == [str(Path(native).resolve())] and provider['executable_sha256'] == file_sha(native),
                'Resume provider differs; never redispatch')
        history = json.loads((root / 'out/trajectory.json').read_text())
    else:
        root, store, contract = build(root, native)
        history = []
    def call(name, function, *args):
        value = function(*args)
        history.append({'operation': name, 'result': value, 'observed_at': time.time()})
        write(root / 'out/trajectory.json', history)
        return value
    retained_baseline = structure._find(store, 'PROPOSAL', 'baseline')
    task = (structure._find(store, 'REQUEST', retained_baseline['request_id']) if retained_baseline
            else call('initial_request', structure.request, root)['tasks'][0])
    baseline = proposal(root, task, 'baseline', contract, 'linear-x', 'Outputs follow x', 'Some observed outputs depart from x',
                        'Try independently verified extrapolation', 'Reconsider the linear premise and generate competing laws',
                        {'name': 'training-squared-error', 'path': 'out/baseline-verdict.json', 'pointer': '/training_squared_error'},
                        {'op': 'eq', 'value': 0}, {'op': 'gt', 'value': 0})
    write(root / 'out/baseline-proposal.json', baseline)
    call('retain_baseline', structure.propose, root, baseline)
    failed = call('failed_experiment', structure.advance, root, 'baseline')
    require(failed['observation'] == 'REFUTE', 'Initial failure must settle before model generation')
    task = call('failure_handoff', structure.request, root)['tasks'][0]
    outputs = ['out/model' + suffix for suffix in ('.json', '.trace.jsonl', '.stderr.bin', '.schema.json', '.provider.json', '.prompt.txt')]
    retained = next((r for r in store.snapshot()['receipts'] if r['run_id'] == 'agent'), None)
    if retained is None:
        require(not resume, 'Unsettled delivery: recover original attempt outside this harness; never redispatch')
        store.register(manifest(root, 'agent', [sys.executable, '-B', 'worker.py'], outputs, 190))
        model_receipt = call('real_agent', store.execute, 'agent')
    else:
        model_receipt = retained
    report = {'scope': 'ONE_REAL_AGENT_PUBLIC_ENGINEERING_TRAJECTORY', 'model_matched_comparison': 'NOT_RUN',
              'scientific_benefit': 'UNMEASURED', 'scientific_support': 'UNKNOWN',
              'status': 'UNKNOWN', 'human_takeovers': [], 'scripted_initial_failures': 1,
              'future_hypotheses_scripted': False, 'trace': 'out/trajectory.json', 'budget': store.snapshot()['budget'],
              'provider': json.loads((root / 'provider.json').read_text()),
              'limitations': ['One finite public task; no causal or generalization claim',
                             'Prompt-only visibility checked by trace; no sealed benchmark',
                             'Token usage is provider-reported, currency/CPU/GPU costs UNKNOWN',
                             'Controller setup and outer orchestration wall time reported separately']}
    if model_receipt['run_status'] == 'SUCCEEDED' or resume:
        envelope, model_sha = structure._artifact(store, model_receipt, 'out/model.json')
        requested = next(e for e in structure._events(store) if e.get('kind') == 'STRUCTURE_REQUEST' and e['sha256'] == envelope['request_sha256'])
        task = structure._read_ref(store, requested['record'])
        reply = envelope['reply']
        require(reply['evidence_id'] == failed['id'] and reply['evidence_sha256'] == digest(failed), 'Model must bind the exact original failure')
        # Review the paid trace original, rather than trusting adapter labels.
        trace_ref = next(a for a in model_receipt['artifacts'] if a.get('kind') == 'project_output' and a['path'] == 'out/model.trace.jsonl')
        raw = (root / trace_ref['path']).read_bytes()
        require(len(raw) <= 8 * 1024 * 1024 and hashlib.sha256(raw).hexdigest() == trace_ref['sha256'], 'Original model trace changed')
        trace = [json.loads(line) for line in raw.decode('utf-8').splitlines()]
        tools, _, completed = review_trace(trace)
        require(not tools and completed, 'Model visibility/completion violation')
        if model_receipt['run_status'] != 'SUCCEEDED':
            require(envelope['tool_items'] and all(t == 'error' for t in envelope['tool_items']), 'Failed worker cannot be silently promoted')
            report['human_takeovers'].append({'reason': 'Adapter classified configuration warnings as tools',
                'action': 'Author corrected trace classification; replayed original paid response as data',
                'original_worker_status': model_receipt['run_status'], 'model_redispatched': False})
        expression, rival = json.loads(reply['expression_json']), json.loads(reply['rival_expression_json'])
        require(calculate(expression, 5) == reply['candidate_probe'] and calculate(rival, 5) == reply['rival_probe'], 'Declared probe must match executable generated law')
        trigger = {'proposal_id': failed['id'], 'feedback_sha256': digest(failed), 'observation': 'REFUTE', 'purpose': 'ALTERNATIVE'}
        p = proposal(root, task, 'candidate', contract, reply['hypothesis_id'], reply['explanation'], reply['rival_explanation'],
                     reply['next_if_supported'], reply['next_if_refuted'],
                     {'name': 'independent-probe-target-at-x5', 'path': 'out/candidate-verdict.json', 'pointer': '/probe_target'},
                     {'op': 'eq', 'value': reply['candidate_probe']}, {'op': 'eq', 'value': reply['rival_probe']}, trigger)
        p['exploration']['sources'].append({'kind': 'agent_interpretation', 'source': {'path': 'out/model.json', 'sha256': model_sha}})
        write(root / 'out/candidate-proposal.json', p)
        call('post_failure_proposal', structure.propose, root, p)
        decision = call('evidence_changed_route', structure.next_step, root)
        require(decision['selected'] == 'candidate', 'New evidence-dependent route must be selected')
        observed = call('precommitted_independent_probe', structure.advance, root, 'candidate')
        final = call('consume_probe', structure.next_step, root)
        report.update(status='COMPLETED', hypothesis_observation=observed['observation'], goal_status=observed['goal_status'],
                      final_decision=final['status'], model_usage=envelope['usage'], model_output_sha256=model_sha)
    report.update(budget=store.snapshot()['budget'], attempts=len(store.snapshot()['receipts']),
                  current_invocation_outer_wall_seconds=time.monotonic() - started,
                  overall_outer_wall_seconds='UNKNOWN' if resume else time.monotonic() - started)
    write(root / 'out/results.json', report)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--codex', required=True, help='Exact native executable; one explicit model call')
    parser.add_argument('--resume', action='store_true', help='Consume settled originals only; never retry a model call')
    args = parser.parse_args()
    print(json.dumps(run(args.workspace, args.codex, args.resume), indent=2))
