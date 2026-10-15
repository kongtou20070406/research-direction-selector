"""Adversarial regressions for Compass invariants.

Each case reproduces a defect found by an independent Codex (gpt-6.1-sol, xhigh) red-team pass
against the 6.0.0 candidate, or by the Codex review of PR #357. Every case failed before its fix.
"""
from contextlib import closing
import hashlib
import json
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
import rds_compass_model as model
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
        # The stored claim rested on that receipt; the report keeps it only as history.
        self.assertEqual((verification['claim'], verification['claim_at_verification']),
                         ('NOT_ESTABLISHED', 'CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS'))

    def test_report_withdraws_a_recommendation_whose_evidence_went_stale(self):
        initialize(self.root, fx.improve_campaign(self.root, {'base': {'s': 10}, 'better': {'s': 12}}))
        store = cstore.store_for(self.root)
        self.assertEqual(drive(self.root, publish=False)['verification']['improvement']['recommended'], 'better')
        fx.write(self.root / 'outputs/compass/better.json', {'outputs': {'score': -100}, 'samples': {'s': -100}})
        improvement = compass.report(store)['verification']['improvement']
        self.assertEqual([a['verdict'] for a in improvement['arms']], ['STALE_EVIDENCE'])
        self.assertEqual((improvement['recommended'], improvement['recommended_at_verification']), (None, 'better'))

    def test_improve_does_not_accept_a_stale_arm(self):
        initialize(self.root, fx.improve_campaign(self.root, {'base': {'s': 10}, 'better': {'s': 12}}))
        store = cstore.store_for(self.root)
        self.assertEqual(drive(self.root, publish=False)['verification']['improvement']['recommended'], 'better')
        fx.write(self.root / 'outputs/compass/better.json', {'outputs': {'score': -100}, 'samples': {'s': -100}})
        verification = compass.verify(store, compass.fold(store))
        self.assertEqual(verification['stale'][0]['binding'], 'OUTPUT_HASH_MISMATCH')
        self.assertEqual(verification['improvement']['arms'][0]['verdict'], 'STALE_EVIDENCE')
        self.assertIsNone(verification['improvement']['recommended'])

    def test_missing_receipt_row_is_stale_evidence(self):
        store = self.law(hypotheses=[('identity', 'x')])
        self.buy(store, 'x0', 'x1', 'x2', 'x3')
        compass.ingest(store)
        # Media damage drops a retained observation's receipt row; the append-only trigger cannot stop it.
        with closing(sqlite3.connect(store.path)) as db, db:
            db.execute('DROP TRIGGER receipts_no_delete')
            db.execute("DELETE FROM receipts WHERE run_id='compass-x0'")
        verification = compass.verify(store, compass.fold(store))
        self.assertFalse(verification['receipts_rechecked'])
        self.assertIn({'candidate': 'x0', 'binding': 'RECEIPT_MISSING'}, verification['stale'])
        self.assertNotEqual(drive(self.root, publish=False)['status'], 'GOAL_IDENTIFIED')


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

    def test_timed_out_agent_streams_are_bound_by_bytes(self):
        path = fx.law_campaign(self.root, '0 if x < 2 else 1', range(3), grammar=GRAMMAR, rsi={'enabled': False},
                               agent='adopt')
        value = json.loads(path.read_text(encoding='utf-8'))
        value['agent']['timeout_seconds'] = 1
        fx.write(path, value)
        initialize(self.root, path)
        fx.write(self.root / 'agent.py', 'import sys, time\nsys.stdin.buffer.read()\n'
                 "sys.stdout.buffer.write(b'partial-out'); sys.stdout.flush()\n"
                 "sys.stderr.buffer.write(b'partial-err'); sys.stderr.flush()\ntime.sleep(20)\n")
        store = cstore.store_for(self.root)
        self.buy(store, 'x0', 'x1')
        compass.ingest(store)
        result = compass.escape(store, compass.fold(store), {'trigger': 'NO_RIVALS'})
        self.assertIn('AGENT_TIMEOUT', result['errors'])
        evidence = [e['value']['agent'] for e in cstore.events(store) if e['kind'] == cstore.PREFIX + 'DISPOSITION'][-1]
        self.assertEqual((evidence['status'], evidence['phase'], evidence['timeout_seconds']), ('AGENT_TIMEOUT', 'RUNNING', 1))
        self.assertEqual(evidence['stdout_sha256'], hashlib.sha256(b'partial-out').hexdigest())
        self.assertEqual(evidence['stderr_sha256'], hashlib.sha256(b'partial-err').hexdigest())
        self.assertEqual(len(evidence['input_sha256']), 64)


