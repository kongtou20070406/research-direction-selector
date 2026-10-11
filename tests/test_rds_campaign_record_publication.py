"""CAS producers settle their original native record before campaign binding."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import os
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import rds_campaign as campaign
import rds_math as math
import rds_mutation
import rds_quick as quick
from rds_advisor_search import search_directions
from rds_checkpoints import read_checkpoint
from rds_project import ProjectStore
from test_rds_advisor_search import node
import test_rds_project as fixture


class CampaignRecordPublicationTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop(campaign.ENVIRONMENT, None)
        temporary = tempfile.TemporaryDirectory(prefix='rds-publication-')
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        helper = fixture.ProjectTests('runTest')
        helper.setUp()
        self.addCleanup(helper.tearDown)
        self.stores = []
        for name in ('canonical', 'producer'):
            root = self.workspace / name
            root.mkdir()
            for binding in helper.contract['bindings']:
                shutil.copyfile(helper.root / binding['path'], root / binding['path'])
            store = ProjectStore(root)
            store.initialize(helper.contract)
            self.stores.append(store)
        self.parent, self.child = self.stores
        self.original = self.child.snapshot()
        self.context = {'decision': {'id': 'choose', 'goal_revision': 'publication-v1',
                                    'scope': {'domain': 'synthetic-software-acceptance'}}, 'facts': {}}
        # Advice generation is outside every producer lock and never runs a worker.
        search = search_directions({'nodes': [node('route')], 'edges': []}, self.context)
        self.advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH', 'search': search}]}

    def accounting_unchanged(self):
        after = self.child.snapshot()
        for key in ('budget', 'runs', 'receipts', 'contract_sha256'):
            self.assertEqual(after[key], self.original[key])

    def files(self):
        return {str(path.relative_to(self.child.root)): path.read_bytes()
                for path in (self.child.root / '.rds/cas').glob('*') if path.is_file()}

    def write_choice(self):
        return quick.record_choice(self.child.root, self.advice, self.context, None, 'original-choice')

    def writer_first(self, module, cas_name, write, verify):
        published, release, attempting, bound = (threading.Event() for _ in range(4))
        binder_thread = {}
        original_cas = getattr(module, cas_name)
        original_gate = rds_mutation._gate

        class ObservedGate:
            def acquire(self, *args, **kwargs):
                if threading.get_ident() == binder_thread.get('id'):
                    attempting.set()
                return original_gate.acquire(*args, **kwargs)

            def release(self):
                return original_gate.release()

        def publish_then_pause(*args, **kwargs):
            result = original_cas(*args, **kwargs)
            published.set()
            if not release.wait(3):
                raise RuntimeError('Producer release timed out')
            return result

        def bind():
            binder_thread['id'] = threading.get_ident()
            try:
                return campaign.bind(self.parent, self.workspace)
            finally:
                bound.set()

        with patch.object(rds_mutation, '_gate', ObservedGate()), \
                patch.object(module, cas_name, publish_then_pause), ThreadPoolExecutor(max_workers=2) as pool:
            writer = pool.submit(write)
            binding = None
            try:
                self.assertTrue(published.wait(3), 'Original CAS bytes were not published')
                self.assertTrue(self.files())
                binding = pool.submit(bind)
                self.assertTrue(attempting.wait(3), 'Binder never attempted the real mutation gate')
                self.assertFalse(bound.wait(.15), 'Binding published before the original record committed')
                self.assertFalse((self.workspace / campaign.MARKER).exists())
            finally:
                release.set()
            result = writer.result(timeout=3)
            binding.result(timeout=3)
        verify(result)
        self.accounting_unchanged()
        self.assertTrue((self.workspace / campaign.MARKER).is_file())

    def test_research_record_cas_and_commit_precede_waiting_binding(self):
        def verify(value):
            self.assertEqual(math.get(self.child.root, 'original-note'), value)
            self.assertEqual(math.blob(self.child.root, value['asset']), b'original finite note')
            self.assertEqual(value['mathematical_status'], 'UNKNOWN')
        self.writer_first(math, 'cas_bytes',
                          lambda: math.put(self.child.root, 'original-note', 'note', b'original finite note'), verify)

    def test_choice_cas_and_checkpoint_precede_waiting_binding(self):
        def verify(saved):
            with self.child._db(True) as db:
                completed = read_checkpoint(db, saved['id'], root=self.child.root)
            self.assertEqual(completed['record']['decision']['outcome'], 'plan_locked')
            self.assertEqual(completed['record']['decision']['scientific_support'], 'UNKNOWN')
            self.assertEqual(completed['record']['contract_sha256'], self.original['contract_sha256'])
        self.writer_first(quick, 'cas_json', self.write_choice, verify)

    def test_binding_first_rejects_record_and_choice_without_new_cas(self):
        campaign.bind(self.parent, self.workspace)
        before = self.files()
        for write in (lambda: math.put(self.child.root, 'blocked-note', 'note', b'blocked original bytes'),
                      self.write_choice):
            with self.subTest(write=write), self.assertRaisesRegex(ValueError, 'canonical project ledger'):
                write()
            self.assertEqual(self.files(), before)
        self.assertIsNone(math.get(self.child.root, 'blocked-note'))
        self.accounting_unchanged()

    def test_binding_first_rejects_rejection_and_falsification_without_new_cas(self):
        self.write_choice()
        evidence = self.workspace / 'original-witness.bin'
        evidence.write_bytes(b'original counterexample bytes')
        campaign.bind(self.parent, self.workspace)
        before = self.files()
        args = SimpleNamespace(root=self.child.root, route=None, reason='declared fixture counterexample',
                               evidence=str(evidence), id='rejected-choice', domain=None)
        for write in (lambda: quick.reject_route(args),
                      lambda: quick.record_falsification(self.child.root, witness={'original': 'finite'}, reason='fixture')):
            with self.subTest(write=write), self.assertRaisesRegex(ValueError, 'canonical project ledger'):
                write()
            self.assertEqual(self.files(), before)
        with self.child._db(True) as db:
            self.assertIsNone(read_checkpoint(db, 'rejected-choice', root=self.child.root))
            self.assertEqual(read_checkpoint(db, 'original-choice', root=self.child.root)['record']['decision']['outcome'],
                             'plan_locked')
        self.accounting_unchanged()


if __name__ == '__main__':
    unittest.main()
