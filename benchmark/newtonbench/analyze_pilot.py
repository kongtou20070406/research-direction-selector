"""Amendment 6 PILOT: describe the transcendental-dialect Compass trials. Descriptive only: no tests, no pooling.

    python -B analyze_pilot.py --out results/pilot-dialect.json --table results/pilot-dialect.md

Correctness is NewtonBench's own judgement (``exact_accuracy`` from ``evaluate_law``), scored strictly as
registered. A certification (``GOAL_IDENTIFIED`` with a translatable law) only says the law agrees with the
receipts within tolerance; it is counted apart from correctness, and a certified wrong law is a false
certification. Trials the budget guard never launched (``NOT_RUN_BUDGET``) are counted and listed, and are left
out of every rate. The smoke trial is reported apart and counted nowhere else. For reference the table cites, on
the same tasks, the registered baselines and the registered and Amendment 5 corrected Compass trials (N = 2).
"""
import argparse
import json
import time
from pathlib import Path

import analyze
import common
from analyze import cell_of, stratum_of

HERE = Path(__file__).resolve().parent
NOT_RUN = 'NOT_RUN_BUDGET'
SUBSETS = ['newly_expressible', 'still_not_expressible', 'regression']
VIEWS = ['pooled', 'noise0', 'noise0.01']


def subset_of(task_id):
    cell = cell_of(task_id)
    if cell in common.PILOT_REGRESSION:
        return 'regression'
    return 'still_not_expressible' if cell in common.NOT_EXPRESSIBLE_TRANSCENDENTAL else 'newly_expressible'


def _usage(job):
    """Known input / cached / output over every jump and fallback call of every attempt (as reported)."""
    job = Path(job)
    calls = [json.loads((d / 'audit.json').read_text(encoding='utf-8')) if (d / 'audit.json').exists() else {}
             for d in sorted(job.glob('jumps-a*/*')) if d.is_dir()]
    record = job / 'trial.json'
    fallback = json.loads(record.read_text(encoding='utf-8')).get('fallback_call') if record.exists() else None
    if fallback:
        calls.append(fallback)
    total = {'input_tokens': 0, 'cached_input_tokens': 0, 'output_tokens': 0}
    for call in calls:
        for key in total:
            total[key] += int((call.get('usage') or {}).get(key, 0))
    return total


def pilot_rows(runs):
    rows = [r for r in analyze.load(runs) if r.get('_dir', '').find('/smoke/') < 0]
    keys = {common.task_key(t) for t in common.pilot_dialect_tasks()}
    assert {r['task_id'] for r in rows} <= keys, 'pilot rows must be pilot tasks'
    return rows


def trial_row(row):
    by = {r['submission']: r for r in analyze.scored([row])}
    own, fb = by.get('compass') or {}, by.get('compass_fallback') or {}
    certified = bool(own.get('certified'))
    job = row.get('_dir')
    spend = common.pilot_spend(job) if job else None
    agent = row.get('agent_tokens') or {}
    return {'task_id': row['task_id'], 'cell': cell_of(row['task_id']), 'stratum': stratum_of(row['task_id']),
            'subset': subset_of(row['task_id']), 'status': row.get('status'),
            'not_run': row.get('status') == NOT_RUN,
            'path': NOT_RUN if row.get('status') == NOT_RUN else analyze.compass_path(row),
            'compass_status': row.get('compass_status'), 'origin': analyze.origin_kind(row),
            'attempts': [a.get('status') for a in row.get('attempts') or []],
            'certified': certified, 'correct': int(own.get('correct', 0)),
            'false_certified': int(certified and not own.get('correct')),
            'rmsle': own.get('rmsle'), 'fallback_used': bool(fb.get('fallback_used')),
            'fallback_correct': int(fb.get('correct', 0)), 'contaminated': bool(own.get('contaminated')),
            'queries': row.get('queries'), 'wall_seconds': row.get('wall_seconds'),
            'tokens_counted': spend and spend['tokens'], 'calls': spend and spend['calls'],
            'unreported_calls': spend and spend['unreported_calls'], 'usage_known': _usage(job) if job else None,
            'agent_tokens_status': agent.get('status'), 'agent_tokens_spent_last_attempt': agent.get('spent'),
            'judge_usage': analyze._sum_usage(row.get('judge_calls') or [])}


