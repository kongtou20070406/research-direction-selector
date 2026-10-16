"""Orchestrate trials: every (trial, task, arm) runs as its own process with a hard wall budget.

    python -B run.py --phase formal --trials 2 --parallel 6 [--arms vanilla code_assisted compass]

A finished trial writes ``trial.json`` and is never rerun. A trial that exceeds the wall budget or exits without a
``trial.json`` is written as TIMEOUT or CRASHED and scored as a failure. Only a trial interrupted by the
orchestrator itself stopping (no exit status: the host slept, the run was cancelled) is restarted, and that
interruption is logged in ``incomplete.jsonl``.

``--phase pilot-dialect`` (amendment 6, PILOT) runs only the Compass arm with ``--dialect transcendental``, one
trial per task, launched in the seeded order of ``common.pilot_dialect_tasks()``. A trial is launched only if the
tokens already spent in the phase plus ``PILOT_RESERVE`` for it and for every running trial fit in
``PILOT_TOTAL_TOKENS``; when the guard refuses with no trial running, the phase stops and every trial not launched
is written as NOT_RUN_BUDGET.
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
    base = runs / f"noise{task['noise']:g}" if task['noise'] else runs
    return base / task['module'] / task['difficulty'] / task['version'] / f'{arm}-t{trial}'


def write_failure(out, job, status, detail):
    task, arm, trial = job
    row = {'schema': 'rds-newtonbench-trial-v1', 'arm': arm, 'trial': trial, 'task': task,
           'task_id': common.task_key(task), 'cell': common.task_id(task['module'], task['difficulty'], task['version']),
           'status': status,
           'failure': detail, 'query_budget': common.QUERY_BUDGET, 'contaminated': False, 'submissions': {},
           'scientific_support': 'UNKNOWN'}
    row['row_sha256'] = common.sha256_json(row)
    (out / 'trial.json').write_text(json.dumps(row, indent=1), encoding='utf-8')


def run_job(runs, job, extra=()):
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
            '--difficulty', task['difficulty'], '--version', task['version'], '--noise', repr(task['noise']),
            '--trial', str(trial), '--out', str(out), *extra]
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
    parser.add_argument('--phase', required=True, choices=['pilot', 'formal', 'formal-rerun', 'pilot-dialect'])
    parser.add_argument('--trials', type=int, required=True)
    parser.add_argument('--parallel', type=int, default=6)
    parser.add_argument('--arms', nargs='*', default=ARMS, choices=ARMS)
    parser.add_argument('--tasks', nargs='*', help='task ids (module/difficulty/version[/noise0.01]); default: the '
                                                   'phase sample, both strata')
    args = parser.parse_args(argv)
    tasks = common.pilot_tasks() if args.phase == 'pilot' else common.formal_tasks()
    if args.phase == 'pilot-dialect':
        if args.trials != 1 or args.arms != ['compass']:
            sys.exit('pilot-dialect runs one Compass trial per task (amendment 6): pass --trials 1 --arms compass')
        tasks = common.pilot_dialect_tasks()
    if args.phase == 'formal-rerun':
        # Amendment 5 (post hoc): the formal tasks whose simulator ignored the Compass harness's input names.
        tasks = [t for t in tasks if t['module'] in common.RERUN_MODULES]
    if args.tasks:
        tasks = [t for t in tasks if common.task_key(t) in args.tasks]
    runs = common.EVAL / 'runs' / args.phase
    runs.mkdir(parents=True, exist_ok=True)
    manifest = {'phase': args.phase, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                'rds_commit': common.git_head(HERE), 'newtonbench_commit': common.git_head(common.NEWTONBENCH),
                'expected_newtonbench_commit': common.NEWTONBENCH_COMMIT, 'trials': args.trials, 'arms': args.arms,
                'tasks': [common.task_key(t) for t in tasks],
                'parallel': args.parallel, 'trial_wall_seconds': TRIAL_WALL,
                **({'dialect': 'transcendental', 'grammar': common.PILOT_GRAMMAR,
                    'agent_max_tokens': common.PILOT_AGENT_MAX_TOKENS, 'total_tokens': common.PILOT_TOTAL_TOKENS,
                    'reserve_per_running_trial': common.PILOT_RESERVE,
                    'unreported_call_counts_as': common.PILOT_UNREPORTED_CALL}
                   if args.phase == 'pilot-dialect' else {})}
    with open(runs / 'manifests.jsonl', 'a', encoding='utf-8') as log:
        log.write(json.dumps(manifest) + '\n')
    if manifest['newtonbench_commit'] != common.NEWTONBENCH_COMMIT:
        sys.exit('NewtonBench checkout is not at the pinned commit')
    jobs = [(task, arm, trial) for trial in range(1, args.trials + 1) for task in tasks for arm in args.arms]
    print(json.dumps({'jobs': len(jobs), 'runs': str(runs)}), flush=True)
    if args.phase == 'pilot-dialect':
        return run_guarded(runs, jobs, args.parallel)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.parallel) as pool:
        futures = {pool.submit(run_job, runs, job): job for job in jobs}
        for future in concurrent.futures.as_completed(futures):
            task, arm, trial = futures[future]
            status, out = future.result()
            print(json.dumps({'at': time.strftime('%H:%M:%S'), 'status': status, 'arm': arm, 'trial': trial,
                              'task': common.task_key(task)}), flush=True)
    return 0


def phase_spend(runs, running):
    """Tokens spent by every job directory of the phase that is not running (interrupted attempts included)."""
    dirs = [d for d in runs.rglob('compass-t*') if d.is_dir() and d not in running]
    return sum(common.pilot_spend(d)['tokens'] for d in dirs)


def run_guarded(runs, jobs, parallel):
    """Amendment 6: launch in order while the spent tokens plus a reserve per running trial fit the total cap."""
    pending, running = list(jobs), {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as pool:
        while pending or running:
            while pending and len(running) < parallel:
                task, arm, trial = pending[0]
                out = job_dir(runs, task, arm, trial)
                if not (out / 'trial.json').exists():
                    spent = phase_spend(runs, {job_dir(runs, *j) for j in running.values()})
                    if spent + common.PILOT_RESERVE * (len(running) + 1) > common.PILOT_TOTAL_TOKENS:
                        break
                running[pool.submit(run_job, runs, pending.pop(0), ['--dialect', 'transcendental'])] = (task, arm, trial)
            if not running:  # The guard refuses with nothing running: stop rule.
                spent = phase_spend(runs, set())
                for task, arm, trial in pending:
                    out = job_dir(runs, task, arm, trial)
                    out.mkdir(parents=True, exist_ok=True)
                    write_failure(out, (task, arm, trial), 'NOT_RUN_BUDGET',
                                  {'phase_spent_tokens': spent, 'reserve': common.PILOT_RESERVE,
                                   'total_tokens': common.PILOT_TOTAL_TOKENS})
                    print(json.dumps({'at': time.strftime('%H:%M:%S'), 'status': 'NOT_RUN_BUDGET', 'arm': arm,
                                      'trial': trial, 'task': common.task_key(task)}), flush=True)
                break
            done, _ = concurrent.futures.wait(running, return_when=concurrent.futures.FIRST_COMPLETED)
            for future in done:
                task, arm, trial = running.pop(future)
                status, out = future.result()
                print(json.dumps({'at': time.strftime('%H:%M:%S'), 'status': status, 'arm': arm, 'trial': trial,
                                  'task': common.task_key(task), 'trial_tokens': common.pilot_spend(out)['tokens'],
                                  'phase_spent_tokens': phase_spend(runs, {job_dir(runs, *j) for j in running.values()})}),
                      flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
