"""Tests for the NewtonBench single-point pulse candidate generator."""
from fractions import Fraction
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'benchmark' / 'newtonbench')]

import rds_compass_model as model
from pulse_patch import pulse_patch


def sparse_mid_data():
    """sparse-mid world: x+1 if x != 20 else 1000 over range(32)."""
    return [{
        'id': f'x{x}',
        'inputs': {'x': Fraction(x)},
        'outputs': {'y': Fraction(1000 if x == 20 else x + 1)}
    } for x in range(32)]


class TestPulsePatch(unittest.TestCase):
    def test_sparse_mid_pulse_patch(self):
        rows = sparse_mid_data()
        observable = 'y'
        leader = '(1 + x)'
        exact_tol = {'y': (Fraction(0), Fraction(0))}

        # 1. Leader alone has exactly 1 counterexample at x=20
        leader_hyp = model.compile_hypothesis(
            {'id': 'leader', 'predict': {observable: leader}},
            ['x'], [observable]
        )
        leader_replay = model.replay(leader_hyp, rows, exact_tol)
        self.assertEqual(leader_replay['status'], 'REFUTED')
        self.assertEqual(len(leader_replay['counterexamples']), 1)
        self.assertEqual(leader_replay['counterexamples'][0]['id'], 'x20')
        self.assertEqual(leader_replay['counterexamples'][0]['observed'], '1000')
        self.assertEqual(leader_replay['counterexamples'][0]['predicted'], '21')

        # 2. Pulse patch generator produces candidates
        candidates = pulse_patch(rows, leader, observable)
        self.assertEqual(len(candidates), 32)

        # 3. Best candidate is x=20 with 0 counterexamples
        best = candidates[0]
        self.assertEqual(best['point'], 20)
        self.assertEqual(best['counterexamples_count'], 0)
        self.assertEqual(best['status'], 'CONSISTENT')
        self.assertEqual(best['expression'], 'ite((x == 20), 1000, (1 + x))')

        # 4. Explicit verification of ite((x == 20), 1000, (1 + x)) hypothesis
        pulse_hyp = model.compile_hypothesis(
            {'id': 'pulse_explicit', 'predict': {observable: 'ite((x == 20), 1000, (1 + x))'}},
            ['x'], [observable]
        )
        pulse_replay = model.replay(pulse_hyp, rows, exact_tol)
        self.assertEqual(pulse_replay['status'], 'CONSISTENT')
        self.assertEqual(len(pulse_replay['counterexamples']), 0)
        self.assertEqual(pulse_replay['tested'], 32)

        # 5. Non-exception candidates have exactly 1 counterexample (x=20)
        for cand in candidates[1:]:
            self.assertEqual(cand['counterexamples_count'], 1)


if __name__ == '__main__':
    unittest.main()