class InterruptTests(Campaign):
    def test_sigterm_before_child_launch_settles_the_attempt(self):
        store = self.law(xs=[1, 2], hypotheses=[('a', 'x'), ('b', '2*x')])
        write_text = Path.write_text

        def interrupt_after_write(path, *args, **kwargs):
            result = write_text(path, *args, **kwargs)
            if path.name == 'manifest.json' and path.parent.parent.name == 'project-artifacts':
                # raise() reaches the installed handler on every platform; os.kill on Windows
                # is TerminateProcess and would end the test runner itself.
                signal.raise_signal(signal.SIGTERM)
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
        with closing(sqlite3.connect(store.path)) as db:
            raw = db.execute('SELECT body FROM events ORDER BY id DESC LIMIT 1').fetchone()[0]
            self.assertEqual(json.loads(raw)['kind'], 'COMPASS_HANDOFF')
            db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        # Media tampering on the tail row: no successor row carries its digest. The bytes are
        # rewritten in place, same length, with no connection open (Windows refuses a truncate).
        damaged = raw.replace('"seq":2', '"seq":0').encode('utf-8')
        self.assertNotEqual(damaged, raw.encode('utf-8'))
        self.assertEqual(len(damaged), len(raw.encode('utf-8')))
        original = store.path.read_bytes()
        self.assertEqual(original.count(raw.encode('utf-8')), 1)
        with store.path.open('r+b') as media:
            media.seek(original.index(raw.encode('utf-8')))
            media.write(damaged)
        with self.assertRaises(cstore.CompassIntegrityError):
            compass.report(store)


class DeclarationReviewTests(Campaign):
    def declaration(self, path, edit):
        value = json.loads(path.read_text(encoding='utf-8'))
        edit(value)
        fx.write(path, value)
        return path

    def test_unknown_improvement_threshold_is_not_a_settled_evaluation(self):
        path = fx.improve_campaign(self.root, {'base': {'s': 10}, 'better': {'s': 12}})
        initialize(self.root, self.declaration(path, lambda d: d['profile'].pop('min_gain')))
        result = drive(self.root, publish=False)
        self.assertEqual((result['status'], exit_code(result)), ('JUDGMENT_REQUIRED', 2))
        self.assertEqual(result['workset']['handoff']['detail']['unknown_thresholds'], ['min_gain'])
        self.assertIsNone(result['verification']['improvement']['recommended'])

    def test_sample_mismatch_is_not_a_settled_evaluation(self):
        # The arm reports sample s only; the baseline reports s and t. No per-sample comparison exists.
        path = fx.improve_campaign(self.root, {'base': {'s': 10, 't': 10}, 'odd': {'s': 12}})
        initialize(self.root, path)
        result = drive(self.root, publish=False)
        self.assertEqual((result['status'], exit_code(result)), ('JUDGMENT_REQUIRED', 2))
        self.assertEqual(result['workset']['handoff']['detail']['judgment_required'], ['odd'])
        self.assertIsNone(result['verification']['improvement']['recommended'])

    def test_negative_min_gain_is_rejected(self):
        path = fx.improve_campaign(self.root, {'base': {'s': 10}, 'worse': {'s': 9}}, min_gain=-1)
        with self.assertRaisesRegex(ValueError, 'min_gain must be at least 0'):
            initialize(self.root, path)

    def test_string_direction_is_rejected(self):
        path = fx.improve_campaign(self.root, {'base': {'s': 10}, 'better': {'s': 12}})
        edit = lambda d: d['observables']['score'].update(higher_is_better='false')
        with self.assertRaisesRegex(ValueError, 'higher_is_better must be true or false'):
            initialize(self.root, self.declaration(path, edit))

    def test_metric_direction_is_never_defaulted(self):
        # For a loss, the arm reporting 12 is worse; a defaulted direction would recommend it.
        path = fx.improve_campaign(self.root, {'base': {'s': 10}, 'worse': {'s': 12}})
        edit = lambda d: d['observables']['score'].pop('higher_is_better')
        plan = initialize(self.root, self.declaration(path, edit))['plan']
        self.assertIn('observables.score.higher_is_better', plan['unknown'])
        result = drive(self.root, publish=False)
        self.assertEqual((result['status'], exit_code(result)), ('JUDGMENT_REQUIRED', 2))
        self.assertIn('higher_is_better', result['workset']['handoff']['detail']['unknown_thresholds'])
        improvement = result['verification']['improvement']
        self.assertEqual([a['verdict'] for a in improvement['arms']], ['JUDGMENT_REQUIRED'])
        self.assertIsNone(improvement['recommended'])

    def test_metric_tolerance_is_never_defaulted(self):
        path = fx.improve_campaign(self.root, {'base': {'s': 10}, 'better': {'s': 12}})
        plan = initialize(self.root, self.declaration(path, lambda d: d['observables']['score'].pop('tolerance')))['plan']
        self.assertIn('observables.score.tolerance', plan['unknown'])
        result = drive(self.root, publish=False)
        self.assertEqual((result['status'], exit_code(result)), ('JUDGMENT_REQUIRED', 2))
        self.assertIn('tolerance', result['workset']['handoff']['detail']['unknown_thresholds'])
        improvement = result['verification']['improvement']
        self.assertEqual([a['verdict'] for a in improvement['arms']], ['JUDGMENT_REQUIRED'])
        self.assertIsNone(improvement['recommended'])

    def test_hypothesis_must_predict_the_goal_observable(self):
        path = fx.law_campaign(self.root, 'x', range(4))

        def edit(value):
            value['observables']['z'] = {'tolerance': {'abs': 0}}
            value['hypotheses'] = [{'id': 'other', 'predict': {'z': 'x'}}]
        with self.assertRaisesRegex(ValueError, 'goal observable y'):
            initialize(self.root, self.declaration(path, edit))
        # The same rule binds agent adaptations and proposals.
        with self.assertRaisesRegex(ValueError, 'goal observable y'):
            model.compile_hypothesis({'id': 'p', 'predict': {'z': 'x'}}, ['x'], ['y', 'z'], goal='y')

    def test_boolean_timeouts_are_rejected(self):
        for index, edit in enumerate((lambda d: d['experiment'].update(timeout_seconds=True),
                                      lambda d: d['agent'].update(timeout_seconds=True))):
            root = self.root / f'case{index}'
            path = fx.law_campaign(root, 'x', range(4), agent='adopt')
            with self.subTest(case=index), self.assertRaises(ValueError):
                initialize(root, self.declaration(path, edit))

    def test_side_observable_receipts_do_not_confirm_the_goal(self):
        path = fx.law_campaign(self.root, 'x', range(4))
        fx.write(self.root / 'experiment.py', "import argparse, json, pathlib\np = argparse.ArgumentParser()\n"
                 "p.add_argument('--x'); p.add_argument('--output', required=True)\na = p.parse_args(); x = int(a.x)\n"
                 "body = {'outputs': {'z': x, **({'y': x} if x < 2 else {})}}\n"
                 "out = pathlib.Path(a.output); out.parent.mkdir(parents=True, exist_ok=True)\n"
                 "out.write_text(json.dumps(body), encoding='utf-8')\n")

        def edit(value):
            value['observables']['z'] = {'tolerance': {'abs': 0}}
            value['hypotheses'] = [{'id': 'both', 'predict': {'y': 'x', 'z': 'x'}}]
        initialize(self.root, self.declaration(path, edit))
        store = cstore.store_for(self.root)
        self.buy(store, 'x2', 'x3')  # Novel receipts that report only the side observable z.
        state, _ = compass.ingest(store)
        self.assertEqual(compass.confirmations(state, state['hypotheses']['both']), 0)
        self.buy(store, 'x0')
        state, _ = compass.ingest(store)
        self.assertEqual(compass.confirmations(state, state['hypotheses']['both']), 1)

    def test_string_integer_domain_is_rejected(self):
        path = fx.law_campaign(self.root, 'x', range(4))
        with self.assertRaisesRegex(ValueError, 'inputs.x.integer must be true or false'):
            initialize(self.root, self.declaration(path, lambda d: d['inputs']['x'].update(integer='false')))

    def test_experiment_budget_must_be_a_bounded_integer(self):
        for index, count in enumerate(('4', 2.5, -1, True, 9)):  # Four candidates hold eight attempt slots.
            root = self.root / f'case{index}'
            path = fx.law_campaign(root, 'x', range(4))
            with self.subTest(count=count), self.assertRaisesRegex(ValueError, 'budget.experiments must be'):
                initialize(root, self.declaration(path, lambda d: d['budget'].update(experiments=count)))
        path = fx.law_campaign(self.root / 'valid', 'x', range(4))
        initialize(self.root / 'valid', self.declaration(path, lambda d: d['budget'].update(experiments=8)))


