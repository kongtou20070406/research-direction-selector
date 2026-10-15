"""Adversarial regressions for Compass invariants.

Each case reproduces a defect found by an independent Codex (gpt-6.1-sol, xhigh) red-team pass
against the 6.0.0 candidate. Every case failed before its fix.
"""
import json
import os
from pathlib import Path
import signal
import sqlite3
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import compass_fixture as fx
import rds_compass as compass
import rds_compass_rsi as rsi
import rds_compass_store as cstore
from rds_compass_cli import exit_code
from rds_compass_declaration import initialize
from rds_compass_drive import drive

GRAMMAR = {'variables': ['x'], 'constants': [0], 'operators': []}
AGENT = '''import json, sys
sys.path.insert(0, SCRIPTS)
import rds_compass as compass
import rds_compass_store as cstore
packet = json.load(sys.stdin)
store = cstore.store_for('.')
EXTRA
decisions = [{'id': i['id'], 'disposition': DISPOSITION,
              'reason': 'Checked the supplied evidence and executable rulebook',
              'next_step': 'Measure another point to discriminate the live rivals'} for i in packet['items']]
json.dump({'schema': 1, 'packet_sha256': packet['sha256'], 'decisions': decisions,
           'proposals': PROPOSALS}, sys.stdout)
'''


