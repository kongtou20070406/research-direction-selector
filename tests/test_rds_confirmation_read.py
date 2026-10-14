"""Original confirmation evidence stays read-only and independent of Advisor."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
import rds_confirmation_read as reader
import rds_domain_confirmation as domain
import rds_original_read as originals
import rds_postcommit_confirmation as post
import test_rds_postcommit_confirmation as fixtures


class ConfirmationReadTests(unittest.TestCase):
    def fixture(self):
        case = fixtures.PostcommitConfirmationTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        _, _, claim, data, kind = case.example.postcommit_sources()
        weights = {'layers': [{'weight': [[0.] * n for _ in range(m)], 'bias': [0.] * m}
                              for n, m in zip(claim['layers'][:-1], claim['layers'][1:])]}
        source = ('import json,pathlib,sys\n'
                  'pathlib.Path(sys.argv[1]).write_text(json.dumps(' + repr(weights) + '),encoding="utf-8")\n')
        # Deterministic artifact fixture, not Torch training or a scientific claim.
        with patch.object(case.example, 'postcommit_sources',
                          return_value=(source, source, claim, data, kind)):
            contract = case.build(zero=True)
        store = case.store
        routes = {r['manifest']['id']: r['manifest'] for r in contract['advisor_policy']['routes']}
        store.register(routes['candidate'])
        result = store.execute('candidate')
        self.assertEqual(result['run_status'], 'SUCCEEDED', result)
        store.register(routes['confirmation'])
        return case, store, contract

    def test_cold_reader_import_never_loads_decision_writer_or_model(self):
        code = ('import sys;sys.path.insert(0,sys.argv[1]);import rds_confirmation_read;'
                'assert not {"rds_project", "rds_owned_advisor", "rds_domain_confirmation",'
                '"rds_postcommit_confirmation", "rds_quick", "rds_cli", "torch"}.intersection(sys.modules)')
        proc = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT / 'scripts')],
                              capture_output=True, timeout=15)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_old_read_entries_are_reexports(self):
        for name in ('_ref', '_json', '_output'):
            self.assertIs(getattr(domain, name), getattr(reader, name))
        for name in ('enabled', 'validate_claim', '_candidate', 'records', 'check_run'):
            self.assertIs(getattr(post, name), getattr(reader, name))

    def test_unrelated_contract_does_not_touch_storage(self):
        self.assertEqual(reader.records(object(), object(), {}), [])
        self.assertIsNone(reader.check_run(object(), object(), {}, {}))

    def test_original_reader_preserves_root_hash_and_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = SimpleNamespace(root=Path(tmp).resolve())
            path = store.root / 'result.json'
            raw = b'{"value":1}'
            path.write_bytes(raw)
            ref = {'path': path.name, 'sha256': hashlib.sha256(raw).hexdigest(), 'size': len(raw)}
            self.assertEqual(originals._read_original(store, ref, keep=True), raw)
            self.assertIsNone(originals._read_original(store, ref, keep=True, max_bytes=1))
            with self.assertRaisesRegex(ValueError, 'escapes'):
                originals._read_original(store, dict(ref, path='../escape.json'))
            path.write_bytes(b'{"value":2}')
            with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                originals._read_original(store, ref, keep=True)

    def test_real_checks_neither_reseed_nor_change_original_state(self):
        case, store, contract = self.fixture()
        before = store.snapshot()
        event = case.events()[0]
        with patch.object(post.secrets, 'token_hex', side_effect=AssertionError('read tried to reseed')), \
                patch.object(post, 'check_run', side_effect=AssertionError('legacy writer callback')):
            with store._db(True) as db:
                db.execute('BEGIN')
                self.assertEqual(reader.records(store, db, contract), [event])
                self.assertEqual(reader._candidate(store, db, contract,
                                 contract['advisor_policy']['confirmation'])[0], before['receipts'][0])
            with store._db() as db:
                db.execute('BEGIN IMMEDIATE')
                store._check_start(db, store._run(db, 'confirmation'))
        self.assertEqual(store.snapshot(), before)
        store.recover('candidate')
        self.assertEqual(case.events(), [event])

    def test_weight_and_challenge_bytes_must_still_match_originals(self):
        case, store, contract = self.fixture()
        before = store.snapshot()
        event = case.events()[0]
        for ref in (event['weights'], event['challenge']):
            path = store.root / ref['path']
            raw = path.read_bytes()
            try:
                path.write_bytes(b'!' + raw[1:])
                with store._db(True) as db:
                    db.execute('BEGIN')
                    with self.assertRaises(ValueError):
                        reader.records(store, db, contract)
            finally:
                path.write_bytes(raw)
        with store._db(True) as db:
            db.execute('BEGIN')
            self.assertEqual(reader.records(store, db, contract), [event])
        self.assertEqual(store.snapshot(), before)

    def test_duplicate_challenge_is_rejected_not_reselected(self):
        case, store, contract = self.fixture()
        original = case.events()[0]
        with store._db() as db:
            db.execute('INSERT INTO events(body) VALUES (?)', (json.dumps(original),))
        with store._db(True) as db:
            db.execute('BEGIN')
            with self.assertRaisesRegex(ValueError, 'reseeding is forbidden'):
                reader.records(store, db, contract)
        self.assertEqual(len(case.events()), 2)

    def test_bound_run_cannot_swap_challenge_or_route(self):
        case, store, contract = self.fixture()
        with store._db(True) as db:
            db.execute('BEGIN')
            original = store._run(db, 'confirmation')
            for key in ('confirmation_challenge', 'manifest_sha256'):
                changed = deepcopy(original)
                changed[key] = {} if key == 'confirmation_challenge' else '0' * 64
                with self.subTest(field=key), self.assertRaisesRegex(ValueError, 'original post-commit challenge'):
                    reader.check_run(store, db, contract, changed)


if __name__ == '__main__':
    unittest.main()
