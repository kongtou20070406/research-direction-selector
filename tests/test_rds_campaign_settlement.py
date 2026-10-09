"""Binding waits for original detached writers without resetting their evidence."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_campaign as campaign
import rds_guard as guard
import rds_method_revision as revision
import rds_quick as quick
import rds_tool_compare as comparisons
import rds_tools as tools
from rds_cli import parser
from rds_project import ProjectStore, canonical, file_sha
import test_rds_campaign_binding as binding_fixture
import test_rds_method_revision as revision_fixture
import test_rds_tool_compare as comparison_fixture


class CampaignSettlementTests(unittest.TestCase):
    def setUp(self):
        self.f = binding_fixture.CampaignBindingTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def clone(self, name, budget=None):
        root = self.f.workspace / name
        root.mkdir()
        contract = deepcopy(self.f.helper.contract)
        if budget is not None:
            contract['budget'] = budget
        for entry in contract['bindings']:
            shutil.copyfile(self.f.helper.root / entry['path'], root / entry['path'])
        store = ProjectStore(root)
        store.initialize(contract)
        return store

    def rejected(self, message):
        before = self.f.store.snapshot()
        with self.assertRaisesRegex(ValueError, message):
            campaign.bind(self.f.store, self.f.workspace)
        self.assertFalse(self.f.marker.exists())
        after = self.f.store.snapshot()
        for key in ('budget', 'runs', 'receipts', 'contract_sha256'):
            self.assertEqual(after[key], before[key], key)

    def test_ordinary_empty_projects_remain_bindable(self):
        self.clone('ordinary-empty')
        campaign.bind(self.f.store, self.f.workspace)
        self.assertTrue(self.f.marker.is_file())

    def test_charged_initialized_retained_job_requires_real_terminal_receipt(self):
        parent = self.clone('wall-parent', {'wall_seconds': 20})
        child = self.clone('empty-child', {'wall_seconds': 20})
        quick._charge_ledger(parent.root, child.root, {'fixture': 'charged-before-register'}, 3)
        charged = parent.snapshot()['budget']
        self.assertEqual(child.snapshot()['runs'], [])
        self.rejected('retained.*run|Retained.*run')
        self.assertEqual(parent.snapshot()['budget'], charged)
        spec = self.f.helper.spec()
        spec['resource_estimates'] = {'wall_seconds': 2.2}
        child.register(spec)
        receipt = child.execute('r1')
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        original = child.snapshot()
        campaign.bind(self.f.store, self.f.workspace)
        after = child.snapshot()
        self.assertEqual(after['receipts'], original['receipts'])
        self.assertEqual(after['budget'], original['budget'])

    def guarded_child(self):
        source = self.f.workspace / 'guard-source'
        source.mkdir()
        comparison = {'question_id': 'fixture', 'goal_revision': 'fixed', 'scope': {'domain': 'fixture'},
                      'metric_definition': 'score', 'unit': 'ratio', 'cohort': 'fixed', 'protocol': 'fixed'}
        baseline = source / 'baseline.json'
        baseline.write_text(json.dumps({'status': 'PASS', 'comparison': comparison, 'score': '1'}), encoding='utf-8')
        policy = {'schema': 1, 'wall_seconds': 1, 'comparison': comparison, 'metrics': [
            {'name': 'score', 'direction': 'max', 'pointer': '/score', 'candidate': 'outputs/candidate.json',
             'baseline': {'path': 'baseline.json', 'sha256': file_sha(baseline)}}]}
        (source / 'guard.json').write_text(json.dumps(policy), encoding='utf-8')
        (source / 'probe.py').write_text('from pathlib import Path\n'
            'Path("outputs/candidate.json").write_bytes(Path("baseline.json").read_bytes())\n', encoding='utf-8')
        args = parser().parse_args(['--root', str(source), 'exec', '--name', 'guard-job', '--timeout', '5',
            '--guard', 'guard.json', '--output', 'outputs/candidate.json', '--', sys.executable, '-B', 'probe.py'])
        with patch.object(guard, 'evaluate', side_effect=RuntimeError('interrupted original guard tail')):
            with self.assertRaisesRegex(RuntimeError, 'original guard tail'):
                quick.execute(args)
        child = ProjectStore(source / '.rds/exec/guard-job')
        self.assertEqual(child.snapshot()['runs'][0]['status'], 'COMPLETED')
        self.assertEqual(child.snapshot()['budget']['wall_seconds']['reserved'], 0)
        return child

    def finish_guard(self, child, report=None):
        if report is None:
            report = guard.evaluate(child.root / 'guard.json', child.root)
        ref = quick.cas_json(child.root, report)
        with child._db() as db:
            db.execute('INSERT INTO events(body) VALUES (?)',
                       (canonical({'kind': 'QUICK_EXEC_REGRESSION_REVIEW', 'report': ref}),))
        return ref

    def test_terminal_quick_receipt_does_not_settle_unfinished_guard_tail(self):
        child = self.guarded_child()
        original = child.snapshot()
        self.rejected('guard review|Guard review')
        self.finish_guard(child)
        campaign.bind(self.f.store, self.f.workspace)
        after = child.snapshot()
        self.assertEqual(after['runs'], original['runs'])
        self.assertEqual(after['receipts'], original['receipts'])
        self.assertEqual(after['budget'], original['budget'])

    def test_guard_unknown_error_is_settled_but_changed_original_report_is_not(self):
        child = self.guarded_child()
        ref = self.finish_guard(child, {'status': 'UNKNOWN', 'promotion_eligible': False,
                                       'reason': 'Retained original evaluator error', 'scientific_support': 'UNKNOWN'})
        report_path = Path(ref['path'])
        original = report_path.read_bytes()
        report_path.write_bytes(b'{}')
        self.rejected('Guard report|guard report')
        report_path.write_bytes(original)
        campaign.bind(self.f.store, self.f.workspace)

    def test_sibling_pending_method_revision_must_resume_before_binding(self):
        helper = revision_fixture.MethodRevisionTests('runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        root = self.f.workspace / 'revision-sibling'
        shutil.copytree(helper.root, root)
        helper.root, helper.store = root, ProjectStore(root)
        proposal = helper.proposal()
        replace, calls = revision._replace, []
        def interrupted(path, data):
            calls.append(path)
            if len(calls) == 2:
                raise OSError('interrupted original method adoption')
            replace(path, data)
        with patch.object(revision, '_replace', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'original method adoption'):
                revision.apply(helper.store, proposal)
        original = helper.store.snapshot()
        self.assertIsNotNone(original['method_revision_pending'])
        self.rejected('method revision|Method revision')
        self.assertEqual(helper.store.snapshot()['method_revision_pending'], original['method_revision_pending'])
        self.assertEqual(revision.apply(helper.store, proposal)['status'], 'ADOPTED')
        campaign.bind(self.f.store, self.f.workspace)
        self.assertEqual(helper.store.snapshot()['budget'], original['budget'])

    def test_native_comparison_plan_requires_original_immutable_result(self):
        root = self.f.workspace / 'comparison-sibling'
        root.mkdir()
        tools.extract(root, comparison_fixture.EXAMPLE / 'newton.py', 'scaled_root', 'before')
        tools.extract(root, comparison_fixture.EXAMPLE / 'isqrt.py', 'scaled_root', 'after')
        cases = root / 'cases.json'
        cases.write_text(json.dumps([{'args': [n], 'kwargs': {'precision': 12},
                                     'expected': -1 if n == -1 else 0} for n in (-1, 0, 1)]), encoding='utf-8')
        with patch.object(tools, 'validate', side_effect=RuntimeError('interrupted original comparison')):
            with self.assertRaisesRegex(RuntimeError, 'original comparison'):
                comparisons.compare(root, 'before', 'after', cases, 'precision', 12)
        self.rejected('comparison result|Comparison result')
        result = comparisons.compare(root, 'before', 'after', cases, 'precision', 12)
        self.assertIsNotNone(result['id'])
        self.assertEqual(result['correctness'], 'PASS')
        campaign.bind(self.f.store, self.f.workspace)
        self.assertEqual(comparisons.get(root, result['id'])['data']['baseline'], result['baseline'])

    def test_canonical_pending_revision_can_bind_then_resume_original_proposal(self):
        helper = revision_fixture.MethodRevisionTests('runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.assertEqual(helper.execute_route('r1')['run_status'], 'FAILED')
        proposal = helper.proposal()
        replace, calls = revision._replace, []
        def interrupted(path, data):
            calls.append(path)
            if len(calls) == 2:
                raise OSError('interrupted canonical method adoption')
            replace(path, data)
        with patch.object(revision, '_replace', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'canonical method adoption'):
                revision.apply(helper.store, proposal)
        original = helper.store.snapshot()
        self.assertIsNotNone(original['method_revision_pending'])
        campaign.bind(helper.store, helper.root)
        self.assertEqual(revision.apply(helper.store, proposal)['status'], 'ADOPTED')
        after = helper.store.snapshot()
        self.assertIsNone(after['method_revision_pending'])
        for key in ('budget', 'runs', 'receipts'):
            self.assertEqual(after[key], original[key], key)


if __name__ == '__main__':
    unittest.main()
