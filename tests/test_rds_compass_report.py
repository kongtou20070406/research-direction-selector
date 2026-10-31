"""Report-time IMPROVE verdicts: an arm is accepted only on every declared run with live receipts."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_compass_report as report

BASE = {'s1': '10', 's2': '10', 's3': '10'}


def measured(samples, receipt):
    return {'status': 'MEASURED', 'samples': dict(samples), 'receipt_sha256': receipt * 64}


def state(observations, *, tolerance=None, replicate=True, profile=None):
    candidates = [{'id': 'base', 'cost': '1'}, {'id': 'arm', 'cost': '1'}, {'id': 'other', 'cost': '1'}]
    if replicate:
        candidates.append({'id': 'arm-again', 'cost': '1', 'replicates': 'arm'})
    plan = {'profile': profile or {'baseline': 'base', 'metric': 'acc', 'min_gain': '1/2', 'max_degradation': '1'},
            'higher_is_better': {'acc': True}, 'tolerances': {'acc': {} if tolerance is None else tolerance},
            'candidates': candidates}
    return {'plan': plan, 'observations': observations}


class ImproveVerdictTests(unittest.TestCase):
    def arms(self, result):
        return {row['arm']: row for row in result['arms']}

    def test_an_arm_with_an_unbought_replicate_is_not_accepted(self):
        result = report._improve_verdicts(state({'base': measured(BASE, 'b'),
                                                  'arm': measured({'s1': '12', 's2': '12', 's3': '12'}, 'a')}))
        arms = self.arms(result)
        self.assertEqual(arms['arm']['verdict'], 'UNREPLICATED')
        self.assertEqual(arms['arm']['declared_runs'], ['arm', 'arm-again'])
        self.assertEqual(arms['other']['verdict'], 'UNMEASURED_OR_SAMPLE_MISMATCH')
        self.assertIsNone(result['recommended'])

    def test_every_declared_run_must_accept_and_the_weakest_gain_is_reported(self):
        result = report._improve_verdicts(state({
            'base': measured(BASE, 'b'),
            'arm': measured({'s1': '13', 's2': '13', 's3': '13'}, 'a'),
            'arm-again': measured({'s1': '11', 's2': '11', 's3': '11'}, 'c')}))
        arm = self.arms(result)['arm']
        self.assertEqual(arm['verdict'], 'ACCEPT')
        self.assertEqual(arm['min_mean_gain'], '1')
        self.assertEqual(arm['baseline_receipt_sha256'], 'b' * 64)
        self.assertEqual(result['recommended'], 'arm')

        result = report._improve_verdicts(state({
            'base': measured(BASE, 'b'),
            'arm': measured({'s1': '13', 's2': '13', 's3': '13'}, 'a'),
            'arm-again': measured({'s1': '13', 's2': '13', 's3': '7'}, 'c')}))
        self.assertEqual(self.arms(result)['arm']['verdict'], 'LOCAL_REGRESSION')
        self.assertIsNone(result['recommended'])

    def test_a_gain_inside_the_tolerance_band_is_not_credited(self):
        observations = {'base': measured(BASE, 'b'), 'arm': measured({'s1': '11', 's2': '11', 's3': '11'}, 'a')}
        loose = report._improve_verdicts(state(observations, replicate=False))
        self.assertEqual(self.arms(loose)['arm']['verdict'], 'ACCEPT')
        banded = report._improve_verdicts(state(observations, replicate=False, tolerance={'abs': '1'}))
        self.assertEqual(self.arms(banded)['arm']['verdict'], 'NO_MEAN_GAIN')

    def test_stale_evidence_settles_no_arm(self):
        observations = {'base': measured(BASE, 'b'), 'arm': measured({'s1': '13', 's2': '13', 's3': '13'}, 'a')}
        result = report._improve_verdicts(state(observations, replicate=False), stale=[{'candidate': 'base'}])
        self.assertEqual(self.arms(result)['arm']['verdict'], 'STALE_EVIDENCE')
        self.assertIsNone(result['recommended'])

    def test_an_untoleranced_metric_or_an_open_profile_needs_judgment(self):
        observations = {'base': measured(BASE, 'b'), 'arm': measured({'s1': '13', 's2': '13', 's3': '13'}, 'a')}
        untoleranced = state(observations, replicate=False)
        untoleranced['plan']['tolerances']['acc'] = None
        self.assertEqual(self.arms(report._improve_verdicts(untoleranced))['arm']['verdict'], 'JUDGMENT_REQUIRED')
        open_profile = state(observations, replicate=False, profile={'baseline': 'base', 'metric': 'acc'})
        self.assertEqual(self.arms(report._improve_verdicts(open_profile))['arm']['verdict'], 'JUDGMENT_REQUIRED')

    def test_mismatched_samples_and_an_unmeasured_baseline_are_unsettled(self):
        mismatch = report._improve_verdicts(state({'base': measured(BASE, 'b'),
                                                    'arm': measured({'s1': '13', 's2': '13'}, 'a')}, replicate=False))
        self.assertEqual(self.arms(mismatch)['arm']['verdict'], 'UNMEASURED_OR_SAMPLE_MISMATCH')
        self.assertEqual(report._improve_verdicts(state({'arm': measured(BASE, 'a')}))['status'],
                         'BASELINE_UNMEASURED')


class RecheckTests(unittest.TestCase):
    def test_a_law_claim_on_stale_evidence_falls_to_not_established(self):
        stored = {'goal': 'IDENTIFY', 'claim': 'GOAL_IDENTIFIED', 'receipts_rechecked': True, 'stale': []}
        now = [{'candidate': 'x', 'binding': 'RECEIPT_MISSING'}]
        value = report._rechecked({'verifications': [stored]}, now)
        self.assertEqual(value['claim'], 'NOT_ESTABLISHED')
        self.assertEqual(value['claim_at_verification'], 'GOAL_IDENTIFIED')
        self.assertFalse(value['receipts_rechecked'])
        self.assertEqual(value['report_recheck'], {'status': 'STALE', 'stale': now})
        self.assertEqual(stored['claim'], 'GOAL_IDENTIFIED')

    def test_bound_evidence_keeps_the_stored_conclusion(self):
        stored = {'goal': 'IDENTIFY', 'claim': 'GOAL_IDENTIFIED', 'receipts_rechecked': True, 'stale': []}
        value = report._rechecked({'verifications': [stored]}, [])
        self.assertEqual(value['claim'], 'GOAL_IDENTIFIED')
        self.assertEqual(value['report_recheck'], {'status': 'BOUND', 'stale': []})
        self.assertIsNone(report._rechecked({'verifications': []}, []))

    def test_a_stale_improve_verification_recommends_nothing(self):
        observations = {'base': measured(BASE, 'b'), 'arm': measured({'s1': '13', 's2': '13', 's3': '13'}, 'a')}
        current = state(observations, replicate=False)
        current['verifications'] = [{'goal': 'IMPROVE', 'receipts_rechecked': True, 'stale': [],
                                     'improvement': {'recommended': 'arm'}}]
        value = report._rechecked(current, [{'candidate': 'arm', 'binding': 'CHANGED'}])
        self.assertIsNone(value['improvement']['recommended'])
        self.assertEqual(value['improvement']['recommended_at_verification'], 'arm')
        self.assertEqual({row['arm']: row['verdict'] for row in value['improvement']['arms']}['arm'], 'STALE_EVIDENCE')


if __name__ == '__main__':
    unittest.main()
