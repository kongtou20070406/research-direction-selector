"""One ledger: generated jump -> AI method -> exact feedback -> continuation.

Default provider is an explicitly scripted engineering fixture. Native Codex
requires --native pointing to the installed executable; at most two calls.
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_project import ProjectStore, canonical, file_sha, require
from rds_autonomy_worker import output_paths


def write(root, name, value):
    (root / name).write_text(value if isinstance(value, str) else canonical(value), encoding='utf-8')


def build(root, native=None):
    root = Path(root).resolve()
    require(not root.exists() and not root.is_relative_to(REPO), 'Use a new absolute sibling workspace')
    root.mkdir(parents=True)
    (root / 'out').mkdir()
    for name in ('candidate.py', 'evaluator.py', 'provider_fixture.py'):
        shutil.copyfile(Path(__file__).parent / name, root / name)
    for origin, dest in [('rds_autonomy_worker.py', 'adapter.py'), ('rds_jump_search.py', 'rds_jump_search.py'),
                         ('rds_polynomial_confirmation.py', 'polynomial_checker.py')]:
        shutil.copyfile(REPO / 'scripts' / origin, root / dest)
    shutil.copyfile(REPO / 'examples/jump-generation/instrument.py', root / 'instrument.py')
    generator = (REPO / 'examples/jump-generation/worker.py').read_text(encoding='utf-8')
    # Reuse the same finite generator; its hypothetical experiment names our
    # pilot output. Actual AI methods are adopted/executed as owned revisions.
    write(root, 'generator.py', generator.replace("'out/verdict.json'", "'out/check1.json'"))
    points = [{'x': 3, 'y': 4}, {'x': 2, 'y': 2}, {'x': 5, 'y': 1}]
    baseline = {'op': 'add', 'args': [{'var': 'x'}, {'var': 'y'}]}
    write(root, 'domain.json', {'variables': ['x', 'y'], 'constants': [0, 1],
        'observations': [{'inputs': {'x': 0, 'y': 0}, 'value': 0},
                         {'inputs': {'x': 1, 'y': 0}, 'value': 0},
                         {'inputs': {'x': 0, 'y': 1}, 'value': 0}],
        'diagnostic_inputs': [{'x': 2, 'y': 3}], 'baseline': baseline, 'probes': points,
        'query': 'interaction', 'base_operators': ['add', 'sub'], 'allowed_operators': ['add', 'sub', 'mul'],
        'max_nodes': 3, 'max_candidates': 2000})
    write(root, 'instrument.json', {'offset': 0})
    write(root, 'corpus.json', [{'id': 'finite-operator-hint', 'text': 'interaction may require a product',
        'operators': ['mul'], 'kind': 'literature_claim', 'locator': 'synthetic://operator-hint'},
        {'id': 'retained-rival', 'text': 'interaction rival from the retained additive branch',
         'kind': 'retained_branch', 'branch_seeds': [baseline], 'locator': 'synthetic://retained-rival'}])
    write(root, 'claim.json', {'schema': 1, 'kind': 'polynomial_rational_evaluation', 'quantities': ['values']})
    write(root, 'points.json', {'schema': 1, 'variables': ['x', 'y'],
        'polynomials': [{'id': 'relation', 'terms': [{'coefficient': '1', 'powers': [[0, 1], [1, 1]]}]}],
        'points': [{'id': 'point' + str(i), 'coordinates': [str(p['x']), str(p['y'])]} for i, p in enumerate(points)]})
    slots = [{'run_id': 'repair' + str(i), 'code_path': 'candidate.py', 'worker_path': 'adapter.py',
              'response_path': 'out/repair' + str(i) + '.json'} for i in (1, 2)]
    python = sys.executable
    provider = ({'kind': 'codex_exec', 'argv': [str(Path(native).resolve())], 'model': 'gpt-6.1-sol',
                 'effort': 'high', 'service_tier': 'default'} if native else
                {'kind': 'fixture', 'argv': [python, '-B', 'provider_fixture.py'],
                 'model': None, 'effort': None, 'service_tier': None})
    provider['executable_sha256'] = file_sha(provider['argv'][0])
    ready = lambda rid: [{'fact': 'run.' + rid + '.succeeded', 'op': 'eq', 'value': True}]
    specs = [('baseline', [python, '-B', 'candidate.py', 'out/baseline.json'], ['out/baseline.json'], [], 8)]
    for i, kind in enumerate(('probe', 'refresh', 'synthesize')):
        rid = 'jump-' + kind
        before = 'baseline' if i == 0 else ('jump-probe' if i == 1 else 'jump-refresh')
        specs.append((rid, [python, '-B', 'generator.py', kind, 'out/' + kind + '.json'], ['out/' + kind + '.json'], ready(before), 10))
    for i in (1, 2):
        rid, response = 'repair' + str(i), 'out/repair' + str(i) + '.json'
        specs.append((rid, [python, '-B', 'adapter.py', '--run', rid], output_paths(response),
                      [{'fact': 'autonomy.' + rid + '.ready', 'op': 'eq', 'value': True}], 185 if native else 10))
        candidate, check = 'candidate' + str(i), 'check' + str(i)
        conditions = ([{'fact': 'autonomy.repair1.adopted', 'op': 'eq', 'value': True}] if i == 1 else
                      [{'fact': 'pilot.verdict', 'op': 'eq', 'value': 'PASS'}])
        specs.append((candidate, [python, '-B', 'candidate.py', 'out/' + candidate + '.json'], ['out/' + candidate + '.json'], conditions, 8))
        specs.append((check, [python, '-B', 'evaluator.py', candidate, 'out/' + candidate + '.json', 'out/' + check + '.json'],
                      ['out/' + check + '.json'], ready(candidate), 8))
    manifests = {rid: {'schema': 1, 'id': rid, 'arm': 'tool', 'control_id': None, 'argv': argv, 'outpaths': outputs,
                       'resource_estimates': {'wall_seconds': cap}, 'timeout_seconds': cap}
                 for rid, argv, outputs, _, cap in specs}
    stages = [{'kind': kind, 'run': {**manifests['jump-' + kind], 'protocol_path': 'protocol.json'},
               'output': 'out/' + kind + '.json'} for kind in ('probe', 'refresh', 'synthesize')]
    write(root, 'jump-generation.json', {'schema': 1, 'stages': stages,
                                        'generator_code_paths': ['generator.py', 'instrument.py', 'rds_jump_search.py']})
    write(root, 'template.json', {'experiment': {'candidate_output': 'out/candidate1.json', 'verdict_output': 'out/check1.json',
        'runs': [{**manifests[rid], 'protocol_path': 'protocol.json'} for rid in ('candidate1', 'check1')]}})
    bindings = [{'path': name, 'role': role, 'sha256': file_sha(root / name)} for role, names in [
        ('code', ['candidate.py', 'adapter.py', 'generator.py', 'instrument.py', 'rds_jump_search.py', 'provider_fixture.py']),
        ('evaluator', ['evaluator.py', 'polynomial_checker.py']),
        ('config', ['claim.json', 'jump-generation.json', 'template.json']),
        ('data', ['domain.json', 'instrument.json', 'corpus.json', 'points.json'])] for name in names]
    contract = {'schema': 1, 'description': 'Recover a finite integer interaction and return exact values at three frozen points',
        'bindings': bindings, 'allowed_commands': [s[1] for s in specs], 'output_roots': ['out'],
        'budget': {'wall_seconds': 800 if native else 350},
        'method_evolution': {'schema': 1, 'max_revisions': 2, 'code_paths': ['candidate.py']},
        'stop_policy': {'schema': 1, 'wall_seconds': 900 if native else 600,
                        'progress': {'window_seconds': 200, 'min_bytes': 0}}}
    protocol = {role + '_sha256': ProjectStore._role_sha(contract, role) for role in ('code', 'config', 'data')}
    protocol.update(data_split='public-finite-engineering', init='fresh', seed=0, checkpoint='none',
        schedule='baseline, generation, AI, pilot, optional AI correction, independent confirmation',
        sample_work={'points': 3, 'max_model_calls': 2}, numeric_protocol='exact integers/rationals')
    write(root, 'protocol.json', protocol)
    ref = {'path': 'protocol.json', 'sha256': file_sha(root / 'protocol.json')}
    bindings.append({'role': 'protocol', **ref})
    nodes, routes = [], []
    for rid, _, _, conditions, _ in specs:
        manifest = {**manifests[rid], 'protocol': ref}
        routes.append({'candidate': rid, 'manifest': manifest})
        action = {'id': rid, 'kind': 'OBLIGATION_CHECK', 'target': 'finite-interaction',
            'description': 'Execute the next bounded frozen evidence step', 'claim': 'Finite engineering example',
            'required_observables': ['original output and independent check'],
            'outcomes': [{'observation': 'verified', 'next_decision': 'inspect exact independent evidence'},
                         {'observation': 'counterexample', 'next_decision': 'return the mismatch to the AI'},
                         {'observation': 'unresolved', 'next_decision': 'retain original evidence and stop honestly'}]}
        nodes.append({'id': rid, 'sources': ['explicit frozen engineering route'],
                      'executable': {'decisions': ['next'], 'preconditions': conditions, 'action': action}})
    bound = lambda path: {'path': path, 'sha256': file_sha(root / path)}
    contract['advisor_policy'] = {'schema': 1,
        'context': {'decision': {'id': 'next', 'goal_revision': 'jump-loop-v1',
            'scope': {'domain': 'three exact finite polynomial points'},
            'goal_conditions': [{'fact': 'final.verdict', 'op': 'eq', 'value': 'PASS'}]}},
        'graph': {'nodes': nodes, 'edges': []}, 'routes': routes,
        'observations': [{'fact': label, 'run_id': rid, 'path': 'out/' + rid + '.json', 'selector': {'pointer': '/verdict'}}
                         for label, rid in [('pilot.verdict', 'check1'), ('final.verdict', 'check2')]],
        'autonomy': {'schema': 1, 'max_steps': 10, 'controller_wall_seconds': 120, 'repair_slots': slots, 'provider': provider},
        'confirmation': {'schema': 1, 'domain': 'mathematics', 'candidate_runs': ['candidate2'], 'confirmation_runs': ['check2'],
            'scope': 'Finite exact values only; no generalization or scientific gain claim', 'claim': bound('claim.json'),
            'evaluator': bound('evaluator.py'), 'data': [bound('points.json')],
            'rules': {'kind': 'polynomial_rational_evaluation', 'candidate_output': 'out/candidate2.json',
                      'confirmation_output': 'out/check2.json'}}}
    write(root, 'contract.json', contract)
    return root, contract


def run(root, native=None):
    root, contract = build(root, native)
    cli = REPO / 'scripts/rds_cli.py'
    trace = []
    for name, args in [('init', ['project', 'init', '--contract', str(root / 'contract.json')]),
                       ('drive', ['project', 'drive', '--max-steps', '10']),
                       ('continue', ['project', 'drive', '--max-steps', '10'])]:
        p = subprocess.run([sys.executable, '-B', str(cli), '--root', str(root), *args], cwd=root,
                           capture_output=True, text=True, encoding='utf-8', timeout=600)
        trace.append({'step': name, 'argv': args, 'exit_code': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
        write(root, 'out/trajectory.json', trace)
        if p.returncode:
            raise ValueError('Inspect original trajectory: ' + str(root / 'out/trajectory.json'))
        if name != 'init':
            result = json.loads(p.stdout)
            if result['status'] not in {'STEP_LIMIT', 'MODEL_REQUEST_READY'} and result['status'] != 'GOAL_CONFIRMED':
                break
    state = ProjectStore(root).snapshot()
    result = json.loads(trace[-1]['stdout'])
    summary = {'status': result['status'], 'provider': contract['advisor_policy']['autonomy']['provider'],
               'runs': [r['id'] for r in state['runs']], 'receipts': len(state['receipts']), 'budget': state['budget'],
               'scientific_gain': 'UNMEASURED', 'trajectory': str(root / 'out/trajectory.json')}
    write(root, 'out/summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--native', help='Installed Codex executable; at most two native model calls')
    args = parser.parse_args()
    print(json.dumps(run(args.workspace, args.native), indent=2))
