"""Fitting must learn the same predictor used at decision time."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from rds_cpu_jepa import LatentCodeDiffJEPA

BASE = "def treatment(x):\n    return x + 1\n"
CANDIDATE = "def treatment(x):\n    return 2 * x + 1\n"


def record():
    return {"base_code": BASE, "candidate_code": CANDIDATE,
            "before_state": {"score": 0.5, "cost": 1, "step": 0},
            "after_state": {"score": 0.2, "cost": 1, "step": 1},
            "outcome_score": 0.2, "cost": 1}


class FitConsistencyTests(unittest.TestCase):
    def test_result_state_cannot_supply_readout_features(self):
        original = record()
        changed = deepcopy(original)
        changed["after_state"] = {"score": 0.99, "cost": 100, "step": 1000}
        a = LatentCodeDiffJEPA().fit([original])
        b = LatentCodeDiffJEPA().fit([changed])
        args = (original["before_state"], BASE, CANDIDATE)
        self.assertEqual(a.predict(*args), b.predict(*args))

    def test_constant_outcome_has_a_fitted_intercept(self):
        row = record()
        model = LatentCodeDiffJEPA().fit([row] * 8, l2_reg=1e-9)
        prediction = model.predict(row["before_state"], BASE, CANDIDATE)
        self.assertAlmostEqual(prediction["score"], row["outcome_score"], places=5)
        self.assertAlmostEqual(prediction["cost"], row["cost"], places=4)

    def test_transition_reproduces_representable_training_displacement(self):
        model = LatentCodeDiffJEPA()
        model.encode_state = lambda state, code: ([0.4 if code == CANDIDATE else 0.0] * 16, {})
        _, diff = model.encode_action(BASE, CANDIDATE)
        model.encode_action = lambda base, candidate: ([1.0] * 16, diff)
        model.fit([record()], l2_reg=1e-6)
        prediction = model.predict_latent([0.0] * 16, [1.0] * 16)
        for value in prediction:
            self.assertAlmostEqual(value, 0.4, places=5)

    def test_parameter_only_actions_survive_structural_fitting(self):
        base = 'LR = 1\n' + BASE
        low, high = base.replace('LR = 1', 'LR = 2'), base.replace('LR = 1', 'LR = 4')
        rows = [{**record(), 'base_code': base, 'candidate_code': code, 'outcome_score': score}
                for code, score in ((low, .2), (high, .8))]
        model = LatentCodeDiffJEPA().fit(rows)
        self.assertLess(model.predict(rows[0]['before_state'], base, low)['score'],
                        model.predict(rows[0]['before_state'], base, high)['score'])


if __name__ == "__main__":
    unittest.main()
