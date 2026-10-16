"""Pre-registered analysis of the NewtonBench comparison (see PREREGISTRATION.md, including amendment 1).

    python -B analyze.py --phase formal --out results/newtonbench-compass.json --table results/per-task.md

Unit of analysis: the cell (module, difficulty, law version). Its two strata (noise 0 and noise 0.01) share one
law, so the primary test pools them: per arm and cell, accuracy is the mean of NewtonBench's ``exact_accuracy``
(its LLM-judge symbolic equivalence) over the cell's trials in both strata. Within one stratum, the same test
runs over that stratum's trials; the noise-free stratum test is the originally registered primary test. A
failed, timed-out, crashed, missing or contaminated trial scores 0. Paired comparisons use an exact two-sided
sign test over cells with a nonzero difference and a paired bootstrap 95% CI of the mean difference (10,000
cell resamples, fixed seed).
"""
import argparse
import json
import math
import random
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common  # noqa: E402

SUBMISSIONS = ['vanilla', 'code_assisted', 'compass', 'compass_fallback']
SOURCE = {'vanilla': 'vanilla', 'code_assisted': 'code_assisted', 'compass': 'compass', 'compass_fallback': 'compass'}
PRIMARY = ('compass', 'code_assisted')
SECONDARY = [('compass', 'vanilla'), ('compass_fallback', 'code_assisted'), ('compass_fallback', 'vanilla'),
             ('code_assisted', 'vanilla')]
# A priori: a law the Compass dialect can state on the declared design (rational operations, integer powers,
# sqrt that is exact on the grid, decimal constants within tolerance). Fixed in PREREGISTRATION.md; a property
# of the law, so the same in both noise strata.
EXPRESSIBLE = {'m0_gravity/easy/v0', 'm0_gravity/medium/v0', 'm1_coulomb_force/easy/v1',
               'm1_coulomb_force/medium/v0', 'm2_magnetic_force/easy/v0', 'm3_fourier_law/easy/v2',
               'm6_underdamped_harmonic/easy/v1', 'm6_underdamped_harmonic/hard/v1', 'm8_sound_speed/easy/v2',
               'm8_sound_speed/medium/v0', 'm9_hooke_law/medium/v1', 'm9_hooke_law/hard/v0',
               'm11_heat_transfer/easy/v1', 'm11_heat_transfer/medium/v2'}
FAILED = ('FAILED', 'TIMEOUT', 'CRASHED', 'MISSING')
BOOTSTRAP = 10000
BOOT_SEED = 20261015


def cell_of(key):
    return '/'.join(key.split('/')[:3])


def stratum_of(key):
    parts = key.split('/')
    return parts[3] if len(parts) > 3 else 'noise0'


def sign_test(diffs):
    pos, neg = sum(d > 0 for d in diffs), sum(d < 0 for d in diffs)
    n = pos + neg
    if n == 0:
        return {'positive': 0, 'negative': 0, 'ties': len(diffs), 'p_two_sided': 1.0}
    k = min(pos, neg)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return {'positive': pos, 'negative': neg, 'ties': len(diffs) - n, 'p_two_sided': min(1.0, 2 * tail)}


def bootstrap_ci(diffs, seed=BOOT_SEED):
    if not diffs:
        return [None, None]
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(BOOTSTRAP))
    return [means[int(0.025 * BOOTSTRAP)], means[int(0.975 * BOOTSTRAP) - 1]]


def wilson(successes, n, z=1.959964):
    if n == 0:
        return [None, None]
    p = successes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return [max(0.0, centre - half), min(1.0, centre + half)]


def load(runs):
    rows = []
    for path in sorted(runs.rglob('trial.json')):
        if any('.incomplete-' in part or '.superseded-' in part for part in path.parts):
            continue
        rows.append(json.loads(path.read_text(encoding='utf-8')))
    # Every job a manifest scheduled counts: one with no trial.json at all is MISSING and scores as a failure.
    have = {(row['task_id'], row['arm'], row['trial']) for row in rows}
    manifests = runs / 'manifests.jsonl'
    lines = manifests.read_text(encoding='utf-8').splitlines() if manifests.exists() else []
    for line in lines:
        m = json.loads(line)
        for key in m['tasks']:
            for arm in m['arms']:
                for trial in range(1, m['trials'] + 1):
                    if (key, arm, trial) not in have:
                        have.add((key, arm, trial))
                        rows.append({'arm': arm, 'task_id': key, 'trial': trial, 'status': 'MISSING',
                                     'submissions': {}, 'contaminated': False})
    return rows


