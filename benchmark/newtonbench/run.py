"""Orchestrate trials: every (trial, task, arm) runs as its own process with a hard wall budget.

    python -B run.py --phase formal --trials 2 --parallel 6 [--arms vanilla code_assisted compass]

A finished trial writes ``trial.json`` and is never rerun. A trial that exceeds the wall budget or exits without a
``trial.json`` is written as TIMEOUT or CRASHED and scored as a failure. Only a trial interrupted by the
orchestrator itself stopping (no exit status: the host slept, the run was cancelled) is restarted, and that
interruption is logged in ``incomplete.jsonl``.
"""
import argparse
import concurrent.futures
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common  # noqa: E402

TRIAL_WALL = 4 * 3600
VENV_PYTHON = common.EVAL / 'venv' / 'bin' / 'python'
ARMS = ['vanilla', 'code_assisted', 'compass']


def job_dir(runs, task, arm, trial):
    return runs / task['module'] / task['difficulty'] / task['version'] / f'{arm}-t{trial}'


def write_failure(out, job, status, detail):
    task, arm, trial = job
    row = {'schema': 'rds-newtonbench-trial-v1', 'arm': arm, 'trial': trial, 'task': task,
           'task_id': common.task_id(task['module'], task['difficulty'], task['version']), 'status': status,
           'failure': detail, 'query_budget': common.QUERY_BUDGET, 'contaminated': False, 'submissions': {},
           'scientific_support': 'UNKNOWN'}
    row['row_sha256'] = common.sha256_json(row)
    (out / 'trial.json').write_text(json.dumps(row, indent=1), encoding='utf-8')


def run_job(runs, job):
    task, arm, trial = job
    out = job_dir(runs, task, arm, trial)
    if (out / 'trial.json').exists():
        return 'DONE_BEFORE', out
    if out.exists():
        stamp = time.strftime('%Y%m%dT%H%M%S')
        out.rename(out.with_name(out.name + '.incomplete-' + stamp))
        with open(runs / 'incomplete.jsonl', 'a', encoding='utf-8') as log:
            log.write(json.dumps({'job': str(out), 'moved_to': out.name + '.incomplete-' + stamp, 'at': stamp}) + '\n')
    out.parent.mkdir(parents=True, exist_ok=True)
    argv = [str(VENV_PYTHON), '-B', str(HERE / 'trial.py'), '--arm', arm, '--module', task['module'],
            '--difficulty', task['difficulty'], '--version', task['version'], '--trial', str(trial), '--out', str(out)]
    started = time.monotonic()
    with open(out.with_name(out.name + '.log'), 'wb') as log:
        proc = subprocess.Popen(argv, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=HERE)
        try:
            code = proc.wait(timeout=TRIAL_WALL)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
            out.mkdir(parents=True, exist_ok=True)
            write_failure(out, job, 'TIMEOUT', {'wall_seconds': TRIAL_WALL})
            return 'TIMEOUT', out
    if not (out / 'trial.json').exists():
        out.mkdir(parents=True, exist_ok=True)
        tail = out.with_name(out.name + '.log').read_bytes()[-3000:].decode('utf-8', 'replace')
        write_failure(out, job, 'CRASHED', {'exit_code': code, 'log_tail': tail,
                                            'wall_seconds': round(time.monotonic() - started, 1)})
        return 'CRASHED', out
    return 'OK', out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--phase', required=True, choices=['pilot', 'formal'])
    parser.add_argument('--trials', type=int, required=True)
    parser.add_argument('--parallel', type=int, default=6)
    parser.add_argument('--arms', nargs='*', default=ARMS, choices=ARMS)
    parser.add_argument('--tasks', nargs='*', help='task ids (module/difficulty/version); default: the phase sample')
    args = parser.parse_args(argv)
    tasks = common.pilot_tasks() if args.phase == 'pilot' else common.formal_tasks()
    if args.tasks:
        tasks = [t for t in tasks if common.task_id(t['module'], t['difficulty'], t['version']) in args.tasks]
    runs = common.EVAL / 'runs' / args.phase
    runs.mkdir(parents=True, exist_ok=True)
    manifest = {'phase': args.phase, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'rds_commit': common.git_head(HERE), 'newtonbench_commit': common.git_head(common.NEWTONBENCH),
                'expected_newtonbench_commit': common.NEWTONBENCH_COMMIT, 'trials': args.trials, 'arms': args.arms,
                'tasks': [common.task_id(t['module'], t['difficulty'], t['version']) for t in tasks],
                'parallel': args.parallel, 'trial_wall_seconds': TRIAL_WALL}
    with open(runs / 'manifests.jsonl', 'a', encoding='utf-8') as log:
        log.write(json.dumps(manifest) + '\n')
    if manifest['newtonbench_commit'] != common.NEWTONBENCH_COMMIT:
        sys.exit('NewtonBench checkout is not at the pinned commit')
    jobs = [(task, arm, trial) for trial in range(1, args.trials + 1) for task in tasks for arm in args.arms]
    print(json.dumps({'jobs': len(jobs), 'runs': str(runs)}), flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(run_job, runs, job): job for job in jobs}
        for future in concurrent.futures.as_completed(futures):
            task, arm, trial = futures[future]
            status, out = future.result()
            print(json.dumps({'at': time.strftime('%H:%M:%S'), 'status': status, 'arm': arm, 'trial': trial,
                              'task': common.task_id(task['module'], task['difficulty'], task['version'])}), flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
