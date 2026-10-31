"""A recorded choice names one returned candidate; discarded actions are explained, never chosen."""
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_advisor_choice as advisor_choice

DECISION = {'id': 'q1', 'goal_revision': 'g1', 'scope': {'area': 'demo'}}


def advice(candidates=(), discarded=(), blocked=()):
    search = {'candidates': list(candidates), 'discarded_candidates': list(discarded)}
    if blocked:
        search['blocked_candidates'] = list(blocked)
    return {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}


class DiscardedHintTests(unittest.TestCase):
    def test_hint_is_bounded_ascii_and_counts_the_rest(self):
        records = [{'id': 'x' * 120, 'reason': 'r' * 250},
                   {'rule_id': 'rule\n"two"', 'reason': 'café'},
                   {'id': 7, 'reason': None},
                   {'id': 'fourth', 'reason': 'hidden'},
                   {'id': 'fifth', 'reason': 'hidden'}]
        hint = advisor_choice._discarded_choice_hint(records)
        self.assertTrue(hint.isascii())
        self.assertNotIn('\n', hint)
        self.assertIn('"' + 'x' * 96 + '...": ' + 'r' * 200 + '...', hint)
        self.assertIn('"rule\\n\\"two\\"": caf\\u00e9', hint)
        self.assertIn('"UNKNOWN": UNKNOWN', hint)
        self.assertNotIn('fourth', hint)
        self.assertIn('2 more discarded actions', hint)
        self.assertTrue(hint.endswith('Repair the configured action; inspect full advice for original reasons'))


class ChoiceRefusalTests(unittest.TestCase):
    def test_a_choice_needs_the_decision_identity(self):
        for decision in ({}, {'id': 'q1'}, {'goal_revision': 'g1'}, {'id': ' ', 'goal_revision': 'g1'}):
            with self.subTest(decision=decision), self.assertRaisesRegex(ValueError, 'decision.id and goal_revision'):
                advisor_choice.choice(advice(), {'decision': decision})

    def test_a_discarded_candidate_cannot_be_selected_by_id(self):
        discarded = [{'id': 'c1', 'reason': 'configured action failed its check'}]
        with self.assertRaisesRegex(ValueError, 'Selected candidate was discarded: "c1": configured action failed'):
            advisor_choice.choice(advice(discarded=discarded), {'decision': DECISION}, 'c1')
        # The action id of a discarded record names it too.
        discarded = [{'id': 'c1', 'action_id': 'a1', 'reason': 'bad'}]
        with self.assertRaisesRegex(ValueError, 'Selected candidate was discarded'):
            advisor_choice.choice(advice(discarded=discarded), {'decision': DECISION}, 'a1')

    def test_with_nothing_ready_the_discarded_reasons_are_the_error(self):
        discarded = [{'id': 'c1', 'reason': 'bad'}]
        with self.assertRaisesRegex(ValueError, 'No READY candidate; configured actions were discarded: "c1": bad'):
            advisor_choice.choice(advice(discarded=discarded), {'decision': DECISION})

    def test_an_unresolved_method_scope_blocks_an_implicit_choice(self):
        pending = [{'id': 'c1', 'status': 'NEEDS_EVIDENCE', 'method_review': {'status': 'UNKNOWN'}}]
        with self.assertRaisesRegex(ValueError, 'Method scope is unresolved'):
            advisor_choice.choice(advice(candidates=pending), {'decision': DECISION})


if __name__ == '__main__':
    unittest.main()
