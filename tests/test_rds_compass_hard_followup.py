"""Regressions for the Compass defects the pre-registered hard tier exposed.

The hard tier (benchmark/compass/HARD-PROTOCOL.md on bench/compass-hard-tier) reports its
outcomes unpatched. These tests pin the follow-up fixes on small surrogate worlds:

- an EIG tie broken by candidate id bought where the evidence already was, and two novel
  predictions then certified a law that a sparse exception refutes;
- the jump agent was never asked when Compass's own synthesis found no rival, so the
  compass_codex arm never called the model;
- IMPROVE judged a variant on one run when the declaration said another independent run exists.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import compass_fixture as fx
import rds_compass as compass
import rds_compass_model as model
import rds_compass_store as cstore
from rds_compass_declaration import initialize
from rds_compass_drive import drive

DIGIT = 'x + 5*(x % 10 > 0)'  # Constant 5 is outside the default synthesis grammar.


class Campaign(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve() / 'c'

    def tearDown(self):
        self.temp.cleanup()

    def edit(self, path, change):
        value = json.loads(path.read_text(encoding='utf-8'))
        change(value)
        fx.write(path, value)
        return path

    def events(self, kind):
        return [e['value'] for e in cstore.events(cstore.store_for(self.root)) if e['kind'] == cstore.PREFIX + kind]


class CertificationTests(Campaign):
    def test_equal_gain_is_broken_by_coverage_not_by_id(self):
        hyps = [model.compile_hypothesis({'id': i, 'predict': {'y': e}}, ['x'], ['y'])
                for i, e in (('a', 'x + 5'), ('b', '6'))]
        candidates = [{'id': f'x{x}', 'inputs': {'x': x}} for x in (2, 10, 64)]
        ranking = model.rank_experiments(hyps, candidates, {'y': (0, 0)}, observed=[{'x': 1}])
        # Every candidate splits the two rulebooks equally; the farthest from x=1 is bought first.
        self.assertEqual([r['id'] for r in ranking['ranked']], ['x64'])
        self.assertEqual({e['id'] for e in ranking['excluded']}, {'x2', 'x10'})

    def test_sparse_exception_is_not_certified_while_budget_remains(self):
        path = fx.law_campaign(self.root, '0 if x == 13 else x + 1', range(0, 17), hypotheses=[('lin', 'x + 1')])
        initialize(self.root, path)
        result = drive(self.root, publish=False)
        self.assertNotEqual(result['status'], 'GOAL_IDENTIFIED', result['steps'])
        state = compass.fold(cstore.store_for(self.root))
        self.assertEqual(state['hypotheses']['lin']['status'], 'REFUTED')
        self.assertIn('x13', state['observations'])

    def test_a_claim_reports_its_coverage_and_the_declared_stopping_rule(self):
        path = fx.law_campaign(self.root, 'x*x + 1', range(-4, 5), hypotheses=[('sq', 'x*x + 1')])
        initialize(self.root, self.edit(path, lambda d: d.update(verification={'spend_budget': False})))
        result = drive(self.root, publish=False)
        self.assertEqual(result['status'], 'GOAL_IDENTIFIED')
        coverage = result['verification']['coverage']
        self.assertLess(coverage['measured'], coverage['declared'])
        self.assertEqual(coverage['unmeasured_inputs_are'], 'PREDICTED_NOT_MEASURED')
        # The default spends the budget on refutation attempts before the same claim.
        other = Path(self.temp.name).resolve() / 'spend'
        initialize(other, fx.law_campaign(other, 'x*x + 1', range(-4, 5), hypotheses=[('sq', 'x*x + 1')]))
        spent = drive(other, publish=False)
        self.assertEqual(spent['status'], 'GOAL_IDENTIFIED')
        self.assertEqual(spent['verification']['coverage']['measured'], 9)

    def test_spend_budget_must_be_a_boolean(self):
        path = fx.law_campaign(self.root, 'x', range(4))
        with self.assertRaisesRegex(ValueError, 'verification.spend_budget must be boolean'):
            initialize(self.root, self.edit(path, lambda d: d.update(verification={'spend_budget': 1})))


class AgentProposalTests(Campaign):
    def campaign(self, proposal=None):
        path = fx.law_campaign(self.root, DIGIT, range(0, 31), hypotheses=[('h1', 'x + 5')], agent='adopt',
                               budget={'wall_seconds': 900, 'experiments': 14})
        if proposal is not None:
            fx.write(self.root / 'agent-mode.json', {'mode': 'adopt', 'proposal': proposal})
        initialize(self.root, path)
        return drive(self.root, publish=False)

    def test_agent_is_asked_for_proposals_when_synthesis_finds_no_rival(self):
        result = self.campaign(proposal=DIGIT)
        self.assertEqual(result['status'], 'GOAL_IDENTIFIED', result['steps'])
        self.assertEqual(result['verification']['predict'], {'y': '(((0 < (x % 10)) * 5) + x)'})
        jump = self.events('JUMP')[0]
        self.assertEqual((jump['status'], jump['packet']['request'], jump['packet']['items']),
                         ('PROPOSALS_REQUESTED', 'PROPOSALS', []))
        outcome = self.events('DISPOSITION')[0]['outcomes'][0]
        self.assertEqual((outcome['disposition'], outcome['admitted']), ('proposal', True))
        # The agent's rulebook is a candidate, never evidence: it still needs novel confirmations.
        self.assertGreaterEqual(result['verification']['novel_confirmations'], 2)
        self.assertEqual(result['verification']['scientific_support'], 'UNKNOWN')

    def test_refuted_proposals_are_never_admitted_and_the_agent_is_not_reasked_on_the_same_evidence(self):
        result = self.campaign()  # The fixture agent proposes x + 100.
        self.assertEqual(result['status'], 'SEARCH_EXHAUSTED')
        for disposition in self.events('DISPOSITION'):
            self.assertFalse(any(o['admitted'] for o in disposition['outcomes']))
        asked = [j['observations'] for j in self.events('JUMP')]
        self.assertEqual(len(asked), len(set(asked)))  # Each call saw evidence the previous one did not.

    def test_no_agent_keeps_the_unattended_record(self):
        path = fx.law_campaign(self.root, DIGIT, range(0, 31), hypotheses=[('h1', 'x + 5')],
                               budget={'wall_seconds': 900, 'experiments': 10})
        initialize(self.root, path)
        drive(self.root, publish=False)
        self.assertEqual({j['status'] for j in self.events('JUMP')}, {'NO_RIVAL_WITHIN_BOUND'})


class RulebookSizeTests(unittest.TestCase):
    def test_rulebooks_beyond_one_synthesized_expression_compile(self):
        digits = 'x + 5*((x % 10 > 0) + (floor(x / 10) % 10 > 0) + (floor(x / 100) % 10 > 0))'
        hyp = model.compile_hypothesis({'id': 'd', 'predict': {'y': digits}}, ['x'], ['y'])
        self.assertGreater(hyp['size'], model.MAX_NODES)
        self.assertEqual(model.predict(hyp, {'x': 105})[0]['y'], 115)
        huge = ' + '.join(['x'] * 40)
        with self.assertRaisesRegex(ValueError, 'oversized'):
            model.compile_hypothesis({'id': 'h', 'predict': {'y': huge}}, ['x'], ['y'])


class ReplicationTests(Campaign):
    ARMS = {'base': {'s1': 10, 's2': 10, 's3': 10, 's4': 10},
            'flashy': {'s1': 13, 's2': 13, 's3': 13, 's4': 13}, 'flashy_r2': {'s1': 14, 's2': 14, 's3': 14, 's4': 7},
            'steady': {'s1': 11, 's2': 11, 's3': 11, 's4': 11}, 'steady_r2': {'s1': 11, 's2': 12, 's3': 10, 's4': 11},
            'weak': {'s1': 9, 's2': 10, 's3': 10, 's4': 10}, 'weak_r2': {'s1': 20, 's2': 20, 's3': 20, 's4': 20}}

    def declare(self, budget=None, root=None):
        path = fx.improve_campaign(root or self.root, self.ARMS, min_gain=0)

        def change(d):
            for row in d['candidates']:
                if row['id'].endswith('_r2'):
                    row.update(replicates=row['id'][:-3], cost=2)
            if budget is not None:
                d['budget']['experiments'] = budget
        return self.edit(path, change)

    def test_an_arm_is_accepted_only_if_every_declared_run_passes(self):
        initialize(self.root, self.declare())
        result = drive(self.root, publish=False)
        self.assertEqual(result['status'], 'IMPROVEMENT_EVALUATED')
        improvement = result['verification']['improvement']
        verdicts = {a['arm']: a['verdict'] for a in improvement['arms']}
        self.assertEqual(verdicts, {'flashy': 'LOCAL_REGRESSION', 'steady': 'ACCEPT', 'weak': 'NO_MEAN_GAIN'})
        self.assertEqual(improvement['recommended'], 'steady')
        flashy = next(a for a in improvement['arms'] if a['arm'] == 'flashy')
        self.assertEqual([r['verdict'] for r in flashy['runs']], ['ACCEPT', 'LOCAL_REGRESSION'])
        # A failed first run can never be accepted, so its replicate is never bought.
        self.assertNotIn('weak_r2', compass.fold(cstore.store_for(self.root))['observations'])

    def test_a_budget_that_cannot_replicate_the_survivor_is_not_a_settled_evaluation(self):
        # base + three first runs + one re-run: the best first run is replicated first and fails.
        initialize(self.root, self.declare(budget=5))
        result = drive(self.root, publish=False)
        self.assertEqual(result['status'], 'JUDGMENT_REQUIRED')
        self.assertEqual(result['workset']['handoff']['detail']['unreplicated'], ['steady'])
        self.assertIsNone(result['verification']['improvement']['recommended'])

    def test_a_failed_baseline_stops_the_purchase_of_arms(self):
        path = self.declare()
        fx.write(self.root / 'task.json', {'arms': {k: v for k, v in self.ARMS.items() if k != 'base'}})
        initialize(self.root, path)
        result = drive(self.root, publish=False)
        self.assertEqual((result['status'], result['verification']['improvement']['status']),
                         ('JUDGMENT_REQUIRED', 'BASELINE_UNMEASURED'))
        state = compass.fold(cstore.store_for(self.root))
        self.assertEqual(state['observations']['base']['status'], 'RUN_FAILED')
        # Every arm needs the baseline, so none was bought after it failed.
        self.assertEqual(sorted(r for r in state['runs'] if r.startswith('compass-')), ['compass-base'])

    def test_replicates_must_name_an_original_non_baseline_arm(self):
        for target in ('base', 'missing', 'flashy_r2'):
            with self.subTest(target=target):
                root = Path(self.temp.name).resolve() / ('r-' + target)
                path = self.edit(self.declare(root=root), lambda d: d['candidates'][-1].update(replicates=target))
                with self.assertRaisesRegex(ValueError, 'must replicate another declared'):
                    initialize(root, path)
        root = Path(self.temp.name).resolve() / 'r-baseline'
        path = self.edit(self.declare(root=root), lambda d: d['candidates'][0].update(replicates='flashy'))
        with self.assertRaisesRegex(ValueError, 'profile baseline base cannot replicate'):
            initialize(root, path)
        law = fx.law_campaign(self.root, 'x', range(4))
        with self.assertRaisesRegex(ValueError, 'Only IMPROVE candidates declare replicates'):
            initialize(self.root, self.edit(law, lambda d: d['candidates'][1].update(replicates='x0')))


if __name__ == '__main__':
    unittest.main()
