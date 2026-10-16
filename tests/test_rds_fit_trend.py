"""Train/test fit-trend conclusions: decided only when every replicate pair agrees, never extrapolated."""
from fractions import Fraction
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compass_fixture as fx
import rds_compass as compass
import rds_compass_store as cstore
import rds_fit_trend as fit
from rds_compass_declaration import initialize

EXACT = {'train': (Fraction(0), Fraction(0)), 'test': (Fraction(0), Fraction(0))}
TOL = {'train': (Fraction(1, 100), Fraction(0)), 'test': (Fraction(1, 100), Fraction(0))}


def curve(x, seed):
    """Train loss keeps falling; test loss falls to a minimum at x=4 and then rises."""
    return {'x': x, 'train': Fraction(1, x) + Fraction(seed, 1000),
            'test': Fraction(3, 10) + Fraction((x - 4) ** 2, 20) + Fraction(seed, 1000)}


class AnalysisTests(unittest.TestCase):
    def test_overfitting_onset_and_an_interior_optimum(self):
        result = fit.analyze([curve(x, s) for x in range(1, 7) for s in (0, 1)], TOL, True)
        self.assertEqual([s['diagnosis'] for s in result['steps']],
                         ['BOTH_IMPROVE', 'BOTH_IMPROVE', 'BOTH_IMPROVE', 'OVERFITTING', 'OVERFITTING'])
        self.assertEqual(result['overfitting'], {'status': 'OBSERVED', 'from': '4', 'to': '5'})
        self.assertEqual(result['best_test'], {'x': ['4'], 'position': 'INSIDE_RANGE'})
        self.assertEqual((result['train_trend'], result['test_trend'], result['noise']),
                         ('IMPROVING', 'MIXED', 'REPLICATED'))
        self.assertEqual(result['levels'][-1]['gap'], ['1/3', '1/3'])  # test - train at x=6
        self.assertEqual((result['extrapolation'], result['scientific_support']), ('NONE', 'UNKNOWN'))

    def test_replicate_spread_and_tolerance_leave_steps_unresolved(self):
        runs = [{'x': 1, 'train': '1', 'test': '1'}, {'x': 1, 'train': '1', 'test': '2'},
                {'x': 2, 'train': '1/2', 'test': '3/2'}, {'x': 3, 'train': '1/2', 'test': '3/2'}]
        result = fit.analyze(runs, EXACT, True)
        # Test at x=1 spans 1..2, so 3/2 at x=2 neither improves nor worsens on every pair.
        self.assertEqual([(s['train'], s['test'], s['diagnosis']) for s in result['steps']],
                         [('IMPROVES', 'UNRESOLVED', 'UNRESOLVED'), ('WITHIN_TOLERANCE', 'WITHIN_TOLERANCE', 'PLATEAU')])
        self.assertEqual((result['overfitting']['status'], result['noise'], result['best_test']['position']),
                         ('NOT_OBSERVED_OVER_MEASURED_RANGE', 'UNREPLICATED', 'NO_DECIDED_OPTIMUM'))

    def test_higher_is_better_reads_the_mirror_image(self):
        accuracy = [{'x': r['x'], 'train': -r['train'], 'test': -r['test']} for r in
                    (curve(x, 0) for x in range(1, 7))]
        result = fit.analyze(accuracy, TOL, False)
        self.assertEqual((result['overfitting']['from'], result['best_test']['x'], result['gap_definition']),
                         ('4', ['4'], 'train - test'))

    def test_random_series_match_an_independent_pairwise_check(self):
        rng = random.Random(20261016)
        for _ in range(40):
            runs = [{'x': x, 'train': Fraction(rng.randint(0, 40), 20), 'test': Fraction(rng.randint(0, 40), 20)}
                    for x in range(rng.randint(2, 6)) for _ in range(rng.randint(1, 3))]
            tol = Fraction(rng.randint(0, 4), 20)
            result = fit.analyze(runs, {'train': (tol, Fraction(0)), 'test': (tol, Fraction(0))}, True)
            levels = sorted({r['x'] for r in runs})
            for step, a, b in zip(result['steps'], levels, levels[1:]):
                for key in ('train', 'test'):
                    pairs = [(u[key], v[key]) for u in runs if u['x'] == a for v in runs if v['x'] == b]
                    expected = ('WITHIN_TOLERANCE' if all(abs(u - v) <= tol for u, v in pairs) else
                                'IMPROVES' if all(u - v > tol for u, v in pairs) else
                                'WORSENS' if all(v - u > tol for u, v in pairs) else 'UNRESOLVED')
                    self.assertEqual(step[key], expected, (runs, tol))

    def test_command_line_fails_closed_on_a_malformed_request(self):
        with tempfile.TemporaryDirectory() as tmp:
            request = Path(tmp) / 'request.json'
            script = [sys.executable, '-B', str(ROOT / 'scripts' / 'rds_fit_trend.py'), '--request', str(request)]
            request.write_text(json.dumps({'lower_is_better': True, 'tolerance': {'train': {'abs': 0}, 'test': {}},
                                           'runs': [{'x': 1, 'train': 1, 'test': 2}, {'x': 2, 'train': 0.5, 'test': 3}]}))
            done = subprocess.run(script, capture_output=True, text=True, check=False)
            self.assertEqual((done.returncode, json.loads(done.stdout)['overfitting']['status']), (0, 'OBSERVED'))
            request.write_text(json.dumps({'lower_is_better': 'yes', 'tolerance': {'train': {}, 'test': {}}, 'runs': []}))
            done = subprocess.run(script, capture_output=True, text=True, check=False)
            self.assertEqual((done.returncode, json.loads(done.stderr)['status']), (2, 'INVALID'))


