"""Compass end to end on real kernel receipts: law, jumps, RSI, IMPROVE, interrupts."""
from argparse import Namespace
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import sqlite3
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compass_fixture as fx
import rds_compass as compass
import rds_compass_model as model
import rds_compass_rsi as rsi
import rds_compass_store as cstore
from rds_compass_cli import command
from rds_compass_declaration import initialize
from rds_compass_drive import drive
from rds_project import ProjectStore

QUAD = dict(law='x*x+1', xs=range(-4, 5), hypotheses=[('lin', 'x+1'), ('dbl', '2*x+1')])
POP = dict(law='2*popcount(x)+1', xs=range(0, 17), hypotheses=[('lin', 'x+1')])
TRAP = dict(law='0 if x in (11, 13) else 2*popcount(x)+1', xs=range(0, 17), hypotheses=[('lin', 'x+1')])
# Reports a side observable z that the declaration leaves without a tolerance.
SIDE = """import argparse, json, pathlib
p = argparse.ArgumentParser(); p.add_argument('--x'); p.add_argument('--output', required=True)
a = p.parse_args(); x = int(a.x)
out = pathlib.Path(a.output); out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps({'outputs': {'y': x, 'z': x + 5}}), encoding='utf-8')
"""


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
    def test_observable_without_tolerance_is_never_adjudicated(self):
        root = self.base / 'side'
        path = fx.law_campaign(root, 'x', range(4))
        fx.write(root / 'experiment.py', SIDE)
        value = json.loads(path.read_text(encoding='utf-8'))
        value['observables']['z'] = {}
        value['hypotheses'] = [{'id': 'both', 'predict': {'y': 'x', 'z': 'x'}}]
        fx.write(path, value)
        self.assertIn('observables.z.tolerance', initialize(root, path)['plan']['unknown'])
        store = cstore.store_for(root)
        for ident in ('x0', 'x1'):
            manifest = compass.manifest(compass.fold(store), ident)
            store.register(manifest)
            store.execute(manifest['id'])
        state, _ = compass.ingest(store)
        # z = x + 5 contradicts the rulebook's z = x, but z has no tolerance: UNKNOWN, never a refutation.
        hyp = state['hypotheses']['both']
        self.assertEqual((hyp['status'], hyp['replay']['tested'], hyp['replay']['unknown_measurements']),
                         ('CONSISTENT', 2, 2))
        # The invariant holds in replay itself, even for a row that carries the untolerated cell.
        rows = [dict(row, outputs={**row['outputs'], 'z': model.exact(99)}) for row in compass.measured(state)]
        result = model.replay(hyp, rows, {'y': (0, 0)})
        self.assertEqual((result['status'], result['tested'], result['unknown_measurements']), ('CONSISTENT', 2, 2))

    def test_a_long_exact_residual_does_not_stop_the_campaign(self):
        # NewtonBench pilot crash: a refuted rival's exact residual spelled more than 256
        # characters, and basin() and _fits() could not read it back with model.exact().
        scaled = f'x * {3 ** 280} / {2 ** 440}'
        root = self.campaign('long-residual', dict(QUAD, hypotheses=[('scaled', scaled)]))
        result = drive(root, max_steps=40)
        hyp = compass.fold(cstore.store_for(root))['hypotheses']['scaled']
        self.assertEqual(hyp['status'], 'REFUTED')
        self.assertGreater(max(len(c['residual']) for c in hyp['replay']['counterexamples']), 256)
        self.assertEqual(result['status'], 'GOAL_IDENTIFIED', result)

    def test_rivals_that_differ_only_on_an_untoleranced_observable_share_a_class(self):
        root = self.base / 'side-rivals'
        path = fx.law_campaign(root, 'x', range(4))
        fx.write(root / 'experiment.py', SIDE)
        value = json.loads(path.read_text(encoding='utf-8'))
        value['observables']['z'] = {}
        value['hypotheses'] = [{'id': 'near', 'predict': {'y': 'x', 'z': 'x'}},
                               {'id': 'far', 'predict': {'y': 'x', 'z': 'x + 9'}}]
        fx.write(path, value)
        initialize(root, path)
        verification = drive(root, publish=False)['verification']
        # z can neither refute nor separate the two; the claim names the observable it never compared.
        self.assertEqual(sorted(verification['equivalence_class']), ['far', 'near'])
        self.assertEqual((verification['predict'], verification['unadjudicated']), ({'y': 'x'}, ['z']))
        self.assertEqual(verification['scientific_support'], 'UNKNOWN')
        near, far = (model.compile_hypothesis(h, ['x'], ['y', 'z']) for h in value['hypotheses'])
        self.assertEqual(model.equivalent(near, far, [{'x': 1}], {'y': (0, 0)}), 'OBSERVATIONALLY_EQUIVALENT')
        self.assertIsNone(model.equivalent(near, far, [{'x': 1}], {}))  # Nothing adjudicable: never equivalent.

    def test_an_open_grammar_escalates_when_the_arithmetic_bound_is_exhausted(self):
        # A sine law is outside the default add/sub/mul/div grammar. With the grammar left open,
        # the synthesizer escalates once to the wider dialect instead of declaring exhaustion.
        root = self.base / 'sine'
        path = fx.law_campaign(root, 'sin(x)', range(0, 5), hypotheses=[('lin', 'x+1')])
        value = json.loads(path.read_text(encoding='utf-8'))
        value['observables']['y']['tolerance'] = {'abs': 0.01}
        fx.write(path, value)
        self.assertEqual(initialize(root, path)['status'], 'INITIALIZED')
        result = drive(root, max_steps=80)
        self.assertEqual(result['status'], 'GOAL_IDENTIFIED', result)
        notes = [jump.get('synthesis') or {} for jump in compass.fold(cstore.store_for(root))['jumps']]
        self.assertTrue(any('GRAMMAR_ESCALATION' in note for note in notes), notes)
        # Control: an explicit arithmetic grammar never escalates, so the same world stays unsolved.
        control = self.base / 'sine-control'
        control_path = fx.law_campaign(control, 'sin(x)', range(0, 5), hypotheses=[('lin', 'x+1')])
        value = json.loads(control_path.read_text(encoding='utf-8'))
        value['observables']['y']['tolerance'] = {'abs': 0.01}
        value['grammar'] = {'operators': ['add', 'sub', 'mul', 'div']}
        fx.write(control_path, value)
        self.assertEqual(initialize(control, control_path)['status'], 'INITIALIZED')
        self.assertNotEqual(drive(control, max_steps=80)['status'], 'GOAL_IDENTIFIED')

    def _minimal_state(self, confirm_by_eig=True):
        candidates = [{'id': f'x{i}', 'inputs': {'x': i}, 'cost': '1', 'run_id': f'c-x{i}',
                       'retry': {'run_id': f'c-x{i}-a2'}, 'output': f'outputs/compass/x{i}.json',
                       'argv': ['python', 'experiment.py', '--x', str(i), '--output', f'outputs/compass/x{i}.json']}
                      for i in range(5)]
        return {'plan': {'verification': {'confirm_by_eig': confirm_by_eig},
                         'inputs': {'x': {'min': 0, 'max': 4, 'integer': True}},
                         'observables': {'y': {'tolerance': {'abs': 0}}},
                         'candidates': candidates, 'goal': {'observable': 'y'}},
                'runs': {}, 'hypotheses': {}, 'observations': {}, 'tools': {},
                'decisions': [], 'jumps': []}

    def test_confirmation_buys_a_separating_point_when_one_exists(self):
        state = self._minimal_state()
        lin = model.compile_hypothesis({'id': 'lin', 'predict': {'y': 'x'}}, ['x'], ['y'])
        quad = model.compile_hypothesis({'id': 'quad', 'predict': {'y': 'x*x'}}, ['x'], ['y'])
        tolerances = {'y': (model.exact(0), model.exact(0))}
        # x0 and x1 agree for both rivals; x2 first separates them. x0 is the farthest spread point.
        self.assertEqual(compass._confirm_candidate(state, [lin, quad], compass.remaining(state), tolerances), 'x2')
        state['plan']['verification']['confirm_by_eig'] = False
        self.assertEqual(compass._confirm_candidate(state, [lin, quad], compass.remaining(state), tolerances), 'x0')

    def test_an_open_grammar_reaches_a_root_law(self):
        # A square-root law needs the wider unary operators; the arithmetic bound cannot express it.
        root = self.base / 'sqrt'
        path = fx.law_campaign(root, 'sqrt(x)', [0, 1, 4, 9, 16], hypotheses=[('lin', 'x+1')])
        value = json.loads(path.read_text(encoding='utf-8'))
        value['observables']['y']['tolerance'] = {'abs': 0.01}
        fx.write(path, value)
        self.assertEqual(initialize(root, path)['status'], 'INITIALIZED')
        result = drive(root, max_steps=80)
        self.assertEqual(result['status'], 'GOAL_IDENTIFIED', result)

    def test_a_confirmation_cap_stops_the_refutation_budget(self):
        # spend_budget buys confirmation points until the experiment budget ends; a declared cap
        # stops it at the stated number, so a large declared domain stays affordable.
        root = self.campaign('cap', QUAD, verification={'confirmations': 1, 'max_confirmations': 1})
        result = drive(root, publish=False)
        self.assertIn(result['status'], ('GOAL_IDENTIFIED', 'JUDGMENT_REQUIRED'), result)
        confirms = [d for d in self.events(root, 'DECISION') if d['action'] == 'CONFIRM']
        self.assertLessEqual(len(confirms), 1)

    def test_a_holdout_point_is_measured_after_the_fit_and_never_used_to_fit(self):
        # verification.holdout reserves the last candidate ids: the drive buys them only after the
        # leader is fitted, and the synthesizer and confirmation counter never see them.
        root = self.campaign('holdout', QUAD,
                             verification={'holdout': 1, 'confirmations': 1, 'max_confirmations': 1})
        result = drive(root, publish=False)
        self.assertIn(result['status'], ('GOAL_IDENTIFIED', 'JUDGMENT_REQUIRED'), result)
        decisions = self.events(root, 'DECISION')
        holdout = [d['candidate'] for d in decisions if d['action'] == 'HOLDOUT']
        self.assertIn('x4', holdout)
        self.assertLessEqual(len(holdout), 2)

    def test_report_publishes_a_recomputable_cost_summary(self):
        root = self.campaign('cost', QUAD)
        drive(root, publish=False)
        body = compass.report(cstore.store_for(root))
        self.assertEqual(body['cost']['experiments'], body['experiments'])
        self.assertEqual(body['cost']['jumps'], len(body['jumps']))
        self.assertIsNone(body['cost']['agent_tokens'])  # No agent declared: no model spend.

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

    def test_tms_mirror_withdraws_evidence_that_fails_its_recheck(self):
        from rds_tms_store import current
        root = self.campaign('tms-stale', QUAD)
        drive(root)
        store = cstore.store_for(root)
        state = compass.fold(store)
        witnesses = [c['id'] for c in state['hypotheses']['lin']['replay']['counterexamples']]
        self.assertGreater(len(witnesses), 1)
        for count, status in ((1, 'CONTRADICTED'), (len(witnesses), 'UNKNOWN')):
            for ident in witnesses[:count]:
                (root / state['observations'][ident]['artifact']['path']).write_text('{"outputs": {"y": 999}}',
                                                                                     encoding='utf-8')
            compass.publish_tms(store, compass.fold(store))
            graph = current(root)['dependency_map']
            nodes = {n['id']: n for n in graph['nodes']}
            self.assertEqual({nodes['owned:compass:obs:' + i]['status'] for i in witnesses[:count]}, {'UNKNOWN'})
            lin = nodes['owned:compass:hyp:lin']
            self.assertEqual(lin['status'], status)
            # A refutation that stands cites a witness that still verifies, never the stale one.
            self.assertEqual((lin.get('evidence') or {}).get('receipt', {}).get('sha256'),
                             state['observations'][witnesses[1]]['receipt_sha256'] if count == 1 else None)
            explains = next(e for e in graph['hyperedges'] if e['id'] == 'owned:compass:explains:lin')
            self.assertEqual(explains['status'], 'CONTRADICTED' if count == 1 else 'PROPOSED')

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


