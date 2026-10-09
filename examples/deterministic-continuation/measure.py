"""Compare efficient advance orchestration with one bounded judgment pass.

The unchanged public six-row fixture has a FALSE goal. No model is invoked.
Three repetitions per arm and all outcomes are retained; do not retry failures.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time

REPO = Path(__file__).resolve().parents[2]


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', required=True, help='New empty sibling directory; original evidence is retained')
    args = parser.parse_args()
    root = Path(args.workspace).resolve()
    if root == REPO or root.is_relative_to(REPO) or root.exists() and any(root.iterdir()):
        parser.error('Choose a new empty workspace outside the checkout')
    root.mkdir(parents=True, exist_ok=True)
    source = REPO / 'examples/owned-advisor/prepare.py'
    spec = importlib.util.spec_from_file_location('owned_example', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    identity = {p.relative_to(REPO).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted((REPO / 'scripts').glob('*.py'))}
    identity.update({str(p.relative_to(REPO).as_posix()): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in [source, REPO / 'examples/project-runner/prepare.py', Path(__file__)]})
    protocol = {'schema': 1, 'repetitions_per_arm': 3, 'order': [['advance', 'drive'], ['drive', 'advance'], ['advance', 'drive']],
                'workload': 'unchanged public owned-advisor six-row CPU fixture',
                'goal': 'treatment_mse <= 0.01', 'expected_goal': 'FALSE',
                'worker_budget': 'original contract caps and commands; unchanged in both arms',
                'controller_allowance_seconds': 2, 'max_worker_steps': 8,
                'advance_stop': 'consume the returned Advisor and stop when selected_run is null; no redundant final call',
                'model_calls': 0, 'scientific_gain': 'UNMEASURED',
                'environment': {'python': sys.version, 'platform': platform.platform()},
                'sources': identity}
    write(root / 'protocol.json', protocol)  # Freeze before either arm executes.
    sys.path.insert(0, str(REPO / 'scripts'))
    from rds_project import ProjectStore
    from rds_owned_advisor import review
    observations = []
    for pair, arms in enumerate(protocol['order']):
        for arm in arms:
            project = root / f'{pair + 1}-{arm}'
            module.prepare(project)
            store = ProjectStore(project)
            records = []
            env = {**os.environ, 'RDS_USAGE_DB': str(project / 'usage.sqlite3')}
            def call(arguments):
                start = time.perf_counter()
                completed = subprocess.run([sys.executable, '-B', str(REPO / 'scripts/rds_cli.py'),
                    '--root', str(project), *arguments], capture_output=True, encoding='utf-8', env=env, timeout=40)
                record = {'argv': arguments, 'returncode': completed.returncode,
                          'elapsed_seconds': time.perf_counter() - start,
                          'stdout': completed.stdout, 'stderr': completed.stderr}
                records.append(record)
                write(project / 'commands.json', records)
                if completed.returncode:
                    raise RuntimeError(completed.stderr or completed.stdout)
                return json.loads(completed.stdout)
            observed = {'pair': pair + 1, 'arm': arm, 'result': 'UNVERIFIED'}
            try:
                call(['project', 'init', '--contract', str(project / 'contract.json')])
                final = None
                for _ in range(8 if arm == 'advance' else 1):
                    argv = ['project', 'advance', '--brief'] if arm == 'advance' else [
                        'project', 'drive', '--until-judgment', '--controller-wall-seconds', '2', '--max-steps', '8']
                    final = call(argv)
                    if arm == 'drive' or final.get('selected_run') is None:
                        break
                # Evaluation is timed separately from either public control path.
                evaluation_start = time.perf_counter()
                snapshot = store.snapshot()
                advice = review(store)
                goal = next(r['search']['selection_review']['goal']['status'] for r in advice['recommendations']
                            if r.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
                receipts = snapshot['receipts']
                observed.update(result='PASS' if len(receipts) == 2 and goal == 'FALSE' and
                    all(r['run_status'] == 'SUCCEEDED' for r in receipts) and advice['selected_run'] is None and
                    (arm == 'advance' or final['status'] == 'JUDGMENT_REQUIRED') else 'FAIL',
                    goal_status=goal, final_status=final.get('status'), next_move=advice['next_move'],
                    executions=[{k: r[k] for k in ('run_id', 'attempt_id', 'sha256', 'run_status', 'resources')} for r in receipts],
                    contract_sha256=snapshot['contract_sha256'], budget=snapshot['budget'],
                    controller_wall_seconds=final.get('controller_wall_seconds'),
                    evaluation_elapsed_seconds=time.perf_counter() - evaluation_start,
                    scientific_support='UNKNOWN')
                write(project / 'final-state.json', snapshot)
                write(project / 'final-advice.json', advice)
            except Exception as exc:
                observed.update(result='FAIL', error=f'{type(exc).__name__}: {exc}')
            control = [r for r in records if r['argv'][1] in ('advance', 'drive')]
            observed.update(control_invocations=len(control),
                            control_cli_elapsed_seconds=sum(r['elapsed_seconds'] for r in control),
                            setup_cli_elapsed_seconds=sum(r['elapsed_seconds'] for r in records if r not in control))
            observations.append(observed)
            write(root / 'observations.json', observations)
    aggregate = {}
    for arm in ('advance', 'drive'):
        rows = [r for r in observations if r['arm'] == arm]
        elapsed = [r['control_cli_elapsed_seconds'] for r in rows]
        aggregate[arm] = {'pass_count': sum(r['result'] == 'PASS' for r in rows),
                          'control_invocations': [r['control_invocations'] for r in rows],
                          'cli_elapsed_seconds': {'min': min(elapsed), 'median': statistics.median(elapsed), 'max': max(elapsed)}}
    report = {'protocol': protocol, 'observations': observations, 'aggregate': aggregate,
              'research_policy_gain_measured': False, 'model_or_token_savings_measured': False,
              'limits': ['Three local deterministic repetitions per arm; no inferential speedup claim',
                         'Controller ledger, full CLI elapsed and separate evaluator elapsed are distinct',
                         'Original worker CPU can be unknown and charged by estimate; do not substitute wall time']}
    write(root / 'report.json', report)
    print(json.dumps(aggregate, indent=2))
    return 0 if all(r['result'] == 'PASS' for r in observations) else 1


if __name__ == '__main__':
    raise SystemExit(main())