class ToolRunnerTests(Campaign):
    def test_runner_timeout_is_settled_and_not_paid_again(self):
        store = self.law('2*popcount(x)+1', range(6))
        self.buy(store, 'x0', 'x1', 'x2', 'x3', 'x4')
        state, _ = compass.ingest(store)
        with patch.object(rsi, 'RUNNER_SECONDS', 0.0001):
            report = rsi.escalate(store, state, {'trigger': 'TEST'})
        self.assertEqual(report['status'], 'TOOL_RUNNER_TIMEOUT')
        rejections = compass.fold(store)['tool_rejections']
        self.assertEqual({(r['reason'], r['phase']) for r in rejections}, {('TOOL_RUNNER_TIMEOUT', 'POOL')})
        self.assertEqual(len(rejections), report['tried'])
        self.assertTrue(all(r['runner']['timeout_seconds'] == 0.0001 and r['runner']['exit_code'] is None
                            for r in rejections))
        # Resuming at the same evidence runs nothing again.
        with patch.object(rsi.subprocess, 'run', side_effect=AssertionError('runner re-executed')):
            self.assertEqual(rsi.escalate(store, compass.fold(store), {'trigger': 'TEST'})['status'],
                             'NO_TOOL_CANDIDATES')

    def test_runner_timeout_does_not_abort_the_drive(self):
        self.law('2*popcount(x)+1', range(6))
        with patch.object(rsi, 'RUNNER_SECONDS', 0.0001):
            result = drive(self.root, publish=False)
        self.assertNotIn(result['status'], ('GOAL_IDENTIFIED', 'INTERRUPTED'), result['steps'])
        store = cstore.store_for(self.root)
        self.assertIn('TOOL_RUNNER_TIMEOUT', {r['reason'] for r in compass.fold(store)['tool_rejections']})


if __name__ == '__main__':
    unittest.main()
