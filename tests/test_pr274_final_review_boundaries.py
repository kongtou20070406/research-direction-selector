"""Retained logical identity and the final measured benchmark operation."""
import ast
import importlib.util
from pathlib import Path
import statistics
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_project_lifecycle as lifecycle
import rds_campaign as campaign
from rds_project import digest
import test_rds_project_lifecycle as fixtures
import test_rds_tool_compare as comparison_fixtures


class RetainedPointerTests(unittest.TestCase):
    def setUp(self):
        self.f = fixtures.ProjectLifecycleTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.init_quick()

    def test_external_resolved_pointer_cannot_replace_original_tool_job(self):
        with tempfile.TemporaryDirectory() as temporary:
            external = self.f.retained_child(Path(temporary).resolve() / 'unrelated', finish=True)
            logical = self.f.root / '.rds/rsi/tool-checks' / ('a' * 32)
            self.f.retained_pointer(self.f.store, logical,
                                    request_sha=digest({'tool_validation': logical.name}))
            before, other = self.f.originals(), external.snapshot()
            resolve = Path.resolve
            # Real completed external ledger, portable simulation of path resolution.
            with patch.object(Path, 'resolve', autospec=True,
                              side_effect=lambda path, *a, **k: external.root if path == logical else resolve(path, *a, **k)):
                with self.assertRaisesRegex(ValueError, 'Retained.*escapes'):
                    lifecycle.enable_advisor(self.f.store, self.f.policy)
            self.assertEqual(self.f.originals(), before)
            self.assertEqual(external.snapshot(), other)
            self.assertEqual(self.f.activations(), [])

    def test_in_root_alias_keeps_original_legacy_token_and_child_shape(self):
        unrelated = self.f.retained_child(self.f.root / 'renamed-finished', finish=True)
        logical = self.f.root / '.rds/rsi/tool-checks' / ('a' * 32)
        self.f.retained_pointer(self.f.store, logical,
                                request_sha=digest({'tool_validation': logical.name}))
        before = self.f.originals()
        resolve = Path.resolve
        with patch.object(Path, 'resolve', autospec=True,
                          side_effect=lambda path, *a, **k: unrelated.root if path == logical else resolve(path, *a, **k)):
            with self.assertRaisesRegex(ValueError, 'Retained child ledger'):
                lifecycle.enable_advisor(self.f.store, self.f.policy)
        self.assertEqual(self.f.originals(), before)
        self.assertEqual(self.f.activations(), [])

    def test_native_external_symlink_refuses_preview(self):
        with tempfile.TemporaryDirectory() as temporary:
            external = self.f.retained_child(Path(temporary).resolve() / 'unrelated', finish=True)
            logical = self.f.root / '.rds/rsi/tool-checks' / ('a' * 32)
            logical.parent.mkdir(parents=True)
            try:
                logical.symlink_to(external.root, target_is_directory=True)
            except (OSError, NotImplementedError) as error:
                self.skipTest('Directory symlink privilege unavailable: ' + str(error))
            self.f.retained_pointer(self.f.store, logical,
                                    request_sha=digest({'tool_validation': logical.name}))
            before, other = self.f.originals(), external.snapshot()
            with self.assertRaisesRegex(ValueError, 'Retained.*escapes'):
                lifecycle.enable_advisor(self.f.store, self.f.policy)
            self.assertEqual(self.f.originals(), before)
            self.assertEqual(external.snapshot(), other)
            self.assertEqual(self.f.activations(), [])

    def test_public_campaign_keeps_original_legacy_child_shape(self):
        unrelated = self.f.retained_child(self.f.root / 'renamed-finished', finish=True)
        logical = self.f.root / '.rds/rsi/tool-checks' / ('a' * 32)
        self.f.retained_pointer(self.f.store, logical,
                                request_sha=digest({'tool_validation': logical.name}))
        before, other = self.f.originals(), unrelated.snapshot()
        resolve = Path.resolve
        with patch.object(Path, 'resolve', autospec=True,
                          side_effect=lambda path, *a, **k: unrelated.root if path == logical else resolve(path, *a, **k)):
            with self.assertRaisesRegex(ValueError, 'Original campaign project ledger is missing'):
                campaign.bind(self.f.store, self.f.root)
        self.assertFalse((self.f.root / campaign.MARKER).exists())
        self.assertEqual(self.f.originals(), before)
        self.assertEqual(unrelated.snapshot(), other)


