"""Qualify finite methods and consume their values through the existing owned ledger."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / 'scripts'))
from rds_result_tools import _document


def check_original_inputs(spec, source_map):
    """Reconcile actual call data with bound bytes, not merely supplied hashes."""
    entry, args = spec['entry'], spec['args']
    if entry in {'compare_paired_metrics', 'check_residuals'}:
        for point in args[:2]:
            identity = point['identity']
            raw = source_map.get(identity['source_path'])
            if raw is None or hashlib.sha256(raw).hexdigest() != identity['source_sha256']:
                raise ValueError('Point source bytes are missing or disagree with identity')
            original = _document(raw.decode('utf-8'))
            if any(json.dumps(point.get(k), allow_nan=False) != json.dumps(original.get(k), allow_nan=False)
                   for k in ('values', 'sample_ids')):
                raise ValueError('Point call inputs disagree with original source values/IDs')
            if any(identity[k] not in {hashlib.sha256(value).hexdigest() for value in source_map.values()}
                   for k in ('data_sha256', 'evaluator_sha256')):
                raise ValueError('Point data/evaluator bytes are missing from bound sources')
    elif entry in {'diagnose_decisions', 'evaluate_decision_requirements'}:
        raw_text, identity = args[:2]
        original = source_map.get(identity['source_path'])
        if (original is None or raw_text.encode('utf-8') != original
                or hashlib.sha256(original).hexdigest() != identity['source_sha256']):
            raise ValueError('Diagnostic call text disagrees with original source bytes')
        hashes = {hashlib.sha256(raw).hexdigest() for raw in source_map.values()}
        if identity['evaluator_sha256'] not in hashes:
            raise ValueError('Diagnostic evaluator bytes are missing')
        document = _document(original.decode('utf-8'))
        if any(record['source_sha256'] not in hashes for record in document['rows'] + document['parents']):
            raise ValueError('Diagnostic upstream/parent original sources are missing')


def run(workspace, specs_path):
    """Specs contain independently declared args/expected/predicates and original source files."""
    root = Path(workspace).resolve()
    if root.is_relative_to(REPO):
        raise ValueError('Choose a fresh sibling workspace outside the checkout')
    root.mkdir(parents=True, exist_ok=True)
    if any(root.iterdir()):
        raise ValueError('Workspace must be empty; preserve original evidence')
    specs_path = Path(specs_path).resolve()
    specs = json.loads(specs_path.read_text(encoding='utf-8-sig'))
    if not isinstance(specs, list) or not 1 <= len(specs) <= 8:
        raise ValueError('Supply 1..8 explicit finite method specifications')
    transcript = []

    def write(path, value):
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')

    def cli(stage, *args):
        argv = [sys.executable, '-B', str(REPO / 'scripts/rds_cli.py'), '--root', str(root), *args]
        proc = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', timeout=60,
                              env={**os.environ, 'RDS_USAGE_DB': str(root / 'usage.sqlite3')})
        transcript.append({'stage': stage, 'argv': argv, 'exit_code': proc.returncode,
                           'stdout': proc.stdout, 'stderr': proc.stderr})
        write('cli-transcript.json', transcript)
        if proc.returncode:
            raise RuntimeError('Inspect cli-transcript.json: ' + stage + '\n' + proc.stdout + proc.stderr)
        value = json.loads(proc.stdout)
        write('reports/' + stage + '.json', value)
        return value

    write('specs.json', specs)
    sources, source_map = [], {}
    for spec in specs:
        for source in spec.get('sources', []):
            relative = Path(source['path'])
            if relative.is_absolute() or '..' in relative.parts or relative.parts[0] != 'sources':
                raise ValueError('Original sources must be under sources/')
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            raw = source['text'].encode('utf-8')
            if hashlib.sha256(raw).hexdigest() != source['sha256']:
                raise ValueError('Original source hash disagrees with declared bytes')
            if target.exists() and target.read_bytes() != raw:
                raise ValueError('Conflicting original source path')
            target.write_bytes(raw)
            sources.append(relative.as_posix())
            source_map[relative.as_posix()] = raw
    for spec in specs:
        check_original_inputs(spec, source_map)
    decision = {'id': 'inspect-method-results', 'goal_revision': 'finite-methods-v1',
                'scope': {'domain': 'declared finite method observations; no scientific gain claim'},
                'goal_conditions': [{'fact': s['name'] + '.' + p.get('fact', p['field']), 'op': p['op'], 'value': p['value']}
                                    for s in specs for p in s['predicates']]}
    write('decision.json', decision)
    routes = []
    for spec in specs:
        name, entry, args = spec['name'], spec['entry'], spec['args']
        if (not isinstance(name, str) or not name.isascii() or not name.isidentifier()
                or entry not in {'compare_paired_metrics', 'check_residuals', 'diagnose_decisions',
                                 'evaluate_decision_requirements'}):
            raise ValueError('Supply a safe method name and supported entry')
        cases = [{'args': args, 'expected': spec['expected']}]
        write(name + '/cases.json', cases)
        write(name + '/inputs.json', [{'args': args, 'kwargs': {}}])
        cli(name + '-extract', 'rsi', 'extract', '--source', str(REPO / 'scripts/rds_result_tools.py'),
            '--entry', entry, '--name', name, '--json')
        validation = cli(name + '-validate', 'rsi', 'validate', '--name', name,
                         '--cases', str(root / name / 'cases.json'), '--timeout', '5', '--json')
        cli(name + '-register', 'rsi', 'register', '--name', name, '--validation', validation['id'], '--json')
        facts = [name + '.' + p.get('fact', p['field']) for p in spec['predicates']]
        action = {'id': name, 'kind': 'PAIRED_TEST', 'target': facts[-1], 'operation': entry,
                  'description': 'Inspect a protocol-bound finite result through its original consumer',
                  'competing_explanations': ['declared observation holds', 'input or observation needs inspection'],
                  'required_observables': facts,
                  'outcomes': [{'observation': 'observed', 'next_decision': 'inspect the returned numeric observation'},
                               {'observation': 'unknown', 'next_decision': 'resolve original input conditions'}]}
        write(name + '/action.json', action)
        cli(name + '-prepare', 'rsi', 'prepare-application', '--name', name,
            '--inputs', name + '/inputs.json', '--cases', name + '/cases.json',
            '--code-path', name + '/qualified.py', '--driver', name + '/apply.py',
            '--request', name + '/request.json', '--output', 'outputs/' + name + '.json',
            '--decision', str(root / 'decision.json'), '--action-file', str(root / name / 'action.json'),
            '--candidate', name, '--run-id', name, '--obligation', action['target'],
            *[arg for fact in facts for arg in ('--observation-fact', fact)], '--json')
        routes.append({'action': action, 'prepared_application': 'reports/' + name + '-prepare.json',
                       'run': {'timeout_seconds': 10, 'resource_estimates': {'wall_seconds': 10}},
                       'observations': [{'fact': name + '.' + p.get('fact', p['field']), 'path': 'outputs/' + name + '.json',
                                         'selector': {'pointer': '/cases/0/value/' + p['field']}}
                                        for p in spec['predicates']]})
    recipe = {'schema': 1, 'files': {'data': ['specs.json', *sorted(set(sources))]},
              'protocol': {'path': 'protocol.json', 'metadata': {'data_split': 'finite-development',
                           'init': 'none', 'seed': 0, 'checkpoint': 'none', 'schedule': 'one call per method',
                           'sample_work': {'methods': len(specs)}, 'numeric_protocol': 'Python finite arithmetic'}},
              'context': {'decision': decision}, 'routes': routes,
              'output_roots': ['outputs'], 'budget': {'wall_seconds': 90}}
    write('recipe.json', recipe)
    cli('init', 'project', 'init', '--recipe', str(root / 'recipe.json'))
    before = cli('before', 'project', 'next')
    for index in range(len(specs)):
        cli('advance-' + str(index), 'project', 'advance')
    final = cli('after', 'project', 'next')
    status = cli('status-before-recovery', 'project', 'status')
    cli('recovery', 'project', 'advance')
    recovered = cli('status-after-recovery', 'project', 'status')
    preserved = status['runs'] == recovered['runs'] and status['budget'] == recovered['budget']
    if not preserved:
        raise RuntimeError('Recovery changed completed runs or budget')
    summary = {'tool_utilization': final['tool_utilization'], 'recovery_preserved_runs_and_budget': preserved,
               'initial_selected_run': before['selected_run'], 'final_selected_run': final['selected_run'],
               'specs_sha256': hashlib.sha256((root / 'specs.json').read_bytes()).hexdigest(),
               'scientific_gain': 'UNKNOWN', 'scope': 'EXACT_REUSED_FINITE_CASES'}
    write('summary.json', summary)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True)
    parser.add_argument('--specs', required=True, help='Frozen args, independent oracle and goal predicates')
    args = parser.parse_args()
    print(json.dumps(run(args.workspace, args.specs), indent=2, allow_nan=False))
