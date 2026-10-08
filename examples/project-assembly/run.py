"""Compare recipe and contract entries on the unchanged public two-route task.

This measures emitted declaration size, not LLM tokens or authoring time. The
existing preparation script is a valid baseline and already automates assembly.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_project import canonical, file_sha


def declaration_counts(*values):
    def hashes(value):
        if isinstance(value, dict):
            return sum(int(key == 'sha256' or key.endswith('_sha256')) + hashes(item)
                       for key, item in value.items())
        if isinstance(value, list):
            return sum(hashes(item) for item in value)
        return 0
    return {'canonical_json_bytes': sum(len(canonical(v).encode('utf-8')) for v in values),
            'sha256_fields': sum(hashes(v) for v in values)}


def run(workspace):
    root = Path(workspace).resolve()
    if root.is_relative_to(REPO):
        raise ValueError('Use a fresh sibling workspace outside the checkout')
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError('Workspace must be empty; evidence is never overwritten')
    spec = importlib.util.spec_from_file_location('owned_example', REPO / 'examples/owned-advisor/prepare.py')
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    summaries = {}
    for mode in ('contract', 'recipe'):
        project = root / mode
        original.prepare(project)
        contract = json.loads((project / 'contract.json').read_text(encoding='utf-8'))
        protocol = json.loads((project / 'protocol.json').read_text(encoding='utf-8'))
        policy = contract['advisor_policy']
        # Both entries receive exactly the original task semantics and fixtures.
        # Conversion here is only the paired comparison fixture, not runtime logic.
        actions = {n['executable']['action']['id']: n['executable'] for n in policy['graph']['nodes']}
        recipe = {'schema': 1, 'description': contract['description'],
            'files': {role: [b['path'] for b in contract['bindings'] if b['role'] == role]
                      for role in ('code', 'config', 'data', 'evaluator')},
            'protocol': {'path': 'assembled-protocol.json', 'metadata': {k: v for k, v in protocol.items()
                         if k not in {'code_sha256', 'config_sha256', 'data_sha256'}}},
            'context': policy['context'], 'output_roots': contract['output_roots'], 'budget': contract['budget'],
            'routes': [{'action': actions[r['candidate']]['action'],
                        'preconditions': actions[r['candidate']]['preconditions'],
                        'run': {k: v for k, v in r['manifest'].items() if k not in {'schema', 'protocol'}},
                        'observations': [{k: v for k, v in o.items() if k != 'run_id'}
                                         for o in policy['observations'] if o['run_id'] == r['manifest']['id']]}
                       for r in policy['routes']]}
        (project / 'recipe.json').write_text(json.dumps(recipe, indent=2), encoding='utf-8')
        trace = []

        def call(*args):
            proc = subprocess.run([sys.executable, '-B', str(REPO / 'scripts/rds_cli.py'),
                '--root', str(project), 'project', *args], capture_output=True, text=True, encoding='utf-8',
                timeout=40, env={**os.environ, 'RDS_USAGE_DB': str(project / 'usage.sqlite3')})
            trace.append({'args': list(args), 'exit_code': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr})
            (project / 'cli-transcript.json').write_text(json.dumps(trace, indent=2), encoding='utf-8')
            if proc.returncode:
                raise RuntimeError('Inspect ' + str(project / 'cli-transcript.json'))
            return json.loads(proc.stdout)

        initialized = call('init', '--' + mode, str(project / (mode + '.json')))
        call('advance')
        call('advance')
        before = call('status')
        call('advance')
        after = call('status')
        if before['runs'] != after['runs'] or before['budget'] != after['budget']:
            raise RuntimeError('Recovery changed completed runs or budget')
        values = {arm: json.loads((project / 'outputs' / (arm + '.json')).read_text())['mse']
                  for arm in ('control', 'treatment')}
        if len(before['runs']) != 2 or any(r['status'] != 'COMPLETED' for r in before['runs']):
            raise RuntimeError('Original two-route workload did not complete')
        summaries[mode] = {'entry_inputs': declaration_counts(recipe) if mode == 'recipe'
                          else declaration_counts(contract, protocol), 'project_cli_calls': len(trace),
            'values': values, 'goal_conditions': policy['context']['decision']['goal_conditions'],
            'goal_met': values['treatment'] <= 0.01, 'completed_runs': len(before['runs']),
            'receipt_count': len(before['receipts']), 'budget': before['budget'],
            'recovery_preserved_runs_and_budget': True,
            'original_inputs': {b['path']: file_sha(project / b['path']) for b in contract['bindings']
                                if b['role'] != 'protocol'}, 'assembly': initialized.get('assembly')}
    for key in ('values', 'goal_conditions', 'goal_met', 'completed_runs', 'receipt_count', 'original_inputs'):
        if summaries['contract'][key] != summaries['recipe'][key]:
            raise RuntimeError('Entry parity failed: ' + key)
    report = {'scope': 'SAME_PUBLIC_TWO_ROUTE_SOFTWARE_TASK', 'arms': summaries,
        'entry_parity': True, 'existing_preparation_script_is_valid_baseline': True,
        'real_model_round_trips': 'NOT_RUN', 'token_savings': 'NOT_MEASURED', 'scientific_gain': 'UNKNOWN'}
    (root / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    print(json.dumps(run(parser.parse_args().workspace), indent=2))
