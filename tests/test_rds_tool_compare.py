"""Real native fixed-precision comparison, recovery and negative evidence."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import rds_math as assets
import rds_tools as tools
import rds_tool_compare as comparisons
from rds_project import ProjectStore, file_sha

EXAMPLE = SCRIPTS.parent / 'examples' / 'tool-comparison'
CONTEXT = {'schema': 'rds-local-runtime-v1', 'host_sha256': 'a' * 64, 'system': 'fixture',
           'release': '1', 'machine': 'fixture', 'processor': 'fixture', 'logical_cpus': 1,
           'python': 'fixture', 'implementation': 'cpython', 'executable_sha256': 'b' * 64}


class ToolComparisonTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        tools.extract(self.root, EXAMPLE / 'newton.py', 'scaled_root', 'before')
        tools.extract(self.root, EXAMPLE / 'isqrt.py', 'scaled_root', 'after')
        self.cases = self.root / 'cases.json'
        self.cases.write_text(json.dumps([{'args': [n], 'kwargs': {'precision': 12},
                                         'expected': -1 if n == -1 else 0} for n in (-1, 0, 1)]), encoding='utf-8')

    def compare(self, **kwargs):
        return comparisons.compare(self.root, 'before', 'after', self.cases, 'precision', 12, **kwargs)

    def parent(self, budget, **policy):
        root = self.root / 'parent'
        root.mkdir()
        source = root / 'fixture.py'
        source.write_text('# Parent budget fixture; no execution.\n', encoding='utf-8')
        bindings = [{'path': 'fixture.py', 'sha256': file_sha(source), 'role': role}
                    for role in ('code', 'config', 'data', 'evaluator', 'protocol')]
        ProjectStore(root).initialize({'schema': 1, 'bindings': bindings, 'allowed_commands': [[sys.executable, '-B', 'fixture.py']],
                                      'output_roots': ['outputs'], 'budget': {'wall_seconds': budget}, **policy})
        return root

    def charges(self, root):
        with closing(sqlite3.connect(root / '.rds' / 'project.sqlite3')) as db:
            return db.execute("SELECT COUNT(*) FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE'").fetchone()[0]

    def test_real_native_comparison_is_prospective_and_reuses_both_attempts(self):
        result = self.compare()
        self.assertEqual(result['correctness'], 'PASS')
        self.assertEqual(result['samples_per_tool'], 1)
        self.assertFalse(result['automatic_adoption'])
        self.assertFalse(result['research_policy_gain_measured'])
        plan = assets.get(self.root, result['plan_id'])
        for arm in ('baseline', 'candidate'):
            observation = result[arm]
            self.assertGreater(observation['wall_seconds'], 0)
            self.assertGreaterEqual(observation['started_at'], plan['data']['declared_at'])
            validation = assets.get(self.root, observation['validation_id'])
            frozen = self.root / validation['data']['job_root'] / 'cases.json'
            self.assertEqual(frozen.read_bytes(), self.cases.read_bytes())
        again = self.compare()
        self.assertEqual(again['id'], result['id'])
        self.assertEqual(again['execution_started'], {'before': False, 'after': False})
        self.assertEqual(again['baseline'], result['baseline'])
        self.assertEqual(tools.register(self.root, 'after', result['candidate']['validation_id'])['assurance'], 'LOCAL_CASES_ONLY')
        with self.assertRaisesRegex(ValueError, 'bound passing'):
            tools.register(self.root, 'after', result['id'])

    def test_wrong_fixed_precision_is_rejected_before_any_validation(self):
        for value in (11, True, None, '12'):
            with self.subTest(value=value):
                self.cases.write_text(json.dumps([{'args': [0], 'kwargs': {'precision': value}, 'expected': 0}]), encoding='utf-8')
                with self.assertRaisesRegex(ValueError, 'same fixed precision'):
                    self.compare()
        self.assertEqual([v for v in assets.records(self.root) if v['kind'] == 'tool-validation'], [])

    def test_invalid_declarations_do_not_dispatch(self):
        for args in [('before', 'before', 'precision', 12, 10, 1.1),
                     ('before', 'after', 'precision', True, 10, 1.1),
                     ('before', 'after', 'precision', 12, 61, 1.1),
                     ('before', 'after', 'precision', 12, 10, 1)]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                comparisons.compare(self.root, args[0], args[1], self.cases, *args[2:])
        self.assertFalse(list((self.root / '.rds').glob('rsi/tool-checks/*')))

    def test_failed_candidate_retains_native_cost_and_has_no_speedup(self):
        bad = self.root / 'bad.py'
        bad.write_text('def scaled_root(offset, precision):\n    return 123\n', encoding='utf-8')
        tools.extract(self.root, bad, 'scaled_root', 'wrong')
        result = comparisons.compare(self.root, 'before', 'wrong', self.cases, 'precision', 12)
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertEqual(result['correctness'], 'FAIL')
        self.assertEqual(result['candidate']['run_status'], 'FAILED')
        self.assertGreater(result['candidate']['wall_seconds'], 0)
        self.assertIsNone(result['speedup_ratio'])

    def test_preexisting_standalone_validation_is_not_relabelled_prospective(self):
        old = tools.validate(self.root, 'after', self.cases)
        result = self.compare()
        self.assertNotEqual(old['id'], result['candidate']['validation_id'])
        self.assertTrue(result['execution_started']['after'])

    def test_timeout_retains_native_receipt_without_successful_performance(self):
        source = self.root / 'loop.py'
        source.write_text('def scaled_root(offset, precision):\n    while True:\n        pass\n', encoding='utf-8')
        tools.extract(self.root, source, 'scaled_root', 'timeout')
        result = comparisons.compare(self.root, 'before', 'timeout', self.cases, 'precision', 12, timeout=1)
        self.assertIn(result['candidate']['run_status'], ('TIMED_OUT', 'FAILED'))
        self.assertTrue(result['candidate']['timeout'])
        self.assertEqual(result['correctness'], 'UNKNOWN')
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertIsNone(result['speedup_ratio'])

    def test_runtime_change_has_unknown_performance_and_cannot_reuse_other_host(self):
        other = dict(CONTEXT, host_sha256='c' * 64)
        with patch.object(tools, '_validation_context', side_effect=[CONTEXT, CONTEXT, other]):
            result = self.compare()
        self.assertEqual(result['correctness'], 'PASS')
        self.assertEqual(result['status'], 'UNKNOWN')
        self.assertFalse(result['comparable_context'])
        self.assertIn('MISSING_OR_DIFFERENT_RUNTIME_CONTEXT', result['reasons'])
        with patch.object(tools, '_validation_context', return_value=other):
            changed = self.compare()
        self.assertNotEqual(changed['plan_id'], result['plan_id'])
        self.assertEqual(changed['execution_started'], {'before': True, 'after': True})

    def test_changed_actual_evaluator_or_output_cannot_be_compared_again(self):
        result = self.compare()
        validation = assets.get(self.root, result['candidate']['validation_id'])
        job = self.root / validation['data']['job_root']
        output = job / 'outputs' / 'result.json'
        original = output.read_bytes()
        output.write_text('{"status":"PASS"}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'artifact changed'):
            self.compare()
        output.write_bytes(original)
        # A separate real comparison checks evaluator tampering before dispatch.
        driver = job / 'driver.py'
        driver.write_text('raise SystemExit(0)\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'bindings changed|do not match'):
            self.compare()

    def test_both_validations_charge_parent_once_and_recovery_keeps_budget(self):
        parent = self.parent(5)
        first = self.compare(timeout=2, ledger=parent)
        self.assertEqual(self.charges(parent), 2)
        before = ProjectStore(parent).snapshot()['budget']
        again = self.compare(timeout=2, ledger=parent)
        self.assertEqual(again['id'], first['id'])
        self.assertEqual(again['execution_started'], {'before': False, 'after': False})
        self.assertEqual(self.charges(parent), 2)
        self.assertEqual(ProjectStore(parent).snapshot()['budget'], before)

    def test_insufficient_parent_budget_stops_second_launch_and_preserves_first(self):
        parent = self.parent(3)
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, 'Insufficient parent'):
                self.compare(timeout=2, ledger=parent)
        values = assets.records(self.root)
        self.assertEqual(sum(v['kind'] == 'tool-validation' for v in values), 1)
        self.assertEqual(sum(v['data'].get('type') == 'tool-comparison-plan' for v in values), 1)
        self.assertEqual(sum(v['data'].get('type') == 'tool-comparison-result' for v in values), 0)
        self.assertEqual(self.charges(parent), 1)

    def test_interruption_after_charge_before_saved_job_cannot_buy_replacement(self):
        parent = self.parent(5)
        with patch.object(tools, 'execute', side_effect=RuntimeError('interrupted before native job')) as launch:
            with self.assertRaisesRegex(RuntimeError, 'interrupted'):
                self.compare(timeout=2, ledger=parent)
            with self.assertRaisesRegex(ValueError, 'no retained native job'):
                self.compare(timeout=2, ledger=parent)
        self.assertEqual(launch.call_count, 1)
        self.assertEqual(self.charges(parent), 1)

    def test_retained_reserved_job_stays_unknown_without_new_attempt_or_immutable_validation(self):
        parent = self.parent(5)
        with patch.object(ProjectStore, 'execute', side_effect=RuntimeError('interrupted after reservation')):
            with self.assertRaisesRegex(RuntimeError, 'after reservation'):
                self.compare(timeout=2, ledger=parent)
        resumed = self.compare(timeout=2, ledger=parent)
        self.assertEqual(resumed['status'], 'UNKNOWN')
        self.assertIsNone(resumed['id'])
        self.assertEqual(resumed['baseline']['run_status'], 'UNRESOLVED')
        self.assertEqual(resumed['candidate']['run_status'], 'NOT_STARTED')
        self.assertEqual(resumed['execution_started'], {'before': False, 'after': False})
        self.assertEqual(self.charges(parent), 1)
        self.assertEqual(sum(v['kind'] == 'tool-validation' for v in assets.records(self.root)), 0)
        job = Path(resumed['baseline']['job_root'])
        state = ProjectStore(job).snapshot()
        self.assertEqual(state['runs'][0]['status'], 'RESERVED')
        self.assertEqual(state['receipts'], [])

    def test_execution_policy_cannot_be_bypassed_by_tool_budget_entry(self):
        parent = self.parent(5, execution_policy={'schema': 1, 'max_attempts': 2})
        with self.assertRaisesRegex(ValueError, 'ordinary wall-only'):
            self.compare(timeout=2, ledger=parent)
        self.assertEqual(self.charges(parent), 0)

    def test_assessment_preserves_missing_costs_context_order_and_slower_result(self):
        plan = {'data': {'request': {'measurement_context': CONTEXT, 'min_speedup': 1.1}, 'declared_at': 10}}
        base = {'measurement_context': CONTEXT, 'correctness': 'PASS', 'wall_seconds': 2, 'started_at': 11}
        fast = dict(base, wall_seconds=1)
        self.assertEqual(comparisons.assess(plan, base, fast)['status'], 'OBSERVED_SPEEDUP')
        self.assertEqual(comparisons.assess(plan, fast, base)['status'], 'NO_OBSERVED_SPEEDUP')
        for broken in (dict(fast, wall_seconds=None), dict(fast, wall_seconds=0), dict(fast, wall_seconds=True),
                       dict(fast, wall_seconds=float('nan')), dict(fast, measurement_context=None),
                       dict(fast, started_at=9), dict(fast, correctness='UNKNOWN')):
            with self.subTest(broken=broken):
                result = comparisons.assess(plan, base, broken)
                self.assertEqual(result['status'], 'UNKNOWN')
                self.assertIsNone(result['speedup_ratio'])

    def test_reserved_or_estimated_cost_is_not_a_measurement(self):
        for resource in ({'unit': 'seconds', 'unknown': True, 'charged_estimate': 5, 'measured': None},
                         {'unit': 'seconds', 'unknown': True, 'measured': 1},
                         {'unit': 'seconds', 'unknown': False, 'measured': True},
                         {'unit': 'other', 'unknown': False, 'measured': 1}):
            with self.subTest(resource=resource):
                self.assertIsNone(comparisons._wall({'resources': {'wall_seconds': resource}}))

    def test_real_cli_reaches_comparison_and_rejects_wrong_precision(self):
        env = dict(os.environ, RDS_USAGE_DB=str(self.root / 'usage.sqlite3'), PYTHONUTF8='1')
        argv = [sys.executable, '-B', str(SCRIPTS / 'rds_cli.py'), '--root', str(self.root), 'rsi', 'compare',
                '--baseline', 'before', '--candidate', 'after', '--cases', str(self.cases),
                '--precision-key', 'precision', '--precision', '12', '--json']
        good = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', env=env, timeout=30)
        self.assertIn(good.returncode, (0, 2), good.stderr)
        self.assertEqual(json.loads(good.stdout)['correctness'], 'PASS')
        argv[-2] = '13'
        bad = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8', env=env, timeout=30)
        self.assertEqual(bad.returncode, 1)
        self.assertIn('same fixed precision', bad.stderr)


if __name__ == '__main__':
    unittest.main()
