"""Exact network analyses: proved unit states, Lipschitz upper bounds, regions, Jacobians, ablations."""
from fractions import Fraction
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_nn_analyze as nn
from rds_nn_verify import _forward_step

# f(x) = relu(x) + relu(-x) + relu(x - 2) = |x| + relu(x - 2)
KINK = {'input_dim': 1, 'layers': [{'kind': 'linear', 'weight': [[1], [-1], [1]], 'bias': [0, 0, -2]},
                                   {'kind': 'relu'}, {'kind': 'linear', 'weight': [[1, 1, 1]], 'bias': [0]}]}


def forward(model, point):
    layers, _ = nn.network(model)
    trace, x = [], [Fraction(v) for v in point]
    for layer in layers:
        x = _forward_step(layer, x)
        trace.append(x)
    return trace


class KnownNetworkTests(unittest.TestCase):
    def test_unit_states_are_proved_only_where_the_box_forces_them(self):
        result = nn.activation_bounds(KINK, [['1', '3']])
        self.assertEqual(result['relu_layers'][0], {'layer': 1, 'units': 3, 'dead': [1], 'active': [0],
                                                          'unproved': [2]})
        self.assertEqual((result['totals'], result['assurance']), ({'dead': 1, 'active': 1, 'unproved': 1},
                                                                   'PROVED_OVER_BOX'))

    def test_dead_units_tighten_the_lipschitz_bound_on_a_box(self):
        self.assertEqual(nn.lipschitz_bound(KINK, [['1', '3']])['lipschitz_upper'], '2')  # Slopes 1 and 2 there.
        whole = nn.lipschitz_bound(KINK)
        self.assertEqual((whole['lipschitz_upper'], whole['scope'], whole['assurance']), ('3', 'ALL_INPUTS', 'UPPER_BOUND'))

    def test_regions_along_a_segment_are_exact(self):
        result = nn.linear_regions(KINK, ['-3'], ['3'])
        self.assertEqual((result['regions'], result['breakpoints']), (3, ['1/2', '5/6']))
        self.assertEqual((result['outputs_at_start'], result['outputs_at_end']), (['3'], ['4']))

    def test_the_region_cap_is_enforced_while_pieces_are_built(self):
        with patch.object(nn, 'MAX_PIECES', 3):
            self.assertEqual(nn.linear_regions(KINK, ['-3'], ['3'])['regions'], 3)
        with patch.object(nn, 'MAX_PIECES', 2), self.assertRaisesRegex(ValueError, 'more than 2 linear pieces'):
            nn.linear_regions(KINK, ['-3'], ['3'])

    def test_jacobian_reports_ties_instead_of_hiding_them(self):
        self.assertEqual(nn.jacobian(KINK, ['1'])['jacobian'], [['1']])
        kink = nn.jacobian(KINK, ['2'])
        self.assertEqual((kink['status'], kink['ties']), ('NON_DIFFERENTIABLE', [{'layer': 1, 'unit': 2}]))

    def test_ablation_deltas_are_exact(self):
        result = nn.ablation(KINK, [['3'], ['1']], [[1, 2]])
        self.assertEqual((result['max_abs_delta'], result['mean_abs_delta'], result['deltas']),
                         (['1'], ['1/2'], [['-1'], ['0']]))
        with self.assertRaises(ValueError):
            nn.ablation(KINK, [['1']], [[0, 0]])  # Layer 0 is linear: only ReLU outputs are ablated.


class RandomNetworkTests(unittest.TestCase):
    """Independent check: exact forward evaluation at sampled inputs never contradicts an analysis."""

    def setUp(self):
        rng = random.Random(20261015)
        value = lambda: str(Fraction(rng.randint(-9, 9), rng.randint(1, 4)))
        self.model = {'input_dim': 2, 'layers': [
            {'kind': 'linear', 'weight': [[value(), value()] for _ in range(5)], 'bias': [value() for _ in range(5)]},
            {'kind': 'relu'},
            {'kind': 'linear', 'weight': [[value() for _ in range(5)] for _ in range(4)], 'bias': [value() for _ in range(4)]},
            {'kind': 'relu'},
            {'kind': 'linear', 'weight': [[value() for _ in range(4)] for _ in range(2)], 'bias': [value(), value()]}]}
        self.points = [[Fraction(rng.randint(-40, 40), 20), Fraction(rng.randint(-40, 40), 20)] for _ in range(300)]

    def test_proved_states_and_the_lipschitz_bound_hold_at_every_sample(self):
        box = [['-2', '2'], ['-2', '2']]
        states = nn.activation_bounds(self.model, box)['relu_layers']
        bound = Fraction(nn.lipschitz_bound(self.model, box)['lipschitz_upper'])
        outputs = []
        for point in self.points:
            trace = forward(self.model, point)
            for layer in states:
                pre = trace[layer['layer'] - 1]
                self.assertTrue(all(pre[j] <= 0 for j in layer['dead']) and all(pre[j] >= 0 for j in layer['active']))
            outputs.append((point, trace[-1]))
        for (p, fp), (q, fq) in zip(outputs, outputs[1:]):
            gap = max(abs(a - b) for a, b in zip(p, q))
            self.assertLessEqual(max(abs(a - b) for a, b in zip(fp, fq)), bound * gap)

    def test_region_count_matches_pattern_changes_on_a_fine_grid(self):
        start, end = ['-2', '-1'], ['2', '3/2']
        result = nn.linear_regions(self.model, start, end)
        layers, _ = nn.network(self.model)
        patterns, a, b = [], [Fraction(v) for v in start], [Fraction(v) for v in end]
        for i in range(4001):
            t = Fraction(2 * i + 1, 8002)  # Grid midpoints never sit exactly on a breakpoint here.
            trace = forward(self.model, [x + t * (y - x) for x, y in zip(a, b)])
            patterns.append(tuple(v > 0 for index, layer in enumerate(layers) if layer['kind'] == 'linear'
                                  and index + 1 < len(layers) for v in trace[index]))
        changes = sum(1 for left, right in zip(patterns, patterns[1:]) if left != right)
        self.assertEqual(result['regions'], changes + 1)


class CommandTests(unittest.TestCase):
    def test_compass_out_writes_a_measurement_and_bad_models_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'model.json').write_text(json.dumps(KINK), encoding='utf-8')
            (tmp / 'request.json').write_text(json.dumps({'start': ['-3'], 'end': ['3']}), encoding='utf-8')
            script = str(ROOT / 'scripts' / 'rds_nn_analyze.py')
            done = subprocess.run([sys.executable, '-B', script, 'regions', '--model', str(tmp / 'model.json'),
                                   '--request', str(tmp / 'request.json'), '--compass-out', str(tmp / 'out.json')],
                                  capture_output=True, text=True, check=False)
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(json.loads((tmp / 'out.json').read_text(encoding='utf-8')), {'outputs': {'linear_regions': 3}})
            self.assertEqual(json.loads(done.stdout)['scientific_support'], 'UNKNOWN')
            (tmp / 'model.json').write_text(json.dumps({'input_dim': 1, 'layers': [{'kind': 'conv'}]}), encoding='utf-8')
            done = subprocess.run([sys.executable, '-B', script, 'regions', '--model', str(tmp / 'model.json'),
                                   '--request', str(tmp / 'request.json')], capture_output=True, text=True, check=False)
            self.assertEqual((done.returncode, json.loads(done.stderr)['status']), (2, 'INVALID'))


if __name__ == '__main__':
    unittest.main()
