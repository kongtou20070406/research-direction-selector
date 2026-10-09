"""Synthetic native-format exports; no provider calls or scientific efficacy trial."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_trajectory import report, SCHEMA
from rds_verify_types import digest


def response(rid='resp-one'):
    return {'id': rid, 'model': 'public-fixture-model', 'status': 'completed',
            'usage': {'input_tokens': 100, 'output_tokens': 30, 'total_tokens': 130,
                      'input_tokens_details': {'cached_tokens': 60, 'cache_write_tokens': 0},
                      'output_tokens_details': {'reasoning_tokens': 20}},
            'metadata': {'host': 'synthetic-host-v1', 'effort': 'high', 'tokenizer': 'provider-reported', 'latency': 2}}


class TrajectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.manifest = {'schema': SCHEMA, 'goal': {'goal_id': 'original-goal', 'evaluator_sha256': 'e' * 64,
            'protocol_sha256': 'f' * 64, 'started_at': '2026-10-08T00:00:00Z'},
            'sources': [], 'providers': [], 'tools': [], 'outcomes': [], 'receipts': []}

    def source(self, name, value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        (self.root / name).write_bytes(raw)
        self.manifest['sources'].append({'id': name, 'path': name, 'sha256': digest(raw)})
        return name

    def provider(self, value=None, name='provider.json', fmt='openai-responses', pointer=''):
        source = self.source(name, value if value is not None else response())
        spec = {'source': source, 'pointer': pointer, 'namespace': 'account-one', 'phase': 'planning', 'format': fmt,
                'fields': {'host': '/metadata/host', 'effort': '/metadata/effort', 'tokenizer': '/metadata/tokenizer',
                           'latency_seconds': '/metadata/latency'}}
        self.manifest['providers'].append(spec)
        return spec

    def tool(self, call_id='one', trip='round-one', origin='llm', **changes):
        value = {'call_id': call_id, 'round_trip_id': trip, 'origin': origin, 'status': 'FAILED',
                 'schema_tokens': 6, 'returned_tokens': 10, 'tokenizer': 'fixture-tokenizer',
                 'format_error': True, 'retry': False, 'regenerated_code': False, **changes}
        name = self.source('tool-' + str(len(self.manifest['tools'])) + '.json', value)
        spec = {'source': name, 'pointer': '', 'namespace': 'session-one', 'phase': 'rework',
                'fields': {key: '/' + key for key in value}}
        self.manifest['tools'].append(spec)
        return spec

    def outcome(self, **changes):
        value = {**self.manifest['goal'], 'artifact_sha256': 'a' * 64, 'status': 'PASS',
                 'observed_at': '2026-10-08T00:00:12Z', 'score': {'metric': 'accuracy', 'value': .9},
                 'delivered': True, 'incorrect_claims': 0, 'human_rescues': 0, **changes}
        value.pop('started_at')
        name = self.source('outcome-' + str(len(self.manifest['outcomes'])) + '.json', value)
        spec = {'source': name, 'pointer': '', 'fields': {key: '/' + key for key in value}}
        self.manifest['outcomes'].append(spec)
        return spec

    def write(self):
        path = self.root / 'manifest.json'
        path.write_text(json.dumps(self.manifest, ensure_ascii=False), encoding='utf-8-sig')
        return path

    def run_report(self):
        return report(self.root, self.write())

    def test_native_usage_subsets_never_count_twice(self):
        self.provider()
        result = self.run_report()
        usage = result['provider_usage']
        self.assertEqual(usage['total_tokens']['value'], 130)
        self.assertEqual(usage['cached_input_tokens']['value'], 60)
        self.assertEqual(usage['reasoning_tokens']['value'], 20)
        self.assertEqual(result['providers'][0]['host'], 'synthetic-host-v1')
        self.assertEqual(result['providers'][0]['source']['pointer'], '')
        self.assertEqual(result['coverage']['complete_research_cost'], 'UNKNOWN')

    def test_anthropic_cache_is_additive_only_within_input(self):
        value = {'id': 'msg-fixture', 'model': 'fixture', 'stop_reason': 'end_turn',
                 'usage': {'input_tokens': 10, 'output_tokens': 7, 'cache_read_input_tokens': 50, 'cache_creation_input_tokens': 20}}
        self.provider(value, fmt='anthropic-message')
        result = self.run_report()
        self.assertEqual(result['provider_usage']['input_tokens']['value'], 80)
        self.assertEqual(result['provider_usage']['total_tokens']['value'], 87)
        self.assertIsNone(result['provider_usage']['reasoning_tokens']['value'])

    def test_chat_usage_and_nested_original_locator(self):
        value = {'id': 'chat-one', 'model': 'fixture', 'usage': {'prompt_tokens': 9, 'completion_tokens': 4,
                 'total_tokens': 13, 'prompt_tokens_details': {'cached_tokens': 3},
                 'completion_tokens_details': {'reasoning_tokens': 2}}}
        self.provider({'response': value}, fmt='openai-chat', pointer='/response')
        result = self.run_report()
        self.assertEqual(result['provider_usage']['total_tokens']['value'], 13)
        self.assertEqual(result['providers'][0]['source']['pointer'], '/response')

    def test_repeated_paid_attempt_and_concurrent_exports_count_once(self):
        self.provider()
        self.provider(name='recovered.json')
        self.write()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: report(self.root, 'manifest.json'), range(4)))
        self.assertTrue(all(r == results[0] for r in results))
        self.assertEqual(results[0]['provider_attempts_observed'], 1)
        self.assertEqual(results[0]['duplicate_records']['providers'], 1)
        self.assertEqual(len(results[0]['providers'][0]['sources']), 2)
        self.assertFalse((self.root / '.rds').exists())

    def test_conflicting_duplicate_cannot_keep_favourable_first(self):
        self.provider()
        other = response()
        other['usage']['input_tokens'] = 101
        other['usage']['total_tokens'] = 131
        self.provider(other, name='conflict.json')
        self.provider(response('independent'), name='independent.json')
        result = self.run_report()
        self.assertEqual(result['status'], 'CONFLICT')
        self.assertEqual(result['provider_attempts_observed'], 1)
        self.assertIsNone(result['provider_usage']['total_tokens']['value'])
        self.assertEqual(result['provider_usage']['total_tokens']['observed_subtotal'], 130)
        self.assertIsNone(result['phases']['planning']['tokens']['total_tokens']['value'])

    def test_phase_conflict_and_different_account_identity(self):
        self.provider()
        other = self.provider(name='copy.json')
        other['phase'] = 'recovery'
        self.assertEqual(self.run_report()['status'], 'CONFLICT')
        other['namespace'] = 'different-account'
        self.assertEqual(self.run_report()['provider_attempts_observed'], 2)

    def test_failed_and_timeout_attempts_with_missing_usage_are_not_zero(self):
        self.provider()
        self.provider({'id': 'failed-request', 'model': 'fixture', 'status': 'failed', 'usage': None}, name='failed.json')
        self.provider({'id': 'timeout-request', 'status': 'timed_out'}, name='timeout.json')
        result = self.run_report()
        self.assertEqual(result['provider_attempts_observed'], 3)
        self.assertIsNone(result['provider_usage']['total_tokens']['value'])
        self.assertEqual(result['provider_usage']['total_tokens']['observed_subtotal'], 130)
        self.assertEqual(result['provider_usage']['total_tokens']['unknown_records'], 2)

    def test_missing_attempt_id_remains_unreconciled(self):
        self.provider({'error': {'code': 'timeout'}})
        result = self.run_report()
        self.assertEqual(result['provider_attempts_observed'], 0)
        self.assertIn('response id missing', result['errors'][0]['reason'])

    def test_invalid_usage_cannot_be_promoted(self):
        for field, bad in [('input_tokens', -1), ('input_tokens', True), ('input_tokens', 2**80),
                           ('total_tokens', 1), ('output_tokens', 1)]:
            with self.subTest(field=field, bad=bad):
                value = response()
                value['usage'][field] = bad
                self.manifest['sources'], self.manifest['providers'] = [], []
                self.provider(value)
                result = self.run_report()
                self.assertIsNone(result['provider_usage']['total_tokens']['value'])
                self.assertTrue(result['providers'][0]['usage_problems'])

    def test_batches_are_one_round_trip_and_internal_steps_are_separate(self):
        self.tool('one')
        self.tool('two')
        self.tool('three', origin='internal')
        result = self.run_report()['tool_burden']
        self.assertEqual(result['llm_tool_calls_observed'], 2)
        self.assertEqual(result['llm_round_trips'], 1)
        self.assertEqual(result['internal_steps_observed'], 1)
        self.assertEqual(result['format_error']['value'], 3)

    def test_tool_material_requires_tokenizer_and_unknown_round_trip_survives(self):
        self.tool(tokenizer=None, trip=None)
        result = self.run_report()['tool_burden']
        self.assertIsNone(result['schema_tokens']['value'])
        self.assertIsNone(result['llm_round_trips'])
        self.assertEqual(result['round_trips_observed_subtotal'], 0)

    def test_tool_duplicates_and_conflicts_preserve_other_cost(self):
        self.tool()
        self.tool()
        self.assertEqual(self.run_report()['tool_burden']['llm_round_trips'], 1)
        self.tool(returned_tokens=99)
        self.tool('independent')
        result = self.run_report()
        self.assertEqual(result['status'], 'CONFLICT')
        self.assertIsNone(result['tool_burden']['returned_tokens']['value'])
        self.assertIsNone(result['tool_burden']['llm_round_trips'])

    def test_outcome_deduplicates_artifact_not_claim_count(self):
        self.provider()
        self.outcome()
        self.outcome()
        self.outcome(artifact_sha256='b' * 64, status='FAIL')
        result = self.run_report()
        summary = result['outcome_summary']
        self.assertEqual(summary['evaluator_reported_pass_artifacts'], 1)
        self.assertEqual(summary['evaluator_reported_fail_artifacts'], 1)
        self.assertEqual(summary['seconds_to_first_reported_pass'], 12)
        self.assertIsNone(summary['independently_verified_results'])
        self.assertEqual(summary['supplied_tokens_per_reported_pass'], 130)
        self.assertEqual(result['outcomes'][0]['score']['value'], .9)

    def test_wrong_goal_or_evaluator_never_counts_as_result(self):
        for key in ('goal_id', 'evaluator_sha256', 'protocol_sha256'):
            self.outcome(**{key: 'wrong'})
        result = self.run_report()
        self.assertEqual(result['outcome_summary']['evaluator_reported_pass_artifacts'], 0)
        self.assertEqual(len(result['errors']), 3)

    def test_mixed_case_artifact_hash_counts_one_reported_pass(self):
        self.provider()
        self.outcome(artifact_sha256='a' * 64)
        self.outcome(artifact_sha256='A' * 64)
        original_sources = {s['path']: (self.root / s['path']).read_bytes() for s in self.manifest['sources']}
        result = self.run_report()
        self.assertEqual(result['outcome_summary']['evaluator_reported_pass_artifacts'], 1)
        self.assertEqual(result['duplicate_records']['outcomes'], 1)
        self.assertEqual(result['conflicts'], [])
        self.assertEqual(result['outcome_summary']['supplied_tokens_per_reported_pass'], 130)
        self.assertEqual(result['outcomes'][0]['artifact_sha256'], 'a' * 64)
        self.assertEqual({p: (self.root / p).read_bytes() for p in original_sources}, original_sources)

    def test_mixed_case_hash_conflict_excludes_favourable_outcome(self):
        self.provider()
        self.outcome(artifact_sha256='a' * 64, status='PASS')
        self.outcome(artifact_sha256='A' * 64, status='FAIL')
        result = self.run_report()
        self.assertEqual(result['status'], 'CONFLICT')
        self.assertEqual(len(result['conflicts']), 1)
        self.assertEqual(result['outcomes'], [])
        self.assertEqual(result['outcome_summary']['evaluator_reported_pass_artifacts'], 0)
        self.assertIsNone(result['outcome_summary']['supplied_tokens_per_reported_pass'])

    def test_sha_case_normalization_preserves_source_and_ordinary_goal_identity(self):
        self.manifest['goal'].update(goal_id='Goal_CASE', evaluator_sha256='E' * 64, protocol_sha256='F' * 64)
        self.outcome(evaluator_sha256='e' * 64, protocol_sha256='f' * 64, artifact_sha256='A' * 64)
        self.outcome(evaluator_sha256='e' * 64, protocol_sha256='f' * 64, artifact_sha256='b' * 64, status='FAIL')
        for source in self.manifest['sources']:
            source['sha256'] = source['sha256'].upper()
        result = self.run_report()
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['goal']['goal_id'], 'Goal_CASE')
        self.assertEqual(result['outcome_summary']['evaluator_reported_pass_artifacts'], 1)
        self.assertEqual(result['outcome_summary']['evaluator_reported_fail_artifacts'], 1)
        self.assertEqual(len(result['outcomes']), 2, 'different digest identities remain separate')
        self.assertEqual(result['manifest_sha256'], digest(self.write().read_bytes()))
        self.assertTrue(all(s['status'] == 'READ' for s in result['source_inventory']))

    def test_contradictory_outcomes_and_invalid_times(self):
        self.outcome()
        self.outcome(status='FAIL')
        result = self.run_report()
        self.assertEqual(result['status'], 'CONFLICT')
        self.assertEqual(result['outcome_summary']['evaluator_reported_pass_artifacts'], 0)
        self.manifest['outcomes'] = self.manifest['outcomes'][:1]
        self.manifest['goal']['started_at'] = '2026-10-09T00:00:00Z'
        self.assertIsNone(self.run_report()['outcome_summary']['seconds_to_first_reported_pass'])

    def test_existing_receipt_cost_accounting_and_duplicate_sources(self):
        from test_rds_costs import receipt
        value = receipt(status='FAILED')
        name = self.source('receipt.json', value)
        self.manifest['receipts'] = [{'source': name, 'pointer': ''}] * 2
        result = self.run_report()
        self.assertEqual(len(result['receipt_costs']['attempts']), 1)
        self.assertEqual(result['receipt_costs']['totals'][0]['value'], 2)
        self.assertEqual(len(result['receipt_sources']), 2)

    def test_conflicting_receipts_surface_in_overall_status(self):
        from test_rds_costs import receipt
        for index, status in enumerate(('FAILED', 'SUCCEEDED')):
            name = self.source('receipt-' + str(index) + '.json', receipt(status=status))
            self.manifest['receipts'].append({'source': name, 'pointer': ''})
        result = self.run_report()
        self.assertEqual(result['status'], 'CONFLICT')
        self.assertEqual(result['receipt_costs']['attempts'], [])

    def test_unknown_outcome_prevents_zero_error_claim_and_ratio(self):
        self.provider()
        self.outcome()
        self.outcome(goal_id='wrong')
        result = self.run_report()['outcome_summary']
        self.assertIsNone(result['incorrect_claims']['value'])
        self.assertIsNone(result['supplied_tokens_per_reported_pass'])

    def test_stale_or_missing_original_never_yields_complete_totals(self):
        self.provider()
        (self.root / 'provider.json').write_text('{}')
        result = self.run_report()
        self.assertIn('hash mismatch', result['errors'][0]['reason'])
        self.assertIsNone(result['provider_usage']['total_tokens']['value'])
        (self.root / 'provider.json').unlink()
        self.assertEqual(self.run_report()['source_inventory'][0]['status'], 'UNKNOWN')

    def test_source_escape_and_duplicate_keys_are_bounded(self):
        self.provider()
        self.manifest['sources'][0]['path'] = '../outside.json'
        self.assertEqual(self.run_report()['source_inventory'][0]['status'], 'UNKNOWN')
        self.manifest['sources'][0]['path'] = 'provider.json'
        raw = b'{"id":"one","id":"two"}'
        (self.root / 'provider.json').write_bytes(raw)
        self.manifest['sources'][0]['sha256'] = digest(raw)
        self.assertIn('duplicate JSON key', self.run_report()['errors'][0]['reason'])

    def test_record_and_byte_bounds(self):
        self.provider()
        self.manifest['providers'] *= 1025
        with self.assertRaisesRegex(ValueError, 'record limit'):
            self.run_report()
        self.manifest['providers'] = self.manifest['providers'][:1]
        raw = b' ' * (2 * 1024 * 1024 + 1)
        (self.root / 'provider.json').write_bytes(raw)
        self.assertIn('2 MiB', self.run_report()['errors'][0]['reason'])

    def test_oversized_selected_score_is_visible_not_copied_into_each_row(self):
        self.outcome(score={'unbounded_commentary': 'x' * 32768})
        result = self.run_report()
        self.assertEqual(result['outcomes'], [])
        self.assertIn('32 KiB', result['errors'][0]['reason'])

    def test_unknown_format_and_malformed_pointer_preserve_problem(self):
        spec = self.provider()
        spec['format'] = 'invented-provider'
        self.assertIn('unsupported provider format', self.run_report()['errors'][0]['reason'])
        spec['format'] = 'openai-responses'
        spec['pointer'] = '/bad~2'
        self.assertIn('pointer escape', self.run_report()['errors'][0]['reason'])

    def test_real_cli_unicode_root_does_not_initialize_research_ledger(self):
        self.root = self.root / '原始轨迹 with spaces'
        self.root.mkdir()
        self.provider()
        self.outcome()
        self.write()
        env = dict(os.environ, PYTHONIOENCODING='utf-8', RDS_USAGE_DB=str(self.root / 'usage.sqlite3'))
        completed = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.root),
                                    'project', 'trajectory', '--manifest', 'manifest.json'],
                                   capture_output=True, encoding='utf-8', env=env, timeout=30)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result['provider_usage']['total_tokens']['value'], 130)
        self.assertFalse((self.root / '.rds/project.sqlite3').exists())
        self.assertFalse((self.root / '.rds/state.sqlite3').exists())
        self.assertEqual(result['outcome_summary']['independently_verified_results'], None)


if __name__ == '__main__':
    unittest.main()
