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
    "_brief_move": "bca2cd6a81d4a3ecaa6ebded6c6a4830b9b7659265bf69256f2bb176b1b23910",
    "serialize_brief": "d22424877bfea363cd918b7583fb5f133a2f552ba2142b5e8d5d21fa5e11d617",
    "compact_brief_planning": "cb5b194a09cb588cff1478d600499ce0183370d2494abaca0c2069abf609cec8",
    "brief": "df56687c1baeb30f47d3429729f3e926b5b15bed9d09c05b584d7d34f2283663"
}


def frozen_ast(node):
    # ast.dump omits empty optional fields on newer supported interpreters.
    if isinstance(node, ast.AST):
        return [type(node).__name__, [[key, frozen_ast(value)]
                for key, value in ast.iter_fields(node) if key != 'type_params' or value]]
    if isinstance(node, list):
        return [frozen_ast(value) for value in node]
    return node


def private_reads(node):
    # 2a75c78 renamed `store._db(True)` to its public forwarder `store.readonly()`. Undo that
    # rename and nothing else, so the hashes above still pin the original bodies.
    for call in ast.walk(node):
        if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                and call.func.attr == 'readonly' and not call.args and not call.keywords):
            call.func.attr = '_db'
            call.args = [ast.Constant(value=True, kind=None)]
    return node


class ResultRecordTests(unittest.TestCase):
    def test_exact_old_bodies_and_aliases(self):
        for name, expected in OLD_AST.items():
            function = getattr(records if name == 'brief' else values, name)
            self.assertIs(getattr(quick, name), function)
            node = private_reads(ast.parse(inspect.getsource(function)).body[0])
            self.assertEqual(hashlib.sha256(json.dumps(frozen_ast(node), ensure_ascii=True, separators=(',', ':')).encode('ascii')).hexdigest(), expected, name)

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
