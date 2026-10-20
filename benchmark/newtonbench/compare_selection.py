"""EIG-selected measurements vs random measurements on the same candidates.

The five candidate laws from the committed llm-verify run are filtered by
replay after each measurement. One arm picks the next input by EIG; the
other picks uniformly at random from the remaining pool. The truth is
generated inside this script, so no agent sees it.

    python -B rds_subset/compare_selection.py out.json
"""
import json
import random
import sys
from pathlib import Path

RDS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RDS / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import rds_compass_model as model
from eig_advisor import choose

C = 4.78e-02
CANDIDATES = [
    '4.78e-02 * (current1 + current2) ** (3 / 2) / distance ** 3',
    '4.78e-02 * (current1 * current2) ** (3 / 2) / distance ** 3',
    '4.78e-02 * (current1 + current2) / distance ** 3',
    '4.78e-02 * current1 * current2 / distance ** 3',
    '4.78e-02 * ((current1 + current2) / distance) ** (3 / 2)',
]
TRUTH = '4.78e-02 * (current1 + current2) ** (3 / 2) / distance ** 3'
POOL = [{'id': f'c{m}_{d[0]}_{d[1]}',
         'inputs': {'current1': str(m), 'current2': str(d[0]), 'distance': str(d[1])}, 'cost': '1'}
        for m in range(1, 6) for d in [(1, 1), (2, 2), (3, 3), (4, 2), (5, 3)]]
TOL = {'y': (model.exact(0), model.exact(3) / 100)}


def value(point):
    env = {k: model.exact(v) for k, v in point['inputs'].items()}
    hyp = model.compile_hypothesis({'id': 't', 'predict': {'y': TRUTH}}, list(env), ['y'])
    return model.text(model.predict(hyp, env)[0]['y'])


def replay_ok(expr, rows):
    try:
        hyp = model.compile_hypothesis({'id': 'h', 'predict': {'y': expr}},
                                       ['current1', 'current2', 'distance'], ['y'])
        parsed = [{'id': r['id'],
                   'inputs': {k: model.exact(v) for k, v in r['inputs'].items()},
                   'outputs': {k: model.exact(v) for k, v in r['outputs'].items()}} for r in rows]
        return model.replay(hyp, parsed, TOL)['status'] == 'CONSISTENT'
    except (ValueError, KeyError, TypeError):
        return False


def run(pick, seed=None):
    rng = random.Random(seed)
    p0 = {'inputs': {'current1': '2', 'current2': '3', 'distance': '2'}}
    p1 = {'inputs': {'current1': '4', 'current2': '1', 'distance': '3'}}
    rows = [{'id': 'p0', **p0, 'outputs': {'y': value(p0)}},
            {'id': 'p1', **p1, 'outputs': {'y': value(p1)}}]
    observed = [r['inputs'] for r in rows]
    survivors = [c for c in CANDIDATES if replay_ok(c, rows)]
    used = []
    while len(survivors) > 1:
        pool = [c for c in POOL if c['inputs'] not in observed]
        if not pool:
            break
        if pick == 'eig':
            rivals = [{'id': f'r{i}', 'predict': e} for i, e in enumerate(survivors)]
            ranking = choose(rows, pool, rivals, TOL)
            if not ranking['ranked']:
                break
            chosen = next(c for c in pool if c['id'] == ranking['ranked'][0]['id'])
        else:
            chosen = rng.choice(pool)
        y = value(chosen)
        rows.append({'id': chosen['id'], 'inputs': chosen['inputs'], 'outputs': {'y': y}})
        observed.append(chosen['inputs'])
        survivors = [c for c in survivors if replay_ok(c, rows)]
        used.append(chosen['id'])
    return {'survivors': survivors, 'points_used': len(rows), 'extra': used,
            'unique_correct': survivors == [TRUTH]}


def main(out):
    eig = run('eig')
    random_runs = [run('random', seed=s) for s in range(30)]
    result = {'eig': eig,
              'random_success': sum(r['unique_correct'] for r in random_runs),
              'random_trials': len(random_runs),
              'random_points_mean': sum(r['points_used'] for r in random_runs) / len(random_runs),
              'random_survivor_counts': [len(r['survivors']) for r in random_runs]}
    Path(out).write_text(json.dumps(result, indent=1), encoding='utf-8')
    print(json.dumps(result, indent=1))


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'compare-selection.json')