class CommonBudgetLedgerTests(unittest.TestCase):
    def test_actual_distinct_tool_source_keeps_charged_project_advisor_preview(self):
        f = fixtures.ProjectLifecycleTests('runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.recipe['budget'] = {'wall_seconds': 30}
        for route in f.recipe['routes']:
            route['run']['resource_estimates'] = {'wall_seconds': 5}
        f.init_quick()
        tool = comparison_fixtures.ToolComparisonTests('runTest')
        tool.setUp()
        self.addCleanup(tool.doCleanups)
        result = tool.compare(timeout=2, ledger=f.root)
        self.assertEqual(result['correctness'], 'PASS')
        self.assertEqual(tool.charges(f.root), 2)
        before = f.originals()
        preview = lifecycle.enable_advisor(f.store, f.policy)
        self.assertEqual(preview['status'], 'ADVISOR_ACTIVATION_PREVIEW')
        self.assertEqual(f.originals(), before)
        self.assertEqual(f.activations(), [])
        self.assertEqual(tool.charges(f.root), 2)

    def test_actual_tool_source_and_distinct_budget_ledger_bind_common_workspace(self):
        f = comparison_fixtures.ToolComparisonTests('runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        parent = f.parent(5)
        result = f.compare(timeout=2, ledger=parent)
        self.assertEqual(result['correctness'], 'PASS')
        self.assertEqual(f.charges(parent), 2)
        for arm in ('baseline', 'candidate'):
            self.assertEqual(result[arm]['run_status'], 'SUCCEEDED')
        store = lifecycle.ProjectStore(parent)
        before = store.snapshot()
        bound = campaign.bind(store, f.root)
        self.assertEqual(store.snapshot()['budget'], before['budget'])
        self.assertEqual(store.snapshot()['receipts'], before['receipts'])
        self.assertEqual(f.charges(parent), 2)
        self.assertTrue((f.root / campaign.MARKER).is_file())
        self.assertEqual(campaign.bind(store, f.root), bound)


class BenchmarkDeadlineTests(unittest.TestCase):
    def timed_loop(self, finish):
        source = ROOT / 'benchmark/result-methods/run.py'
        spec = importlib.util.spec_from_file_location('pr274_result_benchmark', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tree = ast.parse(source.read_text(encoding='utf-8'))
        run = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run')
        deadline = next(node for node in run.body if isinstance(node, ast.FunctionDef) and node.name == 'check_deadline')
        loop = [node for node in run.body if isinstance(node, ast.For)][-1]
        # Execute the actual production timing loop without optional ML dependencies/training.
        code = compile(ast.Module(body=[deadline, loop], type_ignores=[]), str(source), 'exec')
        clock = SimpleNamespace(now=119.0, operations=0)
        def operation():
            clock.operations += 1
            if clock.operations == 30:
                clock.now = finish
        scope = {**vars(module), 'time': SimpleNamespace(perf_counter=lambda: clock.now),
                 'started': 0.0, 'workloads': {'last': operation}, 'timings': {}, 'statistics': statistics}
        # The module helper observes the same isolated clock as the real loop.
        with patch.object(module.time, 'perf_counter', lambda: clock.now):
            exec(code, scope)
        return scope['timings'], clock

    def test_final_timing_operation_over_cap_refuses_pass(self):
        with self.assertRaisesRegex(TimeoutError, '120-second'):
            self.timed_loop(121.0)

    def test_final_timing_operation_at_cap_refuses_pass(self):
        with self.assertRaisesRegex(TimeoutError, '120-second'):
            self.timed_loop(120.0)

    def test_within_cap_keeps_all_original_repeats(self):
        timings, clock = self.timed_loop(119.5)
        self.assertEqual(clock.operations, 30)
        self.assertEqual(timings['last']['repeats'], 30)

    def test_actual_summary_write_over_cap_refuses_success_and_marks_failure(self):
        source = ROOT / 'benchmark/result-methods/run.py'
        spec = importlib.util.spec_from_file_location('pr274_summary_benchmark', source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tree = ast.parse(source.read_text(encoding='utf-8'))
        run = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'run')
        deadline = next(node for node in run.body if isinstance(node, ast.FunctionDef) and node.name == 'check_deadline')
        index = next(i for i, node in enumerate(run.body) if isinstance(node, ast.Expr)
                     and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                     and node.value.func.id == 'dump' and isinstance(node.value.args[-1], ast.Name)
                     and node.value.args[-1].id == 'summary')
        tail = ast.FunctionDef(name='publish', args=ast.arguments(posonlyargs=[], args=[], kwonlyargs=[],
                                kw_defaults=[], defaults=[]), body=run.body[index:], decorator_list=[])
        code = compile(ast.fix_missing_locations(ast.Module(body=[deadline, tail], type_ignores=[])), str(source), 'exec')
        clock = SimpleNamespace(now=119.9)
        for failed_rewrite in (False, True):
            with self.subTest(failed_rewrite=failed_rewrite), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                clock.now = 119.9
                def slow_dump(path, value):
                    if failed_rewrite and value['status'] == 'FAIL':
                        raise OSError('injected failure settling summary')
                    result = module.dump(path, value)
                    clock.now = 120.1
                    return result
                scope = {**vars(module), 'root': root, 'started': 0.0, 'dump': slow_dump,
                         'time': SimpleNamespace(perf_counter=lambda: clock.now),
                         'summary': {'status': 'PASS', 'failures': [], 'total_wall_seconds': 119.9}}
                exec(code, scope)
                with self.assertRaisesRegex(TimeoutError, '120-second') as caught:
                    scope['publish']()
                import json
                saved = json.loads((root / 'summary.json').read_text(encoding='utf-8'))
                if failed_rewrite:
                    # No success was returned; the original timeout carries the failed artifact settlement.
                    self.assertIn('OSError', '\n'.join(caught.exception.__notes__))
                else:
                    self.assertEqual(saved['status'], 'FAIL')
                    self.assertGreaterEqual(saved['total_wall_seconds'], 120)
                    self.assertIn('120-second', saved['failures'][-1])


if __name__ == '__main__':
    unittest.main()
