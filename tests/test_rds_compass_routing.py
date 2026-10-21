"""Regressions for how Compass routes an EIG-triggered jump.

A jump is a two-part event: the DECISION is recorded before ``escape()`` runs, and
the JUMP record proves it actually did.  These tests pin the three consequences of
that split: a decision without its JUMP must not count as routed, basins and EIG
must share one jump budget, and a lone survivor must be offered rivals before EIG
gives up on it.
"""

from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import compass_fixture as fx  # noqa: E402
import rds_compass as compass  # noqa: E402
import rds_compass_store as cstore  # noqa: E402
from rds_compass_declaration import initialize  # noqa: E402
from rds_compass_drive import drive  # noqa: E402

LAW = 'x*x+1'
XS = range(0, 3)
HYPOTHESES = [('lin', 'x+1')]


class EigRoutingTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def campaign(self, name, hypotheses=HYPOTHESES, **overrides):
        root = self.base / name
        declaration = fx.law_campaign(root, LAW, XS, hypotheses=hypotheses, agent='reject', **overrides)
        self.assertEqual(initialize(root, declaration)['status'], 'INITIALIZED')
        return root

    def seed(self, root, ident, expression):
        """A survivor that arrived from outside the declaration, not ``origin='declared'``."""
        store = cstore.store_for(root)
        hyp = compass.model.compile_hypothesis(
            {'id': ident, 'predict': {'y': expression}, 'origin': 'staged'}, ['x'], ['y'])
        cstore.append(store, 'HYPOTHESIS', ident, hyp)

    def record(self, root, *steps):
        store = cstore.store_for(root)
        for step in steps:
            compass.record(store, compass.fold(store), step)

    def test_a_crashed_jump_does_not_block_eig_routing(self):
        root = self.campaign('crashed')
        # Exactly what a drive killed between ``record`` and ``escape`` leaves behind:
        # the EIG ESCAPE is on the ledger, but no JUMP ever followed it.
        self.record(root,
                    {'action': 'RIVALS', 'live': ['lin']},
                    {'action': 'ESCAPE', 'basin': {'trigger': 'LOW_MARGINAL_EIG', 'streak': 0}})
        result = drive(root, max_steps=1, publish=False)
        self.assertEqual(result['steps'][0]['action'], 'ESCAPE')
        self.assertEqual(result['steps'][0]['trigger'], 'LOW_MARGINAL_EIG')

    def test_a_spent_jump_budget_sends_the_lone_survivor_to_confirmation_not_a_jump(self):
        root = self.campaign('spent', escape={'max_jumps': 1})
        # The basin route already took the only jump.  A duplicate proposal keeps the
        # lone hypothesis alive, so only the shared budget can stop the next EIG jump.
        fx.write(root / 'agent-mode.json', {'mode': 'reject', 'proposal': 'x+1'})
        self.record(root, {'action': 'RIVALS', 'live': ['lin']})
        store = cstore.store_for(root)
        jumped = compass.escape(store, compass.fold(store), {'trigger': 'ALL_REFUTED', 'streak': 2})
        self.assertEqual(jumped['status'], 'NONE_ADOPTED')
        self.assertEqual(len(compass.fold(store)['jumps']), 1)
        result = drive(root, max_steps=1, publish=False)
        # No further jump, and no claim either: the survivor is tested at an input it did not see.
        self.assertEqual(result['steps'][0]['action'], 'CONFIRM')
        self.assertNotEqual(result['status'], 'GOAL_IDENTIFIED')
        self.assertEqual(len(compass.fold(store)['jumps']), 1)
        # x + 1 is wrong off its two coincident points (x*x + 1 at 0 and 1): testing refutes it.
        result = drive(root, publish=False)
        self.assertEqual(result['status'], 'SEARCH_EXHAUSTED')
        self.assertEqual(compass.fold(store)['hypotheses']['lin']['status'], 'REFUTED')
        self.assertEqual(len(compass.fold(store)['jumps']), 1)

    def test_a_lone_survivor_gets_the_rival_offer_before_an_eig_jump(self):
        root = self.campaign('rivals-first', hypotheses=())
        # Stage 2 skips the offer for a declared survivor, so the pre-existing route needs a
        # survivor from outside the declaration: the order it pins is unchanged.
        self.seed(root, 'lin', 'x+1')
        self.assertEqual([h['id'] for h in compass.live(compass.fold(cstore.store_for(root)))], ['lin'])
        result = drive(root, max_steps=2, publish=False)
        self.assertEqual([step['action'] for step in result['steps']], ['RIVALS', 'ESCAPE'])
        self.assertEqual(result['steps'][1]['trigger'], 'LOW_MARGINAL_EIG')


class DerivedExpansionTests(unittest.TestCase):
    """An intermediate the synthesizer invented must not reach the hypothesis compiler."""

    def test_a_derived_intermediate_is_expanded_before_it_is_compiled(self):
        # ``_fits`` asks for pairwise intermediates such as ``B_times_g_factor``; a fit may use
        # that leaf, but the campaign never declares it as an input.
        node = compass.model.parse('B_times_g_factor * q / (2 * m)', {'B_times_g_factor', 'q', 'm'})
        expanded = compass._expand_derived(node, {'B_times_g_factor': 'B * g_factor'},
                                           ['B', 'g_factor', 'q', 'm'])
        self.assertEqual(compass.model.render(expanded), '(((B * g_factor) * q) / (2 * m))')
        hyp = compass.model.compile_hypothesis({'id': 'h', 'predict': {'y': expanded}},
                                               ['B', 'g_factor', 'q', 'm'], ['y'], goal='y')
        self.assertIn('y', hyp['predict'])

    def test_an_expression_without_derived_leaves_is_unchanged(self):
        node = compass.model.parse('q / m', {'q', 'm'})
        self.assertEqual(compass.model.render(compass._expand_derived(node, None, ['q', 'm'])),
                         compass.model.render(node))


if __name__ == '__main__':
    unittest.main()
