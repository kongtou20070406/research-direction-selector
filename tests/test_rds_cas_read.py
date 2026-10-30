"""A CAS read returns the bound bytes or nothing: never a sibling file, never a near match."""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_cas_read as cas_read


class CasReadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rds-cas-read-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cas = self.root / '.rds' / 'cas'
        self.cas.mkdir(parents=True)

    def store(self, raw):
        sha = hashlib.sha256(raw).hexdigest()
        (self.cas / (sha + '.bin')).write_bytes(raw)
        return {'path': '.rds/cas/' + sha + '.bin', 'sha256': sha, 'bytes': len(raw)}

    def test_bound_blob_is_returned(self):
        ref = self.store(b'evidence')
        self.assertEqual(cas_read.blob(self.root, ref), b'evidence')

    def test_malformed_binding_is_refused(self):
        ref = self.store(b'evidence')
        for bad in (None, {**ref, 'sha256': ref['sha256'].upper()}, {**ref, 'sha256': ref['sha256'][:63]}):
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, 'Invalid research blob binding'):
                cas_read.blob(self.root, bad)

    def test_a_path_outside_the_cas_directory_is_refused(self):
        ref = self.store(b'evidence')
        (self.root / (ref['sha256'] + '.bin')).write_bytes(b'evidence')
        nested = self.cas / 'nested'
        nested.mkdir()
        (nested / (ref['sha256'] + '.bin')).write_bytes(b'evidence')
        renamed = self.cas / 'other.bin'
        renamed.write_bytes(b'evidence')
        # The same bytes under the right digest still fail when the file is not the CAS entry itself.
        for path in (ref['sha256'] + '.bin', '.rds/cas/nested/' + ref['sha256'] + '.bin',
                     '.rds/cas/../cas/nested/' + ref['sha256'] + '.bin', '.rds/cas/other.bin'):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'outside its CAS'):
                cas_read.blob(self.root, {**ref, 'path': path})

    def test_digest_or_length_mismatch_is_an_integrity_failure(self):
        ref = self.store(b'evidence')
        (self.cas / (ref['sha256'] + '.bin')).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'integrity failure'):
            cas_read.blob(self.root, ref)
        ref = self.store(b'evidence')
        with self.assertRaisesRegex(ValueError, 'integrity failure'):
            cas_read.blob(self.root, {**ref, 'bytes': ref['bytes'] + 1})

    def test_reader_stops_one_byte_past_the_limit(self):
        path = self.root / 'large.bin'
        path.write_bytes(b'x' * 9)
        with patch.object(cas_read, 'MAX_BYTES', 8):
            with self.assertRaisesRegex(ValueError, 'exceeds 8 MiB'):
                cas_read.read_bytes(path)
            path.write_bytes(b'x' * 8)
            self.assertEqual(cas_read.read_bytes(path), b'x' * 8)


if __name__ == '__main__':
    unittest.main()