def scored(rows):
    """One record per (submission, task, trial)."""
    out = []
    for row in rows:
        for name in SUBMISSIONS:
            if SOURCE[name] != row['arm']:
                continue
            sub = (row.get('submissions') or {}).get(name)
            failed = row.get('status') in FAILED or sub is None
            contaminated = bool(row.get('contaminated')) or bool((sub or {}).get('contaminated'))
            ev = (sub or {}).get('evaluation') or {}
            correct = (not failed and not contaminated and ev.get('exact_accuracy') == 1.0)
            rmsle = ev.get('rmsle') if not failed and not contaminated else None
            out.append({'submission': name, 'task_id': row['task_id'], 'cell': cell_of(row['task_id']),
                        'stratum': stratum_of(row['task_id']), 'trial': row['trial'], 'status': row.get('status'),
                        'compass_status': row.get('compass_status'), 'correct': int(correct),
                        'rmsle': rmsle if isinstance(rmsle, (int, float)) and math.isfinite(rmsle) else None,
                        'certified': (sub or {}).get('certified'), 'fallback_used': (sub or {}).get('fallback_used'),
                        'contaminated': contaminated, 'failed': failed, 'queries': row.get('queries'),
                        'law': (sub or {}).get('law'), 'judge_msg': ev.get('symbolic_msg'),
                        'eval_error': ev.get('error')})
    return out


def per_unit(records, unit):
    """(submission, unit) -> accuracy over the unit's trials and median RMSLE (missing = +inf)."""
    table = {}
    for name in SUBMISSIONS:
        for key in sorted({r[unit] for r in records}):
            mine = [r for r in records if r['submission'] == name and r[unit] == key]
            if not mine:
                continue
            rmsles = [r['rmsle'] if r['rmsle'] is not None else math.inf for r in mine]
            table[(name, key)] = {
                'n': len(mine), 'accuracy': statistics.fmean(r['correct'] for r in mine),
                'median_rmsle': statistics.median(rmsles),
                'trials': [{'task_id': r['task_id'], 'trial': r['trial'], 'correct': r['correct'], 'rmsle': r['rmsle'],
                            'certified': r['certified'], 'status': r['status'], 'compass_status': r['compass_status']}
                           for r in sorted(mine, key=lambda r: (r['task_id'], r['trial']))]}
    return table


def compare(table, a, b, units):
    both = [u for u in units if (a, u) in table and (b, u) in table]
    diffs = [table[(a, u)]['accuracy'] - table[(b, u)]['accuracy'] for u in both]
    rm = []
    for u in both:
        x, y = table[(a, u)]['median_rmsle'], table[(b, u)]['median_rmsle']
        rm.append(0 if x == y else (1 if x < y else -1))  # +1: a has the lower (better) RMSLE
    acc = {'units': len(both), 'mean_accuracy': {a: _mean([table[(a, u)]['accuracy'] for u in both]),
                                                  b: _mean([table[(b, u)]['accuracy'] for u in both])},
           'mean_difference': _mean(diffs), 'bootstrap_95ci': bootstrap_ci(diffs), 'sign_test': sign_test(diffs)}
    acc['significant'] = bool(acc['sign_test']['p_two_sided'] < 0.05 and acc['bootstrap_95ci'][0] is not None
                              and (acc['bootstrap_95ci'][0] > 0 or acc['bootstrap_95ci'][1] < 0))
    return {'a': a, 'b': b, 'accuracy': acc, 'rmsle_sign_test': sign_test(rm),
            'rmsle_note': 'per-cell median RMSLE over trials, missing/failed = +inf; positive = a lower (better)'}


def _mean(values):
    return statistics.fmean(values) if values else None


