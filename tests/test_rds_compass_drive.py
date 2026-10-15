"""Compass end to end on real kernel receipts: law, jumps, RSI, IMPROVE, interrupts."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compass_fixture as fx
import rds_compass as compass
import rds_compass_rsi as rsi
import rds_compass_store as cstore
from rds_compass_declaration import initialize
from rds_compass_drive import drive
from rds_project import ProjectStore

QUAD = dict(law='x*x+1', xs=range(-4, 5), hypotheses=[('lin', 'x+1'), ('dbl', '2*x+1')])
POP = dict(law='2*popcount(x)+1', xs=range(0, 17), hypotheses=[('lin', 'x+1')])
TRAP = dict(law='0 if x in (11, 13) else 2*popcount(x)+1', xs=range(0, 17), hypotheses=[('lin', 'x+1')])


class Campaign(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()

    def tearDown(self):
        self.temp.cleanup()

    def campaign(self, name, spec, **extra):
        root = self.base / name
        declaration = fx.law_campaign(root, spec['law'], spec['xs'], hypotheses=spec['hypotheses'], **extra)
        self.assertEqual(initialize(root, declaration)['status'], 'INITIALIZED')
        return root

    def events(self, root, kind):
        return [e['value'] for e in cstore.events(cstore.store_for(root)) if e['kind'] == cstore.PREFIX + kind]

    def runs(self, root):
        store = ProjectStore(root)
        with store._db(True) as db:
            return {run['id']: run['status'] for run in store._runs(db)}


class LawTests(Campaign):
    def test_law_identified_from_receipts_with_bound_relative_claim(self):
        root = self.campaign('quad', QUAD)
        result = drive(root)
        self.assertEqual(result['status'], 'GOAL_IDENTIFIED', result['steps'])
        verification = result['verification']
        self.assertEqual(verification['predict'], {'y': '((x * x) + 1)'})
        self.assertEqual(verification['claim'], 'CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS')
        self.assertEqual(verification['uniqueness'], 'UNIQUE_CLASS_WITHIN_SYNTHESIS_BOUND_ON_DECLARED_EXPERIMENTS')
        self.assertGreaterEqual(verification['novel_confirmations'], verification['required_confirmations'])
        self.assertEqual(verification['lean']['status'], 'PASS')
        self.assertEqual(verification['polynomial']['status'], 'AGREES_WITH_UNIQUE_POLYNOMIAL_ON_DECLARED_EXPERIMENTS')
        self.assertEqual(verification['polynomial']['assurance'], 'CERTIFICATE_CHECKED')
        self.assertEqual(verification['scientific_support'], 'UNKNOWN')
        # Every measurement is the kernel's original receipt, INPUT_REPORTED, never support.
        for obs in self.events(root, 'OBSERVATION'):
            self.assertEqual((obs['provenance'], obs['scientific_support']), ('INPUT_REPORTED', 'UNKNOWN'))
        refutations = self.events(root, 'REFUTATION')
        self.assertLessEqual({'lin', 'dbl'}, {r['hypothesis'] for r in refutations})
        receipts = {o['receipt_sha256'] for o in self.events(root, 'OBSERVATION')}
        for refutation in refutations:
            self.assertEqual(refutation['basis'], 'RECEIPTED_MEASUREMENT_COUNTEREXAMPLE')
            self.assertTrue(refutation['counterexamples'])
            self.assertTrue({c['receipt_sha256'] for c in refutation['counterexamples']} <= receipts)
        # Zero repeat waste: no candidate bought twice, no refuted rulebook re-adopted.
        runs = self.runs(root)
        self.assertEqual(len(runs), len({r.split('-a')[0] for r in runs}))
        refuted_before, adopted = set(), 0
        for event in cstore.events(cstore.store_for(root)):
            value = event['value']
            if event['kind'] == cstore.PREFIX + 'REFUTATION':
                refuted_before.add(value['hypothesis_sha256'])
            elif event['kind'] == cstore.PREFIX + 'HYPOTHESIS':
                adopted += 1
                self.assertNotIn(value['sha256'], refuted_before)  # Never re-buys an already refuted rulebook.
        self.assertTrue(adopted)
        report = compass.report(cstore.store_for(root))
        self.assertEqual(report['scientific_support'], 'UNKNOWN')

    def test_tms_mirror_never_promotes_a_hypothesis_to_support(self):
        from rds_tms_store import current
        root = self.campaign('tms', QUAD)
        drive(root)
        nodes = {n['id']: n for n in current(root)['dependency_map']['nodes'] if n['id'].startswith('owned:compass:')}
        hyps = [n for i, n in nodes.items() if i.startswith('owned:compass:hyp:')]
        self.assertTrue(hyps)
        self.assertTrue(all(n['status'] in ('UNKNOWN', 'CONTRADICTED') for n in hyps))
        self.assertEqual(nodes['owned:compass:hyp:lin']['status'], 'CONTRADICTED')
        self.assertIn('receipt', nodes['owned:compass:hyp:lin']['evidence'])

    def test_changed_original_output_is_reported_stale_not_trusted(self):
        root = self.campaign('stale', QUAD)
        drive(root)
        store = cstore.store_for(root)
        measured = next(o for o in compass.fold(store)['observations'].values() if o['status'] == 'MEASURED')
        (root / measured['artifact']['path']).write_text('{"outputs": {"y": 999}}', encoding='utf-8')
        verification = compass.verify(store, compass.fold(store))
        self.assertFalse(verification['receipts_rechecked'])
        self.assertEqual(verification['stale'], [{'candidate': measured['candidate'], 'binding': 'OUTPUT_HASH_MISMATCH'}])


class JumpAgentTests(Campaign):
    def test_agent_dispositions_are_respected_and_its_proposals_pass_the_record_filter(self):
        root = self.campaign('reject', QUAD, agent='reject', escape={'max_jumps': 2})
        result = drive(root)
        self.assertEqual(result['status'], 'SEARCH_EXHAUSTED')
        dispositions = self.events(root, 'DISPOSITION')
        self.assertEqual([d['disposer'] for d in dispositions], ['AGENT', 'AGENT'])
        packets = [j['packet']['sha256'] for j in self.events(root, 'JUMP')]
        calls = [json.loads(line) for line in (root / 'agent-calls.jsonl').read_text().splitlines()]
        self.assertEqual([c['packet_sha256'] for c in calls], packets)
        for disposition in dispositions:
            proposal = next(o for o in disposition['outcomes'] if o['disposition'] == 'proposal')
            self.assertEqual((proposal['admitted'], proposal['reason']), (False, 'REFUTED_BY_RECORD'))
            self.assertFalse(any(o['admitted'] for o in disposition['outcomes']))
        self.assertFalse([h for h in self.events(root, 'HYPOTHESIS') if h['origin'].startswith('jump:')])

    def test_stale_agent_response_is_recorded_and_the_unattended_policy_continues(self):
        root = self.campaign('stale-agent', QUAD, agent='stale')
        result = drive(root)
        self.assertEqual(result['status'], 'GOAL_IDENTIFIED')
        first = self.events(root, 'DISPOSITION')[0]
        self.assertEqual(first['disposer'], 'COMPASS_AUTO')
        self.assertTrue(first['errors'][0].startswith('INVALID_RESPONSE'))


class RsiTests(Campaign):
    def test_capability_gap_closed_by_an_oracle_qualified_tool_and_reused_from_the_library(self):
        library = self.base / 'library'
        first = self.campaign('pop1', POP, library=str(library))
        self.assertEqual(drive(first)['status'], 'GOAL_IDENTIFIED')
        tool = self.events(first, 'TOOL_QUALIFIED')[0]
        self.assertEqual((tool['status_label'], tool['self_certified'], tool['scientific_support']),
                         ('QUALIFIED_FOR_SCOPE', False, 'UNKNOWN'))
        oracle = tool['oracle']
        self.assertEqual(oracle['expected_values_from'], 'ORIGINAL_RUN_RECEIPTS')
        self.assertTrue(set(oracle['known_positive']['fitted_on']).isdisjoint(h['id'] for h in oracle['known_positive']['holdout']))
        self.assertEqual(oracle['discriminating_negative']['kind'], 'MUTANT_CONTRADICTED_BY_RECEIPT')
        self.assertTrue(oracle['counterexample_witness']['receipt_sha256'])
        self.assertEqual(tool['boundary'], {'declared_candidates': 17, 'defined_on_all': True})
        law = compass.fold(cstore.store_for(first))
        self.assertTrue(any(p.startswith('tool:') for h in law['hypotheses'].values() for p in h['premises']))
        offered = rsi.library_tools(library)
        self.assertEqual([(t['name'], t['library_provenance']) for t in offered], [('digitsum2', 'ORIGIN_VERIFIED')])
        second = self.campaign('pop2', POP, library=str(library))
        self.assertEqual(drive(second)['status'], 'GOAL_IDENTIFIED')
        reused = self.events(second, 'TOOL_QUALIFIED')[0]
        # Reuse is an offer: the tool is re-qualified against the second campaign's own receipts.
        self.assertEqual((reused['origin'], reused['library_provenance']), ('LIBRARY', 'ORIGIN_VERIFIED'))
        self.assertNotEqual(reused['oracle']['counterexample_witness']['receipt_sha256'],
                            oracle['counterexample_witness']['receipt_sha256'])

    def test_later_counterexample_retracts_the_tool_its_dependents_and_its_library_offer(self):
        from rds_tms_store import current
        library = self.base / 'library'
        root = self.campaign('trap', TRAP, library=str(library))
        result = drive(root)
        self.assertEqual(result['status'], 'SEARCH_EXHAUSTED')  # Honest: no law within bound once the trap shows.
        retraction = self.events(root, 'TOOL_RETRACTED')[0]
        self.assertEqual(retraction['reason'], 'QUALIFYING_LAW_CONTRADICTED_BY_RECEIPT')
        state = compass.fold(cstore.store_for(root))
        witness = state['observations'][retraction['witness']['id']]
        self.assertEqual(retraction['witness']['receipt_sha256'], witness['receipt_sha256'])
        self.assertIn(witness['candidate'], ('x11', 'x13'))
        dependents = [h for h in state['hypotheses'].values() if any(p.startswith('tool:') for p in h['premises'])]
        self.assertTrue(dependents)
        self.assertTrue(all(h['status'] == 'RETRACTED' for h in dependents))
        self.assertEqual(list(library.glob('*.retracted.json'))[0].name.split('-')[0], 'digitsum2')
        self.assertEqual(rsi.library_tools(library), [])
        nodes = {n['id']: n for n in current(root)['dependency_map']['nodes']}
        tool_node = next(n for i, n in nodes.items() if i.startswith('owned:compass:tool:'))
        self.assertEqual(tool_node['status'], 'CONTRADICTED')


class ImproveTests(Campaign):
    def test_mean_gain_masking_a_local_regression_is_not_accepted(self):
        root = self.base / 'improve'
        arms = {'base': {'s1': 10, 's2': 10, 's3': 10, 's4': 10},
                'masked': {'s1': 20, 's2': 20, 's3': 20, 's4': 2},
                'steady': {'s1': 12, 's2': 11, 's3': 10, 's4': 10}}
        self.assertEqual(initialize(root, fx.improve_campaign(root, arms))['status'], 'INITIALIZED')
        result = drive(root)
        self.assertEqual(result['status'], 'IMPROVEMENT_EVALUATED')
        improvement = result['verification']['improvement']
        verdicts = {a['arm']: a['verdict'] for a in improvement['arms']}
        self.assertEqual(verdicts, {'masked': 'LOCAL_REGRESSION', 'steady': 'ACCEPT'})
        self.assertEqual(improvement['recommended'], 'steady')
        masked = next(a for a in improvement['arms'] if a['arm'] == 'masked')
        self.assertGreater(float(masked['profile']['mean_gain'].split('/')[0]), 0)
        self.assertEqual((masked['profile']['worst_sample'], masked['profile']['worst_degradation']), ('s4', '8'))


class CliTests(Campaign):
    def cli(self, root, *args):
        done = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(root), *args],
                              capture_output=True, text=True, timeout=300)
        return done.returncode, json.loads(done.stdout) if done.stdout.strip() else done.stderr

    def test_declaration_compiles_with_unknowns_kept_unknown(self):
        root = self.base / 'cli'
        declaration = fx.law_campaign(root, **{k: QUAD[k] for k in ('law', 'xs')}, hypotheses=QUAD['hypotheses'])
        code, value = self.cli(root, 'project', 'init', '--declaration', str(declaration))
        self.assertEqual((code, value['status']), (0, 'INITIALIZED'), value)
        self.assertIn('goal.success', value['unknown'])
        self.assertIn('protocol.seed', value['unknown'])
        self.assertIn('grammar', value['defaulted'])
        self.assertEqual(self.cli(root, 'compass', 'init', '--declaration', str(declaration))[1]['status'],
                         'ALREADY_INITIALIZED')
        code, decision = self.cli(root, 'compass', 'decide')
        self.assertEqual((code, decision['execution_started']), (0, False))
        code, status = self.cli(root, 'compass', 'status')
        self.assertEqual(len(status['sha256']), 64)
        code, value = self.cli(root, 'project', 'drive', '--compass', '--max-steps', '2')
        self.assertEqual((code, value['status']), (2, 'STEP_LIMIT'))
        code, value = self.cli(root, 'compass', 'drive')
        self.assertEqual((code, value['status']), (0, 'GOAL_IDENTIFIED'))

    def test_outer_sigterm_keeps_the_paid_attempt_and_resumes_through_the_retry_slot(self):
        root = self.base / 'sigterm'
        declaration = fx.law_campaign(root, QUAD['law'], QUAD['xs'], hypotheses=QUAD['hypotheses'], sleep=1.5)
        self.assertEqual(self.cli(root, 'compass', 'init', '--declaration', str(declaration))[0], 0)
        process = subprocess.Popen([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(root),
                                    'compass', 'drive'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline, running = time.monotonic() + 60, None
        while running is None and time.monotonic() < deadline:
            running = next((r for r, s in self.runs(root).items() if s == 'RUNNING'), None)
            time.sleep(0.05)
        self.assertIsNotNone(running)
        time.sleep(0.3)
        process.send_signal(signal.SIGTERM)
        out, err = process.communicate(timeout=120)
        self.assertEqual(process.returncode, 2, err)
        value = json.loads(out)
        self.assertEqual(value['status'], 'INTERRUPTED')
        partial = value['workset']['handoff']['detail']
        self.assertEqual((partial['signal'], partial['in_flight_run']), ('SIGTERM', running))
        self.assertEqual(self.runs(root)[running], 'INTERRUPTED')  # Settled, not a FAILED experiment.
        code, value = self.cli(root, 'compass', 'drive')
        self.assertEqual((code, value['status']), (0, 'GOAL_IDENTIFIED'))
        runs = self.runs(root)
        self.assertEqual(runs[running], 'INTERRUPTED')
        self.assertEqual(runs[running + '-a2'], 'COMPLETED')
        state = compass.fold(cstore.store_for(root))
        self.assertEqual(list(state['interrupted']), [running])
        self.assertEqual(state['observations'][running[len('compass-'):]]['run_id'], running + '-a2')


class StoreTests(Campaign):
    def test_records_are_idempotent_hash_chained_and_tamper_evident(self):
        root = self.campaign('store', QUAD)
        store = cstore.store_for(root)
        value = {'trigger': 'TEST', 'note': 'one'}
        first = cstore.append(store, 'HANDOFF', 'hx', value)
        self.assertTrue(cstore.append(store, 'HANDOFF', 'hx', value)['reused'])
        with self.assertRaisesRegex(ValueError, 'already holds different content'):
            cstore.append(store, 'HANDOFF', 'hx', {**value, 'note': 'two'})
        path = root / first['record']['path']
        path.write_bytes(path.read_bytes().replace(b'one', b'two'))
        with self.assertRaises(cstore.CompassIntegrityError):
            cstore.events(store)


if __name__ == '__main__':
    unittest.main()
