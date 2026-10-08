"""Executable three-source generation example; synthetic engineering scope only."""
import argparse
import json
from pathlib import Path
import shutil
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_project import ProjectStore, canonical, file_sha, require
from rds_tms_store import maintain
import rds_structure as structure
from rds_jump import generate


def write(path, value):
    path.write_text(canonical(value), encoding='utf-8')


def build(root, *, corpus=None, measurement_offset=0, max_candidates=2000, search_policy=False):
    root = Path(root).resolve()
    require(not root.exists(), 'Use a new empty workspace; preserve earlier attempts')
    root.mkdir(parents=True)
    (root / 'out').mkdir()
    for name in ('worker.py', 'evaluator.py', 'instrument.py'):
        shutil.copyfile(Path(__file__).parent / name, root / name)
    shutil.copyfile(REPO / 'scripts/rds_jump_search.py', root / 'rds_jump_search.py')
    baseline = {'op': 'add', 'args': [{'var': 'x'}, {'var': 'y'}]}
    probes = [{'x': 3, 'y': 4}, {'x': 2, 'y': 2}, {'x': 5, 'y': 1}]
    write(root / 'domain.json', {'variables': ['x', 'y'], 'constants': [0, 1],
        'observations': [{'inputs': {'x': 0, 'y': 0}, 'value': 0},
                         {'inputs': {'x': 1, 'y': 0}, 'value': 0},
                         {'inputs': {'x': 0, 'y': 1}, 'value': 0}],
        'diagnostic_inputs': [{'x': 2, 'y': 3}],
        'baseline': baseline, 'probes': probes, 'query': 'interaction', 'base_operators': ['add', 'sub'],
        'allowed_operators': ['add', 'sub', 'mul'], 'max_nodes': 3, 'max_candidates': max_candidates})
    write(root / 'corpus.json', corpus if corpus is not None else [
        {'id': 'public-synthetic-note', 'kind': 'literature_claim', 'locator': 'synthetic://interaction-note',
         'date': '2026-10-08', 'text': 'interaction can require a multiplicative term', 'operators': ['mul'],
         'applicability': 'Exact finite integer arithmetic; relevance must be tested'},
        {'id': 'retained-additive-branch', 'kind': 'retained_branch', 'locator': 'synthetic://branch/additive',
         'text': 'interaction rival retained from additive modeling', 'branch_seeds': [baseline]}])
    write(root / 'oracle.json', [{'inputs': p, 'value': p['x'] * p['y']} for p in probes])
    write(root / 'instrument.json', {'offset': measurement_offset})
    protocol = {'data_split': 'public-finite-development', 'init': 'fresh', 'seed': 0, 'checkpoint': 'none',
                'schedule': 'three generation routes then independent finite verification',
                'sample_work': {'training_rows': 4, 'verification_rows': 3}, 'numeric_protocol': 'bounded exact integers'}
    commands = [[sys.executable, '-B', 'worker.py', kind, 'out/' + kind + '.json']
                for kind in ('probe', 'refresh', 'synthesize', 'candidate')]
    commands.append([sys.executable, '-B', 'evaluator.py', 'out/candidate.json', 'out/verdict.json'])
    bindings = [{'path': p, 'role': role, 'sha256': file_sha(root / p)} for p, role in
        [('worker.py', 'code'), ('instrument.py', 'code'), ('rds_jump_search.py', 'code'), ('evaluator.py', 'evaluator'),
         ('domain.json', 'data'), ('instrument.json', 'data'), ('corpus.json', 'data'), ('oracle.json', 'data')]]
    contract = {'schema': 1, 'description': 'Predict the original three finite rows with the independent frozen evaluator',
                'bindings': bindings, 'allowed_commands': commands, 'output_roots': ['out'], 'budget': {'wall_seconds': 180}}
    def manifest(ident, argv, path):
        return {'schema': 1, 'id': ident, 'arm': 'tool', 'argv': argv, 'outpaths': [path],
                'protocol_path': 'protocol.json', 'resource_estimates': {'wall_seconds': 10}, 'timeout_seconds': 10}
    stages = [{'kind': kind, 'run': manifest('jump-' + kind, commands[i], 'out/' + kind + '.json'),
               'output': 'out/' + kind + '.json'} for i, kind in enumerate(('probe', 'refresh', 'synthesize'))]
    write(root / 'jump-generation.json', {'schema': 1, 'stages': stages})
    write(root / 'template.json', {'experiment': {'candidate_output': 'out/candidate.json', 'verdict_output': 'out/verdict.json',
              'runs': [manifest('jump-candidate', commands[3], 'out/candidate.json'),
                       manifest('jump-verifier', commands[4], 'out/verdict.json')]}})
    bindings.extend({'path': p, 'role': role, 'sha256': file_sha(root / p)} for p, role in
                    [('jump-generation.json', 'config'), ('template.json', 'config')])
    if search_policy:
        write(root / 'structure-search.json', {'schema': 1, 'strategy': 'bounded_feedback_v1',
              'metric': {'name': 'independent-probe-value', 'pointer': '/probe_value', 'unit': 'integer',
                         'direction': 'min', 'min_improvement': '1'}, 'baseline_proposal_id': 'baseline',
              'slots': {'explore': 2, 'evidence': 1, 'refine': 1}, 'min_repeats': 2})
        bindings.append({'path': 'structure-search.json', 'role': 'config', 'sha256': file_sha(root / 'structure-search.json')})
    protocol.update({role + '_sha256': ProjectStore._role_sha(contract, role) for role in ('code', 'config', 'data')})
    write(root / 'protocol.json', protocol)
    bindings.append({'path': 'protocol.json', 'role': 'protocol', 'sha256': file_sha(root / 'protocol.json')})
    write(root / 'contract.json', contract)
    store = ProjectStore(root)
    store.initialize(contract)
    maintain(root, initial={'schema': 1, 'nodes': [{'id': 'goal', 'status': 'UNKNOWN', 'source': 'frozen finite acceptance'}],
                            'hyperedges': [], 'goals': ['goal']})
    return root, store


def run(root):
    root, store = build(root)
    generation = generate(root, 3)
    observed = structure.advance(root, generation['proposal_ids'][0]) if generation['status'] == 'JUMP_PROPOSED' else None
    return {'generation': generation, 'feedback': observed, 'decision': structure.next_step(root),
            'budget': store.snapshot()['budget'], 'scientific_gain': 'UNMEASURED',
            'scope': 'PUBLIC_SYNTHETIC_ENGINEERING_EXAMPLE'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.workspace), indent=2))