def official_rmsle(records, name):
    """NewtonBench-style RMSLE: finite values only, modified z-score outliers (>3.5) masked per task, then mean."""
    kept = []
    for task in sorted({r['task_id'] for r in records}):
        values = [r['rmsle'] for r in records if r['submission'] == name and r['task_id'] == task and r['rmsle'] is not None]
        if not values:
            continue
        med = statistics.median(values)
        mad = statistics.median(abs(v - med) for v in values)
        kept += values if mad == 0 else [v for v in values if abs(0.6745 * (v - med) / mad) <= 3.5]
    return {'mean_finite_rmsle': _mean(kept), 'finite_values': len(kept),
            'submissions': sum(1 for r in records if r['submission'] == name)}


def false_certification(records, cells):
    compass_rows = [r for r in records if r['submission'] == 'compass' and r['cell'] in cells]
    certified = [r for r in compass_rows if r['certified']]
    wrong = [r for r in certified if not r['correct']]
    return {'certified': len(certified), 'certified_wrong': len(wrong), 'compass_trials': len(compass_rows),
            'rate': (len(wrong) / len(certified)) if certified else None,
            'wilson_95ci': wilson(len(wrong), len(certified))}


def _tally(values):
    out = {}
    for v in values:
        out[str(v)] = out.get(str(v), 0) + 1
    return out


def descriptive(rows, records):
    out = {}
    for arm in ('vanilla', 'code_assisted', 'compass'):
        mine = [row for row in rows if row['arm'] == arm]
        entry = {
            'trials': len(mine), 'status': _tally(row.get('status') for row in mine),
            'compass_status': _tally(row.get('compass_status') for row in mine) if arm == 'compass' else None,
            'contaminated': sum(bool(row.get('contaminated')) for row in mine),
            'mean_queries': _mean([row['queries'] for row in mine if isinstance(row.get('queries'), int)]),
            'over_budget_requests': sum(row.get('over_budget_requests') or 0 for row in mine),
            'hit_budget': sum(1 for row in mine if (row.get('queries') or 0) >= common.QUERY_BUDGET),
            'mean_wall_seconds': _mean([row['wall_seconds'] for row in mine if row.get('wall_seconds')]),
            'codex_calls': sum(len(row.get('agent_calls') or []) + len(row.get('jump_calls') or [])
                               + (1 if row.get('fallback_call') else 0) + len(row.get('judge_calls') or [])
                               for row in mine),
            'chat_tool_events': sum(row.get('chat_tool_events') or 0 for row in mine)}
        if arm == 'compass':
            entry['fallback_used'] = sum(1 for r in records if r['submission'] == 'compass_fallback' and r['fallback_used'])
            entry['retried_attempts'] = sum(max(0, len(row.get('attempts') or []) - 1) for row in mine)
        out[arm] = entry
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--phase', default='formal')
    parser.add_argument('--out', required=True)
    parser.add_argument('--table', required=True)
    args = parser.parse_args(argv)
    runs = common.EVAL / 'runs' / args.phase
    rows = load(runs)
    records = scored(rows)
    strata = sorted({r['stratum'] for r in records})
    views = {'pooled': records, **{s: [r for r in records if r['stratum'] == s] for s in strata}}
    comparisons, fcr, rmsle, desc = {}, {}, {}, {}
    for label, view in views.items():
        table = per_unit(view, 'cell')
        cells = sorted({r['cell'] for r in view})
        subsets = {'all': cells, 'expressible': [c for c in cells if c in EXPRESSIBLE],
                   'not_expressible': [c for c in cells if c not in EXPRESSIBLE]}
        comparisons[label] = {name: {'cells': len(subset), 'primary': compare(table, *PRIMARY, subset),
                                     'secondary': [compare(table, a, b, subset) for a, b in SECONDARY]}
                              for name, subset in subsets.items()}
        fcr[label] = {name: false_certification(view, set(subset)) for name, subset in subsets.items()}
        rmsle[label] = {n: official_rmsle(view, n) for n in SUBMISSIONS}
        view_rows = rows if label == 'pooled' else [row for row in rows if stratum_of(row['task_id']) == label]
        desc[label] = descriptive(view_rows, view)
    task_table = per_unit(records, 'task_id')
    body = {'schema': 'rds-newtonbench-comparison-v2', 'generated_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
            'phase': args.phase, 'rds_commit': common.git_head(HERE),
            'newtonbench': {'repository': 'https://github.com/HKUST-KnowComp/NewtonBench', 'license': 'MIT',
                            'commit': common.git_head(common.NEWTONBENCH)},
            'model': {'cli': 'codex', 'model': common.MODEL, 'reasoning_effort': common.EFFORT,
                      'service_tier': common.TIER, 'judge': common.MODEL},
            'design': {'system': 'vanilla_equation', 'strata': strata, 'noise_level': common.NOISE_LEVEL,
                       'query_budget': common.QUERY_BUDGET, 'compass_design_points': common.DESIGN_POINTS,
                       'tolerance_noise_free': {'rel': common.REL_TOLERANCE},
                       'tolerance_noisy': f'rel {common.NOISE_SIGMAS} x noise, abs {common.NOISE_SIGMAS} x module floor',
                       'cells': len({r['cell'] for r in records}),
                       'expressible_cells': len({r['cell'] for r in records} & EXPRESSIBLE)},
            'primary': comparisons['pooled']['all']['primary'],
            'registered_noise_free_primary': (comparisons.get('noise0') or {}).get('all', {}).get('primary'),
            'comparisons': comparisons, 'false_certification': fcr, 'official_style_rmsle': rmsle,
            'descriptive': desc,
            'per_task': [{'submission': n, 'task_id': t, **v} for (n, t), v in sorted(task_table.items())],
            'trials': [{k: row.get(k) for k in ('arm', 'task_id', 'trial', 'status', 'compass_status', 'queries',
                                                'wall_seconds', 'contaminated', 'row_sha256', 'ledger_head',
                                                'report_sha256', 'query_chain_head', 'transcript_sha256')}
                       | {'receipts': row.get('receipts'),
                          'submissions': {n: {'law': s.get('law'), 'certified': s.get('certified'),
                                              'fallback_used': s.get('fallback_used'),
                                              'evaluation': {k: (s.get('evaluation') or {}).get(k) for k in
                                                             ('exact_accuracy', 'rmsle', 'symbolic_equivalent',
                                                              'symbolic_msg', 'error', 'eval_seed')}}
                                          for n, s in (row.get('submissions') or {}).items()}}
                       for row in sorted(rows, key=lambda r: (r['task_id'], r['arm'], r['trial']))],
            'scientific_support': 'UNKNOWN'}
    body['bundle_sha256'] = common.sha256_json(body)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(body, indent=1, default=str) + '\n', encoding='utf-8')
    Path(args.table).write_text(render_table(task_table, rows), encoding='utf-8')
    print(json.dumps({'primary': body['primary'], 'registered_noise_free_primary': body['registered_noise_free_primary'],
                      'false_certification': {k: v['all'] for k, v in fcr.items()},
                      'bundle_sha256': body['bundle_sha256']}, indent=1))
    return 0


