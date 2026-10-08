"""Exercise three finite result tools through the original owned RSI consumer.

The fixtures and oracles below are public synthetic software cases. They are
declared before candidate execution; no model calls or scientific trial occur.
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
from rds_project import ProjectStore


def run(workspace, *, compose=False):
    root = Path(workspace).resolve()
    if root.is_relative_to(REPO):
        raise ValueError('Choose a fresh sibling workspace outside the checkout')
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError('Workspace must be empty; original evidence is never overwritten')
    trace = []

    def write(name, value):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False,
                                                                       allow_nan=False), encoding='utf-8')
        return {'path': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

    def call(stage, *args):
        if args[0] == 'rsi':
            args = (*args, '--json')
        argv = [sys.executable, '-B', str(REPO / 'scripts/rds_cli.py'), '--root', str(root), *args]
        proc = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', timeout=40,
                              env={**os.environ, 'RDS_USAGE_DB': str(root / 'usage.sqlite3')})
        trace.append({'stage': stage, 'argv': argv, 'returncode': proc.returncode,
                      'stdout': proc.stdout, 'stderr': proc.stderr})
        write('cli-transcript.json', trace)
        if proc.returncode:
            raise RuntimeError('Inspect cli-transcript.json: ' + stage + '\n' + proc.stdout + proc.stderr)
        result = json.loads(proc.stdout)
        write('reports/' + stage + '.json', result)
        return result

    def source(name, document, run_id):
        ref = write('sources/' + name, document)
        raw = (root / ref['path']).read_bytes().decode('utf-8')
        return raw, {'run_id': run_id, 'source_path': ref['path'], 'source_sha256': ref['sha256']}, ref

    # Known finite values and exact original bytes, never candidate-generated oracles.
    extract_raw, extract_id, extract_ref = source('结果.json', {'case_count': 72, 'detail': 'x' * 16384}, 'fixture-count')
    inventory = {'case_count': 72, 'failure_count': 64, 'skipped_count': 1,
                 'failures': [{'case_id': 'case-' + str(i), 'trace': 'x' * 8192} for i in range(64)],
                 'skipped': [{'case_id': 'optional-backend', 'reason': 'not installed'}]}
    inventory_raw, inventory_id, inventory_ref = source('test-results.json', inventory, 'fixture-development')
    _, baseline_id, baseline_ref = source('baseline.json', {'loss': 5}, 'fixture-baseline')
    _, candidate_id, candidate_ref = source('candidate.json', {'loss': 3}, 'fixture-candidate')
    data_ref = write('sources/data.json', [1, 2, 3])
    evaluator_ref = write('sources/evaluator.txt', 'Public synthetic metric: mean absolute residual; dimensionless; minimize.\n')
    match = {'data_sha256': data_ref['sha256'], 'data_split': 'public-development',
             'evaluator_sha256': evaluator_ref['sha256'], 'metric': 'loss',
             'metric_definition': 'mean absolute residual', 'reduction': 'mean',
             'unit': 'dimensionless', 'direction': 'minimize'}
    candidate_id.update(match)
    baseline_id.update(match)
    candidate = {'identity': candidate_id, 'value': 3}
    baseline = {'identity': baseline_id, 'value': 5}
    common = {'identity_assurance': 'CALLER_METADATA', 'scientific_support': 'UNKNOWN'}
    extract_expected = {'operation': 'extract_json_result', 'status': 'OBSERVED', 'value': 72,
                        'identity': extract_id, 'pointer': '/case_count', 'missing_fields': [],
                        'conflicting_fields': [], 'reason': None, **common}
    compare_expected = {'operation': 'compare_metrics', 'status': 'COMPARABLE', 'delta': -2, 'improvement': 2,
                        'candidate': {**candidate, 'value_omitted': False},
                        'baseline': {**baseline, 'value_omitted': False}, 'matched_fields': list(match),
                        'missing_fields': [], 'conflicting_fields': [], 'reason': None, **common}
    inventory_expected = {'operation': 'summarize_failures', 'status': 'OBSERVED', 'identity': inventory_id,
        'case_count': 72, 'failure_count': 64, 'skipped_count': 1, 'unknown_count': 0,
        'observed_failure_records': 64, 'observed_skipped_records': 1, 'total_records': 65,
        'omitted_records': 61, 'reasons': [], 'entries': [
            {'kind': 'failures', 'case_id': 'case-' + str(i), 'pointer': '/failures/' + str(i),
             'field': 'trace', 'text': None, 'text_bytes': 8192, 'text_omitted': True, 'status': 'OBSERVED'}
            for i in range(4)], **common}
    specs = [
        ('extract', 'extract_json_result', [extract_raw, '/case_count', extract_id], extract_expected,
         [('status', 'eq', 'OBSERVED'), ('value', 'gt', 0)], [extract_ref]),
        ('compare', 'compare_metrics', [candidate, baseline], compare_expected,
         [('status', 'eq', 'COMPARABLE'), ('improvement', 'gt', 0)], [candidate_ref, baseline_ref, data_ref, evaluator_ref]),
        ('inventory', 'summarize_failures', [inventory_raw, inventory_id, 4], inventory_expected,
         [('status', 'eq', 'OBSERVED'), ('failure_count', 'gt', 0), ('unknown_count', 'eq', 0)], [inventory_ref])]
    decision = {'id': 'inspect-results', 'goal_revision': 'public-result-tools-v1',
                'scope': {'domain': 'finite synthetic software; select original failures for inspection'},
                'goal_conditions': [{'fact': name + '.' + field, 'op': op, 'value': value}
                                    for name, _, _, _, predicates, _ in specs for field, op, value in predicates]}
    write('decision.json', decision)
    bindings, tools, commands, nodes, routes, observations = [], [], [], [], [], []
    for name, entry, args, expected, predicates, sources in specs:
        cases = [{'args': args, 'expected': expected}]
        write(name + '/cases.json', cases)
        write(name + '/inputs.json', [{'args': args, 'kwargs': {}}])
        call(name + '-extract', 'rsi', 'extract', '--source', str(REPO / 'scripts/rds_result_tools.py'),
             '--entry', entry, '--name', name)
        validation = call(name + '-validate', 'rsi', 'validate', '--name', name,
                          '--cases', str(root / name / 'cases.json'), '--timeout', '5')
        call(name + '-register', 'rsi', 'register', '--name', name, '--validation', validation['id'])
        facts = [name + '.' + field for field, _, _ in predicates]
        action = {'id': name, 'kind': 'PAIRED_TEST', 'target': facts[-1], 'operation': entry,
                  'description': 'Read the frozen finite result and retain its original source locator',
                  'competing_explanations': ['declared finite result is observed', 'result requires original inspection'],
                  'required_observables': facts,
                  'outcomes': [{'observation': 'observed', 'next_decision': 'inspect the bound original result'},
                               {'observation': 'unknown', 'next_decision': 'resolve missing or conflicting input'}]}
        write(name + '/action.json', action)
        prepared = call(name + '-prepare', 'rsi', 'prepare-application', '--name', name,
            '--inputs', name + '/inputs.json', '--cases', name + '/cases.json',
            '--code-path', name + '/qualified.py', '--driver', name + '/apply.py',
            '--request', name + '/request.json', '--output', 'outputs/' + name + '.json',
            '--decision', str(root / 'decision.json'), '--action-file', str(root / name / 'action.json'),
            '--candidate', name, '--run-id', name, '--obligation', action['target'],
            *[arg for fact in facts for arg in ('--observation-fact', fact)])
        if prepared['execution_started'] or (root / 'outputs' / (name + '.json')).exists():
            raise RuntimeError('Preparation unexpectedly executed a result tool')
        bindings.extend(prepared['required_bindings'])
        bindings.extend({'role': 'data', **ref} for ref in sources)
        tools.append(prepared['binding'])
        commands.append(prepared['argv'])
        nodes.append({'id': name + '-task', 'sources': ['public synthetic fixture'],
                      'executable': {'decisions': [decision['id']], 'preconditions': [], 'action': action}})
        observations.extend({'fact': name + '.' + field, 'run_id': name, 'path': 'outputs/' + name + '.json',
                             'selector': {'pointer': '/cases/0/value/' + field}} for field, _, _ in predicates)
    protocol = {role + '_sha256': ProjectStore._role_sha({'bindings': bindings}, role)
                for role in ('code', 'config', 'data')}
    protocol.update({
                'data_split': 'public-finite-software', 'init': 'none', 'seed': 0, 'checkpoint': 'none',
                'schedule': 'three finite result calls', 'sample_work': {'cases': 3}, 'numeric_protocol': 'Python integers'})
    protocol_ref = write('protocol.json', protocol)
    bindings.append({'role': 'protocol', **protocol_ref})
    for spec, argv in zip(specs, commands):
        name = spec[0]
        routes.append({'candidate': name, 'manifest': {'schema': 1, 'id': name, 'arm': 'tool', 'control_id': None,
            'protocol': protocol_ref, 'argv': argv, 'outpaths': ['outputs/' + name + '.json'],
            'timeout_seconds': 10, 'resource_estimates': {'wall_seconds': 10}}})
    contract = {'schema': 1, 'bindings': bindings, 'allowed_commands': commands, 'output_roots': ['outputs'],
                'budget': {'wall_seconds': 60}, 'advisor_policy': {'schema': 1, 'context': {'decision': decision},
                    'graph': {'nodes': nodes, 'edges': []}, 'routes': routes, 'observations': observations,
                    'tool_bindings': tools}}
    write('contract.json', contract)
    call('init', 'project', 'init', '--contract', str(root / 'contract.json'))
    if compose:
        from rds_project import digest
        from rds_tool_calls import SCHEMA
        call('discovery', 'rsi', 'discover', '--name', 'extract')
        # Follow this example's declared route order, as in the existing consumer.
        # Each internal call still rechecks selection; a mismatch stops the request.
        request = {'schema': SCHEMA, 'contract_sha256': digest(contract), 'max_wall_seconds': 60,
                   'steps': [{'op': 'status'}, {'op': 'collect'}] + [
                       {'op': 'execute_tool', 'run_id': spec[0]} for spec in specs] + [
                       {'op': 'collect'}, {'op': 'costs'}]}
        write('composition.json', request)
        call('composition', 'project', 'compose-tools', '--request', 'composition.json')
        final = call('verification', 'project', 'next')
    else:
        call('before', 'project', 'next')
        for index in range(3):
            call('advance-' + str(index), 'project', 'advance')
        final = call('after', 'project', 'next')
    before_recovery = call('status-before-recovery', 'project', 'status')
    if compose:
        call('recovery', 'project', 'compose-tools', '--request', 'composition.json')
    else:
        call('recovery', 'project', 'advance')
    after_recovery = call('status-after-recovery', 'project', 'status')
    if before_recovery['runs'] != after_recovery['runs'] or before_recovery['budget'] != after_recovery['budget']:
        raise RuntimeError('Recovery changed completed runs or budget')
    with closing(sqlite3.connect((root / '.rds/project.sqlite3').as_uri() + '?mode=ro', uri=True)) as db:
        write('reports/checkpoints.json', [json.loads(r[0]) for r in db.execute('SELECT body FROM checkpoints ORDER BY rowid')])
        write('reports/preparation-costs.json', [json.loads(r[0]) for r in db.execute(
            "SELECT body FROM events WHERE json_extract(body,'$.kind')='TOOL_PREPARATION_COST' ORDER BY id")])
    sizes = []
    for name, _, _, _, _, sources in specs:
        output = root / 'outputs' / (name + '.json')
        raw_bytes = sum((root / ref['path']).stat().st_size for ref in sources)
        returned_bytes = output.stat().st_size
        sizes.append({'operation': name, 'original_source_bytes': raw_bytes,
                      'returned_application_bytes': returned_bytes, 'bytes_difference': raw_bytes - returned_bytes})
    summary = {'workspace': str(root), 'tool_utilization': final['tool_utilization'],
               'execution_interface': 'composition' if compose else 'existing entries in a host program',
               'consumer_cli_calls': sum(t['stage'] in {'before', 'after', 'composition'} or t['stage'].startswith('advance-')
                                         for t in trace),
               'verification_cli_calls': sum(t['stage'] == 'verification' for t in trace),
               'discovery_cli_calls': sum(t['stage'] == 'discovery' for t in trace),
               'selected_run': final['selected_run'], 'material_bytes': sizes,
               'recovery_preserved_runs_and_budget': True, 'real_model_round_trips': 'NOT_RUN',
               'token_savings': 'NOT_MEASURED', 'scientific_gain': 'UNKNOWN',
               'scope': 'THREE_REUSED_FINITE_SYNTHETIC_CASES'}
    write('summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--compose', action='store_true', help='Use the same tools through bounded composition')
    args = parser.parse_args()
    print(json.dumps(run(args.workspace, compose=args.compose), indent=2, allow_nan=False))
