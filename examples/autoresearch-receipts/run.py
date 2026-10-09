"""CPU fixture comparison and identical-call receipt reuse through the existing CLI."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

REPO = Path(__file__).resolve().parents[2]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, help='New empty sibling directory outside the repository')
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if root == REPO or root.is_relative_to(REPO) or (root.exists() and (not root.is_dir() or any(root.iterdir()))):
        parser.error('Choose a new empty sibling workspace; do not overwrite existing records')
    root.mkdir(parents=True, exist_ok=True)
    for filename in ('experiment.py', 'evaluate.py', 'data.csv'):
        shutil.copyfile(REPO / 'examples/project-runner' / filename, root / filename)
    (root / 'config.json').write_text(json.dumps({'data': 'data.csv'}), encoding='utf-8')
    records = root / 'records'
    records.mkdir()
    sys.path.insert(0, str(REPO / 'scripts'))
    from rds_project import ProjectStore
    results = {}
    for arm in ('control', 'treatment'):
        output = 'outputs/' + arm + '.json'
        argv = [sys.executable, '-B', str(REPO / 'scripts/rds_cli.py'), '--root', str(root),
                'exec', '--name', arm, '--timeout', '10', '--bind', 'data=data.csv',
                '--bind', 'config=config.json', '--output', output, '--json', '--',
                sys.executable, '-B', 'experiment.py', '--arm', arm, '--output', output]
        returned = []
        for label in ('first', 'identical'):
            proc = subprocess.run(argv, cwd=REPO, capture_output=True, text=True, encoding='utf-8', timeout=30)
            (records / (arm + '-' + label + '.json')).write_text(json.dumps({
                'argv': argv, 'exit_code': proc.returncode, 'stdout': proc.stdout, 'stderr': proc.stderr
            }, indent=2), encoding='utf-8')
            if proc.returncode:
                raise RuntimeError('CLI failed; inspect ' + str(records / (arm + '-' + label + '.json')))
            value = json.loads(proc.stdout)
            returned.append(value)
            state = ProjectStore(value['job_root']).snapshot()
            if label == 'first':
                before = state
            else:
                if (value['status'] != 'EXISTING_JOB' or value['execution_started'] is not False
                        or value['receipt'] != returned[0]['receipt'] or state != before):
                    raise RuntimeError('Identical-call recovery changed original paid-run state')
        first = returned[0]
        if first['status'] != 'SUCCEEDED' or len(before['runs']) != 1 or len(before['receipts']) != 1:
            raise RuntimeError('The fixture did not complete exactly one original attempt')
        metric = json.loads((Path(first['job_root']) / output).read_text(encoding='utf-8'))
        results[arm] = {'metric': metric, 'receipt_sha256': first['receipt']['sha256'],
                        'attempt_id': first['receipt']['attempt_id'], 'reuse': returned[1]['status']}
    summary = {'hypothesis': 'A line has lower recorded mean squared error than a constant on these six points',
               'results': results,
               'observed_on_fixture': results['treatment']['metric']['mse'] < results['control']['metric']['mse'],
               'scientific_support': 'UNKNOWN',
               'scope': 'Existing finite CPU fixture; no causal, generalization or agent-policy-gain claim'}
    (root / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    print(json.dumps(summary, indent=2))


if __name__ == '__main__':
    main()
