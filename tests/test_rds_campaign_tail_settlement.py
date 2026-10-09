"""Binding preserves the original native producers' post-receipt settlement."""
from pathlib import Path
import json
import shutil
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_autonomy as autonomy
import rds_campaign as campaign
import rds_owned_advisor as owned
from rds_project import ProjectStore
import test_rds_autonomy as autonomy_fixture
import test_rds_campaign_binding as binding_fixture
import test_rds_owned_advisor as owned_fixture


class CampaignTailSettlementTests(unittest.TestCase):
    def setUp(self):
        self.f = binding_fixture.CampaignBindingTests('runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def foreign_owned(self, mode='negative', interrupt=False):
        helper = owned_fixture.OwnedAdvisorCLITests('runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.root = self.f.workspace / 'owned-sibling'
        helper.root.mkdir()
        helper.initialize(mode)
        store = ProjectStore(helper.root)
        store.register(helper.manifests['baseline'])
        if interrupt:
            with patch.object(store, '_advisor_finished'):
                receipt = store.execute('baseline')
        else:
            receipt = store.execute('baseline')
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        self.assertEqual(helper.starts(), ['baseline'])
        return helper, store, receipt

    def foreign_model(self):
        helper = autonomy_fixture.AutonomyTests('runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.root = self.f.workspace / 'model-sibling'
        helper.root.mkdir()
        helper.store = ProjectStore(helper.root)
        helper.build(modes={'repair1': 'authority'})
        event = helper.request()
        manifest = next(route['manifest'] for route in helper.contract['advisor_policy']['routes']
                        if route['manifest']['id'] == event['run_id'])
        helper.store.register(manifest)
        receipt = helper.store.execute(event['run_id'])
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        self.assertEqual(helper.calls(), ['repair1'])
        return helper, event, receipt

    def refused(self, message):
        with self.assertRaisesRegex(ValueError, message):
            campaign.bind(self.f.store, self.f.workspace)
        self.assertFalse(self.f.marker.exists())

    def test_foreign_committed_intent_cannot_be_replaced_before_marker_recovery(self):
        sibling = self.f.workspace / 'other-canonical'
        shutil.copytree(self.f.helper.root, sibling)
        with patch.object(campaign, '_publish', side_effect=OSError('interrupted original marker')):
            with self.assertRaisesRegex(OSError, 'original marker'):
                campaign.bind(self.f.store, self.f.workspace)
        original = self.f.events()[0]
        with self.assertRaisesRegex(ValueError, 'intent|binding'):
            campaign.bind(ProjectStore(sibling), self.f.workspace)
        self.assertFalse(self.f.marker.exists())
        recovered = campaign.bind(self.f.store, self.f.workspace)
        self.assertEqual(recovered['binding_id'], original['binding_id'])
        self.assertEqual(self.f.events(), [original])

    def test_owned_receipt_needs_original_final_collection_then_retry_without_run(self):
        helper, store, receipt = self.foreign_owned(interrupt=True)
        original = store.snapshot()
        self.refused('Advisor.*settlement|Advisor.*review|owned.*review')
        report = owned.review(store)
        self.assertEqual(report['status'], 'REVIEWED')
        with store._db(True) as db:
            import json
            event = json.loads(db.execute("SELECT body FROM events WHERE "
                "json_extract(body,'$.kind')='OWNED_ADVISOR_REVIEW' ORDER BY id DESC LIMIT 1").fetchone()[0])
        report_path = Path(event['report']['path'])
        report_raw = report_path.read_bytes()
        report_path.write_bytes(report_raw + b' ')
        self.refused('report CAS integrity')
        report_path.write_bytes(report_raw)
        campaign.bind(self.f.store, self.f.workspace)
        after = store.snapshot()
        for key in ('runs', 'receipts', 'budget'):
            self.assertEqual(after[key], original[key], key)
        self.assertEqual(after['receipts'][0]['sha256'], receipt['sha256'])
        self.assertEqual(helper.starts(), ['baseline'])

    def test_completed_collection_failed_report_is_settlement_not_scientific_success(self):
        helper, store, receipt = self.foreign_owned(mode='badjson')
        self.assertEqual(store.last_advisor_review['status'], 'COLLECTION_FAILED')
        self.assertEqual(store.last_advisor_review['scientific_support'], 'UNKNOWN')
        original = store.snapshot()
        campaign.bind(self.f.store, self.f.workspace)
        after = store.snapshot()
        self.assertEqual(after['receipts'], original['receipts'])
        self.assertEqual(after['budget'], original['budget'])
        self.assertEqual(helper.starts(), ['baseline'])

    def test_foreign_recover_reuses_the_original_settled_review_without_collecting(self):
        helper, store, receipt = self.foreign_owned()
        campaign.bind(self.f.store, self.f.workspace)
        before = store.snapshot()
        with store._db(True) as db:
            events = [tuple(row) for row in db.execute('SELECT id,body FROM events ORDER BY id')]
        cas = {path.name: path.read_bytes() for path in (helper.root / '.rds/cas').glob('*') if path.is_file()}
        from rds_cli import cmd_project
        args = SimpleNamespace(root=str(helper.root), action='recover', id='baseline')
        with patch.object(owned, 'after_finish', side_effect=AssertionError('foreign recovery tried fresh collection')):
            first = cmd_project(args)
            second = cmd_project(args)
        self.assertEqual(first['receipt'], receipt)
        self.assertEqual(second, first)
        self.assertEqual(first['advisor']['status'], 'REVIEWED')
        self.assertTrue(first['advisor_review_reused'])
        self.assertEqual(store.snapshot(), before)
        with store._db(True) as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT id,body FROM events ORDER BY id')], events)
        self.assertEqual({path.name: path.read_bytes() for path in (helper.root / '.rds/cas').glob('*') if path.is_file()}, cas)
        self.assertEqual(helper.starts(), ['baseline'])

    def test_foreign_recovery_does_not_accept_a_corrupt_retained_review(self):
        helper, store, receipt = self.foreign_owned()
        campaign.bind(self.f.store, self.f.workspace)
        store.recover('baseline')
        self.assertTrue(store.last_advisor_review_reused)
        with store._db(True) as db:
            row = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='OWNED_ADVISOR_REVIEW' ORDER BY id DESC LIMIT 1").fetchone()
        ref = json.loads(row[0])['report']
        path = Path(ref['path'])
        path.write_bytes(path.read_bytes() + b' ')
        before = store.snapshot()
        store.recover('baseline')
        self.assertEqual(store.last_advisor_review['status'], 'COLLECTION_FAILED')
        self.assertFalse(store.last_advisor_review_reused)
        from rds_cli import cmd_project
        result = cmd_project(SimpleNamespace(root=str(helper.root), action='recover', id='baseline'))
        self.assertEqual(result['receipt'], receipt)
        self.assertEqual(result['advisor']['status'], 'COLLECTION_FAILED')
        self.assertNotIn('advisor_review_reused', result)
        self.assertEqual(store.snapshot(), before)
        self.assertEqual(helper.starts(), ['baseline'])

    def test_prospective_parent_outside_workspace_blocks_binding_before_marker(self):
        import test_rds_quick as quick_fixture
        helper = quick_fixture.QuickTests('runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.initialize_policy_ledger()
        source = self.f.workspace / 'quick-source'
        source.mkdir()
        (source / 'probe.py').write_text('print("original external-parent job")\n', encoding='utf-8')
        result = helper.call('exec', '--name', 'external-parent', '--timeout', '5',
            *helper.policy_options(), '--', sys.executable, '-B', 'probe.py', root=source)
        summary = json.loads(result.stdout)
        retained = json.loads(Path(summary['record']).read_text(encoding='utf-8'))
        self.assertEqual(retained['receipt']['run_status'], 'SUCCEEDED')
        before = ProjectStore(helper.ledger).snapshot()
        self.refused('parent ledger escapes the selected workspace')
        self.assertEqual(ProjectStore(helper.ledger).snapshot(), before)
        self.assertFalse(self.f.marker.exists())

    def test_model_paid_receipt_requires_processed_original_result_without_relaunch(self):
        helper, event, receipt = self.foreign_model()
        original = helper.store.snapshot()
        self.refused('model.*outcome|model.*result|Autonomy.*processed')
        self.assertEqual(autonomy.process_result(helper.store, event, receipt), 'NEEDS_AUTHORIZATION')
        campaign.bind(self.f.store, self.f.workspace)
        after = helper.store.snapshot()
        for key in ('runs', 'receipts', 'budget'):
            self.assertEqual(after[key], original[key], key)
        self.assertEqual(helper.calls(), ['repair1'])

    def test_canonical_unprocessed_model_can_bind_and_resume_original_result(self):
        helper, event, receipt = self.foreign_model()
        original = helper.store.snapshot()
        campaign.bind(helper.store, helper.root)
        self.assertEqual(autonomy.process_result(helper.store, event, receipt), 'NEEDS_AUTHORIZATION')
        after = helper.store.snapshot()
        for key in ('runs', 'receipts', 'budget'):
            self.assertEqual(after[key], original[key], key)
        self.assertEqual(helper.calls(), ['repair1'])

    def test_canonical_owned_collection_can_resume_after_binding(self):
        helper, store, receipt = self.foreign_owned(interrupt=True)
        original = store.snapshot()
        campaign.bind(store, helper.root)
        self.assertEqual(owned.review(store)['status'], 'REVIEWED')
        after = store.snapshot()
        for key in ('runs', 'receipts', 'budget'):
            self.assertEqual(after[key], original[key], key)
        self.assertEqual(after['receipts'][0]['sha256'], receipt['sha256'])
        self.assertEqual(helper.starts(), ['baseline'])


if __name__ == '__main__':
    unittest.main()
