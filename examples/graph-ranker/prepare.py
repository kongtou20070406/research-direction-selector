"""Prepare an opt-in graph neural inference example; no training or downloads."""
import argparse
import importlib.util
import json
from pathlib import Path


def prepare(root, mode='shadow'):
    location = Path(__file__).resolve().parents[1] / 'owned-advisor' / 'prepare.py'
    spec = importlib.util.spec_from_file_location('owned_graph_example', location)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.prepare(root)
    root = Path(result['root'])
    contract = json.loads((root / 'contract.json').read_text(encoding='utf-8'))
    policy = contract['advisor_policy']
    config = json.loads(Path(__file__).with_name('synthetic-model.json').read_text(encoding='utf-8'))
    config['mode'] = mode
    policy['graph_ranker'] = config
    for node in policy['graph']['nodes']:
        node['executable']['preconditions'] = []
    # Two independently executable synthetic comparisons, rather than a paired
    # treatment that must run after its control. This changes only this demo.
    for route in policy['routes']:
        route['manifest'].update(arm='control', control_id=None)
    policy['graph']['nodes'].append({'id': 'pending-control', 'executable': {
        'preconditions': [], 'satisfied_when': [{'fact': 'run.control.completed', 'value': False}]}})
    policy['graph']['edges'].append({'from': 'pending-control', 'to': 'treatment-route',
                                    'relation': 'prerequisite_for'})
    (root / 'contract.json').write_text(json.dumps(contract, indent=2), encoding='utf-8')
    return {**result, 'graph_ranker_mode': mode, 'training_status': 'UNVERIFIED',
            'research_policy_gain_measured': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--mode', choices=('shadow', 'tie_break'), default='shadow')
    print(json.dumps(prepare(**vars(parser.parse_args())), indent=2))
