"""Exact train/test fit-trend conclusions over measured runs.

Each run reports one metric on the training set and on the test set at an input
level ``x`` (epochs, width, depth, data size, regularization ...). A change
between consecutive levels is decided only when every pair of replicate runs
says the same: all pairs move the same way beyond the declared tolerance
(``IMPROVES`` / ``WORSENS``) or all pairs agree within it
(``WITHIN_TOLERANCE``). Anything else is ``UNRESOLVED``.

Conclusions describe the measured levels only. Nothing is extrapolated, the
values stay ``INPUT_REPORTED``, and ``scientific_support`` is ``UNKNOWN``.
Underfitting needs a target the declaration does not state, so it is not
concluded.
"""
import argparse
import json
import sys

import rds_compass_model as model
from rds_compass_model import require

MAX_RUNS = 4096
MAX_LEVELS = 256
# (train step, test step) -> diagnosis; any other pair is UNRESOLVED.
DIAGNOSES = {('IMPROVES', 'WORSENS'): 'OVERFITTING', ('IMPROVES', 'IMPROVES'): 'BOTH_IMPROVE',
             ('WORSENS', 'WORSENS'): 'BOTH_WORSEN', ('WITHIN_TOLERANCE', 'WITHIN_TOLERANCE'): 'PLATEAU',
             ('WITHIN_TOLERANCE', 'WORSENS'): 'TEST_WORSENS', ('WITHIN_TOLERANCE', 'IMPROVES'): 'TEST_IMPROVES',
             ('IMPROVES', 'WITHIN_TOLERANCE'): 'TRAIN_ONLY_IMPROVES'}


def step(before, after, tol, lower_is_better):
    """How a metric moved from one level's replicate values to the next level's."""
    pairs = [(u, v) for u in before for v in after]
    if all(model.agree(u, v, tol) for u, v in pairs):
        return 'WITHIN_TOLERANCE'
    for label, better in (('IMPROVES', True), ('WORSENS', False)):
        if all(not model.agree(u, v, tol) and (v < u) == (better == lower_is_better) for u, v in pairs):
            return label
    return 'UNRESOLVED'


def _trend(labels):
    up, down = 'IMPROVES' in labels, 'WORSENS' in labels
    return 'MIXED' if up and down else 'IMPROVING' if up else 'WORSENING' if down else 'NO_DECIDED_CHANGE'


def analyze(runs, tolerances, lower_is_better):
    """``runs``: ``[{x, train, test}]``; ``tolerances``: ``{'train': (abs, rel), 'test': (abs, rel)}``."""
    require(type(lower_is_better) is bool, 'lower_is_better must be true or false')
    require(isinstance(runs, list) and 0 < len(runs) <= MAX_RUNS, f'Give 1 to {MAX_RUNS} runs')
    levels = {}
    for run in runs:
        require(isinstance(run, dict) and {'x', 'train', 'test'} <= set(run) <= {'x', 'train', 'test', 'id'},
                'A run is {x, train, test, id?}')
        levels.setdefault(model.exact(run['x']), []).append((model.exact(run['train']), model.exact(run['test'])))
    xs = sorted(levels)
    require(2 <= len(xs) <= MAX_LEVELS, f'A trend needs 2 to {MAX_LEVELS} distinct levels of x')
    values = lambda x, k: [pair[k] for pair in levels[x]]
    sign = 1 if lower_is_better else -1
    steps = []
    for a, b in zip(xs, xs[1:]):
        train = step(values(a, 0), values(b, 0), tolerances['train'], lower_is_better)
        test = step(values(a, 1), values(b, 1), tolerances['test'], lower_is_better)
        steps.append({'from': model.text(a), 'to': model.text(b), 'train': train, 'test': test,
                      'diagnosis': DIAGNOSES.get((train, test), 'UNRESOLVED')})
    onset = next((s for s in steps if s['diagnosis'] == 'OVERFITTING'), None)
    # A level is best on test unless some other level is decidedly better over every replicate pair.
    best = [x for x in xs if not any(step(values(x, 1), values(y, 1), tolerances['test'], lower_is_better)
                                     == 'IMPROVES' for y in xs if y != x)]
    span = lambda items: [model.text(min(items)), model.text(max(items))]
    return {'levels': [{'x': model.text(x), 'runs': len(levels[x]), 'train': span(values(x, 0)),
                        'test': span(values(x, 1)), 'gap': span([sign * (t - r) for r, t in levels[x]])}
                       for x in xs],
            'gap_definition': 'test - train' if lower_is_better else 'train - test',
            'steps': steps, 'train_trend': _trend([s['train'] for s in steps]),
            'test_trend': _trend([s['test'] for s in steps]),
            'overfitting': {'status': 'OBSERVED', 'from': onset['from'], 'to': onset['to']} if onset
            else {'status': 'NOT_OBSERVED_OVER_MEASURED_RANGE'},
            'best_test': {'x': [model.text(x) for x in best],
                          'position': 'NO_DECIDED_OPTIMUM' if len(best) == len(xs) else
                          'AT_RANGE_EDGE' if {xs[0], xs[-1]} & set(best) else 'INSIDE_RANGE'},
            # Unreplicated levels decide changes against the declared tolerance alone.
            'noise': 'REPLICATED' if all(len(levels[x]) > 1 for x in xs) else 'UNREPLICATED',
            'assurance': 'DESCRIPTIVE_OVER_MEASURED_LEVELS', 'extrapolation': 'NONE',
            'measurement': 'INPUT_REPORTED', 'scientific_support': 'UNKNOWN'}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Train/test fit-trend conclusions over measured runs')
    parser.add_argument('--request', required=True,
                        help='JSON {lower_is_better, tolerance: {train, test}, runs: [{x, train, test, id?}]}')
    args = parser.parse_args(argv)
    try:
        with open(args.request, encoding='utf-8') as stream:
            request = json.load(stream)
        require(isinstance(request, dict) and set(request) == {'lower_is_better', 'tolerance', 'runs'},
                'The request is {lower_is_better, tolerance, runs}')
        tolerance = request['tolerance']
        require(isinstance(tolerance, dict) and set(tolerance) == {'train', 'test'}
                and all(isinstance(v, dict) for v in tolerance.values()), 'tolerance needs train and test objects')
        result = analyze(request['runs'], {k: model.tolerance(v) for k, v in tolerance.items()},
                         request['lower_is_better'])
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(json.dumps({'status': 'INVALID', 'error': str(exc)[:300]}), file=sys.stderr)
        return 2
    print(json.dumps(result, indent=1))
    return 0


if __name__ == '__main__':
    sys.exit(main())
