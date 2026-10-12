"""The original QUICK settlement implementation must not depend on its CLI."""
import ast
import hashlib
import inspect
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
    "_policy_route": "ec63cf6cf9cf7bb9f7daf148bff7162601e6a5acb3a765586515c3f8ba160e8f",
    "_charge_ledger": "422fd6d632ee74ec41f9ec2897f11575e4d7e8b8015384bdc03207e03e120e63",
    "_checkpoint_name": "ab5859663a2246e0722e5498b974d449a3dcb6bee523f3f169f22ce6e9ce75c0",
    "_validate_recorded_choice": "f9a8bfea2ba3fcddab3661493731f3f736f9cd775d49306861e63550c57d65bf",
    "_pending_quick_choices": "5b4b88f88ed03951e19882e0e2c9ae32a645194a4b81cf24727d6195876a5896",
    "_publish_quick_request": "9a86d52d9e844c01bcbdc85cb41dcc06055fefb580720ab662429a0c2528cbc7",
    "_pending_partial_child": "d29bc90c9ec7915393ead8cabe2f2a5b30c38271aaed5c1e17d425a96daeceab",
    "_is_unmaterialized_allowance": "8b343b827acfb76b4ab759186e42bd046279cb06b7a11261c58330dead81747c",
    "_prospective_completion": "de3b3204ca36f0011a97032095d565c41380b7bd6678c368b0af994d325dcb83",
    "_complete_prospective": "46d1382e6458f630fac4bca33e2342597246c152bc34079b50f713a94892bdc6",
    "_recover_prospective": "dc652da89a40280252b59dfc9a82b21d3db42140773b19e3a466beba37e4497d"
}


class QuickLedgerServiceTests(unittest.TestCase):
    def test_unchanged_implementation_and_compatibility_aliases(self):
        self.assertEqual(len(OLD_AST), 11)
        for name, expected in OLD_AST.items():
            function = getattr(ledger, name)
            self.assertIs(getattr(quick, name), function)
            node = ast.parse(inspect.getsource(function)).body[0]
            for child in ast.walk(node):
                if hasattr(child, 'type_params'):
                    del child.type_params
            self.assertEqual(hashlib.sha256(ast.dump(node, include_attributes=False).encode()).hexdigest(), expected, name)

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