class Campaign(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def law(self, law='x', xs=range(4), **options):
        declaration = fx.law_campaign(self.root, law, xs, **options)
        self.assertEqual(initialize(self.root, declaration)['status'], 'INITIALIZED')
        return cstore.store_for(self.root)

    def buy(self, store, *ids):
        for ident in ids:
            manifest = compass.manifest(compass.fold(store), ident)
            store.register(manifest)
            self.assertEqual(store.execute(manifest['id'])['run_status'], 'SUCCEEDED')

    def agent(self, extra, disposition, proposals):
        source = AGENT.replace('SCRIPTS', repr(str(ROOT / 'scripts'))).replace('EXTRA', extra)
        fx.write(self.root / 'agent.py', source.replace('DISPOSITION', repr(disposition)).replace('PROPOSALS', proposals))


class ReceiptBindingTests(Campaign):
    def test_changed_stream_binding_blocks_goal_identification(self):
        store = self.law(hypotheses=[('identity', 'x')])
        self.buy(store, 'x0', 'x1', 'x2', 'x3')
        stream = next(a for a in cstore.receipts(store)['compass-x0']['artifacts'] if a['kind'] == 'stdout.bin')
        (self.root / stream['path']).write_bytes(b'forged stdout evidence\n')
        result = drive(self.root, publish=False)
        self.assertNotEqual(result['status'], 'GOAL_IDENTIFIED', result['verification'])
        self.assertIn({'candidate': 'x0', 'binding': 'ARTIFACT_HASH_MISMATCH', 'artifact': 'stdout.bin'},
                      result['verification']['stale'])

    def test_report_rechecks_changed_output_after_success(self):
        store = self.law(hypotheses=[('identity', 'x')])
        self.buy(store, 'x0', 'x1', 'x2', 'x3')
        self.assertEqual(drive(self.root, publish=False)['status'], 'GOAL_IDENTIFIED')
        fx.write(self.root / 'outputs/compass/x0.json', {'outputs': {'y': 999}})
        verification = compass.report(store)['verification']
        self.assertFalse(verification['receipts_rechecked'], verification)
        self.assertEqual(verification['report_recheck']['status'], 'STALE')

    def test_improve_does_not_accept_a_stale_arm(self):
        initialize(self.root, fx.improve_campaign(self.root, {'base': {'s': 10}, 'better': {'s': 12}}))
        store = cstore.store_for(self.root)
        self.assertEqual(drive(self.root, publish=False)['verification']['improvement']['recommended'], 'better')
        fx.write(self.root / 'outputs/compass/better.json', {'outputs': {'score': -100}, 'samples': {'s': -100}})
        verification = compass.verify(store, compass.fold(store))
        self.assertEqual(verification['stale'][0]['binding'], 'OUTPUT_HASH_MISMATCH')
        self.assertEqual(verification['improvement']['arms'][0]['verdict'], 'STALE_EVIDENCE')
        self.assertIsNone(verification['improvement']['recommended'])


class NoveltyTests(Campaign):
    def test_pre_adoption_receipts_are_not_novel_confirmations(self):
        store = self.law(grammar=GRAMMAR, synthesis={'max_nodes': 1}, rsi={'enabled': False})
        self.buy(store, 'x0', 'x1')
        compass.ingest(store)
        self.buy(store, 'x2', 'x3')  # Completed before adoption, not yet ingested.
        self.assertLess(max(r['ended_at'] for r in cstore.receipts(store).values()), time.time())
        self.assertTrue(compass.escape(store, compass.fold(store), {'trigger': 'NO_RIVALS'})['adopted'])
        result = drive(self.root, publish=False)
        self.assertEqual(len(cstore.receipts(store)), 4)  # No new prediction was bought.
        self.assertNotEqual(result['status'], 'GOAL_IDENTIFIED', result.get('verification'))


class JumpResponseTests(Campaign):
    def test_response_to_old_evidence_is_not_adopted(self):
        store = self.law('0 if x < 2 else 1', range(3), grammar=GRAMMAR, rsi={'enabled': False}, agent='adopt')
        # The responder buys and ingests a refuting measurement while it "thinks".
        self.agent("manifest = compass.manifest(compass.fold(store), 'x2')\nstore.register(manifest)\n"
                   "store.execute(manifest['id'])\ncompass.ingest(store)", 'adopt', '[]')
        self.buy(store, 'x0', 'x1')
        compass.ingest(store)
        result = compass.escape(store, compass.fold(store), {'trigger': 'NO_RIVALS'})
        self.assertEqual(compass.fold(store)['observations']['x2']['outputs'], {'y': '1'})
        self.assertEqual((result['status'], result['adopted']), ('STALE_RESPONSE', []), result)

    def test_agent_tool_hypothesis_is_retracted_with_its_tool(self):
        store = self.law('0 if x == 11 else 2*popcount(x)+1', [0, 1, 2, 3, 11], agent='adopt')
        self.agent("tool = next(t for t in compass.fold(store)['tools'].values() if t['status'] == 'QUALIFIED')\n"
                   "feature = next(iter(tool['features']))", 'reject',
                   "[{'id': 'agentlaw', 'predict': {'y': '2*' + feature + '+1'}}]")
        self.buy(store, 'x0', 'x1', 'x2', 'x3')
        state, _ = compass.ingest(store)
        self.assertEqual(rsi.escalate(store, state, {'trigger': 'TEST'})['qualified'], ['digitsum2'])
        self.assertIn('agentlaw', compass.escape(store, compass.fold(store), {'trigger': 'NO_RIVALS'})['adopted'])
        self.buy(store, 'x11')
        state, _ = compass.ingest(store)
        self.assertEqual(next(iter(state['tools'].values()))['status'], 'RETRACTED')
        self.assertEqual(state['hypotheses']['agentlaw']['status'], 'RETRACTED', state['hypotheses']['agentlaw'])


class InterruptTests(Campaign):
    def test_sigterm_before_child_launch_settles_the_attempt(self):
        store = self.law(xs=[1, 2], hypotheses=[('a', 'x'), ('b', '2*x')])
        write_text = Path.write_text

        def interrupt_after_write(path, *args, **kwargs):
            result = write_text(path, *args, **kwargs)
            if path.name == 'manifest.json' and path.parent.parent.name == 'project-artifacts':
                os.kill(os.getpid(), signal.SIGTERM)
            return result

        with patch.object(Path, 'write_text', interrupt_after_write):
            result = drive(self.root, publish=False)
        self.assertEqual((result['status'], exit_code(result)), ('INTERRUPTED', 2))
        run_id = result['workset']['handoff']['detail']['in_flight_run']
        self.assertEqual(compass.fold(store)['runs'][run_id], 'INTERRUPTED', cstore.receipts(store))


class HashChainTests(Campaign):
    def test_tampered_terminal_sequence_is_detected(self):
        store = self.law()
        compass.handoff(store, 'JUDGMENT_REQUIRED')
        with sqlite3.connect(store.path) as db:
            raw = db.execute('SELECT body FROM events ORDER BY id DESC LIMIT 1').fetchone()[0]
            self.assertEqual(json.loads(raw)['kind'], 'COMPASS_HANDOFF')
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        # Media tampering on the tail row: no successor row carries its digest.
        damaged = raw.replace('"seq":2', '"seq":0').encode('utf-8')
        self.assertNotEqual(damaged, raw.encode('utf-8'))
        original = store.path.read_bytes()
        self.assertEqual(original.count(raw.encode('utf-8')), 1)
        store.path.write_bytes(original.replace(raw.encode('utf-8'), damaged))
        with self.assertRaises(cstore.CompassIntegrityError):
            compass.report(store)


if __name__ == '__main__':
    unittest.main()
