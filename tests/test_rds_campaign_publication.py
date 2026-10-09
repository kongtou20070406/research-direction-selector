"""Public campaign publication and native writers share the real mutation gate."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sys
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_campaign as campaign
import rds_mutation as locks
import rds_tools as tools
from rds_project import ProjectStore
import test_rds_project as fixtures


class CampaignPublicationTests(unittest.TestCase):
    def setUp(self):
        environment = mock.patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(campaign.ENVIRONMENT, None)
        self.fixture = fixtures.ProjectTests('runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown)
        self.root, self.store = self.fixture.root.resolve(), self.fixture.store
        self.child_root = self.root / 'sibling'
        self.child_root.mkdir()
        for item in self.fixture.contract['bindings']:
            shutil.copyfile(self.root / item['path'], self.child_root / item['path'])
        self.child = ProjectStore(self.child_root)
        self.marker = self.root / campaign.MARKER

    def request(self):
        token = '0123456789abcdef0123456789abcdef'
        return {'token': token, 'validation_id': 'validation:' + token,
                'job_root': '.rds/rsi/tool-checks/' + token + '/.rds/exec/tool-check'}

    def assert_no_child_work(self, before=None):
        if before is None:
            self.assertFalse(self.child.path.exists())
        else:
            self.assertEqual(self.child.snapshot(), before)
        self.assertFalse((self.child_root / 'outputs/r1.json').exists())
        self.assertFalse((self.child_root / '.rds/rsi').exists())

    def bind_wins(self, operation, before=None):
        publishing, release, writer_started, writer_finished = [threading.Event() for _ in range(4)]
        original_publish = campaign._publish
        parent_before = self.store.snapshot()

        def hold_real_publish(path, value):
            self.assertEqual(path, self.marker)
            self.assertGreater(getattr(locks._local, 'depth', 0), 0)
            self.assertFalse(path.exists())
            publishing.set()
            if not release.wait(5):
                raise RuntimeError('Publication fixture release timed out')
            return original_publish(path, value)

        def write():
            writer_started.set()
            return operation()

        with ThreadPoolExecutor(max_workers=2) as pool, mock.patch.object(campaign, '_publish', hold_real_publish):
            binding = pool.submit(campaign.bind, self.store, self.root)
            writer = None
            try:
                self.assertTrue(publishing.wait(3), 'Bind did not reach its real publication boundary')
                writer = pool.submit(write)
                writer.add_done_callback(lambda _: writer_finished.set())
                self.assertTrue(writer_started.wait(1))
                self.assertFalse(writer_finished.wait(0.15), 'Writer completed before publication release')
                self.assertFalse(self.marker.exists())
            finally:
                release.set()
            result = binding.result(timeout=5)
            with self.assertRaisesRegex(ValueError, 'canonical project ledger'):
                writer.result(timeout=5)
        self.assertEqual(campaign.binding(self.child_root)['binding_id'], result['binding_id'])
        self.assert_no_child_work(before)
        self.assertEqual(self.store.snapshot(), parent_before)

    def writer_wins(self, operation, *, preparation=False):
        guarded, release, bind_started, published, bind_finished = [threading.Event() for _ in range(5)]
        original_enforce, original_publish = campaign.enforce, campaign._publish
        parent_before = self.store.snapshot()

        def hold_after_real_guard(root, *args, **kwargs):
            result = original_enforce(root, *args, **kwargs)
            if Path(root).resolve() == self.child_root and getattr(locks._local, 'depth', 0) > 0 \
                    and not guarded.is_set():
                guarded.set()
                if not release.wait(5):
                    raise RuntimeError('Writer fixture release timed out')
            return result

        def observe_real_publish(path, value):
            published.set()
            return original_publish(path, value)

        def bind():
            bind_started.set()
            return campaign.bind(self.store, self.root)

        with ThreadPoolExecutor(max_workers=2) as pool, \
                mock.patch.object(campaign, 'enforce', hold_after_real_guard), \
                mock.patch.object(campaign, '_publish', observe_real_publish):
            writer = pool.submit(operation)
            binding = None
            try:
                self.assertTrue(guarded.wait(3), 'Writer did not return from a real guarded mutation')
                binding = pool.submit(bind)
                binding.add_done_callback(lambda _: bind_finished.set())
                self.assertTrue(bind_started.wait(1))
                self.assertFalse(bind_finished.wait(0.15), 'Bind completed while the real writer held its gate')
                self.assertFalse(self.marker.exists())
            finally:
                release.set()
            writer.result(timeout=5)
            with self.assertRaisesRegex(ValueError, 'preparation|active runs|reserved resources|settled'):
                binding.result(timeout=5)
        self.assertFalse(published.is_set())
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.store.snapshot(), parent_before)
        if preparation:
            with self.child._db(True) as db:
                intents = [json.loads(row['body']) for row in db.execute(
                    "SELECT body FROM events WHERE json_extract(body,'$.kind')='TOOL_PREPARATION_STARTED'")]
            self.assertEqual(len(intents), 1)
            self.assertEqual(intents[0]['request'], self.request())
        else:
            snapshot = self.child.snapshot()
            self.assertEqual(len(snapshot['runs']), 1)
            self.assertEqual(snapshot['runs'][0]['status'], 'RESERVED')
            self.assertIsNone(snapshot['runs'][0]['attempt_id'])
            self.assertEqual(snapshot['receipts'], [])
            self.assertGreater(snapshot['budget']['wall_seconds']['reserved'], 0)
        self.assertFalse((self.child_root / 'outputs/r1.json').exists())

    def test_bind_publication_wins_before_sibling_initialize(self):
        self.bind_wins(lambda: self.child.initialize(deepcopy(self.fixture.contract)))

    def test_bind_publication_wins_before_sibling_reservation(self):
        self.child.initialize(self.fixture.contract)
        before = self.child.snapshot()
        self.bind_wins(lambda: self.child.register(self.fixture.spec()), before)

    def test_bind_publication_wins_before_sibling_preparation_intent(self):
        self.bind_wins(lambda: tools._begin_preparation(self.child, self.request()))

    def test_committed_sibling_reservation_wins_and_bind_refuses(self):
        self.child.initialize(self.fixture.contract)
        self.writer_wins(lambda: self.child.register(self.fixture.spec()))

    def test_committed_preparation_intent_wins_and_bind_refuses(self):
        # Native extraction creates this storage before publishing its intent.
        # Keep the real intent writer; no contract, event, or run is fabricated.
        self.child.state_dir.mkdir()
        self.writer_wins(lambda: tools._begin_preparation(self.child, self.request()), preparation=True)

    def test_public_bind_retries_only_after_original_sibling_run_finishes(self):
        self.child.initialize(self.fixture.contract)
        self.child.register(self.fixture.spec())
        before = self.child.snapshot()
        with self.assertRaisesRegex(ValueError, 'no active runs or reserved resources'):
            campaign.bind(self.store, self.root)
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.child.snapshot(), before)
        receipt = self.child.execute('r1')
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        settled = self.child.snapshot()
        result = campaign.bind(self.store, self.root)
        self.assertEqual(self.child.snapshot(), settled)
        self.assertEqual(settled['receipts'], [receipt])
        self.assertEqual(campaign.binding(self.child_root)['binding_id'], result['binding_id'])
        with self.assertRaisesRegex(ValueError, 'canonical project ledger'):
            self.child.register(self.fixture.spec(rid='r2'))
        self.assertEqual(self.child.snapshot(), settled)


if __name__ == '__main__':
    unittest.main()
