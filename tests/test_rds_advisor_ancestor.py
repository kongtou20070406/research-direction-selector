"""Real CLI ancestor authority and actionable initialization rejections."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import test_rds_owned_advisor as fixture
from test_rds_theory_directions import obligation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_cli import RDSState, RESOURCES, VERSION
from rds_project import digest
from rds_tms_store import current, save


class AdvisorAncestorTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.OwnedAdvisorCLITests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)

    def inputs(self, root):
        context, graph = root / 'context.json', root / 'graph.json'
        context.write_text(json.dumps({'decision': 'next'}), encoding='utf-8')
        graph.write_text(json.dumps({'nodes': [obligation()], 'edges': []}), encoding='utf-8')
        return ('--research-context', str(context), '--graph', str(graph))

    def test_full_ancestor_rejects_child_overrides_even_with_local_native_state(self):
        self.f.initialize()
        before = self.f.snapshot()
        for kind in ('empty', 'dependencies', 'reference'):
            with self.subTest(local_scope=kind):
                child = self.f.root / kind
                child.mkdir()
                token = None
                if kind == 'dependencies':
                    token = save(child, {'schema': 1, 'nodes': [], 'hyperedges': [], 'goals': []}, expected=None)
                if kind == 'reference':
                    with RDSState(child).transaction(create=True) as (_, state):
                        state.update(version=VERSION, contract={}, contract_sha256=digest({}), plans={},
                                     budget={field: {name: 0 for name in RESOURCES}
                                             for field in ('limits', 'spent', 'reserved')})
                result = self.f.call('advise', *self.inputs(child), root=child, ok=False)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn('Advisor must use the existing FULL project root', result.stderr)
                self.assertIn(str(self.f.root), result.stderr)
                self.assertEqual(result.stdout, '')
                if token:
                    self.assertEqual(current(child)['sha256'], token)
                for key in ('contract', 'contract_sha256', 'budget', 'runs', 'receipts'):
                    self.assertEqual(self.f.snapshot()[key], before[key])

    def test_current_full_and_uninitialized_advice_remain_available(self):
        self.f.initialize()
        owned = self.f.call('advise')
        self.assertEqual(json.loads(owned.stdout)['status'], 'REVIEWED')
        self.assertIn('mode=FULL advisor=PROGRAM_OWNED', owned.stderr)
        with tempfile.TemporaryDirectory(prefix='rds-uninitialized-advisor-') as temporary:
            root = Path(temporary)
            result = self.f.call('advise', *self.inputs(root), root=root)
            report = json.loads(result.stdout)
            search = next(row['search'] for row in report['recommendations']
                          if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
            self.assertEqual(search['candidates'][0]['status'], 'READY')
            self.assertIn('mode=UNINITIALIZED', result.stderr)
            self.assertFalse((root / '.rds').exists())

    def test_missing_root_init_rejection_has_no_unexecutable_discovery_hint(self):
        contract = self.f.root / 'bad.json'
        contract.write_text('5', encoding='utf-8')
        missing = self.f.root / 'not-created'
        result = self.f.call('project', 'init', '--contract', str(contract), root=missing, ok=False)
        self.assertEqual(result.returncode, 1)
        self.assertIn('[RDS-REJECT]', result.stderr)
        self.assertNotIn('project discover', result.stderr)
        self.assertFalse(missing.exists())


if __name__ == '__main__':
    unittest.main()
