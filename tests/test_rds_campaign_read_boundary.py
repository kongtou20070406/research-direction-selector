"""Real campaign readers do not import Store, writers, or planning modules."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'scripts'), str(ROOT/'tests')]
import rds_campaign as campaign
import test_rds_campaign_binding as fixtures


class CampaignReadBoundaryTests(unittest.TestCase):
    def setUp(self):
        fixture = fixtures.CampaignBindingTests('runTest')
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        self.fixture = fixture

    def read(self, root, kind='project'):
        code = '''import json,sys
from pathlib import Path
import rds_campaign as campaign
try:
    result={'binding':campaign.enforce(Path(sys.argv[1]),kind=sys.argv[2])}
except (ValueError,OSError) as exc:
    result={'error':str(exc)}
forbidden={'rds_project','rds_method_revision','rds_owned_advisor','rds_quick','rds_cli'}
assert not forbidden.intersection(sys.modules), sorted(forbidden.intersection(sys.modules))
print(json.dumps(result))
'''
        env = dict(os.environ)
        env.pop(campaign.ENVIRONMENT, None)
        result = subprocess.run([sys.executable, '-B', '-c', code, str(root), kind],
                                cwd=ROOT/'scripts', env=env, capture_output=True,
                                text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def test_unbound_read_needs_no_store_or_side_effects(self):
        root = self.fixture.workspace/'unbound'
        root.mkdir()
        self.assertEqual(self.read(root), {'binding': None})
        self.assertEqual(list(root.iterdir()), [])

    def test_bound_ledger_read_and_reference_refusal_keep_original_identity(self):
        f = self.fixture
        expected = campaign.bind(f.store, f.workspace)
        before = f.store.path.read_bytes(), f.marker.read_bytes()
        self.assertEqual(self.read(f.root), {'binding': expected})
        self.assertIn('canonical project ledger', self.read(f.root, 'reference')['error'])
        self.assertEqual((f.store.path.read_bytes(), f.marker.read_bytes()), before)

    def test_missing_binding_event_is_not_replaced_or_accepted(self):
        f = self.fixture
        campaign.bind(f.store, f.workspace)
        f.native_update("DELETE FROM events WHERE json_extract(body,'$.kind')=?", (campaign.KIND,))
        before = f.store.path.read_bytes(), f.marker.read_bytes()
        self.assertIn('error', self.read(f.root))
        self.assertEqual((f.store.path.read_bytes(), f.marker.read_bytes()), before)

    def test_corrupt_genesis_is_rejected_by_original_lineage_reader(self):
        f = self.fixture
        campaign.bind(f.store, f.workspace)
        f.native_update('DROP TRIGGER contract_no_update')
        f.native_update('UPDATE contract SET sha256=? WHERE id=1', ('0'*64,))
        before = f.store.path.read_bytes(), f.marker.read_bytes()
        self.assertIn('error', self.read(f.root))
        self.assertEqual((f.store.path.read_bytes(), f.marker.read_bytes()), before)


if __name__ == '__main__':
    unittest.main()
