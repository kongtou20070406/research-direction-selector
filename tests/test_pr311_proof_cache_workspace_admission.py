"""Default proof-cache writes require fresh workspace admission, not a verdict."""
from contextlib import closing, contextmanager
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_proof_cache as cache_module
import rds_workspace as workspace
import test_pr301_descendant_binding as fixture
from test_rds_proof_cache import FakeEngine


class ProofCacheWorkspaceAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.DescendantBindingTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.child = self.f.child()
        self.path = self.child.root / '.rds/proofs.sqlite3'
        self.spec = json.loads((ROOT / 'examples/formal/affine_dynamics.json').read_text(encoding='utf-8'))

    def snapshot(self):
        return {p.name: p.read_bytes() for p in self.path.parent.glob('proofs.sqlite3*')}

    def test_new_default_cache_refuses_inherited_binding_with_real_proof(self):
        workspace.bind(self.f.root)
        before = self.f.dump(self.child)
        result = cache_module.ProofCache(self.path).verify(self.spec)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['cache'], {'hit': False, 'stored': False})
        self.assertEqual(self.snapshot(), {})
        self.assertEqual(self.f.dump(self.child), before)

    @unittest.skipUnless(os.name == 'nt', 'Windows case-insensitive metadata')
    def test_default_cache_casing_cannot_bypass_admission(self):
        workspace.bind(self.f.root)
        result = cache_module.ProofCache(self.child.root / '.RDS/PROOFS.SQLITE3').verify(self.spec)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['cache'], {'hit': False, 'stored': False})
        self.assertEqual(self.snapshot(), {})

    def test_already_open_cache_rechecks_binding_before_store_with_real_proof(self):
        cache = cache_module.ProofCache(self.path)
        self.assertTrue(cache.ready)
        cache._read('missing')
        workspace.bind(self.f.root)
        before, ledger = self.snapshot(), self.f.dump(self.child)
        result = cache.verify(self.spec)
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['cache'], {'hit': False, 'stored': False})
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.f.dump(self.child), ledger)

    def test_refusal_does_not_create_missing_metadata_directory(self):
        root = self.f.root / 'plain-child'
        root.mkdir()
        workspace.bind(self.f.root)
        cache = cache_module.ProofCache(root / '.rds/proofs.sqlite3')
        self.assertFalse(cache.ready)
        self.assertEqual(cache.verify(self.spec)['status'], 'PASS')
        self.assertFalse((root / '.rds').exists())

    def test_checked_readonly_hit_remains_available_after_binding(self):
        cache = cache_module.ProofCache(self.path)
        self.assertTrue(cache.verify(self.spec)['cache']['stored'])
        workspace.bind(self.f.root)
        # Opening a read-only WAL snapshot may create SQLite sidecars.
        reopened = cache_module.ProofCache(self.path)
        before = self.path.read_bytes()
        for reader in (cache, reopened):
            result = reader.verify(self.spec)
            self.assertEqual(result['status'], 'PASS')
            self.assertEqual(result['cache'], {'hit': True, 'stored': False})
        self.assertEqual(self.path.read_bytes(), before)

    def test_unbound_and_canonical_default_cache_can_store(self):
        self.assertTrue(cache_module.ProofCache(self.path).verify(self.spec)['cache']['stored'])
        workspace.bind(self.f.root)
        canonical = cache_module.ProofCache(self.f.root / '.rds/proofs.sqlite3')
        self.assertTrue(canonical.verify(self.spec)['cache']['stored'])
        self.assertTrue(canonical.verify(self.spec)['cache']['hit'])
        self.assertEqual(workspace.coverage(self.f.root)['status'], 'BOUND')

    def test_standalone_explicit_path_preserves_compatibility(self):
        workspace.bind(self.f.root)
        cache = cache_module.ProofCache(self.child.root / 'explicit-cache.sqlite3')
        self.assertTrue(cache.verify(self.spec)['cache']['stored'])
        self.assertTrue(cache.verify(self.spec)['cache']['hit'])

    def test_binding_during_proving_refuses_store_without_changing_verdict(self):
        cache = cache_module.ProofCache(self.path)
        engine = FakeEngine()
        engine.callback = lambda: workspace.bind(self.f.root)
        with patch.object(cache_module, 'engine', engine):
            result = cache.verify({'want': 'FAIL'})
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(result['cache'], {'hit': False, 'stored': False})
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM proofs').fetchone()[0], 0)

    def test_gate_covers_admission_connections_commit_and_close_only(self):
        active, admissions = [], []
        original_connect = cache_module.ProofCache._connect
        original_admission = workspace.check_admission

        @contextmanager
        def gate(**kwargs):
            self.assertEqual(kwargs.get('timeout'), cache_module.BUSY_TIMEOUT_SECONDS)
            active.append(True)
            try:
                yield
            finally:
                active.pop()

        def admission(root):
            self.assertTrue(active)
            admissions.append(root)
            return original_admission(root)

        case = self

        class Spy:
            def __init__(self, db):
                self.db = db

            def execute(self, *args):
                case.assertTrue(active)
                return self.db.execute(*args)

            def commit(self):
                case.assertTrue(active)
                return self.db.commit()

            def close(self):
                case.assertTrue(active)
                return self.db.close()

        def connect(cache, readonly=False):
            if not readonly:
                self.assertTrue(active)
            db = original_connect(cache, readonly=readonly)
            return db if readonly else Spy(db)

        engine = FakeEngine()
        engine.callback = engine.check_callback = lambda: self.assertFalse(active)
        with patch.object(cache_module, 'mutation', gate, create=True), \
                patch.object(workspace, 'check_admission', side_effect=admission), \
                patch.object(cache_module.ProofCache, '_connect', connect), \
                patch.object(cache_module, 'engine', engine):
            cache = cache_module.ProofCache(self.path)
            self.assertTrue(cache.verify({'want': 'PASS'})['cache']['stored'])
            self.assertTrue(cache.verify({'want': 'PASS'})['cache']['hit'])
        self.assertEqual(admissions, [self.child.root, self.child.root])
        self.assertFalse(active)

    def test_gate_unavailable_is_soft_for_init_and_already_open_store(self):
        cache = cache_module.ProofCache(self.path)
        cache._read('missing')
        before = self.snapshot()
        with patch.object(cache_module, 'mutation', side_effect=TimeoutError('busy'), create=True):
            fresh = cache_module.ProofCache(self.f.root / '.rds/proofs.sqlite3')
            self.assertFalse(fresh.ready)
            with patch.object(cache_module, 'engine', FakeEngine()):
                for verdict in ('PASS', 'FAIL', 'UNKNOWN'):
                    for instance in (cache, fresh):
                        result = instance.verify({'want': verdict})
                        self.assertEqual(result['status'], verdict)
                        self.assertEqual(result['cache'], {'hit': False, 'stored': False})
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.f.root / '.rds/proofs.sqlite3').exists())


if __name__ == '__main__':
    unittest.main()
