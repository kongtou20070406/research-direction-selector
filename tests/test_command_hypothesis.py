"""F1 gate: a hypothesis outside the declared grammar enters as a frozen command.

The declared dialect rejects a rational exponent whose denominator exceeds 8
(``x ** 1.3`` = ``x ** 13/10``, the NewtonBench case). Such a law must still be
admissible as a black-box command hypothesis whose identity is the digest of its
executable payload, and replay must adjudicate it through the executor instead
of ``parse``.
"""
from fractions import Fraction
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_compass as compass
import rds_compass_model as model


def _command_hypothesis():
    return model.compile_hypothesis(
        {'id': 'newton-m13', 'predict': {'y': 'x ** 1.3'},
         'command': {'argv': ['python', '-B', 'law.py'], 'io': {'inputs': ['x'], 'outputs': ['y']},
                     'code_sha256': '0' * 64}},
        ['x'], ['y'])


class CommandHypothesisTests(unittest.TestCase):
    def test_parse_rejects_the_newtonbench_exponent(self):
        # The bug: the declared dialect cannot express x ** (13/10) at all.
        with self.assertRaises(Exception):
            model.parse('x ** 1.3', {'x'})

    def test_command_hypothesis_skips_parse_and_keeps_executable_identity(self):
        hyp = _command_hypothesis()
        self.assertTrue(hyp['command_hypothesis'])
        self.assertEqual(hyp['predict'], {'y': None})
        self.assertEqual(hyp['command']['argv'], ['python', '-B', 'law.py'])
        again = _command_hypothesis()
        self.assertEqual(hyp['sha256'], again['sha256'])

    def test_predict_returns_undecided_for_a_command_hypothesis(self):
        hyp = _command_hypothesis()
        values, status = model.predict(hyp, {'x': Fraction(4)})
        self.assertEqual((values, status), ({'y': None}, 'UNDECIDED'))

    def test_replay_adjudicates_the_command_output_by_tolerance(self):
        hyp = _command_hypothesis()
        # Receipted observations carry the true values of x ** 1.3 at x = 4 and x = 8.
        rows = [{'id': 'c1', 'inputs': {'x': Fraction(4)}, 'outputs': {'y': model.exact(str(round(float(4) ** 1.3, 6)))}, 'receipt_sha256': 'r1'},
                {'id': 'c2', 'inputs': {'x': Fraction(8)}, 'outputs': {'y': model.exact(str(round(float(8) ** 1.3, 6)))}, 'receipt_sha256': 'r2'}]

        def fake_run(argv, input=None, capture_output=None, timeout=None, text=None):
            payload = json.loads(input)
            x = Fraction(payload['inputs']['x'])
            # float(x ** 1.3) rounded to 6 decimals: inside a 1e-3 band.

            class Done:
                returncode = 0
                stdout = json.dumps({'outputs': {'y': str(round(float(x) ** 1.3, 6))}}) + chr(10)

            return Done()

        tolerances = {'y': model.tolerance({'abs': '1e-3'})}
        with patch.object(compass.subprocess, 'run', side_effect=fake_run):
            result = compass._replay_command(hyp, rows, tolerances)
        self.assertEqual(result['status'], 'CONSISTENT')
        self.assertEqual(result['tested'], 2)

    def test_replay_refutes_a_command_whose_output_leaves_the_band(self):
        hyp = _command_hypothesis()
        rows = [{'id': 'c1', 'inputs': {'x': Fraction(4)}, 'outputs': {'y': Fraction(6)}, 'receipt_sha256': 'r1'}]

        def fake_run(argv, input=None, capture_output=None, timeout=None, text=None):
            class Done:
                returncode = 0
                stdout = json.dumps({'outputs': {'y': '9.5'}})

            return Done()

        with patch.object(compass.subprocess, 'run', side_effect=fake_run):
            result = compass._replay_command(hyp, rows, {'y': model.tolerance({'abs': '1e-3'})})
        self.assertEqual(result['status'], 'REFUTED')
        self.assertEqual(result['counterexamples'][0]['id'], 'c1')

    def test_command_failure_is_unknown_never_a_manufactured_refutation(self):
        hyp = _command_hypothesis()
        rows = [{'id': 'c1', 'inputs': {'x': Fraction(4)}, 'outputs': {'y': Fraction(6)}, 'receipt_sha256': 'r1'}]

        def fake_run(argv, input=None, capture_output=None, timeout=None, text=None):
            raise compass.subprocess.TimeoutExpired(cmd=argv, timeout=60)

        with patch.object(compass.subprocess, 'run', side_effect=fake_run):
            result = compass._replay_command(hyp, rows, {'y': model.tolerance({'abs': '1e-3'})})
        # A crashed command leaves the prediction unknown; it does not refute.
        self.assertEqual(result['status'], 'UNTESTED')
        self.assertEqual(result['unknown_measurements'], 1)
        self.assertEqual(result['counterexamples'], [])


if __name__ == '__main__':
    unittest.main()