def reference(rows, tasks):
    """Per task: mean exact accuracy of each submission, and Compass certified / false-certified counts."""
    out = {}
    for rec in analyze.scored([r for r in rows if r['task_id'] in tasks]):
        entry = out.setdefault(rec['task_id'], {})
        sub = entry.setdefault(rec['submission'], {'trials': 0, 'correct': 0, 'certified': 0, 'false_certified': 0})
        sub['trials'] += 1
        sub['correct'] += rec['correct']
        if rec['submission'] == 'compass' and rec['certified']:
            sub['certified'] += 1
            sub['false_certified'] += int(not rec['correct'])
    return out


def corrected_rows():
    formal = analyze.load(common.EVAL / 'runs' / 'formal')
    rerun = [r for r in analyze.load(common.EVAL / 'runs' / 'formal-rerun') if r['arm'] == 'compass']
    affected = lambda r: r['arm'] == 'compass' and cell_of(r['task_id']).split('/')[0] in common.RERUN_MODULES
    return formal, [r for r in formal if not affected(r)] + rerun


def _in_view(row, view):
    return view == 'pooled' or row['stratum'] == view


def aggregate(trials, refs):
    out = {}
    for view in VIEWS:
        for subset in SUBSETS + ['all']:
            sel = [t for t in trials if _in_view(t, view) and (subset == 'all' or t['subset'] == subset)]
            run = [t for t in sel if not t['not_run']]
            tasks = {t['task_id'] for t in sel}
            entry = {'tasks': len(sel), 'trials_run': len(run), 'not_run_budget': len(sel) - len(run),
                     'failed': sum(t['status'] in analyze.FAILED for t in run),
                     'compass_correct': sum(t['correct'] for t in run),
                     'certified': sum(t['certified'] for t in run),
                     'certified_correct': sum(t['certified'] and t['correct'] for t in run),
                     'false_certified': sum(t['false_certified'] for t in run),
                     'fallback_used': sum(t['fallback_used'] for t in run),
                     'fallback_correct': sum(t['fallback_correct'] for t in run),
                     'contaminated': sum(t['contaminated'] for t in run),
                     'paths': analyze._tally(t['path'] for t in run),
                     'tokens_counted': sum(t['tokens_counted'] or 0 for t in run),
                     'usage_known': {k: sum((t['usage_known'] or {}).get(k, 0) for t in run)
                                     for k in ('input_tokens', 'cached_input_tokens', 'output_tokens')},
                     'unreported_calls': sum(t['unreported_calls'] or 0 for t in run)}
            entry['compass_accuracy'] = entry['compass_correct'] / len(run) if run else None
            entry['fallback_accuracy'] = entry['fallback_correct'] / len(run) if run else None
            for name, ref in refs.items():
                acc = {}
                for sub in ('vanilla', 'code_assisted', 'compass', 'compass_fallback'):
                    cells = [ref[t][sub] for t in tasks if t in ref and sub in ref[t]]
                    trials_ = sum(c['trials'] for c in cells)
                    acc[sub] = {'trials': trials_, 'correct': sum(c['correct'] for c in cells),
                                'accuracy': sum(c['correct'] for c in cells) / trials_ if trials_ else None}
                comp = [ref[t]['compass'] for t in tasks if t in ref and 'compass' in ref[t]]
                acc['compass']['certified'] = sum(c['certified'] for c in comp)
                acc['compass']['false_certified'] = sum(c['false_certified'] for c in comp)
                entry[name] = acc
            out.setdefault(view, {})[subset] = entry
    return out


def _fmt(x):
    return '-' if x is None else f'{x:.3f}' if isinstance(x, float) else str(x)


