"""The ``_fits`` synthesis memo: equal input bytes reuse the search, nothing else does."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compass_fixture as fx
import rds_compass as compass
import rds_compass_model as model
import rds_compass_store as cstore
from rds_compass_declaration import initialize


class Memo(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        compass.clear_fits_memo()

    def tearDown(self):
        compass.clear_fits_memo()
        self.temp.cleanup()

    def state(self, name='campaign', law='x+1', xs=range(6), **extra):
        """A real declared campaign, folded into state, with no measurements yet."""
        root = self.base / name
        path = fx.law_campaign(root, law, xs, hypotheses=[('lin', 'x+1')], **extra)
        self.assertEqual(initialize(root, path)['status'], 'INITIALIZED')
        self.store = cstore.store_for(root)
        return compass.fold(self.store)

    def observe(self, state, values):
        """Install MEASURED rows (candidate id, y) so ``_fits`` has evidence to fit."""
        for seq, (ident, y) in enumerate(values, 1):
            state['observations'][ident] = {
                'candidate': ident, 'run_id': 'compass-' + ident, 'status': 'MEASURED',
                'seq': seq, 'outputs': {'y': model.text(y)},
                'receipt_sha256': 'receipt:' + ident}
        return compass.measured(state)

    def spy(self):
        return mock.patch.object(model, 'synthesize', wraps=model.synthesize)

    def test_identical_inputs_fit_once_and_return_the_same_bytes(self):
        state = self.state()
        self.observe(state, [('x0', 1), ('x1', 2), ('x2', 3)])
        with self.spy() as call:
            first = compass._fits(state)
            second = compass._fits(state)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(compass.canonical(first), compass.canonical(second))
        self.assertTrue(first[0], 'the campaign must produce at least one fit')

    def test_a_changed_row_is_a_different_signature(self):
        state = self.state()
        rows = self.observe(state, [('x0', 1), ('x1', 2), ('x2', 3)])
        changed = copy.deepcopy(rows)
        changed[1]['outputs']['y'] = model.exact(99)
        with self.spy() as call:
            compass._fits(state, rows=rows)
            compass._fits(state, rows=copy.deepcopy(rows))
            compass._fits(state, rows=changed)
        self.assertEqual(call.call_count, 2)

    def test_an_input_value_change_is_a_different_signature(self):
        state = self.state()
        rows = self.observe(state, [('x0', 1), ('x1', 2), ('x2', 3)])
        moved = copy.deepcopy(rows)
        moved[0]['inputs']['x'] = model.exact(7)
        with self.spy() as call:
            compass._fits(state, rows=rows)
            compass._fits(state, rows=moved)
        self.assertEqual(call.call_count, 2)

    def test_grammar_operator_order_and_content_are_part_of_the_signature(self):
        state = self.state()
        rows = self.observe(state, [('x0', 1), ('x1', 2), ('x2', 3)])
        state['plan']['grammar'] = {'operators': ['add', 'sub']}
        with self.spy() as call:
            compass._fits(state, rows=rows)
            compass._fits(state, rows=rows)
            state['plan']['grammar'] = {'operators': ['sub', 'add']}
            compass._fits(state, rows=rows)
            state['plan']['grammar'] = {'operators': ['add', 'mul']}
            compass._fits(state, rows=rows)
        # Same call once, then each reordering and each substitution is a fresh search.
        self.assertEqual(call.call_count, 3)

    def test_probe_order_is_part_of_the_signature(self):
        state = self.state()
        rows = self.observe(state, [('x0', 1), ('x1', 2), ('x2', 3)])
        left = [{'x': model.exact(0)}, {'x': model.exact(4)}]
        right = list(reversed(left))
        with self.spy() as call:
            compass._fits(state, rows=rows, probes=left)
            compass._fits(state, rows=rows, probes=left)
            compass._fits(state, rows=rows, probes=right)
        self.assertEqual(call.call_count, 2)

    def test_the_environment_variable_bypasses_the_memo(self):
        state = self.state()
        self.observe(state, [('x0', 1), ('x1', 2), ('x2', 3)])
        with mock.patch.dict(os.environ, {'RDS_NO_FITS_MEMO': '1'}):
            with self.spy() as call:
                compass._fits(state)
                compass._fits(state)
        self.assertEqual(call.call_count, 2)
        self.assertEqual((compass._FITS_MEMO_STATS['hit'], compass._FITS_MEMO_STATS['miss']), (0, 0))

    def test_switching_the_memo_off_returns_the_same_fits(self):
        # The reverse test for the memo: with RDS_NO_FITS_MEMO=1 the search runs exactly as before
        # the change and must land on byte-identical fits and notes.
        state = self.state()
        rows = self.observe(state, [('x0', 1), ('x1', 2), ('x2', 3)])
        memoized = compass.canonical(compass._fits(state, rows=copy.deepcopy(rows)))
        with mock.patch.dict(os.environ, {'RDS_NO_FITS_MEMO': '1'}):
            bypassed = compass.canonical(compass._fits(state, rows=copy.deepcopy(rows)))
        self.assertEqual(memoized, bypassed)

    def test_a_hit_returns_a_deep_copy(self):
        state = self.state()
        self.observe(state, [('x0', 1), ('x1', 2), ('x2', 3)])
        snapshot = compass.canonical(compass._fits(state))
        found, notes = compass._fits(state)
        self.assertEqual(compass.canonical((found, notes)), snapshot)
        notes['rsi'] = {'qualified': True}
        notes['WHOLE_LAW']['status'] = 'TAMPERED'
        found[0][0]['expression'] = {'op': 'tampered'}
        found.append(('junk', 'WHOLE_LAW', None))
        self.assertEqual(compass.canonical(compass._fits(state)), snapshot)

    def test_escape_both_escalated_refits_hit_when_the_fold_adds_no_evidence(self):
        # Mirrors scripts/rds_compass.py: the first ``_fits(state, escalate=True)`` at the jump
        # and the refit after a qualified RSI folded the same ledger back. Real receipts, so the
        # second pair of searches sees the same rows and must be memos, not rescans.
        state = self.state(law='x*x+1', xs=range(-4, 5),
                           synthesis={'max_nodes': 1, 'max_candidates': 10})
        for ident in ('m4', 'm3', 'm2'):
            manifest = compass.manifest(compass.fold(self.store), ident)
            self.store.register(manifest)
            self.store.execute(manifest['id'])
        state, _ = compass.ingest(self.store)
        qualified = {'qualified': True, 'tools': [], 'status': 'QUALIFIED'}
        with mock.patch('rds_compass_rsi.escalate', return_value=qualified):
            with self.spy() as call:
                result = compass.escape(self.store, state, {'trigger': 'REFUTED_STREAK'})
        self.assertEqual(result['status'], 'NO_RIVAL_WITHIN_BOUND')
        # Two `_fits` calls, whole + escalated each: two real searches, two hits. The other two
        # calls are RESIDUAL_CORRECTION's ``shifted`` search, which is deliberately not memoized
        # (its base comes from the refuted rulebook, so it is not one of the three cached modes).
        self.assertEqual((call.call_count, compass._FITS_MEMO_STATS['miss'],
                          compass._FITS_MEMO_STATS['hit']), (4, 2, 2))


if __name__ == '__main__':
    unittest.main()
