import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import rds_research as research


def proposal(goal, parent=None):
    return {'goal': goal, 'parent': parent, 'summary': 'Find a fixed point of 2x+1',
            'change': {'kind': 'representation' if parent else 'baseline',
                       'before': 'Iterate the affine map' if parent else 'No route yet',
                       'after': 'Solve x=2x+1 algebraically' if parent else 'Iterate the affine map'},
            'discriminator': {'question': 'Does this route satisfy its exact obligation?',
                              'if_pass': 'Interpret this scoped result for the fixed-point goal',
                              'if_fail': 'Replace this route; retain the counterexample'},
            'obligations': [{'id': 'claim', 'meaning': 'Check the proposed mathematical route',
                'tool': 'formal', 'spec': {'schema': 1, 'kind': 'affine_fixed_point',
                                          'model': {'matrix': [['2']], 'bias': ['1']}, 'point': ['-1']}}]}


class ResearchStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.goal = research.start(self.root, 'Find a fixed point')['record_id']

    def test_restart_and_child_directory_reuse_same_goal_and_ledger(self):
        child = self.root / 'experiment'
        child.mkdir()
        repeated = research.start(child, 'Find a fixed point')
        self.assertEqual(repeated['record_id'], self.goal)
        self.assertEqual(repeated['context']['project_root'], str(self.root.resolve()))
        self.assertFalse((child / '.rds').exists())

    def test_equivalent_root_spelling_replays_original_records(self):
        child = self.root / 'child'
        child.mkdir()
        rows = research._entries(child / '..')
        self.assertEqual(rows[self.goal]['payload']['summary'], 'Find a fixed point')
        self.assertEqual(rows, research._entries(self.root.resolve()))

    def test_change_retains_parent_and_goal(self):
        old = research.propose(self.root, proposal(self.goal))['record_id']
        new = research.propose(self.root, proposal(self.goal, old))
        self.assertEqual(new['context']['routes'][-1]['refs'], [self.goal, old])
        self.assertNotIn('payload', new['context']['routes'][-1])
        self.assertEqual(new['context']['routes'][-1]['change']['kind'], 'representation')
        self.assertEqual(len(new['context']['routes']), 2)
        self.assertEqual(new['context']['scientific_support'], 'UNKNOWN')

    def test_unchanged_or_cross_goal_jump_is_rejected(self):
        old = research.propose(self.root, proposal(self.goal))['record_id']
        spec = proposal(self.goal, old)
        spec['change']['after'] = spec['change']['before']
        with self.assertRaises(ValueError):
            research.propose(self.root, spec)
        other = research.start(self.root, 'A different question')['record_id']
        with self.assertRaises(ValueError):
            research.propose(self.root, proposal(other, old))
        with self.assertRaises(ValueError):
            research.select(self.root, old, 'Use this route')

    def test_original_bytes_are_checked_on_every_read(self):
        research.propose(self.root, proposal(self.goal))
        path = next((self.root / '.rds' / 'cas').glob('*.json'))
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            research.context(self.root)

    def test_selection_is_a_declared_choice_not_evidence(self):
        ident = research.propose(self.root, proposal(self.goal))['record_id']
        state = research.select(self.root, ident, 'Try this explanation')['context']
        self.assertEqual(state['selected']['refs'], [ident])
        self.assertEqual(state['routes'][0]['obligations'][0]['result']['status'], 'UNTESTED')
        self.assertEqual(state['next_action'], 'CHECK_OR_COLLECT_EVIDENCE')


if __name__ == '__main__':
    unittest.main()
