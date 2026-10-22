"""fold/_derive replay-cache profile: 20 hypotheses x 60 measurements, in memory.

``ingest`` calls ``fold`` three or four times and ``drive`` folds again every
step, so every hypothesis is replayed against all measured rows many times.
This profile hand-builds a state dict (no campaign, no store) and times
``_derive`` with the replay cache off vs on. The cache is toggled by
monkeypatching ``compass._REPLAY_CACHE_DISABLED`` (the same flag the
environment variable ``RDS_NO_REPLAY_CACHE=1`` sets at import time).

    python -B benchmark/newtonbench/fold_profile.py
"""
import sys
import time
from copy import deepcopy
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'scripts')]

import rds_compass as compass
import rds_compass_model as model

INPUTS = ['x1', 'x2', 'x3', 'x4']
LAWS = [
    'x1 + x2',
    'x1 - x2',
    'x1 * x2',
    '2*x1 + 3*x2',
    'x1**2 + x2',
    'x1**2 - x2**2',
    'abs(x1) + x2',
    'min(x1, x2) + x3',
    'max(x1, x2) * x3',
    'x1 % 7',
    'x1 * x2 + x3 * x4',
    'x1**3 + 2',
    '-x1 + x2',
    'x2',
    'x1 + 2*x2 + 3*x3',
    'x3 - x4',
    '(x1 + x2) * (x3 - x4)',
    'x1 - x4',
    '2*x1 + x4',
    'x1 * x3 + 1',
]
ROWS = 60
FOLDS = 20


def build_state():
    """20 rival rulebooks, 60 receipted measurements; half the rows match the first law."""
    candidates, observations = [], {}
    for i in range(ROWS):
        ident = f'c{i}'
        inputs = {name: str((i * 13 + 7 * k) % 17 + 1) for k, name in enumerate(INPUTS)}
        x1, x2 = int(inputs['x1']), int(inputs['x2'])
        # Even rows agree with LAWS[0] ('x1 + x2') inside the 1% band; odd rows
        # are far off, so counterexamples and their receipt patching are exercised.
        y = Fraction(x1 + x2) if i % 2 == 0 else Fraction(10 * (x1 + x2) + 3)
        candidates.append({'id': ident, 'inputs': inputs, 'run_id': f'compass-{ident}',
                           'output': f'out/{ident}.json', 'argv': []})
        observations[ident] = {'candidate': ident, 'status': 'MEASURED',
                               'outputs': {'y': model.text(y)}, 'receipt_sha256': f'{i:064x}',
                               'seq': i + 1}
    hypotheses = {}
    for k, law in enumerate(LAWS):
        hyp = model.compile_hypothesis({'id': f'h{k}', 'predict': {'y': law}}, INPUTS, ['y'])
        hypotheses[hyp['id']] = hyp
    plan = {'inputs': INPUTS, 'observables': ['y'], 'tolerances': {'y': {'rel': '1/100'}},
            'candidates': candidates}
    return {'plan': plan, 'hypotheses': hypotheses, 'observations': observations, 'tools': {}}


def timed_derives(state, rounds):
    times = []
    for _ in range(rounds):
        start = time.perf_counter()
        compass._derive(state)
        times.append(time.perf_counter() - start)
    return times


def derived(state):
    return {hid: (h['status'], h['replay']) for hid, h in state['hypotheses'].items()}


def main():
    print(f'[fold profile] {len(LAWS)} hypotheses x {ROWS} measured rows, {FOLDS} _derive rounds each')
    print(f'[fold profile] RDS_NO_REPLAY_CACHE env at import: '
          f"{sys.modules['os'].environ.get('RDS_NO_REPLAY_CACHE', '<unset>')}")
    states = {'off': build_state(), 'on': None}

    compass._REPLAY_CACHE_DISABLED = True  # monkeypatch: old behavior
    off = timed_derives(states['off'], FOLDS)

    compass._REPLAY_CACHE_DISABLED = False  # monkeypatch over the env default
    compass._REPLAY_CACHE.clear()
    compass._REPLAY_CACHE_STATS.update(hit=0, miss=0)
    states['on'] = build_state()
    on = timed_derives(states['on'], FOLDS)
    hit, miss = compass._REPLAY_CACHE_STATS['hit'], compass._REPLAY_CACHE_STATS['miss']

    print(f"cache off : total {sum(off):7.3f}s   per _derive avg {sum(off) / len(off) * 1e3:7.1f}ms")
    print(f"cache on  : total {sum(on):7.3f}s   first (cold miss) {on[0] * 1e3:7.1f}ms, "
          f"warm avg {sum(on[1:]) / max(len(on) - 1, 1) * 1e3:6.1f}ms")
    print(f"speedup   : {sum(off) / sum(on):5.1f}x total")
    print(f"replays   : hit {hit} / miss {miss} -> hit rate "
          f"{hit / (hit + miss) * 100 if hit + miss else 0:.1f}% "
          f"(cache entries {len(compass._REPLAY_CACHE)})")
    same = derived(states['off']) == derived(states['on'])
    print(f'derived states identical, cache on vs off: {same}')
    if not same:
        raise SystemExit('FAIL: replay cache changed derived statuses')
    raise SystemExit(0 if hit > 0 and sum(on) < sum(off) else 'FAIL: no cache speedup measured')


if __name__ == '__main__':
    main()
