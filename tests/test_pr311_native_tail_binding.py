"""Real native publication tails must settle before ancestor workspace binding.

Adapted from the independent PR311 counterexamples. Fixtures and all records use
the existing producers; injected faults interrupt publication, not receipt data.
Successful software settlement never upgrades scientific support from UNKNOWN.
"""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))

import rds_cli
import rds_owned_advisor as owned
import rds_quick as quick
import rds_tools as tools
import rds_workspace as workspace
from rds_advisor_coverage import project_context
from rds_campaign import _native_tails_quiescent
from rds_checkpoints import read_checkpoint
from rds_math import blob, get
from rds_project import ProjectStore, digest
import test_rds_owned_advisor as owned_fixture
import test_rds_project as project_fixture
import test_rds_quick as quick_fixture
import test_rds_tool_preparation as tool_fixture


class NativeTailBindingTests(unittest.TestCase):
    def fixture(self, fixture_class, parent=None):
        original = tempfile.TemporaryDirectory
        if parent is None:
            helper = fixture_class(methodName='runTest')
            helper.setUp()
        else:
            with patch('tempfile.TemporaryDirectory', side_effect=lambda *a, **k:
                       original(*a, **{**k, 'dir': str(parent)})):
                helper = fixture_class(methodName='runTest')
                helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.addCleanup(helper.tmp.cleanup)
        return helper

    def deferred_parent(self):
        # Construct descendants before initializing their enclosing project, so
        # real lifecycle checks admit the original independent producers.
        with patch.object(ProjectStore, 'initialize', return_value=None):
            return self.fixture(project_fixture.ProjectTests)

    def evidence(self, root):
        evidence = {}
        for path in sorted(root.rglob('*')):
            if not path.is_file() or path.name.endswith(('-shm', '-wal', '-journal')):
                continue
            key = str(path.relative_to(root))
            # SQLite readers can create transient WAL sidecars. Compare ledger
            # contents, while pinning original request, pointer and CAS bytes.
            if path.suffix == '.sqlite3':
                with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as db:
                    evidence[key] = tuple(db.iterdump())
            else:
                evidence[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        return evidence

    def assert_binding_refused_without_mutation(self, parent):
        before = self.evidence(parent.root)
        try:
            with self.assertRaises(ValueError):
                workspace.bind(parent.root)
        finally:
            after = self.evidence(parent.root)
            changed = sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))
            self.assertEqual(changed, [],
                             'Ancestor refusal must preserve ledger contents and pointer/request/CAS bytes')

    def assert_terminal_receipt(self, store, receipt):
        state = store.snapshot()
        self.assertEqual(len(state['runs']), 1)
        self.assertEqual(len(state['receipts']), 1)
        run = state['runs'][0]
        self.assertEqual(state['receipts'][0], receipt)
        self.assertEqual(run['attempt_id'], receipt['attempt_id'])
        self.assertEqual(run['manifest_sha256'], receipt['manifest_sha256'])
        self.assertEqual(run['status'], receipt['process_status'])
        self.assertEqual(receipt['sha256'], digest({k: v for k, v in receipt.items() if k != 'sha256'}))
        self.assertTrue(all(value['reserved'] == 0 for value in state['budget'].values()))
        return state

    def test_uninitialized_tool_preparation_refuses_until_original_validation_published(self):
        parent = self.deferred_parent()
        helper = self.fixture(tool_fixture.ToolPreparationTests, parent.root)
        self.enterContext(patch.dict(os.environ, {'RDS_USAGE_DB': str(parent.root / 'usage.sqlite3')}))
        publish = tools.put
        pending_publication = []

        def interrupt(*args, **kwargs):
            if args[2] == 'tool-validation':
                pending_publication.append((args, kwargs))
                raise RuntimeError('native-tail fixture: terminal tool child before validation publication')
            return publish(*args, **kwargs)

        with patch.object(tools, 'put', side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, 'before validation publication'):
                helper.qualify()
        intent, = helper.intents()
        self.assertEqual(intent['request_sha256'], digest(intent['request']))
        with helper.store._db(True) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertNotIn('contract', tables)
            self.assertNotIn('runs', tables)
        self.assertIsNone(get(helper.root, intent['request']['validation_id']))
        child = ProjectStore(helper.root / intent['request']['job_root'])
        receipt = helper.native_receipt()
        self.assert_terminal_receipt(child, receipt)
        parent.store.initialize(parent.contract)

        self.assert_binding_refused_without_mutation(parent)

        # Resume the exact interrupted producer publication. Replaying qualify
        # after ancestor initialization would hit its unrelated lifecycle gate.
        publication_args, publication_kwargs = pending_publication[0]
        self.assertEqual(len(pending_publication), 1)
        published = publish(*publication_args, **publication_kwargs)
        self.assertEqual(published['id'], intent['request']['validation_id'])
        validation = get(helper.root, published['id'])
        self.assertEqual(validation['kind'], 'tool-validation')
        self.assertEqual(validation['data']['receipt_sha256'], digest(receipt))
        self.assertEqual(json.loads(blob(helper.root, validation['asset'])), {'receipt': receipt})
        self.assertEqual(validation['data']['total_budget'], 'UNKNOWN')
        self.assert_terminal_receipt(child, receipt)
        self.assertEqual(len(helper.intents()), 1)
        self.assertEqual(workspace.bind(parent.root)['status'], 'BOUND')

    def test_prospective_root_and_receipt_require_original_after_checkpoint(self):
        parent = self.deferred_parent()
        helper = self.fixture(quick_fixture.QuickTests, parent.root)
        helper.initialize_ledger()
        helper.script('print("native prospective binding fixture")\n')
        parsed = rds_cli.parser().parse_args([
            '--root', str(helper.root), 'exec', '--name', 'tail', '--timeout', '5',
            *helper.policy_options(), '--', sys.executable, '-B', 'probe.py'])
        review_args = argparse.Namespace(
            root=str(helper.ledger), research_context=str(helper.context_path),
            graph=str(helper.graph_path), saved_dependencies=False)
        advice = rds_cli.cmd_advise(review_args, rds_cli.RDSState(helper.ledger))
        context = project_context(helper.ledger, json.loads(helper.context_path.read_text(encoding='utf-8')))
        with patch.dict(os.environ, helper.env), patch.object(
                quick, '_complete_prospective', side_effect=RuntimeError('native-tail fixture: before after-checkpoint')):
            with self.assertRaisesRegex(RuntimeError, 'before after-checkpoint'):
                quick.execute(parsed, review=(advice, context))
        child = ProjectStore(helper.root / '.rds/exec/tail')
        request = json.loads((child.root / 'rds-exec-request.json').read_text(encoding='utf-8'))
        self.assertEqual(request['research_context']['ledger'], str(helper.ledger.resolve()))
        self.assertTrue(Path(request['research_context']['ledger']).is_dir())
        receipt, = child.snapshot()['receipts']
        initial = self.assert_terminal_receipt(child, receipt)
        ledger = ProjectStore(helper.ledger)
        with ledger._db(True) as db:
            before = read_checkpoint(db, 'exec-before-tail', root=ledger.root)
            self.assertIsNotNone(before)
            self.assertEqual(before['record']['decision']['outcome'], 'plan_locked')
            self.assertEqual(before['record']['decision']['scientific_support'], 'UNKNOWN')
            self.assertIsNone(read_checkpoint(db, 'exec-after-tail', root=ledger.root))
        completion = quick._prospective_completion(child.root, request, receipt)
        self.assertIsNone(completion['after_checkpoint'])
        parent.store.initialize(parent.contract)

        self.assert_binding_refused_without_mutation(parent)

        recovered = child.recover('tail')
        self.assertEqual(recovered['sha256'], receipt['sha256'])
        settled = self.assert_terminal_receipt(child, receipt)
        self.assertEqual(settled['budget'], initial['budget'])
        completion = quick._prospective_completion(child.root, request, receipt)
        after = completion['after_checkpoint']
        self.assertIsNotNone(after)
        self.assertEqual(after['record']['contract_sha256'], before['record']['contract_sha256'])
        self.assertEqual(after['record']['decision']['execution'], {
            'job_root': str(child.root), 'receipt_sha256': receipt['sha256'],
            'run_status': receipt['run_status']})
        self.assertEqual(after['record']['decision']['scientific_support'], 'UNKNOWN')
        with ledger._db(True) as db:
            self.assertEqual(read_checkpoint(db, 'exec-before-tail', root=ledger.root), before)
        self.assertEqual(workspace.bind(parent.root)['status'], 'BOUND')

    def test_late_old_cas_review_requires_original_terminal_receipt_coverage(self):
        parent = self.deferred_parent()
        helper = self.fixture(owned_fixture.OwnedAdvisorCLITests, parent.root)
        helper.initialize('positive')
        helper.create()
        child = ProjectStore(helper.root)
        import rds_cas
        publish_cas = rds_cas.cas_json
        executed = []

        def delayed_review(root, value, **kwargs):
            ref = publish_cas(root, value, **kwargs)
            if 'coverage' in value and 'fingerprint' in value and not executed:
                executed.append(True)
                with patch.object(owned, 'after_finish', side_effect=RuntimeError(
                        'native-tail fixture: interrupted terminal collection')):
                    executed.append(child.execute('baseline'))
            return ref

        with patch.dict(os.environ, helper.env), patch.object(rds_cas, 'cas_json', side_effect=delayed_review):
            old_report = owned.review(child)
        self.assertEqual(len(executed), 2)
        receipt = executed[1]
        initial = self.assert_terminal_receipt(child, receipt)
        self.assertEqual(old_report['coverage']['runs'], 1)
        self.assertEqual(old_report['coverage']['receipts'], 0)
        self.assertEqual(old_report['scientific_support'], 'UNKNOWN')
        with child._db(True) as db:
            finished = db.execute("SELECT MAX(id) FROM events WHERE json_extract(body,'$.kind')='ATTEMPT_FINISHED'").fetchone()[0]
            late = db.execute("SELECT id,body FROM events WHERE json_extract(body,'$.kind')='OWNED_ADVISOR_REVIEW' ORDER BY id DESC LIMIT 1").fetchone()
            self.assertGreater(late['id'], finished)
            event = json.loads(late['body'])
            ref = event['report']
            raw = Path(ref['path']).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), ref['sha256'])
            self.assertEqual(len(raw), ref['bytes'])
            self.assertEqual(json.loads(raw), old_report)
            with self.assertRaisesRegex(ValueError, 'event/receipt inventory'):
                _native_tails_quiescent(child.root, db, child._contract(db), child._runs(db),
                                        [child._receipt(row) for row in db.execute(
                                            'SELECT run_id,sha256,body FROM receipts ORDER BY run_id')])
        parent.store.initialize(parent.contract)

        self.assert_binding_refused_without_mutation(parent)

        fresh = owned.review(child)
        self.assertEqual(fresh['coverage']['runs'], 1)
        self.assertEqual(fresh['coverage']['receipts'], 1)
        self.assertEqual(fresh['scientific_support'], 'UNKNOWN')
        self.assertEqual(helper.starts(), ['baseline'])
        settled = self.assert_terminal_receipt(child, receipt)
        self.assertEqual(settled['budget'], initial['budget'])
        with child._db(True) as db:
            retained = _native_tails_quiescent(
                child.root, db, child._contract(db), child._runs(db),
                [child._receipt(row) for row in db.execute(
                    'SELECT run_id,sha256,body FROM receipts ORDER BY run_id')])
        self.assertEqual(retained, fresh)
        self.assertEqual(workspace.bind(parent.root)['status'], 'BOUND')


if __name__ == '__main__':
    unittest.main()
