"""Run a tiny public native-tool -> owned-result -> decision-consumer example.

Use a fresh empty sibling workspace. This exercises finite software cases, not
scientific gain or a general solver. Every child command and output is retained.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_method_revision import _code_sha


def run(workspace):
    root = Path(workspace).resolve()
    if root.is_relative_to(REPO):
        raise ValueError('Choose a fresh sibling workspace outside the checkout')
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError('Workspace must be empty; existing evidence is never overwritten')
    trace = []

    def write(name, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, indent=2, allow_nan=False), encoding='utf-8')

    def call(stage, *args, expected_returncode=0):
        if args[0] == 'rsi':
            args = (*args, '--json')
        argv = [sys.executable, '-B', str(REPO / 'scripts/rds_cli.py'), '--root', str(root), *args]
        proc = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', timeout=40,
                              env={**os.environ, 'RDS_USAGE_DB': str(root / 'usage.sqlite3')})
        trace.append({'stage': stage, 'argv': argv, 'returncode': proc.returncode,
                      'stdout': proc.stdout, 'stderr': proc.stderr})
        write('cli-transcript.json', trace)
        if proc.returncode != expected_returncode:
            raise RuntimeError('Inspect cli-transcript.json: ' + stage)
        result = json.loads(proc.stdout)
        write('reports/' + stage + '.json', result)
        return result

    # These oracle values are declared independently, before candidate execution.
    cases = [{'args': [[1, 2, 3]], 'expected': 14}, {'args': [[-3, 3]], 'expected': 18}]
    write('cases.json', cases)
    write('inputs.json', [{'args': c['args'], 'kwargs': {}} for c in cases])
    write('wrong.py', 'def wrong(values):\n    return 0\n')
    call('wrong-extract', 'rsi', 'extract', '--source', str(root / 'wrong.py'), '--entry', 'wrong', '--name', 'wrong')
    failed = call('wrong-validate', 'rsi', 'validate', '--name', 'wrong', '--cases', str(root / 'cases.json'),
                  '--timeout', '3', expected_returncode=1)
    if failed['status'] != 'FAILED':
        raise RuntimeError('The wrong candidate did not fail its independent cases')
    write('source.py', 'def sum_squares(values):\n    return sum(v * v for v in values)\n')
    call('extract', 'rsi', 'extract', '--source', str(root / 'source.py'), '--entry', 'sum_squares', '--name', 'squares')
    validation = call('validate', 'rsi', 'validate', '--name', 'squares', '--cases', str(root / 'cases.json'), '--timeout', '3')
    call('register', 'rsi', 'register', '--name', 'squares', '--validation', validation['id'])
    decision = {'id': 'next', 'goal_revision': 'finite-squares-v1', 'scope': {'domain': 'public finite software cases'},
                'goal_conditions': [{'fact': 'task.status', 'op': 'eq', 'value': 'PASS'}]}
    action = {'id': 'apply', 'kind': 'PAIRED_TEST', 'target': 'task.status', 'operation': 'sum-squares-current-inputs',
              'description': 'Apply the qualified function to the two frozen task inputs',
              'competing_explanations': ['finite cases pass', 'finite cases fail'],
              'required_observables': ['task.status'],
              'outcomes': [{'observation': 'PASS', 'next_decision': 'read original finite results'},
                           {'observation': 'FAIL', 'next_decision': 'inspect original failure'}]}
    write('decision.json', decision)
    write('action.json', action)
    prepared = call('prepare-application', 'rsi', 'prepare-application', '--name', 'squares',
                    '--inputs', 'inputs.json', '--cases', 'cases.json', '--code-path', 'qualified.py',
                    '--driver', 'apply.py', '--request', 'request.json', '--output', 'outputs/application.json',
                    '--decision', str(root / 'decision.json'), '--action-file', str(root / 'action.json'),
                    '--candidate', 'apply', '--run-id', 'apply', '--obligation', 'task.status',
                    '--observation-fact', 'task.status')
    if prepared['execution_started'] or (root / 'outputs/application.json').exists():
        raise RuntimeError('Preparation unexpectedly executed the application')
    bindings = prepared['required_bindings']
    protocol = {'code_sha256': _code_sha({'bindings': bindings}),
                'config_sha256': next(b['sha256'] for b in bindings if b['role'] == 'config'),
                'data_sha256': next(b['sha256'] for b in bindings if b['role'] == 'data'),
                'data_split': 'public-finite-software', 'init': 'none', 'seed': 0, 'checkpoint': 'none',
                'schedule': 'two bounded calls', 'sample_work': {'cases': 2}, 'numeric_protocol': 'Python integers'}
    write('protocol.json', protocol)
    protocol_ref = {'path': 'protocol.json', 'sha256': hashlib.sha256((root / 'protocol.json').read_bytes()).hexdigest()}
    bindings.append({'role': 'protocol', **protocol_ref})
    manifest = {'schema': 1, 'id': 'apply', 'arm': 'tool', 'control_id': None, 'protocol': protocol_ref,
                'argv': prepared['argv'], 'outpaths': ['outputs/application.json'],
                'timeout_seconds': 10, 'resource_estimates': {'wall_seconds': 10}}
    contract = {'schema': 1, 'bindings': bindings, 'allowed_commands': [prepared['argv']],
                'output_roots': ['outputs'], 'budget': {'wall_seconds': 40},
                'advisor_policy': {'schema': 1, 'context': {'decision': decision},
                    'graph': {'nodes': [{'id': 'finite-task', 'sources': ['public synthetic oracle'],
                        'executable': {'decisions': ['next'], 'preconditions': [], 'action': action}}], 'edges': []},
                    'routes': [{'candidate': 'apply', 'manifest': manifest}],
                    'observations': [{'fact': 'task.status', 'run_id': 'apply', 'path': 'outputs/application.json',
                                      'selector': {'pointer': '/status'}}],
                    'tool_bindings': [prepared['binding']]}}
    write('contract.json', contract)
    call('init', 'project', 'init', '--contract', str(root / 'contract.json'))
    call('before', 'project', 'next')
    call('advance', 'project', 'advance')
    final = call('after', 'project', 'next')
    call('status', 'project', 'status')
    # Display existing checkpoint originals; owned next already verifies history.
    with closing(sqlite3.connect((root / '.rds/project.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        write('reports/checkpoints.json', [json.loads(r[0]) for r in db.execute('SELECT body FROM checkpoints ORDER BY rowid')])
        write('reports/preparation-costs.json', [json.loads(r[0]) for r in db.execute(
            "SELECT body FROM events WHERE json_extract(body,'$.kind')='TOOL_PREPARATION_COST' ORDER BY id")])
    summary = {'workspace': str(root), 'tool_utilization': final['tool_utilization'],
               'selected_run': final['selected_run'], 'scientific_gain': 'UNKNOWN',
               'scope': 'TWO_REUSED_FINITE_SOFTWARE_CASES'}
    write('summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.workspace), indent=2, allow_nan=False))
