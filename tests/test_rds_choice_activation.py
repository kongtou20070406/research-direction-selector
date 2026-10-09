"""Caller-directed choices cannot cross native Advisor activation."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_rds_owned_advisor as fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_advisor import RDSAdvisor
from rds_project_lifecycle import enable_advisor
import rds_quick as quick


class ChoiceActivationTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.OwnedAdvisorCLITests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.initialize(include_policy=False)
        self.store = fixture.ProjectStore(self.f.root)
        self.before = self.store.snapshot()
        self.context = deepcopy(self.f.policy['context'])
        self.context['facts'] = {'baseline.score': {'value': 0, 'source': 'synthetic API fixture'}}
        recommendations = RDSAdvisor(self.f.root).recommend_next_directions(
            {**self.before, 'advisor_context': self.context}, deepcopy(self.f.policy['graph']))
        self.advice = {'recommendations': recommendations}

    def activate(self):
        preview = enable_advisor(self.store, self.f.policy)
        result = enable_advisor(self.store, self.f.policy, apply=True,
                                expected_snapshot=preview['snapshot_sha256'])
        self.assertEqual(result['status'], 'ADVISOR_ENABLED')
        self.assertFalse(result['execution_started'])

    def assert_no_checkpoint_or_dispatch(self):
        with self.store._db(True) as db:
            table = db.execute("SELECT 1 FROM sqlite_master WHERE name='checkpoints'").fetchone()
            if table:
                self.assertEqual(db.execute('SELECT count(*) FROM checkpoints').fetchone()[0], 0)
        after = self.store.snapshot()
        for key in ('runs', 'receipts', 'budget'):
            self.assertEqual(after[key], self.before[key])
        self.assertEqual(self.f.starts(), [])

    def test_completed_activation_rejects_prior_quick_choice_before_cas(self):
        self.activate()
        activated = self.store.snapshot()
        for expected in (None, self.before['contract_sha256']):
            with self.subTest(original_pin=expected):
                with patch.object(quick, 'cas_json', side_effect=AssertionError('owned choice wrote CAS')):
                    with self.assertRaisesRegex(ValueError, 'Program-owned Advisor'):
                        quick.record_choice(self.f.root, self.advice, self.context, 'baseline',
                                            'late-choice', _expected_contract_sha256=expected)
        self.assertEqual(self.store.snapshot(), activated)
        self.assert_no_checkpoint_or_dispatch()

    def test_activation_after_real_cas_is_rejected_by_final_contract_guard(self):
        original_cas = quick.cas_json
        observed = {}

        def activate_after_cas(*args, **kwargs):
            observed['ref'] = original_cas(*args, **kwargs)
            self.activate()
            observed['activated'] = self.store.snapshot()
            return observed['ref']

        with patch.object(quick, 'cas_json', side_effect=activate_after_cas) as cas:
            with self.assertRaisesRegex(ValueError, 'Quick parent contract changed before checkpoint publication'):
                quick.record_choice(self.f.root, self.advice, self.context, 'baseline', 'racing-choice')
            self.assertEqual(cas.call_count, 1)
        self.assertTrue(Path(observed['ref']['path']).is_file())
        self.assertEqual(self.store.snapshot(), observed['activated'])
        self.assert_no_checkpoint_or_dispatch()

    def test_public_choice_succeeds_and_explicit_contract_pin_is_never_refreshed(self):
        with patch.object(quick, 'cas_json', side_effect=AssertionError('wrong pin wrote CAS')):
            with self.assertRaisesRegex(ValueError, 'Expected choice contract differs'):
                quick.record_choice(self.f.root, self.advice, self.context, 'baseline', 'wrong-pin',
                                    _expected_contract_sha256='0' * 64)
        self.assertEqual(self.store.snapshot(), self.before)
        saved = quick.record_choice(self.f.root, self.advice, self.context, 'baseline', 'public-choice',
                                    _expected_contract_sha256=self.before['contract_sha256'])
        self.assertEqual(saved['status'], 'SAVED')
        self.assertEqual(saved['contract_sha256'], self.before['contract_sha256'])
        self.assertEqual(self.store.snapshot(), self.before)
        with self.store._db(True) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM checkpoints').fetchone()[0], 1)
        self.assertEqual(self.f.starts(), [])


if __name__ == '__main__':
    unittest.main()
