"""CAS extraction preserves byte identity, bounds and publication admission."""
import ast
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_cas as cas
import rds_cli as cli
import rds_quick as quick

# Exact original function ASTs from parent e6f22e2, including @mutation().
OLD_AST = {
    "cas_json": "e4399ccfe6995d4fc09299c33a503e2c4b9c0c4ad15dbd2e1d2944e3e910a3cc",
    "cas_bytes": "a0e9424af3e56b3ac06881665829bf4a31cbb378ae09f08bb2d4765daf127f43"
}


def frozen_ast(node):
    # ast.dump omits empty optional fields on newer supported interpreters.
    if isinstance(node, ast.AST):
        return [type(node).__name__, [[key, frozen_ast(value)]
                for key, value in ast.iter_fields(node) if key != 'type_params' or value]]
    if isinstance(node, list):
        return [frozen_ast(value) for value in node]
    return node


class CasServiceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        env = patch.dict(os.environ, RDS_USAGE_DB=str(self.root / 'usage.sqlite3'))
        env.start()
        self.addCleanup(env.stop)

    def test_original_asts_and_public_aliases(self):
        for name, expected in OLD_AST.items():
            function = getattr(cas, name)
            self.assertIs(function, getattr(quick, name))
            node = ast.parse(inspect.getsource(function)).body[0]
            self.assertEqual(hashlib.sha256(json.dumps(frozen_ast(node), ensure_ascii=True, separators=(',', ':')).encode('ascii')).hexdigest(), expected)

    def test_original_bytes_json_and_existing_integrity(self):
        raw = b'\x00\xffpayload'
        ref = cas.cas_bytes(self.root, raw)
        self.assertEqual(ref['sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(Path(ref['path']).read_bytes(), raw)
        self.assertEqual(cas.cas_bytes(self.root, raw), ref)
        record = cas.cas_json(self.root, {'b': 2, 'a': 1})
        self.assertEqual(Path(record['path']).read_bytes(), b'{"a":1,"b":2}')
        Path(ref['path']).write_bytes(b'corrupt fixture')
        with self.assertRaisesRegex(ValueError, 'CAS integrity failure'):
            cas.cas_bytes(self.root, raw)

    def test_limits_do_not_publish(self):
        for cap in (0, True, 1.5):
            with self.subTest(cap=cap), self.assertRaisesRegex(ValueError, 'Invalid CAS JSON publication byte limit'):
                cas.cas_json(self.root, {}, max_bytes=cap)
        with self.assertRaisesRegex(ValueError, 'CAS JSON exceeds'):
            cas.cas_json(self.root, {'large': 'abc'}, max_bytes=2)
        self.assertFalse((self.root / '.rds/cas').exists())

    def test_both_original_guards_precede_publication(self):
        for guard in ('rds_campaign.enforce', 'rds_workspace.check_admission'):
            with self.subTest(guard=guard), patch(guard, side_effect=ValueError('denied')) as checked:
                with self.assertRaisesRegex(ValueError, 'denied'):
                    cas.cas_bytes(self.root, b'no publication')
                checked.assert_called_once_with(self.root)
                self.assertFalse((self.root / '.rds/cas').exists())

    def test_engine_identity_covers_extracted_implementation(self):
        before = cli.engine_id()
        original = Path.read_bytes
        def changed(path):
            raw = original(path)
            return raw + b'\n# fixture\n' if path.name == 'rds_cas.py' else raw
        with patch.object(Path, 'read_bytes', changed):
            self.assertNotEqual(cli.engine_id(), before)


if __name__ == '__main__':
    unittest.main()