def render(body):
    lines = ['# Amendment 6 PILOT: Compass with the transcendental dialect (descriptive only)', '',
             'PILOT. Descriptive only: no hypothesis test, not pooled with the registered or the post-hoc analysis. '
             'N = 1 trial per task; the reference columns are N = 2 per task. Correctness is NewtonBench\'s own '
             'judgement. A certification is counted apart from correctness: it says only that the law agrees with '
             'the receipts within tolerance.', '',
             f"Runs: `{body['runs']}` (RDS commit {', '.join(body['rds_commit_runs'])}). Tokens counted against "
             f"the cap: {body['budget']['spent_counted']:,} of {body['budget']['total_cap']:,} (pilot "
             f"{body['budget']['pilot_counted']:,}, smoke {body['budget']['smoke_counted']:,}).", '']
    lines += ['| Stratum | Subset | Tasks | Run | Not run | Compass correct | Certified | Certified correct | '
              'False certified | Fallback correct | Registered Compass | Corrected Compass | code_assisted | '
              'vanilla | Corrected false certified | Tokens counted |',
              '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for view in VIEWS:
        for subset in SUBSETS + ['all']:
            e = body['aggregate'][view][subset]
            reg, cor = e['registered'], e['corrected']
            lines.append(
                f"| {view} | {subset} | {e['tasks']} | {e['trials_run']} | {e['not_run_budget']} | "
                f"{e['compass_correct']}/{e['trials_run']} | {e['certified']} | {e['certified_correct']} | "
                f"{e['false_certified']} | {e['fallback_correct']}/{e['trials_run']} | "
                f"{reg['compass']['correct']}/{reg['compass']['trials']} | "
                f"{cor['compass']['correct']}/{cor['compass']['trials']} | "
                f"{reg['code_assisted']['correct']}/{reg['code_assisted']['trials']} | "
                f"{reg['vanilla']['correct']}/{reg['vanilla']['trials']} | "
                f"{cor['compass']['false_certified']}/{cor['compass']['certified']} | {e['tokens_counted']:,} |")
    lines += ['', '## Per trial', '',
              '| Task | Subset | Status | Path | Origin | Certified | Correct | False cert. | RMSLE | Fallback correct | '
              'Queries | Wall s | Tokens counted | Unreported calls | Agent cap status |',
              '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|']
    for t in body['trials']:
        lines.append(f"| {t['task_id']} | {t['subset']} | {t['status']} | {_fmt(t['path'])} | {_fmt(t['origin'])} | "
                     f"{int(t['certified'])} | {t['correct']} | {t['false_certified']} | "
                     f"{'-' if t['rmsle'] is None else format(t['rmsle'], '.3g')} | {t['fallback_correct']} | "
                     f"{_fmt(t['queries'])} | {'-' if t['wall_seconds'] is None else round(t['wall_seconds'])} | "
                     f"{_fmt(t['tokens_counted'])} | "
                     f"{_fmt(t['unreported_calls'])} | {_fmt(t['agent_tokens_status'])} |")
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--out', required=True)
    parser.add_argument('--table', required=True)
    parser.add_argument('--runs', default=str(common.EVAL / 'runs' / 'pilot-dialect'))
    args = parser.parse_args(argv)
    runs = Path(args.runs)
    rows = pilot_rows(runs)
    trials = sorted((trial_row(r) for r in rows), key=lambda t: (SUBSETS.index(t['subset']), t['task_id']))
    tasks = {t['task_id'] for t in trials}
    formal, corrected = corrected_rows()
    refs = {'registered': reference(formal, tasks), 'corrected': reference(corrected, tasks)}
    smoke = [d for d in (runs / 'smoke').rglob('compass-t*') if d.is_dir()] if (runs / 'smoke').exists() else []
    smoke_counted = sum(common.pilot_spend(d)['tokens'] for d in smoke)
    pilot_counted = sum(t['tokens_counted'] or 0 for t in trials)
    judge = [t['judge_usage'] for t in trials if not t['not_run']]
    body = {'schema': 'rds-newtonbench-pilot-dialect-v1', 'label': 'PILOT (amendment 6), descriptive only',
            'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'runs': str(runs),
            'rds_commit_analysis': common.git_head(HERE), 'rds_commit_runs': analyze._manifest_commits(runs),
            'grammar': common.PILOT_GRAMMAR, 'agent_max_tokens': common.PILOT_AGENT_MAX_TOKENS,
            'budget': {'total_cap': common.PILOT_TOTAL_TOKENS, 'pilot_counted': pilot_counted,
                       'smoke_counted': smoke_counted, 'spent_counted': pilot_counted + smoke_counted,
                       'unreported_call_counts_as': common.PILOT_UNREPORTED_CALL,
                       'judge_usage_known': {k: sum((j or {}).get(k, 0) for j in judge)
                                             for k in ('input_tokens', 'cached_input_tokens', 'output_tokens')},
                       'judge_trials_usage_unknown': sum(j is None for j in judge)},
            'regression_cells': common.PILOT_REGRESSION,
            'still_not_expressible_cells': sorted(common.NOT_EXPRESSIBLE_TRANSCENDENTAL),
            'aggregate': aggregate(trials, refs), 'trials': trials,
            'smoke': [{'dir': str(d), **common.pilot_spend(d)} for d in smoke],
            'scientific_support': 'UNKNOWN'}
    body['bundle_sha256'] = common.sha256_json(body)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(body, indent=1, default=str) + '\n', encoding='utf-8')
    Path(args.table).write_text(render(body), encoding='utf-8')
    print(json.dumps({'budget': body['budget'], 'all': body['aggregate']['pooled']}, indent=1, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
