"""Protocol check 2: a campaign that declares its hypotheses is not missing rivals.

The stage-2 seam is ``_all_declared`` in ``decide``: while every surviving hypothesis
was declared by the caller, the ``RIVALS`` round is skipped.  These four tests pin it
to the acceptance criteria in ``docs/stage2-hypothesis-protocol.md``: a declared
campaign never enumerates, a campaign without the block still does, a refuted
declaration reopens enumeration, and the helper's boundary.
"""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

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
SAY = 'synthesizer called: the declared set was not respected'


class DeclaredSetTests(unittest.TestCase):

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        compass.clear_fits_memo()

    def tearDown(self):
        self.temp.cleanup()

    def campaign(self, name, hypotheses):
        root = self.base / name
        declaration = fx.law_campaign(root, LAW, XS, hypotheses=hypotheses)
        self.assertEqual(initialize(root, declaration)['status'], 'INITIALIZED')
        return root

    def actions(self, root):
        return [d['action'] for d in compass.fold(cstore.store_for(root))['decisions']]

    def test_a_declared_campaign_never_reaches_the_synthesizer(self):
        """Check 2: with the block present, every round is a purchase or a verdict."""
        root = self.campaign('declared', [('sq', LAW)])
        with mock.patch('rds_compass_model.synthesize', side_effect=AssertionError(SAY)):
            result = drive(root, publish=False)
        self.assertEqual(result['status'], 'GOAL_IDENTIFIED')
        self.assertNotIn('RIVALS', self.actions(root))

    def test_the_same_campaign_without_the_block_still_enumerates(self):
        """The reverse reading: absent the block, enumeration is the path taken."""
        root = self.campaign('undeclared', [])
        with mock.patch('rds_compass_model.synthesize', side_effect=AssertionError(SAY)):
            with self.assertRaisesRegex(AssertionError, SAY):
                drive(root, publish=False)

    def test_a_refuted_declaration_reopens_enumeration(self):
        """Once the assertion is refuted, evidence is all that is left: synthesis runs."""
        root = self.campaign('refuted', [('lin', 'x+1')])
        real = compass._fits
        seen = []

        def spy(state, **kwargs):
            seen.append({hyp['id']: hyp['status'] for hyp in state['hypotheses'].values()})
            return real(state, **kwargs)

        with mock.patch.object(compass, '_fits', side_effect=spy):
            drive(root, publish=False)
        hypotheses = compass.fold(cstore.store_for(root))['hypotheses']
        self.assertEqual(hypotheses['lin']['status'], 'REFUTED')
        self.assertTrue(seen, 'the refuted declaration never reopened synthesis')
        self.assertTrue(any(rows.get('lin') == 'REFUTED' for rows in seen),
                        'synthesis ran only while the declaration was still untested')

    def test_all_declared_boundary(self):
        """Empty, mixed and all-declared rows: only the last suppresses enumeration."""
        self.assertFalse(compass._all_declared([]))
        self.assertFalse(compass._all_declared([{'origin': 'declared'},
                                                {'origin': 'rival-synthesis:fit'}]))
        self.assertTrue(compass._all_declared([{'origin': 'declared'},
                                               {'origin': 'declared'}]))