class TokenBudgetTests(Campaign):
    """A declared agent.max_tokens is checked before every call against the agent's own usage reports."""

    def budgeted(self, name, max_tokens, usage):
        root = self.base / name
        path = fx.law_campaign(root, QUAD['law'], QUAD['xs'], hypotheses=QUAD['hypotheses'], agent='reject',
                               escape={'max_jumps': 2})
        value = json.loads(path.read_text(encoding='utf-8'))
        if max_tokens is not None:
            value['agent']['max_tokens'] = max_tokens
        fx.write(path, value)
        fx.write(root / 'agent-mode.json', {'mode': 'reject', **({'usage': usage} if usage else {})})
        self.assertEqual(initialize(root, path)['status'], 'INITIALIZED')
        drive(root)
        calls = len((root / 'agent-calls.jsonl').read_text(encoding='utf-8').splitlines())
        return root, calls, [j['agent_tokens'] for j in self.events(root, 'JUMP')]

    def test_a_spent_budget_withholds_further_agent_calls(self):
        usage = {'input_tokens': 600, 'output_tokens': 500, 'cached_input_tokens': 200}
        root, calls, tokens = self.budgeted('spent', 1000, usage)
        self.assertEqual((calls, [t['status'] for t in tokens]), (1, ['AVAILABLE', 'EXHAUSTED']))
        self.assertEqual(self.events(root, 'JUMP')[0]['packet']['budget'],
                         {'agent_tokens': {'cap': 1000, 'spent': 0, 'remaining': 1000}})
        final = compass.report(cstore.store_for(root))['agent_tokens']
        self.assertEqual({k: final[k] for k in ('spent', 'cached_input_tokens', 'calls', 'provenance')},
                         {'spent': 1100, 'cached_input_tokens': 200, 'calls': 1, 'provenance': 'INPUT_REPORTED'})

    def test_unreported_usage_under_a_cap_fails_closed(self):
        _, calls, tokens = self.budgeted('silent', 10 ** 6, None)
        self.assertEqual((calls, [t['status'] for t in tokens]), (1, ['AVAILABLE', 'USAGE_UNREPORTED']))

    def test_an_undeclared_cap_is_unknown_and_spend_is_still_counted(self):
        root, calls, tokens = self.budgeted('uncapped', None, {'input_tokens': 7, 'output_tokens': 3})
        self.assertEqual((calls, [t['status'] for t in tokens]), (2, ['CAP_UNKNOWN', 'CAP_UNKNOWN']))
        self.assertIn('agent.max_tokens', compass.fold(cstore.store_for(root))['plan']['unknown'])
        self.assertEqual(compass.report(cstore.store_for(root))['agent_tokens']['spent'], 20)

    def test_malformed_caps_and_usage_reports_are_rejected(self):
        for index, bad in enumerate((0, True, '100', 1.5, 10 ** 11)):
            root = self.base / ('bad%d' % index)
            path = fx.law_campaign(root, 'x', range(3), agent='reject')
            value = json.loads(path.read_text(encoding='utf-8'))
            value['agent']['max_tokens'] = bad
            fx.write(path, value)
            with self.assertRaisesRegex(ValueError, 'max_tokens'):
                initialize(root, path)
        self.assertEqual(compass._usage(b'{"usage": {"input_tokens": 3, "output_tokens": 4}}'),
                         {'input_tokens': 3, 'output_tokens': 4})
        for bad in (b'{"usage": {"input_tokens": -1, "output_tokens": 4}}', b'{"usage": {"input_tokens": true, '
                    b'"output_tokens": 4}}', b'{"usage": {"input_tokens": 3}}', b'[1]', b'not json',
                    b'{"usage": {"input_tokens": 3, "output_tokens": 4, "cost": 1}}'):
            self.assertIsNone(compass._usage(bad), bad)


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

    def test_agent_tool_primitives_need_no_ledger(self):
        from rds_compass_cli import agent_tool
        request = {'inputs': ['x'], 'observable': 'y', 'tolerances': {'y': {'abs': 0, 'rel': 0}},
                   'rows': [{'id': 'x1', 'inputs': {'x': '1'}, 'outputs': {'y': '2'}},
                            {'id': 'x2', 'inputs': {'x': '2'}, 'outputs': {'y': '3'}}]}
        self.assertEqual(agent_tool('replay', {**request, 'law': 'x + 1'})['status'], 'CONSISTENT')
        synth = agent_tool('synthesize', {**request, 'grammar': {'operators': ['add']},
                                          'max_nodes': 4, 'max_candidates': 100})
        self.assertEqual(synth['status'], 'FIT_FOUND')
        rank = agent_tool('rank', {**request, 'candidates': [{'id': 'x3', 'inputs': {'x': '3'}, 'cost': '1'}],
                                   'rivals': [{'id': 'a', 'predict': 'x + 1'},
                                              {'id': 'b', 'predict': 'x * 2'}]})
        self.assertTrue(rank['ranked'])

    def test_agent_tool_holdout_reads_the_ledger(self):
        from rds_compass_cli import agent_tool
        root = self.campaign('holdout-tool', QUAD)
        drive(root, publish=False)
        state = compass.fold(cstore.store_for(root))
        hypothesis = compass.live(state)[0]['id']
        measurement = next(iter(state['observations']))
        result = agent_tool('holdout', {'root': str(root), 'hypothesis_id': hypothesis,
                                        'measurement_ids': [measurement], 'observable': 'y',
                                        'tolerances': {'y': {'abs': 0, 'rel': 0}}})
        self.assertIn(result['status'], ('CONSISTENT', 'REFUTED', 'UNTESTED'), result)
        self.assertTrue(result['receipts_rechecked'])

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
        # Windows cannot deliver SIGTERM (it is TerminateProcess); its settled stop is CTRL_BREAK_EVENT.
        windows = os.name == 'nt'
        process = subprocess.Popen([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(root),
                                    'compass', 'drive'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if windows else 0)
        deadline, running = time.monotonic() + 60, None
        while running is None and time.monotonic() < deadline:
            running = next((r for r, s in self.runs(root).items() if s == 'RUNNING'), None)
            time.sleep(0.05)
        self.assertIsNotNone(running)
        time.sleep(0.3)
        process.send_signal(signal.CTRL_BREAK_EVENT if windows else signal.SIGTERM)
        out, err = process.communicate(timeout=120)
        self.assertEqual(process.returncode, 2, err)
        value = json.loads(out)
        self.assertEqual(value['status'], 'INTERRUPTED')
        partial = value['workset']['handoff']['detail']
        self.assertEqual((partial['signal'], partial['in_flight_run']), ('SIGBREAK' if windows else 'SIGTERM', running))
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



class UninitializedLedgerTests(Campaign):
    """#359: a workspace holding only research records is not a Compass ledger."""

    def research_only(self):
        import rds_math
        root = self.base / 'research'
        root.mkdir()
        rds_math.put(root, 'note', 'note', b'Unverified mathematical notes')
        return root

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_research_only_ledger_is_refused_with_the_init_command(self):
        root = self.research_only()
        ledger = root / '.rds' / 'project.sqlite3'
        before = self.sha(ledger)
        for args in (('compass', 'status'), ('compass', 'decide'), ('compass', 'report'), ('compass', 'tools'),
                     ('compass', 'drive'), ('project', 'drive', '--compass')):
            done = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(root), *args],
                                  capture_output=True, text=True, timeout=300)
            self.assertNotEqual(done.returncode, 0, args)
            self.assertIn('compass init --declaration', done.stderr, args)
            self.assertNotIn('no such table', done.stderr, args)
        self.assertEqual(self.sha(ledger), before)
        # Pre-init tool preparation rows in an events table are no execution history either.
        with closing(sqlite3.connect(ledger)) as db, db:
            db.execute('CREATE TABLE events(id INTEGER PRIMARY KEY,body TEXT NOT NULL)')
            db.execute("INSERT INTO events(body) VALUES ('{\"kind\":\"TOOL_PREPARATION_STARTED\"}')")
        with self.assertRaisesRegex(ValueError, 'compass init --declaration') as caught:
            command(Namespace(root=root, action='status'))
        self.assertNotIsInstance(caught.exception, cstore.CompassIntegrityError)

    def test_compass_init_in_the_same_root_keeps_research_records(self):
        import rds_math
        root = self.research_only()
        record = rds_math.get(root, 'note')
        asset = root / record['asset']['path']
        raw = asset.read_bytes()
        declaration = fx.law_campaign(root, QUAD['law'], QUAD['xs'], hypotheses=QUAD['hypotheses'])
        self.assertEqual(initialize(root, declaration)['status'], 'INITIALIZED')
        self.assertEqual((rds_math.get(root, 'note'), asset.read_bytes()), (record, raw))
        status = command(Namespace(root=root, action='status'))
        self.assertEqual((status['observations'], self.runs(root)), (0, {}))

    def test_damaged_execution_ledger_is_diagnosed_not_reinitialized(self):
        root = self.campaign('damaged', QUAD)
        ledger = root / '.rds' / 'project.sqlite3'
        with closing(sqlite3.connect(ledger)) as db, db:
            db.execute('DROP TABLE events')
        before = self.sha(ledger)
        for action in ('status', 'decide', 'report'):
            with self.assertRaisesRegex(cstore.CompassIntegrityError, 'missing tables events.*do not re-run init'):
                command(Namespace(root=root, action=action))
        with self.assertRaisesRegex(cstore.CompassIntegrityError, 'missing tables events'):
            drive(root, publish=False)
        self.assertEqual(self.sha(ledger), before)

    def test_absent_ledger_names_the_init_command(self):
        root = self.base / 'empty'
        root.mkdir()
        with self.assertRaisesRegex(ValueError, 'compass init --declaration'):
            command(Namespace(root=root, action='status'))
        self.assertFalse((root / '.rds').exists())


if __name__ == '__main__':
    unittest.main()
