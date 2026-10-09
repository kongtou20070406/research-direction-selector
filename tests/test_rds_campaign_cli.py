"""Campaign CLI continuity over real failed attempts, not scientific outcomes."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest import mock

import test_rds_project_lifecycle as lifecycle_fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import ProjectStore


class CampaignCLITests(unittest.TestCase):
    def setUp(self):
        self.fixture = lifecycle_fixture.ProjectLifecycleTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.workspace = self.fixture.root
        self.root = self.workspace / 'canonical'
        self.root.mkdir()
        for name in ('code.py', 'config.json', 'data.json', 'evaluator.json'):
            (self.workspace / name).rename(self.root / name)
        self.store = ProjectStore(self.root)
        self.fixture.root = self.fixture.fixture.root = self.root
        self.fixture.store = self.fixture.fixture.store = self.store
        self.env = {**os.environ, 'RDS_USAGE_LOG': '0', 'PYTHONIOENCODING': 'utf-8'}
        self.env.pop('RDS_CAMPAIGN_BINDING', None)
        self.fixture.env = self.env
        self.fixture.recipe['routes'][1]['preconditions'] = [
            {'fact': 'run.baseline.failed', 'op': 'eq', 'value': True}]
        source = lifecycle_fixture.fixture_module.SCRIPT + '\nif run_id == "baseline": raise SystemExit(7)\n'
        self.fixture.prepare_contract(owned=True, source=source)
        self.call('project', 'init', '--contract', str(self.fixture.contract_path))
        self.fixture.create('baseline')
        failed = self.call('project', 'execute', '--id', 'baseline', ok=False)
        self.assertNotEqual(failed.returncode, 0)
        self.failed = self.fixture.receipt(json.loads(failed.stdout), 'baseline')
        self.assertEqual(self.failed['run_status'], 'FAILED')
        self.call('checkpoint', 'save', '--id', 'failed-before-binding')
        self.originals = self.fixture.originals()
        self.before_events = self.fixture.rows('events')
        self.bound = json.loads(self.call('project', 'bind-workspace',
                                         '--workspace-root', str(self.workspace)).stdout)
        self.marker = self.workspace / '.rds-campaign.json'
        self.assertTrue(self.marker.is_file())
        self.env['RDS_CAMPAIGN_BINDING'] = str(self.marker.resolve())
        self.fixture.env = self.env
        self.assertEqual(self.fixture.originals(), self.originals)
        self.bound_events = self.fixture.rows('events')
        self.assertEqual(len(self.bound_events), len(self.before_events) + 1)
        self.assertEqual(self.fixture.starts(), ['baseline'])

    def call(self, *args, root=None, ok=True, env=None):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'),
                                 '--root', str(root or self.root), *args],
                                capture_output=True, text=True, encoding='utf-8', timeout=40,
                                env=env or self.env)
        if ok:
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-6000:])
        return result

    def assert_originals_retained(self):
        self.assertEqual(self.fixture.originals(), self.originals)
        self.assertEqual(self.fixture.rows('events'), self.bound_events)
        self.assertEqual(self.fixture.starts(), ['baseline'])

    def test_sibling_discovery_reuses_canonical_and_continuation_retains_failure(self):
        sibling = self.workspace / 'sibling'
        sibling.mkdir()
        found = json.loads(self.call('project', 'discover', root=sibling).stdout)
        self.assertEqual(found['project_root'], str(self.root.resolve()))
        self.assertEqual(found['relation'], 'WORKSPACE_BINDING')
        self.assertEqual(found['search_scope'], 'BOUND_CAMPAIGN')
        self.assertFalse((sibling / '.rds').exists())
        self.assert_originals_retained()
        report = json.loads(self.call('project', 'next').stdout)
        self.assertEqual(report['context']['facts']['baseline.score']['kind'], 'UNKNOWN')
        self.assertTrue(report['context']['facts']['run.baseline.failed']['value'])
        continued = json.loads(self.call('project', 'advance').stdout)
        self.assertEqual(continued['receipt']['run_status'], 'SUCCEEDED')
        self.assertEqual(continued['receipt']['run_id'], 'repair')
        after = self.store.snapshot()
        self.assertEqual(next(r for r in after['receipts'] if r['run_id'] == 'baseline'), self.failed)
        self.assertEqual(len(after['runs']), 2)
        self.assertEqual(len(after['receipts']), 2)
        self.assertEqual(len({r['attempt_id'] for r in after['runs']}), 2)
        self.assertEqual(after['budget']['cpu_seconds']['charged_estimate'], 2)
        recovered = self.call('project', 'recover', '--id', 'baseline', ok=False)
        self.assertEqual(recovered.returncode, 1)  # The retained receipt still reports the original failure.
        self.assertEqual(self.fixture.receipt(json.loads(recovered.stdout), 'baseline'), self.failed)
        self.assertEqual(self.store.snapshot()['budget'], after['budget'])
        self.assertEqual(self.fixture.starts(), ['baseline', 'repair'])
        history = self.call('checkpoint', 'restore', '--id', 'failed-before-binding')
        self.assertIn('failed-before-binding', history.stdout)

    def test_fresh_same_contract_separate_successor_and_quick_roots_cannot_reset(self):
        for label, flags in [('fresh', []), ('separate', ['--separate-project', 'Copied experiment']),
                             ('successor', ['--supersedes', str(self.root)]), ('quick', ['--mode', 'quick'])]:
            with self.subTest(case=label):
                sibling = self.workspace / label
                sibling.mkdir()
                for binding in self.fixture.contract['bindings']:
                    destination = sibling / binding['path']
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(self.root / binding['path'], destination)
                contract = deepcopy(self.fixture.contract)
                if label == 'quick':
                    contract.pop('advisor_policy')
                path = sibling / 'contract.json'
                path.write_text(json.dumps(contract), encoding='utf-8')
                rejected = self.call('project', 'init', '--contract', str(path), *flags, root=sibling, ok=False)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn('canonical project ledger', rejected.stderr)
                self.assertFalse((sibling / '.rds/project.sqlite3').exists())
                self.assertFalse((sibling / 'outputs/launches.txt').exists())
                self.assert_originals_retained()

    def test_public_quick_and_same_root_reference_writer_fail_before_launch_or_budget(self):
        rejected = self.call('exec', '--name', 'escape', '--timeout', '5', '--', sys.executable, '-c',
                             'from pathlib import Path; Path("escape-marker").write_text("unexpected")', ok=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertFalse((self.root / '.rds/exec/escape').exists())
        self.assertFalse((self.root / 'escape-marker').exists())
        reference = ROOT / 'examples/reference-run/contract.json'
        legacy = self.call('init', '--contract', str(reference), ok=False)
        self.assertNotEqual(legacy.returncode, 0)
        self.assertIn('L3 kernel not initialized in this root', legacy.stderr)
        self.assertIn('project ledger found', legacy.stderr)
        self.assertFalse((self.root / '.rds/state.sqlite3').exists())
        self.assert_originals_retained()

    def test_missing_original_ledger_or_required_marker_cannot_initialize_replacement(self):
        for original in (self.store.path, self.marker):
            with self.subTest(missing=original.name):
                backup = original.with_name(original.name + '.retained-test-backup')
                original.rename(backup)
                try:
                    rejected = self.call('project', 'init', '--contract', str(self.fixture.contract_path), ok=False)
                    self.assertNotEqual(rejected.returncode, 0)
                    self.assertIn('campaign', rejected.stderr.lower())
                    self.assertFalse(original.exists())
                    self.assertTrue(backup.exists())
                finally:
                    backup.rename(original)
                self.assert_originals_retained()

    def test_adapter_preflight_rejects_foreign_writes_and_new_qualification(self):
        import rds_project_assembly as assembly
        import rds_tools
        import rds_autonomy_worker
        from rds_advisor import RDSAdvisor
        sibling = self.workspace / 'adapter'
        sibling.mkdir()
        recipe = deepcopy(self.fixture.recipe)
        recipe['protocol']['path'] = 'generated.json'
        recipe_path = sibling / 'recipe.json'
        recipe_path.write_text(json.dumps(recipe), encoding='utf-8')
        source = sibling / 'candidate.py'
        source.write_text('def candidate(x):\n    return x\n', encoding='utf-8')
        with mock.patch.dict(os.environ, self.env), mock.patch.object(rds_autonomy_worker.subprocess, 'Popen') as launch:
            for operation in (lambda: assembly.initialize(ProjectStore(sibling), recipe_path),
                              lambda: rds_tools.extract(sibling, source, 'candidate', 'candidate'),
                              lambda: rds_autonomy_worker.execute(sibling, 'invented'),
                              lambda: RDSAdvisor(sibling).ingest_document(source)):
                with self.assertRaisesRegex(ValueError, 'canonical project ledger'):
                    operation()
            with self.assertRaisesRegex(ValueError, 'Bound campaign tool qualification'):
                rds_tools.validate(self.root, 'invented', sibling / 'absent-cases.json')
            launch.assert_not_called()
        self.assertFalse((sibling / 'generated.json').exists())
        self.assertFalse((sibling / '.rds').exists())
        self.assert_originals_retained()

    def test_owned_registered_tool_retains_read_only_qualification_checks(self):
        import test_rds_owned_tools as tools_fixture
        from rds_math import get, records
        from rds_tools import _check_validation
        helper = tools_fixture.OwnedToolsCLITests(methodName='runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.setup_campaign(negative_first=True)
        helper.call('checkpoint', 'save', '--id', 'tool-original')
        helper.call('project', 'bind-workspace', '--workspace-root', str(helper.root))
        helper.env['RDS_CAMPAIGN_BINDING'] = str(helper.root / '.rds-campaign.json')
        candidate = get(helper.root, 'tool:squares')
        adoption = get(helper.root, 'tool-adoption:squares')
        validation = get(helper.root, adoption['data']['validation'])
        before, before_records = helper.store.snapshot(), records(helper.root)
        with mock.patch.dict(os.environ, helper.env):
            receipt = _check_validation(helper.root, validation, candidate)
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        self.assertEqual(helper.store.snapshot(), before)
        self.assertEqual(records(helper.root), before_records)
        denied = helper.call('rsi', 'validate', '--name', 'squares', '--cases', str(helper.root / 'cases.json'),
                             '--timeout', '5', ok=False)
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn('Bound campaign tool qualification', denied.stderr)
        self.assertEqual(helper.store.snapshot(), before)
        self.assertEqual(records(helper.root), before_records)

    def test_bound_copied_worker_preserves_owned_provider_and_method_revision(self):
        import test_rds_autonomy as autonomy_fixture
        helper = autonomy_fixture.AutonomyTests(methodName='runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.build()
        with helper.store._db(True) as db:
            original_contract = tuple(db.execute('SELECT sha256,body FROM contract WHERE id=1').fetchone())
        helper.cli('project', 'bind-workspace', '--workspace-root', str(helper.root))
        with mock.patch.dict(os.environ, {'RDS_CAMPAIGN_BINDING': str(helper.root / '.rds-campaign.json')}):
            result = helper.cli('project', 'drive', '--max-steps', '4')
        after = helper.store.snapshot()
        self.assertEqual(helper.calls(), ['repair1'])
        self.assertEqual([run['id'] for run in after['runs']], ['repair1', 'solve'])
        self.assertEqual([receipt['run_status'] for receipt in after['receipts']], ['SUCCEEDED', 'SUCCEEDED'])
        with helper.store._db(True) as db:
            self.assertEqual(tuple(db.execute('SELECT sha256,body FROM contract WHERE id=1').fetchone()), original_contract)
        self.assertEqual(len(helper.events('METHOD_REVISION_ADOPTED')), 1)
        self.assertEqual(json.loads((helper.root / 'outputs/solve.json').read_text())['score'], 6)
        request = helper.events(autonomy_fixture.autonomy.REQUESTED)[0]
        receipt = next(r for r in after['receipts'] if r['run_id'] == 'repair1')
        self.assertEqual(receipt['autonomy_request'], request['request'])
        self.assertEqual(receipt['effective_contract_sha256'], request['parent_sha256'])
        self.assertEqual(result['scientific_support'], 'UNKNOWN')
        self.assertNotEqual(result['status'], 'GOAL_CONFIRMED')


if __name__ == '__main__':
    unittest.main()
