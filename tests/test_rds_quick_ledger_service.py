"""The original QUICK settlement implementation must not depend on its CLI."""
import ast
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_cli as cli
import rds_quick as quick
import rds_quick_ledger as ledger

# Generated from the original functions in 65d45a5, with empty type_params removed.
OLD_AST = {
    "_policy_route": "216d5ff5ab887b967a2bab687186366799d77480e56ba91d39eb5105cf0b5797",
    "_charge_ledger": "4d882fdd8aba3d00751894e5bc1b21f6875e2ed804cbdb776e7cd080dfb63efd",
    "_checkpoint_name": "8acef498db59fbc1d3153d3429f1fe2aa27c10fac61d9194e3372c3e45d5b7d3",
    "_validate_recorded_choice": "16e17b54b7463d6cc6e01fea37367608197946b7a463edd131a01821a33c414f",
    "_pending_quick_choices": "313a028e5aeafc7163091ed2f107195dc46180440d4416cbc795c77604fd3ca3",
    "_publish_quick_request": "69ea4b3a3bfd415112ae30d8f46f77961b2a7922cff10e09a1cf44908955d46e",
    "_pending_partial_child": "46834f45ce7e59707df5d037b768d6117071d41e6d931325492026e8e3ffdf26",
    "_is_unmaterialized_allowance": "f68b0cdb0e93cc7d20584dbe72fafe31a2c6f70403855d266dfe726e209206a6",
    "_prospective_completion": "4cc98679e905a7a8b8fe9f40fc2220df0f82c2407da62c24ecc057e7fa88d8f8",
    "_complete_prospective": "37c4003f44c62eb691f6216cbef28ddd27ea242574ed6a781e07bf9f45dd83e4",
    "_recover_prospective": "297038ee002edbac878468e678aba42221639a8d20f9b0f13d22bf09b2d589ab"
}


def frozen_ast(node):
    # ast.dump omits empty optional fields on newer supported interpreters.
    if isinstance(node, ast.AST):
        return [type(node).__name__, [[key, frozen_ast(value)]
                for key, value in ast.iter_fields(node) if key != 'type_params' or value]]
    if isinstance(node, list):
        return [frozen_ast(value) for value in node]
    return node


class QuickLedgerServiceTests(unittest.TestCase):
    def test_unchanged_implementation_and_compatibility_aliases(self):
        self.assertEqual(len(OLD_AST), 11)
        for name, expected in OLD_AST.items():
            function = getattr(ledger, name)
            self.assertIs(getattr(quick, name), function)
            node = ast.parse(inspect.getsource(function)).body[0]
            self.assertEqual(hashlib.sha256(json.dumps(frozen_ast(node), ensure_ascii=True, separators=(',', ':')).encode('ascii')).hexdigest(), expected, name)

    def test_new_service_does_not_import_upper_layers(self):
        tree = ast.parse(Path(ledger.__file__).read_text(encoding='utf-8'))
        imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        imports.update(a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names)
        self.assertLessEqual({name for name in imports if name.startswith('rds_')},
                            {'rds_project', 'rds_project_values', 'rds_mutation', 'rds_project_hooks',
                             'rds_workspace', 'rds_campaign', 'rds_steering', 'rds_checkpoints', 'rds_guard'})

    def test_recovery_provider_loads_without_quick_or_advisor(self):
        code = """import sys
sys.path.insert(0,sys.argv[1])
from rds_project_hooks import provider
value=provider('quick')
assert value.__name__ == 'rds_quick_ledger'
assert value._recover_prospective(None, {}, None) is None
assert 'rds_quick' not in sys.modules
assert 'rds_cli' not in sys.modules
assert 'rds_owned_advisor' not in sys.modules
"""
        run = subprocess.run([sys.executable, '-B', '-c', code, str(ROOT / 'scripts')],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_incomplete_attempt_does_not_publish_completion(self):
        with patch.object(ledger, '_prospective_completion', side_effect=AssertionError('not complete')):
            self.assertIsNone(ledger._complete_prospective(None, {}, {'status': 'RUNNING'}))
            self.assertIsNone(ledger._recover_prospective(None, {}, {'status': 'RUNNING'}))

    def test_engine_hash_covers_ledger_implementation(self):
        before = cli.engine_id()
        original = Path.read_bytes
        def changed(path):
            raw = original(path)
            return raw + b'\n# fixture\n' if path.name == 'rds_quick_ledger.py' else raw
        with patch.object(Path, 'read_bytes', changed):
            self.assertNotEqual(cli.engine_id(), before)


if __name__ == '__main__':
    unittest.main()
