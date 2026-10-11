"""Short native initialization admission; no workers or scientific claims."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import json
from pathlib import Path
import sys
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from rds_project import ProjectStore, file_sha
import rds_project_lifecycle as lifecycle


class ProjectAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='rds-admission-')
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.parent, self.contract = self.prepare(self.workspace / 'parent')
        self.child, self.child_contract = self.prepare(self.parent.root / 'child')

    @staticmethod
    def prepare(root):
        root.mkdir(parents=True)
        files = {'code': ('code.py', 'print("finite admission fixture")\n'),
                 'config': ('config.json', '{}'), 'data': ('data.json', '[1]'),
                 'evaluator': ('evaluator.json', '{}')}
        for name, raw in files.values():
            (root / name).write_text(raw, encoding='utf-8')
        protocol = {'code_sha256': file_sha(root / 'code.py'),
                    'config_sha256': file_sha(root / 'config.json'),
                    'data_sha256': file_sha(root / 'data.json'), 'data_split': 'synthetic',
                    'init': 'none', 'seed': 0, 'checkpoint': 'none',
                    'schedule': 'admission only', 'sample_work': {'rows': 1},
                    'numeric_protocol': 'Python integer'}
        (root / 'protocol.json').write_text(json.dumps(protocol), encoding='utf-8')
        files['protocol'] = ('protocol.json', '')
        contract = {'schema': 1,
                    'bindings': [{'role': role, 'path': name, 'sha256': file_sha(root / name)}
                                 for role, (name, _) in files.items()],
                    'allowed_commands': [[sys.executable, '-B', 'code.py']],
                    'output_roots': ['outputs'], 'budget': {'wall_seconds': 10}}
        return ProjectStore(root), contract

    def race(self, *, bottom=False, recipe=False):
        parent_ready, release_parent = Event(), Event()
        child_attempting, child_done = Event(), Event()
        original_initialize, original_db = self.parent.initialize, self.parent._db

        def pause_initialize(*args, **kwargs):
            parent_ready.set()
            if not release_parent.wait(3):
                raise RuntimeError('Parent admission test release timed out')
            return original_initialize(*args, **kwargs)

        @contextmanager
        def pause_db(readonly=False):
            if not readonly:
                parent_ready.set()
                if not release_parent.wait(3):
                    raise RuntimeError('Parent admission test release timed out')
            with original_db(readonly) as db:
                yield db

        def child_initialize():
            child_attempting.set()
            try:
                return lifecycle.initialize(self.child, self.child_contract, mode='quick')
            finally:
                child_done.set()

        assembly = None
        if recipe:
            # Compose declarations only; do not inherit or execute another suite.
            import test_rds_project_assembly as fixtures
            import rds_project_assembly as assembly
            fixture = fixtures.ProjectAssemblyTests('test_compile_preserves_declared_semantics_and_original_contract_shape')
            fixture.root, fixture.store = self.parent.root, self.parent
            fixture.write('code.py', fixtures.SCRIPT)
            (self.parent.root / 'protocol.json').unlink()
            recipe_path = fixture.write_json('recipe.json', fixture.make_recipe())
            original_compile = assembly.compile_recipe

            def pause_compile(*args, **kwargs):
                parent_ready.set()
                if not release_parent.wait(3):
                    raise RuntimeError('Recipe admission test release timed out')
                return original_compile(*args, **kwargs)

        target = 'compile_recipe' if recipe else '_db' if bottom else 'initialize'
        patched = assembly if recipe else self.parent
        callback = pause_compile if recipe else pause_db if bottom else pause_initialize
        with patch.object(patched, target, callback):
            with ThreadPoolExecutor(max_workers=2) as pool:
                parent_call = (lambda: assembly.initialize(self.parent, recipe_path)) if recipe else (
                    (lambda: self.parent.initialize(self.contract)) if bottom else (
                        lambda: lifecycle.initialize(self.parent, self.contract, mode='quick')))
                parent = pool.submit(parent_call)
                child = None
                try:
                    self.assertTrue(parent_ready.wait(3), 'Parent never entered native initialization')
                    child = pool.submit(child_initialize)
                    self.assertTrue(child_attempting.wait(3), 'Child writer never attempted initialization')
                    self.assertFalse(child_done.wait(.1), 'Child committed before parent admission completed')
                finally:
                    release_parent.set()
                parent.result(timeout=3)
                with self.assertRaisesRegex(ValueError, '--separate-project'):
                    child.result(timeout=3)
        self.assertFalse(self.child.path.exists())
        if recipe:
            self.assertEqual(lifecycle.describe(self.parent.root)['mode'], 'FULL')
            self.assertTrue((self.parent.root / 'protocol.json').is_file())
        else:
            self.assertEqual(self.parent.snapshot()['contract'], self.contract)

    def test_parent_first_public_initialization_serializes_child_discovery(self):
        self.race()

    def test_bottom_initializer_participates_in_same_admission_gate(self):
        self.race(bottom=True)

    def test_recipe_admission_holds_gate_before_protocol_publication(self):
        self.race(recipe=True)

    def test_child_first_sequential_parent_remains_valid(self):
        lifecycle.initialize(self.child, self.child_contract, mode='quick')
        before = self.child.snapshot()
        lifecycle.initialize(self.parent, self.contract, mode='quick')
        self.assertEqual(self.child.snapshot(), before)
        self.assertEqual(lifecycle.discover(self.child.root)['project_root'], str(self.child.root))

    def test_explicit_separate_child_preserves_parent_and_scope_declaration(self):
        lifecycle.initialize(self.parent, self.contract, mode='quick')
        before = self.parent.snapshot()
        reason = 'Independent finite fixture with separate declared budget'
        lifecycle.initialize(self.child, self.child_contract, mode='quick', separate_reason=reason)
        with self.child._db(True) as db:
            declarations = [json.loads(row['body']) for row in db.execute(
                "SELECT body FROM events WHERE json_extract(body,'$.kind')='PROJECT_SCOPE_DECLARED'")]
        self.assertEqual(len(declarations), 1)
        self.assertEqual(declarations[0]['reason'], reason)
        self.assertEqual(declarations[0]['ancestor_root'], str(self.parent.root))
        self.assertEqual(self.parent.snapshot(), before)

    def test_exact_ancestor_supersedes_preserves_original_ledger(self):
        lifecycle.initialize(self.parent, self.contract, mode='quick')
        before = self.parent.snapshot()
        lifecycle.initialize(self.child, self.child_contract, mode='quick', supersedes=self.parent.root)
        with self.child._db(True) as db:
            link, _ = self.child._link_record(db)
        self.assertEqual((self.child.root / link['root_path']).resolve(), self.parent.root)
        self.assertEqual(self.parent.snapshot(), before)


if __name__ == '__main__':
    unittest.main()
