"""Output extraction preserves persistence, projections and old QUICK names."""
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
import rds_cli as cli
import rds_output_values as values
import rds_quick as quick
import rds_result_record as records

# Unmodified original definition ASTs from 0b65049; normalize empty type_params.
OLD_AST = {
    '_brief_move': '3bdf21d996ebe55c0213c3079b2203414e21ccd6fe3c14b0a3c6987fd57d82ca',
    'serialize_brief': '875584f19b3d0238c2f638d4394e613f7020bd7792c2b9ad48a381d1d1f3cc04',
    'compact_brief_planning': '609e4f8ad2649aaf380a98814e1f7e8f35adfe1a519d21fa082a8aa8cae2d3fd',
    'brief': 'f65fa968e76f89e1c6fed6a3e6396315607320dcd906d63cbc4f5d6c9e8639e5',
}


class ResultRecordTests(unittest.TestCase):
    def test_exact_old_bodies_and_aliases(self):
        for name, expected in OLD_AST.items():
            function = getattr(records if name == 'brief' else values, name)
            self.assertIs(getattr(quick, name), function)
            node = ast.parse(inspect.getsource(function)).body[0]
            for child in ast.walk(node):
                if hasattr(child, 'type_params'):
                    del child.type_params
            self.assertEqual(hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest(), expected, name)

    def test_full_record_is_preserved_and_summary_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, RDS_USAGE_DB=str(Path(folder) / 'usage.db')):
            value = {'status': 'UNKNOWN', 'raw': 'x' * 10000, 'scientific_support': 'UNKNOWN'}
            summary = records.brief(folder, value, cli.VERSION)
            self.assertEqual(summary['status'], 'UNKNOWN')
            path = Path(summary['record'])
            self.assertEqual(json.loads(path.read_text(encoding='utf-8')), value)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), summary['sha256'])
            self.assertNotIn('raw', summary)
            self.assertLess(len(values.serialize_brief(summary).encode()), 1024)

    def test_pure_formatter_has_only_standard_library_imports(self):
        tree = ast.parse(Path(values.__file__).read_text(encoding='utf-8'))
        imports = {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        imports.update(node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom))
        self.assertEqual(imports, {'json', 'os'})
        self.assertEqual(values.serialize_brief({'status': 'UNKNOWN'}, newline=True),
                         '{"status":"UNKNOWN"}' + os.linesep)

    def test_engine_covers_both_extracted_files(self):
        before = cli.engine_id()
        original = Path.read_bytes
        for name in ('rds_output_values.py', 'rds_result_record.py'):
            def changed(path):
                raw = original(path)
                return raw + b'\n# fixture\n' if path.name == name else raw
            with self.subTest(name=name), patch.object(Path, 'read_bytes', changed):
                self.assertNotEqual(cli.engine_id(), before)


if __name__ == '__main__':
    unittest.main()
