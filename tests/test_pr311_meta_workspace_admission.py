"""Refused workspaces cannot adopt rules or consume confirmation freshness."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_workspace as workspace
from rds_host_hook import install
from rds_rsi import evaluate_candidate
import test_rds_rsi as rsi_fixture
import test_pr301_descendant_binding as fixture


class MetaWorkspaceAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.DescendantBindingTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.child = self.f.child()
        graph, rule, cases = rsi_fixture.fixture()
        report = evaluate_candidate(rule, graph, cases)
        self.assertTrue(report['adoption_eligible'])
        for name, value in [('graph', graph), ('rule', rule), ('cases', cases), ('evaluation', report)]:
            (self.child.root / (name + '.json')).write_text(json.dumps(value), encoding='utf-8')

    def call(self, *args):
        return subprocess.run(
            [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(self.child.root), *args],
            capture_output=True, text=True, timeout=15, env={**os.environ, 'RDS_USAGE_LOG': '0'})

    def meta(self, action, *extra):
        return self.call('meta', action, '--graph', str(self.child.root / 'graph.json'),
                         '--rule', str(self.child.root / 'rule.json'),
                         '--cases', str(self.child.root / 'cases.json'), *extra)

    def test_apply_rule_cannot_publish_records_or_replace_graph(self):
        workspace.bind(self.f.root)
        graph = self.child.root / 'graph.json'
        before = graph.read_bytes(), self.f.dump(self.child)
        result = self.meta('apply-rule', '--evaluation', str(self.child.root / 'evaluation.json'))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('identity does not match', result.stderr + result.stdout)
        self.assertEqual((graph.read_bytes(), self.f.dump(self.child)), before)
        self.assertFalse((self.child.root / '.rds/rsi').exists())

    def test_evaluate_rule_does_not_consume_confirmation_freshness(self):
        workspace.bind(self.f.root)
        result = self.meta('evaluate-rule')
        self.assertNotEqual(result.returncode, 0)
        report = json.loads(result.stdout)
        self.assertEqual(report['status'], 'REJECTED')
        self.assertIn('identity does not match', ' '.join(report['rejection_reasons']))
        self.assertFalse((self.child.root / '.rds/rsi').exists())

    def test_host_install_refuses_new_and_existing_configuration(self):
        install(self.child.root)
        guard = self.child.root / '.rds/host-guard.json'
        before = guard.read_bytes()
        other = self.f.child('other')
        workspace.bind(self.f.root)
        for child in (self.child, other):
            with self.subTest(root=child.root):
                with self.assertRaisesRegex(ValueError, 'identity does not match'):
                    install(child.root, strict=False)
        self.assertEqual(guard.read_bytes(), before)
        self.assertFalse((other.root / '.rds/host-guard.json').exists())


if __name__ == '__main__':
    unittest.main()
