"""Posterior algebra and pre-result feature boundaries."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import rds_bayes_scorer as B

BASE = 'LR = 1\ndef treatment(x):\n return x\n'
CODE = BASE.replace('LR = 1', 'LR = 2')
BEFORE = {'score': .5, 'diagnostics': {'gap': .2}}


def records():
    return [dict(before_state=BEFORE, base_code=BASE, candidate_code=code, outcome_score=y)
            for code, y in [(CODE, .7), (CODE, .6), (BASE, .4), (BASE, .5)]]


class BayesTests(unittest.TestCase):
    def test_rank_one_matches_batch_precision(self):
        m = B.task_posterior(B.fit_blr(records(), ['gap']))
        frozen = deepcopy(m)
        u = B.update_blr(m, BEFORE, BASE, CODE, .8)
        x = B.features(m, BEFORE, BASE, CODE)
        P = B.inverse(m['covariance'])
        h = [B.dot(row, m['mean']) + m['beta'] * v * .3 for row, v in zip(P, x)]
        S = B.inverse([[v + m['beta'] * x[i] * x[j] for j, v in enumerate(row)] for i, row in enumerate(P)])
        for got, row in zip(u['mean'], S):
            self.assertAlmostEqual(got, B.dot(row, h), places=8)
        self.assertEqual(m, frozen)
        self.assertLess(B.predict_blr(u, BEFORE, BASE, CODE)[1], B.predict_blr(m, BEFORE, BASE, CODE)[1])

    def test_normal_update_and_prediction_variance(self):
        m = B.fit_normal(records())
        frozen = deepcopy(m)
        old, sd = B.predict_normal(m, BEFORE, BASE, CODE)
        u = B.update_normal(m, BEFORE, BASE, CODE, .9)
        new, sd2 = B.predict_normal(u, BEFORE, BASE, CODE)
        self.assertGreater(new, old)
        self.assertLess(sd2, sd)
        self.assertEqual(m, frozen)

    def test_labels_do_not_enter_features_and_confirmation_is_rejected(self):
        m = B.fit_blr(records())
        r = records()
        r[0]['after_state'] = {'score': 1e10}
        self.assertEqual(m, B.fit_blr(r))
        r[0]['split'] = 'confirmation'
        for fit in (B.fit_blr, B.fit_normal):
            with self.assertRaises(ValueError):
                fit(r)

    def test_nonfinite_updates_and_task_variance_are_rejected(self):
        for value in (float('nan'), float('inf'), float('-inf')):
            with self.assertRaises(ValueError):
                B.fit_blr(records(), task_variance=value)
            for fit, update in ((B.fit_blr, B.update_blr), (B.fit_normal, B.update_normal)):
                model = fit(records())
                with self.assertRaises(ValueError):
                    update(model, BEFORE, BASE, CODE, value)
        damaged = B.task_posterior(B.fit_blr(records()))
        damaged['covariance'][0][0] = float('nan')
        with self.assertRaises(ValueError):
            B.predict_blr(damaged, BEFORE, BASE, CODE)


if __name__ == '__main__':
    unittest.main()
