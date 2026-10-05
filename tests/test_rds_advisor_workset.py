"""Public CLI continuation and read-only projection boundary regressions.

Reuses labelled synthetic workers; no model or scientific-benefit trial.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import ProjectStore, digest
import rds_structure as structure
from rds_advisor_workset import build, _section, _compact
from rds_owned_advisor import review


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


owned_fixture = module('_workset_owned', ROOT / 'tests/test_rds_owned_advisor.py')
structure_fixture = module('_workset_structure', ROOT / 'examples/problem-structure/run.py')


class WorkingSetTests(unittest.TestCase):
    def setUp(self):
        self.harness = owned_fixture.OwnedAdvisorCLITests(methodName='runTest')
        self.harness.setUp()
        self.addCleanup(self.harness.doCleanups)
        self.root = self.harness.root

    def output(self, *args):
        return self.harness.output(*args)

    def test_final_owned_selection_goals_and_recovery_preserve_attempts_and_budget(self):
        h = self.harness
        h.initialize()
        first = self.output('advise', '--working-set', '--brief')['working_set']
        self.assertEqual(first['selection']['selected_run'], 'baseline')
        h.output('project', 'advance')
        before = h.snapshot()
        selected = self.output('advise')['selected_run']
        for _ in range(2):  # Fresh continuation, without rerunning the failed hypothesis.
            brief = self.output('advise', '--working-set', '--brief')
            view = brief['working_set']
            self.assertEqual(view['status'], 'CURRENT')
            self.assertEqual(view['selection']['selected_run'], selected)
            self.assertEqual(selected, 'repair')
            goal = view['goals']['items'][0]
            self.assertEqual(goal['evaluation']['truth'], 'FALSE')
            self.assertEqual(goal['evidence']['source']['path'], 'outputs/baseline.json')
            self.assertEqual(view['scope']['decision']['goal_revision'], 'synthetic-v1')
            self.assertEqual(view['operational_results']['items'][0]['run_status'], 'SUCCEEDED')
            self.assertEqual(view['operational_results']['items'][0]['scientific_support'], 'UNKNOWN')
            self.assertEqual(view['scoped_feedback']['items'], [])
            self.assertEqual(view['budget']['wall_seconds']['remaining'], before['budget']['wall_seconds']['remaining'])
            self.assertEqual(h.snapshot()['runs'], before['runs'])
            self.assertEqual(h.snapshot()['budget'], before['budget'])
            self.assertEqual(h.starts(), ['baseline'])
        h.output('project', 'advance')
        after = self.output('advise', '--working-set')['working_set']
        self.assertIsNone(after['selection']['selected_run'])
        self.assertEqual(h.starts(), ['baseline', 'repair'])

    def test_execution_failure_is_not_scientific_refutation(self):
        self.harness.initialize('nonzero')
        self.harness.output('project', 'advance', status_codes=(0, 1))
        view = self.output('advise', '--working-set')['working_set']
        self.assertEqual(view['operational_results']['items'][0]['run_status'], 'FAILED')
        self.assertEqual(view['scoped_feedback']['items'], [])
        self.assertEqual(view['goals']['items'][0]['evaluation']['truth'], 'UNKNOWN')

    def make_structure(self, mode='ok', legacy=False):
        original_manifest = structure_fixture.manifest
        def frozen_manifest(root, ident, strategy='interaction', **kwargs):
            kwargs['strategy'] = strategy
            if ident == 'candidate':
                kwargs['strategy'] = 'linear'
                if kwargs.get('verifier'):
                    kwargs['mode'] = mode
            return original_manifest(root, ident, **kwargs)
        with patch.object(structure_fixture, 'manifest', side_effect=frozen_manifest):
            root = structure_fixture.prepare(self.root / 'structure', 'negative', owned=True)
        task = structure.request(root)['tasks'][0]
        proposal = structure_fixture.proposal(root, task, strategy='linear', mode=mode)
        if legacy:
            del proposal['discriminator']
            with patch('rds_structure.discrimination.validate', return_value=None):
                structure.propose(root, proposal)
            original_put = structure._put
            def old_writer(store, kind, ident, value, **kwargs):
                if kind == 'FEEDBACK':
                    value = {k: v for k, v in value.items() if k not in ('discrimination', 'evaluator_observation')}
                    value.update(status='TEST_REFUTED', observation='REFUTE')
                return original_put(store, kind, ident, value, **kwargs)
            with patch('rds_structure._put', side_effect=old_writer):
                observed = structure.advance(root, 'candidate')
        else:
            structure.propose(root, proposal)
            observed = structure.advance(root, 'candidate')
        store = ProjectStore(root)
        env = {**os.environ, 'RDS_USAGE_DB': str(self.root / 'usage.sqlite3')}
        def cli():
            result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(root),
                'advise', '--working-set', '--brief'], capture_output=True, text=True, encoding='utf-8', env=env, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            return json.loads(result.stdout)['working_set']
        return root, store, observed, cli

    def test_exact_refutation_conditions_and_original_locators_are_consumable(self):
        root, store, observed, cli = self.make_structure()
        before = store.snapshot()
        report = review(store)
        with patch('rds_structure.next_step', side_effect=AssertionError('metered API')), \
             patch('rds_structure.request', side_effect=AssertionError('new exploration')), \
             patch.object(ProjectStore, 'register', side_effect=AssertionError('registration')), \
             patch.object(ProjectStore, 'execute', side_effect=AssertionError('launch')):
            direct = build(store, report)
        view = cli()
        self.assertEqual(direct['scoped_feedback'], view['scoped_feedback'])
        feedback = view['scoped_feedback']['items'][0]
        if feedback.get('details_omitted'):
            feedback = json.loads(Path(feedback['original']['path']).read_text())
        self.assertEqual(feedback['observation'], 'REFUTE')
        self.assertEqual(feedback['feedback_sha256'], digest(observed))
        self.assertIsNotNone(feedback['blocked_hypothesis_key'])
        self.assertEqual(len(feedback['discriminator']['conditions']), 2)
        self.assertEqual(feedback['trigger']['proposal_id'], 'candidate')
        self.assertEqual(feedback['measurement']['measured']['value'], False)
        self.assertTrue(feedback['originals'])
        self.assertEqual(store.snapshot()['runs'], before['runs'])
        self.assertEqual(store.snapshot()['budget'], before['budget'])

    def test_unknown_and_legacy_refute_require_evidence_without_rewriting_original(self):
        for legacy in (False, True):
            if legacy:
                # Each independent fixture uses a distinct child, not a reset of the first ledger.
                self.root = self.harness.root / 'legacy'
                self.root.mkdir()
            root, store, observed, cli = self.make_structure(mode='unknown', legacy=legacy)
            feedback = cli()['scoped_feedback']['items'][0]
            if feedback.get('details_omitted'):
                feedback = json.loads(Path(feedback['original']['path']).read_text())
            self.assertEqual(feedback['observation'], 'UNKNOWN')
            self.assertEqual(feedback['allowed_trigger_purposes'], ['EVIDENCE'])
            self.assertIsNone(feedback['blocked_hypothesis_key'])
            self.assertEqual(feedback['feedback_sha256'], digest(observed))
            if legacy:
                self.assertEqual(feedback['original_observation'], 'REFUTE')
                self.assertEqual(structure._find(store, 'FEEDBACK', 'candidate'), observed)

    def test_changed_original_fails_closed_without_partial_feedback(self):
        root, store, observed, cli = self.make_structure()
        (root / 'out/candidate.json').write_text('{}')
        before = store.snapshot()
        report = review(store)
        view = build(store, report)
        self.assertEqual(view['status'], 'UNAVAILABLE')
        self.assertNotIn('scoped_feedback', view)
        self.assertEqual(store.snapshot()['budget'], before['budget'])

    def test_stale_report_and_snapshot_cannot_supply_current_selection(self):
        self.harness.initialize()
        store = ProjectStore(self.root)
        report = review(store)
        self.harness.output('project', 'advance')
        view = build(store, report)
        self.assertEqual(view['status'], 'UNAVAILABLE')
        self.assertIn('stale', view['diagnostic'])
        self.assertNotIn('selection', view)

    def test_other_structure_scope_is_not_presented_as_current_refutation(self):
        root, store, observed, cli = self.make_structure()
        from rds_tms_store import maintain
        maintain(root, updates=[{'nodes': [{'id': 'new-obligation', 'status': 'UNKNOWN',
            'source': {'locator': 'distinct declared structure scope'}}], 'goals': ['new-obligation']}])
        view = cli()
        self.assertEqual(view['status'], 'CURRENT')
        self.assertEqual(view['outside_scope_proposals'], 1)
        self.assertEqual(view['scoped_feedback']['items'], [])

    def test_tampered_feedback_cas_is_unavailable_and_does_not_charge_or_repeat(self):
        root, store, observed, cli = self.make_structure()
        event = next(e for e in structure._events(store) if e['kind'] == 'STRUCTURE_FEEDBACK')
        Path(event['record']['path']).write_text('{}')
        before = store.snapshot()
        view = cli()
        self.assertEqual(view['status'], 'UNAVAILABLE')
        self.assertIn('hash mismatch', view['diagnostic'])
        self.assertNotIn('scoped_feedback', view)
        self.assertEqual(store.snapshot()['budget'], before['budget'])
        self.assertEqual(store.snapshot()['runs'], before['runs'])

    def test_output_option_does_not_open_caller_override_path_and_requires_owned_policy(self):
        self.harness.initialize(include_policy=False)
        result = self.harness.call('advise', '--working-set', ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('advisor_policy', result.stdout + result.stderr)

    def test_owned_workset_rejects_caller_context_and_public_goal_omission_is_complete(self):
        def goals(policy):
            policy['context']['decision']['goal_conditions'] = [
                {'fact': 'baseline.score', 'op': 'gte', 'value': i} for i in range(9)]
        self.harness.initialize(mutate_policy=goals)
        context = self.harness.write_json('override.json', {'facts': {}})
        before = self.harness.snapshot()
        result = self.harness.call('advise', '--working-set', '--context', context, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('overrides are not accepted', result.stdout + result.stderr)
        view = self.output('advise', '--working-set', '--brief')['working_set']
        section = view['goals']
        self.assertEqual((section['total'], section['omitted'], len(section['items'])), (9, 5, 4))
        original = json.loads(Path(section['original']['path']).read_text())
        self.assertEqual([r['condition']['value'] for r in original], list(range(9)))
        self.assertEqual(self.harness.snapshot()['budget'], before['budget'])
        self.assertEqual(self.harness.starts(), [])

    def test_omissions_have_complete_hash_bound_originals_and_large_predicates_not_truncated(self):
        self.harness.initialize()
        store = ProjectStore(self.root)
        rows = [{'id': str(i)} for i in range(7)]
        section = _section(store, rows)
        self.assertEqual((len(section['items']), section['total'], section['omitted']), (4, 7, 3))
        original = Path(section['original']['path'])
        self.assertEqual(json.loads(original.read_text()), rows)
        self.assertEqual(digest(rows), section['original']['sha256'])
        value = {'id': 'exact-id', 'prediction': {'value': 'x' * 3000}}
        compact = _compact(store, value)
        self.assertEqual(compact['id'], 'exact-id')
        self.assertNotIn('prediction', compact)
        self.assertEqual(json.loads(Path(compact['original']['path']).read_text()), value)


if __name__ == '__main__':
    unittest.main()
