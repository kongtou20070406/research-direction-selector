"""Executable rival boundaries and evidence-dependent route admission."""
from copy import deepcopy
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import rds_discrimination as d
import rds_structure as s
from rds_project import ProjectStore, digest
spec = importlib.util.spec_from_file_location('discrimination_fixture', REPO / 'examples/problem-structure/run.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class DiscriminationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = fixture.prepare(Path(self.temp.name) / 'project', 'negative')
        self.store = ProjectStore(self.root)
        self.task = s.request(self.root)['tasks'][0]

    def tearDown(self):
        self.temp.cleanup()

    def test_equivalent_prose_cannot_hide_same_executable_prediction(self):
        p = fixture.proposal(self.root, self.task)
        p['prediction'].update(if_proposal='All residuals equal zero', if_rival='Each residual equals 0')
        p['discriminator'].update(proposal={'op': 'eq', 'value': 0}, rival={'op': 'eq', 'value': 0.0})
        with self.assertRaisesRegex(ValueError, 'overlap or are equivalent'):
            s.propose(self.root, p)
        self.assertEqual(self.store.snapshot()['runs'], [])

    def test_interval_boundaries_neither_match_and_strict_types(self):
        value = fixture.proposal(self.root, self.task)['discriminator']
        value.update(proposal={'op': 'lt', 'value': 0}, rival={'op': 'gte', 'value': 0})
        d.validate(value)
        self.assertEqual(d.observe(value, {'correct': 0}, 'a' * 64)['observation'], 'REFUTE')
        value['rival']['op'] = 'lte'
        with self.assertRaisesRegex(ValueError, 'overlap'):
            d.validate(value)
        value.update(proposal={'op': 'eq', 'value': 0}, rival={'op': 'eq', 'value': 1})
        d.validate(value)
        self.assertEqual(d.observe(value, {'correct': 2}, 'a' * 64)['observation'], 'UNKNOWN')
        self.assertEqual(d.observe(value, {'correct': True}, 'a' * 64)['observation'], 'UNKNOWN')
        for bad in (float('nan'), float('inf'), [], True):
            value['proposal'] = {'op': 'lt', 'value': bad}
            with self.assertRaises(ValueError):
                d.validate(value)

    def test_original_measurement_overrides_reported_observation(self):
        p = fixture.proposal(self.root, self.task, strategy='linear')
        # Reversed measurable prediction deliberately contradicts evaluator enum.
        p['discriminator'].update(proposal={'op': 'eq', 'value': False}, rival={'op': 'eq', 'value': True})
        s.propose(self.root, p)
        feedback = s.advance(self.root, 'candidate')
        self.assertEqual(feedback['evaluator_observation'], 'REFUTE')
        self.assertEqual(feedback['observation'], 'SUPPORT')
        self.assertEqual(feedback['goal_status'], 'FAIL')
        self.assertFalse(feedback['discrimination']['measured']['value'])

    def test_conditions_and_independent_output_must_match_contract(self):
        for kind in ('hash', 'missing', 'candidate_output'):
            p = fixture.proposal(self.root, self.task)
            if kind == 'hash':
                p['discriminator']['conditions'][0]['sha256'] = '0' * 64
            elif kind == 'missing':
                p['discriminator']['conditions'].pop()
            else:
                p['discriminator']['measurement']['path'] = p['experiment']['candidate_output']
            with self.assertRaises(ValueError):
                s.propose(self.root, p)

    def test_refuted_hypothesis_blocks_prequeued_alias_and_direct_advance(self):
        p = fixture.proposal(self.root, self.task, strategy='linear')
        alias = fixture.proposal(self.root, self.task, ident='rival', strategy='linear')
        s.propose(self.root, p)
        s.propose(self.root, alias)
        original = s.advance(self.root, 'candidate')
        decision = s.next_step(self.root)
        self.assertIsNone(decision['selected'])
        self.assertEqual(decision['blocked'][0]['reason'], 'HYPOTHESIS_REFUTED')
        self.assertEqual(decision['blocked'][0]['evidence']['feedback_sha256'], digest(original))
        before = self.store.snapshot()
        with self.assertRaisesRegex(ValueError, 'HYPOTHESIS_REFUTED'):
            s.advance(self.root, 'rival')
        self.assertEqual(self.store.snapshot(), before)
        self.assertEqual(len(before['receipts']), 2)

    def test_post_failure_trigger_is_exact_and_packet_contains_originals(self):
        s.propose(self.root, fixture.proposal(self.root, self.task, strategy='linear'))
        observed = s.advance(self.root, 'candidate')
        packet = s.request(self.root)['tasks'][0]['prior_feedback'][0]
        self.assertEqual(packet['feedback_sha256'], digest(observed))
        self.assertEqual(len(packet['originals']), 2)
        self.assertIn('predictions', packet['originals'][0]['text'])
        p = fixture.proposal(self.root, self.task, ident='rival', strategy='periodic')
        for kind in ('missing', 'wrong_hash', 'wrong_observation'):
            bad = deepcopy(p)
            if kind == 'missing':
                del bad['trigger']
            elif kind == 'wrong_hash':
                bad['trigger']['feedback_sha256'] = '0' * 64
            else:
                bad['trigger']['observation'] = 'SUPPORT'
            with self.assertRaisesRegex(ValueError, 'TRIGGER'):
                s.propose(self.root, bad)
        s.propose(self.root, p)
        self.assertEqual(s.next_step(self.root)['selected'], 'rival')
        self.assertEqual(s.advance(self.root, 'rival')['goal_status'], 'PASS')

    def test_unknown_requires_evidence_and_changed_originals_fail_closed(self):
        s.propose(self.root, fixture.proposal(self.root, self.task, mode='unknown', strategy='linear'))
        observed = s.advance(self.root, 'candidate')
        self.assertEqual(observed['observation'], 'UNKNOWN')
        p = fixture.proposal(self.root, self.task, ident='rival', strategy='periodic')
        p['trigger']['purpose'] = 'ALTERNATIVE'
        with self.assertRaisesRegex(ValueError, 'UNKNOWN_REQUIRES_EVIDENCE'):
            s.propose(self.root, p)
        p['trigger']['purpose'] = 'EVIDENCE'
        s.propose(self.root, p)
        (self.root / 'out/candidate-verdict.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            s.next_step(self.root)
        before = self.store.snapshot()
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            s.advance(self.root, 'rival')
        self.assertEqual(self.store.snapshot(), before)

    def test_missing_measurement_never_infers_refutation(self):
        value = fixture.proposal(self.root, self.task)['discriminator']
        self.assertEqual(d.observe(value, {}, 'a' * 64)['reason'], 'MEASUREMENT_MISSING')
        self.assertEqual(d.observe(None, {'correct': True}, 'a' * 64)['observation'], 'UNKNOWN')

    def test_legacy_unbound_goal_pass_does_not_support_or_activate_explanation(self):
        p = fixture.proposal(self.root, self.task, strategy='periodic')
        del p['discriminator']
        # Emulate an already-retained pre-schema row, not new production admission.
        with patch('rds_structure.discrimination.validate', return_value=None):
            s.propose(self.root, p)
        result = s.advance(self.root, 'candidate')
        self.assertEqual((result['goal_status'], result['observation']), ('PASS', 'UNKNOWN'))
        self.assertEqual(s.next_step(self.root)['status'], 'ORIGINAL_EVALUATOR_PASSED')
        with self.assertRaisesRegex(ValueError, 'supporting'):
            s.activate(self.root, 'candidate')

    def test_already_saved_legacy_feedback_is_effectively_unknown_without_rewriting(self):
        p = fixture.proposal(self.root, self.task, strategy='periodic')
        del p['discriminator']
        with patch('rds_structure.discrimination.validate', return_value=None):
            s.propose(self.root, p)
        original_put = s._put
        def old_writer(store, kind, ident, value, **kwargs):
            if kind == 'FEEDBACK':
                value = {k: v for k, v in value.items() if k not in ('discrimination', 'evaluator_observation')}
                value.update(status='TEST_SUPPORTED', observation='SUPPORT')
            return original_put(store, kind, ident, value, **kwargs)
        with patch('rds_structure._put', side_effect=old_writer):
            original = s.advance(self.root, 'candidate')
        before = self.store.snapshot()
        effective = s.advance(self.root, 'candidate')
        self.assertEqual((effective['goal_status'], effective['observation']), ('PASS', 'UNKNOWN'))
        self.assertEqual(effective['feedback_sha256'], digest(original))
        self.assertEqual(self.store.snapshot(), before)
        packet = s.request(self.root)['tasks'][0]['prior_feedback'][0]
        self.assertEqual((packet['original_observation'], packet['observation']), ('SUPPORT', 'UNKNOWN'))
        rival = fixture.proposal(self.root, self.task, ident='rival', strategy='linear')
        rival['trigger'].update(observation='UNKNOWN', purpose='ALTERNATIVE')
        with self.assertRaisesRegex(ValueError, 'UNKNOWN_REQUIRES_EVIDENCE'):
            s.propose(self.root, rival)
        rival['trigger']['purpose'] = 'EVIDENCE'
        s.propose(self.root, rival)


if __name__ == '__main__':
    unittest.main()
