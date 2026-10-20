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

    def campaign(self, name, **overrides):
        root = self.base / name
        declaration = fx.law_campaign(root, LAW, XS, hypotheses=HYPOTHESES, agent='reject', **overrides)
        self.assertEqual(initialize(root, declaration)['status'], 'INITIALIZED')
        return root

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

    def test_a_spent_jump_budget_ends_eig_routing_with_a_handoff(self):
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
        self.assertEqual(result['status'], 'SEARCH_EXHAUSTED')
        self.assertEqual(result['steps'][0]['action'], 'HANDOFF')
        self.assertEqual(result['steps'][0]['trigger'], 'LOW_MARGINAL_EIG')
        self.assertEqual(len(compass.fold(store)['jumps']), 1)

    def test_a_lone_survivor_gets_the_rival_offer_before_an_eig_jump(self):
        root = self.campaign('rivals-first')
        self.assertEqual([h['id'] for h in compass.live(compass.fold(cstore.store_for(root)))], ['lin'])
        result = drive(root, max_steps=2, publish=False)
        self.assertEqual([step['action'] for step in result['steps']], ['RIVALS', 'ESCAPE'])
        self.assertEqual(result['steps'][1]['trigger'], 'LOW_MARGINAL_EIG')


if __name__ == '__main__':
    unittest.main()
