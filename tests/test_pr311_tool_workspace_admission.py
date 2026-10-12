"""Tool filesystem publication requires fresh workspace admission."""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_tools as tools
import rds_workspace as workspace
from rds_project import ProjectStore
import test_rds_native_research as native
import test_pr301_descendant_binding as fixture


class ToolWorkspaceAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.DescendantBindingTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        temporary = tempfile.TemporaryDirectory(prefix='rds-tool-bind-')
        self.addCleanup(temporary.cleanup)
        self.parent = Path(temporary.name).resolve()
        self.root = self.parent / 'native-tools'
        self.root.mkdir()
        source, self.cases = self.root / 'source.py', self.root / 'cases.json'
        source.write_text(native.SOURCE, encoding='utf-8')
        self.cases.write_text(json.dumps(native.CASES), encoding='utf-8')
        tools.extract(self.root, source, 'decide', 'compare-v1')
        self.validation = tools.validate(self.root, 'compare-v1', self.cases)
        self.assertEqual(self.validation['status'], 'LOCAL_CASES_PASSED')
        for binding in self.f.helper.contract['bindings']:
            shutil.copyfile(self.f.root / binding['path'], self.parent / binding['path'])
        ProjectStore(self.parent).initialize(self.f.helper.contract)

    def test_cli_registration_refuses_before_installing_module(self):
        workspace.bind(self.parent)
        result = subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.root),
             'rsi', 'register', '--name', 'compare-v1', '--validation', self.validation['id']],
            capture_output=True, text=True, timeout=15, env={**os.environ, 'RDS_USAGE_LOG': '0'})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('identity does not match', result.stderr + result.stdout)
        self.assertFalse((self.root / '.rds/tools').exists())

    def test_tool_export_rechecks_binding_after_registration(self):
        tools.register(self.root, 'compare-v1', self.validation['id'])
        register = tools.register

        def bind_after_registration(*args, **kwargs):
            result = register(*args, **kwargs)
            workspace.bind(self.parent)
            return result

        args = SimpleNamespace(action='use', root=self.root, name='compare-v1', output='exported.py')
        with patch.object(tools, 'register', side_effect=bind_after_registration):
            with self.assertRaisesRegex(ValueError, 'identity does not match'):
                tools.command(args)
        self.assertFalse((self.root / 'exported.py').exists())

    def test_ancestor_bind_refuses_active_native_tool_tail_and_preserves_ledgers(self):
        self.cases.write_text('[{"args":[0,3],"expected":true}]', encoding='utf-8')
        begin = tools._begin_preparation
        frozen = {}
        parent_before = []

        def bind_after_preparation(store, request):
            result = begin(store, request)
            self.assertIsNone(tools.get(self.root, request['validation_id']))
            parent_before.append(self.f.dump(ProjectStore(self.parent)))
            # Pin real read-only WAL readers before freezing all file bytes.
            for path in self.root.rglob('*.sqlite3'):
                reader = self.enterContext(closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)))
                reader.execute('BEGIN')
                reader.execute('SELECT name FROM sqlite_master').fetchall()
            # The real intent makes ancestor binding refuse before publication.
            frozen.update({str(path.relative_to(self.root)): path.read_bytes()
                           for path in self.root.rglob('*') if path.is_file()})
            workspace.bind(self.parent)
            return result

        with patch.object(tools, '_begin_preparation', side_effect=bind_after_preparation) as preparation, \
                patch.object(tools, 'execute', wraps=tools.execute) as launch:
            with self.assertRaisesRegex(ValueError, 'active descendant.*resolved tool preparation'):
                tools.validate(self.root, 'compare-v1', self.cases)
        self.assertEqual(preparation.call_count, 1)
        launch.assert_not_called()
        self.assertTrue(frozen)
        self.assertEqual(parent_before, [self.f.dump(ProjectStore(self.parent))])
        self.assertEqual({str(path.relative_to(self.root)): path.read_bytes()
                          for path in self.root.rglob('*') if path.is_file()}, frozen)


if __name__ == '__main__':
    unittest.main()