EXPERIMENT = '''import argparse, json, pathlib
from fractions import Fraction
p = argparse.ArgumentParser(); p.add_argument('--x'); p.add_argument('--seed'); p.add_argument('--output', required=True)
a = p.parse_args(); x, s = int(a.x), int(a.seed)
train = Fraction(1, x) + Fraction(s, 1000)
test = Fraction(3, 10) + Fraction((x - 4) ** 2, 20) + Fraction(s, 1000)
out = pathlib.Path(a.output); out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({'outputs': {'train': str(train), 'test': str(test)}}), encoding='utf-8')
'''


class CompassTrendTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name).resolve()

    def campaign(self, name, trend, direction=True):
        root = self.base / name
        fx.write(root / 'experiment.py', EXPERIMENT)
        fx.write(root / 'task.json', {})
        metric = {'tolerance': {'abs': '1/100'}, **({'higher_is_better': False} if direction else {})}
        declaration = {
            'schema': 'rds-compass-declaration-v1',
            'goal': {'kind': 'IDENTIFY_LAW', 'statement': 'Identify how test loss depends on epochs', 'observable': 'test'},
            'inputs': {'x': {'min': 1, 'max': 6, 'integer': True}, 'seed': {'min': 0, 'max': 1, 'integer': True}},
            'observables': {'train': metric, 'test': metric},
            'experiment': {'argv': ['{python}', '-B', 'experiment.py', '--x', '{x}', '--seed', '{seed}',
                                    '--output', '{output}'],
                           'files': {'code': ['experiment.py'], 'data': ['task.json'], 'evaluator': ['experiment.py']},
                           'timeout_seconds': 20, 'wall_seconds': 25},
            'candidates': [{'id': f'e{x}s{s}', 'inputs': {'x': x, 'seed': s}} for x in range(1, 7) for s in (0, 1)],
            'budget': {'wall_seconds': 900}, 'trend': trend}
        fx.write(root / 'compass.json', declaration)
        initialize(root, root / 'compass.json')
        return cstore.store_for(root)

    def buy(self, store):
        for candidate in compass.fold(store)['plan']['candidates']:
            manifest = compass.manifest(compass.fold(store), candidate['id'])
            store.register(manifest)
            self.assertEqual(store.execute(manifest['id'])['run_status'], 'SUCCEEDED')
        compass.ingest(store)

    def test_receipted_runs_yield_the_overfitting_onset_for_the_agent(self):
        store = self.campaign('trend', {'along': 'x', 'train': 'train', 'test': 'test', 'replicate': 'seed'})
        self.buy(store)
        state = compass.fold(store)
        full = compass.report(store)['fit_trend']
        self.assertEqual((full['status'], full['overfitting'], len(full['receipts'])),
                         ('DESCRIBED', {'status': 'OBSERVED', 'from': '4', 'to': '5'}, 12))
        compact = compass.workset(state)['fit_trend']
        self.assertNotIn('receipts', compact)
        self.assertEqual(compact['best_test'], {'x': ['4'], 'position': 'INSIDE_RANGE'})
        packet = compass.packet(state, [], {'trigger': 'TEST'}, {})
        self.assertEqual(packet['evidence']['fit_trend']['overfitting']['status'], 'OBSERVED')

    def test_a_varying_factor_or_an_undeclared_direction_concludes_nothing(self):
        store = self.campaign('confounded', {'along': 'x', 'train': 'train', 'test': 'test'})
        self.buy(store)
        self.assertEqual(compass.fit_trend(compass.fold(store))['varying_inputs'], ['seed'])
        store = self.campaign('direction', {'along': 'x', 'train': 'train', 'test': 'test', 'replicate': 'seed'},
                              direction=False)
        state = compass.fold(store)
        self.assertIn('observables.test.higher_is_better', state['plan']['unknown'])
        self.assertEqual(compass.fit_trend(state)['reason'], 'DIRECTION_UNDECLARED')
        for index, bad in enumerate(({'along': 'epochs', 'train': 'train', 'test': 'test'},
                                     {'along': 'x', 'train': 'test', 'test': 'test'},
                                     {'along': 'x', 'train': 'train', 'test': 'test', 'replicate': 'x'},
                                     {'along': 'x', 'train': 'train'})):
            with self.subTest(trend=bad), self.assertRaises(ValueError):
                self.campaign(f'bad{index}', bad)


if __name__ == '__main__':
    unittest.main()
