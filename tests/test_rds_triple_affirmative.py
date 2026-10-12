"""Triple Affirmative: found, portable, applicable to the whole declared scope (#80).

All goals, files and values are synthetic. The project declares what portable and applicable
mean; the Advisor only refuses to affirm without importer-read or program-derived evidence.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_advisor_search import AFFIRMATIVE_MOVE_TEXT, review_selection
from rds_verify_types import digest

BINDING = {'run_id': 'run', 'code_sha256': 'a' * 64, 'config_sha256': 'b' * 64, 'data_sha256': 'c' * 64,
           'data_split': 'development', 'metric': {'definition': 'synthetic quantity', 'reduction': 'single value'}}


def route(goal):
    action = {'id': 'route-check', 'kind': 'PAIRED_TEST', 'description': 'Synthetic bounded check', 'target': goal,
              'competing_explanations': ['holds', 'fails'], 'required_observables': ['reported outcome'],
              'outcomes': [{'observation': 'positive', 'next_decision': 'continue'},
                           {'observation': 'negative', 'next_decision': 'revise'}],
              'goal_contribution': {'target': goal, 'path': [goal], 'source': 'synthetic-protocol.json'}}
    return {'nodes': [{'id': 'route', 'executable': {'decisions': ['next'], 'preconditions': [], 'action': action}}],
            'edges': []}


def predicate(fact, op, value):
    return {'fact': fact, 'op': op, 'value': value}


def context(goal, affirmations=None, facts=None):
    decision = {'id': 'next', 'goal_revision': 'synthetic-v1', 'scope': {'domain': 'synthetic'}, 'goal_conditions': [goal]}
    if affirmations is not None:
        decision['affirmations'] = affirmations
    return {'research_mode': 'empirical', 'facts': facts or {}, 'decision': decision}


# Initial acceptance settings, in the documented order; not domain restrictions.
DL_GOAL = predicate('heldout_error', 'lte', 0.1)
DL_AFFIRM = {'portable': [predicate('clean_root_replay_error', 'lte', 0.1)],
             'applicable': [predicate('worst_declared_cohort_error', 'lte', 0.1)]}
SW_GOAL = predicate('suite_failures', 'eq', 0)
SW_AFFIRM = {'portable': [predicate('other_platform_suite_failures', 'eq', 0)],
             'applicable': [predicate('declared_interfaces_failing', 'eq', 0)]}
MATH_GOAL = predicate('bounded_identity_holds', 'eq', 'yes')
MATH_AFFIRM = {'portable': [predicate('independent_checker_result', 'eq', 'accepted')],
               'applicable': [predicate('all_n_proof_status', 'eq', 'checked')]}


class TripleAffirmativeCLITests(unittest.TestCase):
    def advise(self, ctx, goal_fact, observed=None, declared=None, *extra, expect=0, aliases=None, derived=None,
               jsonl=None):
        """Run the real `advise` entry; `observed` values are imported from a hash-bound metric file."""
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            sources = []
            if jsonl:
                data, rows = jsonl
                (project / 'log.jsonl').write_bytes(data)
                sources.append({'id': 'log', 'kind': 'log', 'path': 'log.jsonl', 'format': 'jsonl',
                                'expected_sha256': digest(data), 'binding': BINDING,
                                'facts': [{'id': k, 'row': r, 'pointer': p} for k, (r, p) in rows.items()]})
            if observed:
                metric = json.dumps(observed).encode('utf-8')
                (project / 'metrics.json').write_bytes(metric)
                sources.append({'id': 'metrics', 'kind': 'metric', 'path': 'metrics.json', 'format': 'json',
                                'expected_sha256': digest(metric), 'binding': BINDING,
                                'facts': [{'id': k, 'pointer': '/' + k} for k in observed] +
                                         [{'id': k, 'pointer': '/' + v} for k, v in (aliases or {}).items()]})
            if declared:
                config = json.dumps(declared).encode('utf-8')
                (project / 'config.json').write_bytes(config)
                sources.append({'id': 'config', 'kind': 'config', 'path': 'config.json', 'format': 'json',
                                'expected_sha256': digest(config), 'binding': {**BINDING, 'run_id': 'config-run', 'config_sha256': digest(config)},
                                'facts': [{'id': k, 'pointer': '/' + k} for k in declared]})
            (project / 'context.json').write_text(json.dumps(ctx), encoding='utf-8')
            (project / 'graph.json').write_text(json.dumps(route(goal_fact)), encoding='utf-8')
            args = ['--research-context', str(project / 'context.json'), '--graph', str(project / 'graph.json')]
            if sources:
                (project / 'manifest.json').write_text(json.dumps({'schema': 'rds-artifact-manifest-v1', 'sources': sources,
                                                                   'derived': derived or []}), encoding='utf-8')
                args += ['--artifacts', str(project / 'manifest.json')]
            proc = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(project),
                                   'advise', *args, *extra],
                                  cwd=ROOT, capture_output=True, encoding='utf-8', timeout=20)
            self.assertEqual(proc.returncode, expect, proc.stderr)
            if '--brief' in extra:
                return json.loads(proc.stdout)
            self.assertFalse((project / '.rds').exists())
            if expect:
                return proc
            answer = json.loads(proc.stdout)
            return next(row['search'] for row in answer['recommendations']
                        if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')['selection_review']

    def assertOpen(self, review, open_, status, move):
        triple = review['goal']['triple_affirmative']
        self.assertEqual(review['goal']['status'], 'TRUE')
        self.assertEqual(triple['status'], status)
        self.assertEqual(triple['open'], open_)
        self.assertEqual(review['flags'][0]['kind'], 'TRIPLE_AFFIRMATIVE_OPEN')
        self.assertEqual(review['flags'][0]['open'], open_)
        self.assertEqual(review['next_move']['kind'], move)
        self.assertEqual(review['next_move']['affirmatives'], open_)
        self.assertTrue(review['next_move']['prompt'].startswith(AFFIRMATIVE_MOVE_TEXT))
        self.assertEqual(review['next_move']['authorization'], 'UNCHANGED')

    # Deep learning
    def test_deep_learning_all_observed_affirms_and_leaves_goal_closed(self):
        values = {'heldout_error': 0.05, 'clean_root_replay_error': 0.06, 'worst_declared_cohort_error': 0.09}
        review = self.advise(context(DL_GOAL, DL_AFFIRM), 'heldout_error', values)
        triple = review['goal']['triple_affirmative']
        self.assertEqual(triple['status'], 'TRUE')
        self.assertEqual(triple['open'], [])
        for name in ('found', 'portable', 'applicable'):
            self.assertEqual(triple[name]['status'], 'TRUE')
            self.assertTrue(all(r['evidence_status'] == 'ARTIFACT_OBSERVED' for r in triple[name]['conditions']))
        self.assertNotIn('next_move', review)
        self.assertNotIn('TRIPLE_AFFIRMATIVE_OPEN', [f['kind'] for f in review['flags']])

    def test_deep_learning_typed_values_close_the_goal_but_are_not_found(self):
        facts = {k: {'value': v, 'source': 'synthetic-notes.txt'} for k, v in
                 {'heldout_error': 0.05, 'clean_root_replay_error': 0.06, 'worst_declared_cohort_error': 0.09}.items()}
        baseline = self.advise(context(DL_GOAL, facts=facts), 'heldout_error')
        self.assertEqual(baseline['goal']['status'], 'TRUE')
        self.assertNotIn('next_move', baseline)
        review = self.advise(context(DL_GOAL, DL_AFFIRM, facts), 'heldout_error')
        self.assertOpen(review, ['FOUND', 'PORTABLE', 'APPLICABLE'], 'UNKNOWN', 'RESOLVE_PREMISE')
        row = review['goal']['triple_affirmative']['found']['conditions'][0]
        self.assertEqual((row['truth'], row['affirms'], row['evidence_status']), ('TRUE', 'UNKNOWN', 'INPUT_REPORTED'))
        self.assertIn('reported, not found', row['affirmation_reason'])
        # The declared goal status, its conditions and every other flag are unchanged.
        self.assertEqual(review['goal']['conditions'], baseline['goal']['conditions'])
        self.assertEqual(review['flags'][1:], baseline['flags'])

    def test_deep_learning_failing_cohort_refutes_applicability(self):
        values = {'heldout_error': 0.05, 'clean_root_replay_error': 0.06, 'worst_declared_cohort_error': 0.4}
        review = self.advise(context(DL_GOAL, DL_AFFIRM), 'heldout_error', values)
        self.assertOpen(review, ['APPLICABLE'], 'FALSE', 'DIAGNOSE_GOAL_GAP')
        self.assertEqual(review['next_move']['reason'],
                         'The found result fails the declared APPLICABLE affirmative; this is a scoped gap, '
                         'not an impossibility result or a causal diagnosis.')

    # Software/tool development
    def test_software_missing_other_platform_run_keeps_portability_unknown(self):
        values = {'suite_failures': 0, 'declared_interfaces_failing': 0}
        review = self.advise(context(SW_GOAL, SW_AFFIRM), 'suite_failures', values)
        self.assertOpen(review, ['PORTABLE'], 'UNKNOWN', 'RESOLVE_PREMISE')
        row = review['goal']['triple_affirmative']['portable']['conditions'][0]
        self.assertEqual((row['truth'], row['affirms']), ('UNKNOWN', 'UNKNOWN'))

    def test_software_unresolved_affirmative_comes_before_a_failed_one(self):
        # As in the existing next move, UNKNOWN evidence is resolved before a reported failure is acted on.
        values = {'suite_failures': 0, 'declared_interfaces_failing': 2}
        review = self.advise(context(SW_GOAL, SW_AFFIRM), 'suite_failures', values)
        self.assertOpen(review, ['PORTABLE', 'APPLICABLE'], 'FALSE', 'RESOLVE_PREMISE')
        self.assertNotIn('found result fails', review['next_move']['reason'])
        values['other_platform_suite_failures'] = 0
        review = self.advise(context(SW_GOAL, SW_AFFIRM), 'suite_failures', values)
        self.assertOpen(review, ['APPLICABLE'], 'FALSE', 'DIAGNOSE_GOAL_GAP')

    def test_typed_goal_with_failed_portability_is_not_called_a_found_result(self):
        facts = {'heldout_error': {'value': 0.05, 'source': 'synthetic-notes.txt'}}
        values = {'clean_root_replay_error': 0.5, 'worst_declared_cohort_error': 0.09}
        review = self.advise(context(DL_GOAL, DL_AFFIRM, facts), 'heldout_error', values)
        self.assertOpen(review, ['FOUND', 'PORTABLE'], 'FALSE', 'RESOLVE_PREMISE')
        self.assertEqual(review['next_move']['reason'],
                         'The goal predicates compare TRUE, but FOUND is not affirmed by unreused importer-read or '
                         'program-derived evidence; resolve it first. PORTABLE already failed and stays open.')

    def test_typed_or_configured_failure_refutes_but_never_affirms(self):
        # Like a typed failing goal predicate, a typed failing affirmative is a reported failure.
        values = {'heldout_error': 0.05, 'worst_declared_cohort_error': 0.09}
        facts = {'clean_root_replay_error': {'value': 0.5, 'source': 'synthetic-notes.txt'}}
        review = self.advise(context(DL_GOAL, DL_AFFIRM, facts), 'heldout_error', values)
        self.assertOpen(review, ['PORTABLE'], 'FALSE', 'DIAGNOSE_GOAL_GAP')
        self.assertEqual(review['next_move']['reason'],
                         'The found result fails the declared PORTABLE affirmative; this is a scoped gap, '
                         'not an impossibility result or a causal diagnosis.')
        review = self.advise(context(DL_GOAL, DL_AFFIRM), 'heldout_error', values, {'clean_root_replay_error': 0.5})
        self.assertOpen(review, ['PORTABLE'], 'FALSE', 'DIAGNOSE_GOAL_GAP')
        facts['clean_root_replay_error']['value'] = 0.06
        review = self.advise(context(DL_GOAL, DL_AFFIRM, facts), 'heldout_error', values)
        self.assertOpen(review, ['PORTABLE'], 'UNKNOWN', 'RESOLVE_PREMISE')
        self.assertEqual(review['next_move']['reason'],
                         'The goal predicates compare TRUE, but PORTABLE is not affirmed by unreused importer-read or '
                         'program-derived evidence; resolve it first.')

    # Mathematics
    def test_mathematics_bounded_check_is_not_a_for_all_result(self):
        values = {'bounded_identity_holds': 'yes', 'independent_checker_result': 'accepted'}
        review = self.advise(context(MATH_GOAL, {'portable': MATH_AFFIRM['portable']}), 'bounded_identity_holds', values)
        self.assertOpen(review, ['APPLICABLE'], 'UNKNOWN', 'RESOLVE_PREMISE')
        self.assertIn('never vacuously true', review['goal']['triple_affirmative']['applicable']['reason'])

    def test_mathematics_configured_value_never_affirms(self):
        values = {'bounded_identity_holds': 'yes', 'independent_checker_result': 'accepted'}
        review = self.advise(context(MATH_GOAL, MATH_AFFIRM), 'bounded_identity_holds', values,
                             {'all_n_proof_status': 'checked'})
        self.assertOpen(review, ['APPLICABLE'], 'UNKNOWN', 'RESOLVE_PREMISE')
        row = review['goal']['triple_affirmative']['applicable']['conditions'][0]
        self.assertEqual((row['truth'], row['evidence_status'], row['affirms']), ('TRUE', 'ARTIFACT_DECLARED', 'UNKNOWN'))

    # Edges
    def test_reused_measurement_is_not_an_independent_affirmation(self):
        affirm = {'portable': [predicate('heldout_error', 'lte', 0.1)],
                  'applicable': [predicate('worst_declared_cohort_error', 'lte', 0.1)]}
        values = {'heldout_error': 0.05, 'worst_declared_cohort_error': 0.09}
        review = self.advise(context(DL_GOAL, affirm), 'heldout_error', values)
        self.assertOpen(review, ['FOUND', 'PORTABLE'], 'UNKNOWN', 'RESOLVE_PREMISE')
        row = review['goal']['triple_affirmative']['portable']['conditions'][0]
        self.assertIn('reuses the evidence', row['affirmation_reason'])
        self.assertIn('reuses the evidence', review['goal']['triple_affirmative']['found']['conditions'][0]['affirmation_reason'])
        # Shared between portable and applicable is also reuse, in both directions.
        affirm = {'portable': [predicate('shared_check', 'eq', 0)], 'applicable': [predicate('shared_check', 'eq', 0)]}
        review = self.advise(context(DL_GOAL, affirm), 'heldout_error', {'heldout_error': 0.05, 'shared_check': 0})
        self.assertOpen(review, ['PORTABLE', 'APPLICABLE'], 'UNKNOWN', 'RESOLVE_PREMISE')

    def test_renamed_reading_of_the_same_bytes_is_reuse(self):
        # Two fact names selecting the same located value in the same hash-bound file are one measurement.
        values = {'heldout_error': 0.05}
        review = self.advise(context(DL_GOAL, DL_AFFIRM), 'heldout_error', values,
                             aliases={'clean_root_replay_error': 'heldout_error', 'worst_declared_cohort_error': 'heldout_error'})
        self.assertOpen(review, ['FOUND', 'PORTABLE', 'APPLICABLE'], 'UNKNOWN', 'RESOLVE_PREMISE')
        for name in ('found', 'portable', 'applicable'):
            row = review['goal']['triple_affirmative'][name]['conditions'][0]
            self.assertEqual((row['truth'], row['evidence_status']), ('TRUE', 'ARTIFACT_OBSERVED'))
            self.assertIn('reuses the evidence', row['affirmation_reason'])

    def test_non_ascii_index_spelling_of_the_same_element_is_not_a_new_reading(self):
        # Unicode digits are not RFC 6901 indexes; they must not mint a second locator for one element.
        values = {'runs': [0.05]}
        review = self.advise(context(DL_GOAL, DL_AFFIRM), 'heldout_error', values,
                             aliases={'heldout_error': 'runs/0', 'clean_root_replay_error': 'runs/٠',
                                      'worst_declared_cohort_error': 'runs/０'})
        self.assertOpen(review, ['PORTABLE', 'APPLICABLE'], 'UNKNOWN', 'RESOLVE_PREMISE')
        self.assertEqual(review['goal']['triple_affirmative']['found']['status'], 'TRUE')
        for name in ('portable', 'applicable'):
            row = review['goal']['triple_affirmative'][name]['conditions'][0]
            self.assertEqual((row['truth'], row['affirms']), ('UNKNOWN', 'UNKNOWN'))

    def test_malformed_tilde_escape_spelling_of_the_same_key_is_not_a_new_reading(self):
        # RFC 6901 spells the key '~~~1' only as '~0~0~01'; '~' must be followed by 0 or 1.
        values = {'runs': {'~~~1': 0.05}}
        review = self.advise(context(DL_GOAL, DL_AFFIRM), 'heldout_error', values,
                             aliases={'heldout_error': 'runs/~0~0~01', 'clean_root_replay_error': 'runs/~~0~01',
                                      'worst_declared_cohort_error': 'runs/~0~~01'})
        self.assertOpen(review, ['PORTABLE', 'APPLICABLE'], 'UNKNOWN', 'RESOLVE_PREMISE')
        self.assertEqual(review['goal']['triple_affirmative']['found']['status'], 'TRUE')
        for name in ('portable', 'applicable'):
            row = review['goal']['triple_affirmative'][name]['conditions'][0]
            self.assertEqual((row['truth'], row['affirms']), ('UNKNOWN', 'UNKNOWN'))

    def test_non_integer_jsonl_row_spelling_of_the_same_line_is_not_a_new_reading(self):
        # 1.0 and true equal line 1 in Python; they must not mint a second locator for one physical line.
        data = b'{"err":0.05}\n'
        review = self.advise(context(DL_GOAL, DL_AFFIRM), 'heldout_error',
                             jsonl=(data, {'heldout_error': (1, '/err'), 'clean_root_replay_error': (1.0, '/err'),
                                           'worst_declared_cohort_error': (True, '/err')}))
        self.assertOpen(review, ['PORTABLE', 'APPLICABLE'], 'UNKNOWN', 'RESOLVE_PREMISE')
        self.assertEqual(review['goal']['triple_affirmative']['found']['status'], 'TRUE')
        for name in ('portable', 'applicable'):
            row = review['goal']['triple_affirmative'][name]['conditions'][0]
            self.assertEqual((row['truth'], row['affirms']), ('UNKNOWN', 'UNKNOWN'))
        # Three integer reads of the same line are reuse, not three affirmations.
        review = self.advise(context(DL_GOAL, DL_AFFIRM), 'heldout_error',
                             jsonl=(data, {'heldout_error': (1, '/err'), 'clean_root_replay_error': (1, '/err'),
                                           'worst_declared_cohort_error': (1, '/err')}))
        self.assertOpen(review, ['FOUND', 'PORTABLE', 'APPLICABLE'], 'UNKNOWN', 'RESOLVE_PREMISE')
        # Distinct physical lines are distinct readings and still affirm.
        review = self.advise(context(DL_GOAL, DL_AFFIRM), 'heldout_error',
                             jsonl=(b'{"err":0.05}\n{"err":0.06}\n{"err":0.09}\n',
                                    {'heldout_error': (1, '/err'), 'clean_root_replay_error': (2, '/err'),
                                     'worst_declared_cohort_error': (3, '/err')}))
        self.assertEqual(review['goal']['triple_affirmative']['status'], 'TRUE')
        self.assertNotIn('next_move', review)

    def test_derivation_from_the_goal_measurement_is_reuse(self):
        values = {'heldout_error': 0.05, 'worst_declared_cohort_error': 0.09}
        derived = [{'id': 'clean_root_replay_error', 'method': 'mean', 'input_fact_ids': ['heldout_error']}]
        review = self.advise(context(DL_GOAL, DL_AFFIRM), 'heldout_error', values, derived=derived)
        self.assertOpen(review, ['FOUND', 'PORTABLE'], 'UNKNOWN', 'RESOLVE_PREMISE')
        row = review['goal']['triple_affirmative']['portable']['conditions'][0]
        self.assertEqual(row['evidence_status'], 'PROGRAM_DERIVED')
        self.assertIn('reuses the evidence', row['affirmation_reason'])
        # A derivation from its own independent reading still affirms.
        values['replay_reading'] = 0.06
        derived = [{'id': 'clean_root_replay_error', 'method': 'mean', 'input_fact_ids': ['replay_reading']}]
        review = self.advise(context(DL_GOAL, DL_AFFIRM), 'heldout_error', values, derived=derived)
        self.assertEqual(review['goal']['triple_affirmative']['status'], 'TRUE')
        # Once the researcher disputes that input, the derivation built on it no longer affirms.
        for dispute in ({'value': 0.9, 'source': 'synthetic-notes.txt'},
                        {'value': 0.06, 'source': 'synthetic-notes.txt', 'reliable': False}):
            with self.subTest(dispute=dispute):
                review = self.advise(context(DL_GOAL, DL_AFFIRM, {'replay_reading': dispute}), 'heldout_error',
                                     values, derived=derived)
                self.assertOpen(review, ['PORTABLE'], 'UNKNOWN', 'RESOLVE_PREMISE')
                row = review['goal']['triple_affirmative']['portable']['conditions'][0]
                self.assertEqual((row['truth'], row['evidence_status']), ('TRUE', 'PROGRAM_DERIVED'))
                self.assertIn('no longer importer-read evidence', row['affirmation_reason'])

    def test_open_goal_keeps_its_existing_move_and_reports_found_false(self):
        values = {'heldout_error': 0.3, 'clean_root_replay_error': 0.06, 'worst_declared_cohort_error': 0.09}
        baseline = self.advise(context(DL_GOAL), 'heldout_error', values)
        review = self.advise(context(DL_GOAL, DL_AFFIRM), 'heldout_error', values)
        self.assertEqual(review['goal']['status'], 'FALSE')
        self.assertEqual(review['goal']['triple_affirmative']['found']['status'], 'FALSE')
        self.assertEqual(review['goal']['triple_affirmative']['status'], 'FALSE')
        self.assertEqual(review['next_move'], baseline['next_move'])
        self.assertEqual(review['flags'], baseline['flags'])

    def test_absent_affirmations_leave_review_unchanged(self):
        values = {'heldout_error': 0.05}
        review = self.advise(context(DL_GOAL), 'heldout_error', values)
        self.assertNotIn('triple_affirmative', review['goal'])
        self.assertNotIn('next_move', review)

    def test_malformed_affirmations_are_field_level_rejections(self):
        cases = [({'portable': []}, 'decision.affirmations.portable must be 1 to 32'),
                 ({'portable': [{'op': 'eq', 'value': 1}]}, 'decision.affirmations.portable must be 1 to 32'),
                 ({'applicable': {'fact': 'x'}}, 'decision.affirmations.applicable must be 1 to 32'),
                 ({'portable': [predicate('x', 'eq', 1)] * 33}, 'decision.affirmations.portable must be 1 to 32'),
                 ({'found': [predicate('x', 'eq', 1)]}, "unsupported key(s) 'found'"),
                 ({}, 'decision.affirmations must be an object'),
                 ([predicate('x', 'eq', 1)], 'decision.affirmations must be an object')]
        for affirm, message in cases:
            with self.subTest(affirm=affirm):
                proc = self.advise(context(DL_GOAL, affirm), 'heldout_error', expect=1)
                self.assertIn('[RDS-REJECT]', proc.stderr)
                self.assertIn(message, proc.stderr)
                self.assertNotIn('Traceback', proc.stderr)
        ctx = context(DL_GOAL, DL_AFFIRM)
        del ctx['decision']['goal_conditions']
        proc = self.advise(ctx, 'heldout_error', expect=1)
        self.assertIn('decision.affirmations requires decision.goal_conditions', proc.stderr)

    def test_existing_move_keeps_precedence_over_the_affirmative_move(self):
        facts = {'heldout_error': {'value': 0.05, 'source': 'synthetic-notes.txt'}}
        ctx = context(DL_GOAL, DL_AFFIRM, facts)
        search = {'candidates': [], 'loop_review': {'flags': [{'kind': 'LOOP_HISTORY_REVIEW_ERROR'}]}}
        review = review_selection(search, ctx, _dependency=lambda: None)
        self.assertIn('TRIPLE_AFFIRMATIVE_OPEN', [f['kind'] for f in review['flags']])
        self.assertEqual(review['next_move']['kind'], 'RESOLVE_PREMISE')
        self.assertEqual(review['next_move']['reason'], 'Recorded history integrity is unresolved; inspect the existing loop review.')
        self.assertNotIn('affirmatives', review['next_move'])

    def test_repeated_review_is_identical_and_brief_shows_the_open_affirmative(self):
        facts = {'heldout_error': {'value': 0.05, 'source': 'synthetic-notes.txt'}}
        ctx = context(DL_GOAL, DL_AFFIRM, facts)
        self.assertEqual(self.advise(ctx, 'heldout_error'), self.advise(ctx, 'heldout_error'))
        brief = self.advise(ctx, 'heldout_error', None, None, '--brief')
        self.assertEqual(brief['goal_input_status'], 'TRUE')
        self.assertEqual(brief['next_move'], 'RESOLVE_PREMISE')
        self.assertEqual(brief['flags'][0], 'TRIPLE_AFFIRMATIVE_OPEN')
        # The digest grows only by the bounded planning projection; the affirmations
        # are open obligations, so they stay visible instead of an UNSCOPED scope.
        self.assertLess(len(json.dumps(brief)), 1280)


if __name__ == '__main__':
    unittest.main()
