"""Rejected workspace writes must not leave new CAS publications behind."""
import os
import json
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_workspace as workspace
from rds_quick import cas_bytes, cas_json
import test_pr301_descendant_binding as fixture


class CasWorkspaceAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.DescendantBindingTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.child = self.f.child()

    def test_direct_cas_publications_refuse_inherited_binding(self):
        workspace.bind(self.f.root)
        before = self.f.dump(self.child)
        for publish in (lambda: cas_bytes(self.child.root, b'new raw asset'),
                        lambda: cas_json(self.child.root, {'new': 'asset'})):
            with self.subTest(publish=publish):
                with self.assertRaisesRegex(ValueError, 'identity does not match'):
                    publish()
        self.assertFalse((self.child.root / '.rds/cas').exists())
        self.assertEqual(self.f.dump(self.child), before)

    def test_real_math_add_refusal_leaves_no_blob(self):
        from rds_math import bind_objective
        from rds_project import ProjectStore
        import test_rds_native_research as native_fixture
        root = self.f.root / 'native-assets'
        root.mkdir()
        bind_objective(root, json.dumps(native_fixture.GOAL).encode('utf-8'))
        self.child = ProjectStore(root)
        source = self.child.root / 'asset.txt'
        source.write_text('bounded research note', encoding='utf-8')
        workspace.bind(self.f.root)
        before = self.f.dump(self.child)
        cas = self.child.root / '.rds/cas'
        before_blobs = {path.name: path.read_bytes() for path in cas.iterdir()}
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.child.root),
             'math', 'add', '--id', 'refused-note', '--kind', 'note', '--file', str(source)],
            capture_output=True, text=True, timeout=15, env={**os.environ, 'RDS_USAGE_LOG': '0'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('identity does not match', result.stderr + result.stdout)
        self.assertEqual({path.name: path.read_bytes() for path in cas.iterdir()}, before_blobs)
        self.assertEqual(self.f.dump(self.child), before)

    def test_unbound_and_canonical_cas_publication_remain_supported(self):
        from rds_math import put, blob
        self.assertEqual(Path(cas_bytes(self.child.root, b'unbound asset')['path']).read_bytes(), b'unbound asset')
        workspace.bind(self.f.root)
        ref = cas_json(self.f.root, {'canonical': True})
        self.assertTrue(Path(ref['path']).is_file())
        record = put(self.f.root, 'canonical-note', 'note', b'canonical note')
        self.assertEqual(blob(self.f.root, record['asset']), b'canonical note')
        self.assertEqual(workspace.coverage(self.f.root)['status'], 'BOUND')
