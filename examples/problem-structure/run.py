"""Real CLI fixture trajectories and equal-cap three-arm wiring comparison.

No model is invoked. Strategies/proposals are scripted replay inputs. The
grader is a frozen separate program; success is scoped to finite public data.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_project import ProjectStore, digest, file_sha
from rds_tms_store import maintain


def prepare(root, case='knowledge', budget=100):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    for name in ('candidate.py', 'evaluator.py'):
        shutil.copyfile(Path(__file__).parent / name, root / name)
    inputs = {'knowledge': [[0, 0], [0, 1], [1, 0], [1, 1]],
              'decomposition': [[3, 1], [5, 1], [7, 3]],
              'negative': [[0], [1], [2], [3]]}[case]
    (root / 'data.json').write_text(json.dumps({'case': case, 'inputs': inputs}), encoding='utf-8')
    (root / 'config.json').write_text('{}', encoding='utf-8')
    protocol = {'code_sha256': file_sha(root / 'candidate.py'), 'config_sha256': file_sha(root / 'config.json'),
                'data_sha256': file_sha(root / 'data.json'), 'data_split': 'public-synthetic-development',
                'init': 'none', 'seed': 0, 'checkpoint': 'none', 'schedule': 'finite exact calculation',
                'sample_work': {'rows': len(inputs)}, 'numeric_protocol': 'finite integer fixtures'}
    (root / 'protocol.json').write_text(json.dumps(protocol), encoding='utf-8')
    commands = []
    for ident in ('baseline', 'candidate', 'rival'):
        for strategy in ('additive', 'interaction', 'simultaneous', 'periodic', 'linear', 'timeout'):
            commands.append([sys.executable, '-B', 'candidate.py', strategy, f'out/{ident}.json'])
        for mode in ('ok', 'wrong_scope', 'unknown'):
            commands.append([sys.executable, '-B', 'evaluator.py', ident, ident, f'out/{ident}.json', f'out/{ident}-verdict.json', mode])
    contract = {'schema': 1, 'description': 'Independently predict all declared finite cases; preserve this acceptance',
                'bindings': [{'path': p, 'role': role, 'sha256': file_sha(root / p)} for p, role in
                             [('candidate.py', 'code'), ('config.json', 'config'), ('data.json', 'data'),
                              ('evaluator.py', 'evaluator'), ('protocol.json', 'protocol')]],
                'allowed_commands': commands, 'output_roots': ['out'], 'budget': {'wall_seconds': budget}}
    ProjectStore(root).initialize(contract)
    initial = {'schema': 1, 'nodes': [{'id': n, 'status': 'SUPPORTED' if n == 'observations' else 'UNKNOWN',
                'source': 'synthetic initial problem', 'label': n} for n in ('observations', 'old-model', 'goal')],
               'hyperedges': [{'id': 'old-decomposition', 'premises': ['observations', 'old-model'],
                              'conclusion': 'goal', 'status': 'SUPPORTED', 'source': 'initial assumed decomposition'}], 'goals': ['goal']}
    maintain(root, initial=initial)
    return root


def manifest(root, ident, strategy='interaction', verifier=False, mode='ok', timeout=2):
    if verifier:
        argv = [sys.executable, '-B', 'evaluator.py', ident, ident, f'out/{ident}.json', f'out/{ident}-verdict.json', mode]
        rid, output = ident + '-verifier', f'out/{ident}-verdict.json'
    else:
        argv = [sys.executable, '-B', 'candidate.py', strategy, f'out/{ident}.json']
        rid, output = ident, f'out/{ident}.json'
    return {'schema': 1, 'id': rid, 'arm': 'tool', 'protocol': {'path': 'protocol.json', 'sha256': file_sha(Path(root) / 'protocol.json')},
            'argv': argv, 'outpaths': [output], 'resource_estimates': {'wall_seconds': timeout}, 'timeout_seconds': timeout}


def proposal(root, task, ident='candidate', strategy='interaction', action='knowledge_expansion', mode='ok', timeout=2):
    node = {'id': ident + '-concept', 'kind': 'concept', 'label': 'Alternative representation from external fixture source',
            'source': {'locator': 'public synthetic source; prescribed fixture concept'}}
    return {'id': ident, 'request_id': task['id'], 'gap_id': task['gap_id'], 'action_kind': action,
            'new_nodes': [node], 'relations': [], 'assumptions': ['Finite original measurements and unchanged independent acceptance'],
            'prediction': {'observable': node['id'], 'if_proposal': 'All finite oracle outputs match', 'if_rival': 'At least one mismatch persists'},
            'test': {'protocol': 'Frozen exact finite inputs', 'measurement': 'Independent output equality', 'stop_condition': 'One candidate and one verifier; stop on timeout'},
            'next_if_positive': 'Retain and test the alternative representation', 'next_if_negative': 'Reject this representation and test a distinct alternative',
            'exploration': {'limitation': 'The initial decomposition does not provide an independently checked solution',
                            'change': 'Introduce ' + strategy + ' and revise the dependency representation',
                            'rationale': 'The alternative computation makes a different checkable prediction',
                            'sources': [{'kind': 'literature_claim', 'source': node['source']},
                                        {'kind': 'agent_interpretation', 'source': 'scripted fixture proposal; no real LLM'}],
                            'unknown_premises': ['Whether this representation fits original observations'],
                            'search_directions': ['Look for current primary descriptions of the alternative representation'],
                            'cost': {'wall_seconds': 2 * timeout}},
            'topology': {'retire_hyperedges': ['old-decomposition'] if action == 'structural_reconstruction' else [],
                         'hyperedges': [{'id': ident + '-alternative', 'premises': ['observations', node['id']],
                                         'conclusion': 'goal', 'source': node['source']}]},
            'experiment': {'runs': [manifest(root, ident, strategy, timeout=timeout), manifest(root, ident, verifier=True, mode=mode, timeout=timeout)],
                           'candidate_output': f'out/{ident}.json', 'verdict_output': f'out/{ident}-verdict.json'}}


def cli(root, *args):
    start = time.monotonic()
    completed = subprocess.run([sys.executable, '-B', str(REPO / 'scripts/rds_cli.py'), '--root', str(root), *args],
                               capture_output=True, text=True, encoding='utf-8', timeout=30)
    entry = {'argv': list(args), 'exit_code': completed.returncode, 'wall_seconds': time.monotonic() - start,
             'stdout': completed.stdout, 'stderr': completed.stderr}
    with (root / 'cli-trace.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(entry) + '\n')
    if completed.returncode:
        raise RuntimeError(entry)
    return json.loads(completed.stdout)


def run(workspace):
    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=False)
    results = []
    start = time.monotonic()
    for case, strategies in [('knowledge', ['interaction']), ('decomposition', ['simultaneous']), ('negative', ['linear', 'periodic'])]:
        for arm in ('no_graph', 'edges_only', 'expand_restructure'):
            root = prepare(workspace / (case + '-' + arm), case)
            t0 = time.monotonic()
            if arm != 'expand_restructure':
                store = ProjectStore(root)
                if arm == 'edges_only':
                    maintain(root, updates=[{'hyperedges': [{'id': 'extra-bridge', 'premises': ['observations'],
                            'conclusion': 'old-model', 'status': 'PROPOSED', 'source': 'scripted path repair; unchanged model'}]}])
                for spec in (manifest(root, 'baseline', 'additive'), manifest(root, 'baseline', verifier=True)):
                    store.register(spec)
                    store.execute(spec['id'])
                verdict = json.loads((root / 'out/baseline-verdict.json').read_text())
                feedbacks = [verdict]
            else:
                task = cli(root, 'structure', 'request')['tasks'][0]
                feedbacks = []
                for ident, strategy in zip(('candidate', 'rival'), strategies):
                    p = proposal(root, task, ident, strategy, 'knowledge_expansion' if case == 'knowledge' else 'structural_reconstruction')
                    path = root / (ident + '-proposal.json')
                    path.write_text(json.dumps(p), encoding='utf-8')
                    cli(root, 'structure', 'propose', '--proposal', str(path))
                    cli(root, 'structure', 'next')
                    feedbacks.append(cli(root, 'structure', 'advance', '--id', ident))
                cli(root, 'structure', 'next')  # Explicit decision consumption of feedback.
                cli(root, 'structure', 'activate', '--id', ident)
                cli(root, 'structure', 'rollback', '--id', ident)
            state = ProjectStore(root).snapshot()
            results.append({'case': case, 'arm': arm, 'goal_status': feedbacks[-1]['goal_status'],
                            'feedback': feedbacks, 'seconds_to_exit_dead_end': time.monotonic() - t0 if feedbacks[-1]['goal_status'] == 'PASS' else None,
                            'total_wall_seconds': time.monotonic() - t0, 'budget': state['budget'], 'attempts': len(state['receipts']),
                            'human_interventions': 0, 'scripted_interventions': len(strategies) if arm == 'expand_restructure' else 1})
    report = {'schema': 1, 'model': None, 'tools': 'same frozen Python candidate/evaluator and RDS kernel', 'budget_cap_per_arm': 100,
              'scope': 'SCRIPTED_PROTOCOL_FIXTURE_ONLY', 'scientific_benefit': 'UNMEASURED',
              'model_matched_comparison': 'NOT_RUN', 'limitations': ['No LLM discovery or equal consumed-token experiment',
              'Arms use prescribed policies; edges_only adds a proposed edge then uses the same additive baseline as no_graph',
              'Controller, search, failed runs and verifier costs remain in original ledger; outer CLI overhead is separately timed'],
              'total_wall_seconds': time.monotonic() - start, 'results': results}
    (workspace / 'results.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--workspace', required=True)
    args = parser.parse_args()
    result = run(args.workspace)
    print(json.dumps({'scope': result['scope'], 'trials': len(result['results']), 'results':
                     [{'case': r['case'], 'arm': r['arm'], 'goal_status': r['goal_status']} for r in result['results']]}, indent=2))
