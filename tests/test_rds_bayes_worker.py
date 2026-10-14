"""Actual fixed child adapter and fail-closed model/domain contracts."""
from copy import deepcopy
from pathlib import Path
import sys
import time
import unittest
sys.path[:0] = [str(Path(__file__).resolve().parents[1] / p) for p in ('scripts', 'tests')]
from rds_bayes_scorer import fit_blr
from rds_core import object_digest
from rds_predictor_contract import validate_result
from rds_predictor_worker import PredictionWorker
from test_rds_offline_contracts import prediction_request
from test_rds_bayes_scorer import BASE, CODE, BEFORE, records


def request():
    model = fit_blr(records(), ['gap'])
    req = prediction_request()
    req.update(issued_at=time.time() - 1, expires_at=time.time() + 30, timeout_seconds=4)
    req['model']['sha256'] = object_digest(model)
    req['context'] = dict(base_code=BASE, before_state=deepcopy(BEFORE),
                          scorer_contract=dict(schema=1, model=model, model_sha256=object_digest(model), abstain_threshold=1.))
    req['candidates'] = [dict(id='increase-lr', parameters={'code': CODE})]
    return req


class BayesWorkerTests(unittest.TestCase):
    def run_worker(self, req):
        rep = PredictionWorker(req, adapter='bayes_blr', snapshot_sha256='a' * 64).run()
        validate_result(req, rep['prediction'])
        self.assertEqual(rep['prediction']['selection_effect'], 'NONE')
        self.assertEqual(rep['prediction']['evidence_status'], 'UNKNOWN')
        return rep['prediction']

    def test_real_child_scores_and_keeps_inputs_immutable(self):
        req = request()
        original = deepcopy(req)
        result = self.run_worker(req)
        self.assertEqual(result['status'], 'SCORED', result)
        self.assertGreater(result['forecasts'][0]['uncertainty'], 0.)
        self.assertEqual(req, original)

    def test_ood_unknown_role_missing_diagnostic_hash_and_sd_abstain(self):
        for change in ('bounds', 'role', 'added', 'diagnostic', 'hash', 'sd', 'path'):
            req = request()
            if change == 'bounds':
                req['candidates'][0]['parameters']['code'] = CODE.replace('LR = 2', 'LR = 200')
            elif change == 'role':
                req['context']['base_code'] += 'WEIGHT_DECAY = 1\n'
                req['candidates'][0]['parameters']['code'] += 'WEIGHT_DECAY = 2\n'
            elif change == 'added':
                req['candidates'][0]['parameters']['code'] += 'NEW_PARAMETER = 2\n'
            elif change == 'diagnostic':
                req['context']['before_state']['diagnostics'].clear()
            elif change == 'hash':
                req['context']['scorer_contract']['model']['mean'][0] += 1.
            elif change == 'sd':
                req['context']['scorer_contract']['abstain_threshold'] = 0.
            else:
                req['context']['scorer_contract']['model'] = {'path': 'never-load.json'}
            result = self.run_worker(req)
            self.assertEqual(result['reason_code'], 'PROVIDER_ABSTAINED', (change, result))
            self.assertEqual(result['forecasts'], [])


if __name__ == '__main__':
    unittest.main()