def _cell(entry):
    if entry is None:
        return '—'
    marks = ''.join('✓' if t['correct'] else '✗' for t in entry['trials'])
    rm = entry['median_rmsle']
    return f"{marks} ({'inf' if rm == math.inf else f'{rm:.3g}'})"


def render_table(table, rows):
    status = {(r['task_id'], r['trial']): r.get('compass_status') or r.get('status') for r in rows if r['arm'] == 'compass'}
    keys = sorted({t for (_, t) in table}, key=lambda k: (stratum_of(k), k))
    lines = ['# Per-task results', '',
             'Each cell: one mark per trial (✓ = NewtonBench judge says equivalent, ✗ otherwise), then the per-task '
             'median RMSLE (inf = no finite score). E = expressible in the Compass dialect (a priori).', '']
    for stratum in sorted({stratum_of(k) for k in keys}):
        lines += [f'## Stratum {stratum}', '',
                  '| Task | E | vanilla | code_assisted | compass | compass_fallback | Compass end states |',
                  '|---|---|---|---|---|---|---|']
        for t in (k for k in keys if stratum_of(k) == stratum):
            ends = ', '.join(str(status[(t, i)]) for i in sorted({i for (tt, i) in status if tt == t}))
            lines.append(f"| `{t}` | {'E' if cell_of(t) in EXPRESSIBLE else ''} | " +
                         ' | '.join(_cell(table.get((n, t))) for n in SUBMISSIONS) + f' | {ends} |')
        lines.append('')
    return '\n'.join(lines)


if __name__ == '__main__':
    sys.exit(main())
