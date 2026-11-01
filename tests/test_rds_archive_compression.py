"""Archive byte stability across supported gzip runtime variants."""
import base64
import gzip
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import rds_hypergraph_archive as archive


class ArchiveCompressionTests(unittest.TestCase):
    def test_host_os_header_does_not_change_packed_bytes(self):
        original = gzip.compress
        for raw in (b"", b"archive\r\n" * 128, bytes(range(256))):
            expected = archive._packed(raw)
            for host in (0, 3, 255):
                with self.subTest(size=len(raw), host=host):
                    def host_compress(data, compresslevel=6, *, mtime=None):
                        packed = bytearray(original(data, compresslevel=compresslevel, mtime=mtime))
                        packed[9] = host
                        return bytes(packed)

                    with patch.object(archive.gzip, "compress", side_effect=host_compress) as compress:
                        actual = archive._packed(raw)
                    compress.assert_called_once_with(raw, compresslevel=9, mtime=0)
                    self.assertEqual(actual, expected)
                    packed = base64.b64decode(actual)
                    self.assertEqual(packed[9], 255)
                    self.assertEqual(packed[4:8], b"\x00" * 4)
                    self.assertEqual(gzip.decompress(packed), raw)


if __name__ == "__main__":
    unittest.main()
