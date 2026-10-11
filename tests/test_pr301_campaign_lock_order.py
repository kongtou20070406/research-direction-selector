"""Campaign writer contexts acquire mutation before SQLite and retain it."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'tests'))
import rds_campaign as campaign
import rds_mutation
import test_rds_project


class CampaignLockOrderTests(unittest.TestCase):
    def setUp(self):
        helper = test_rds_project.ProjectTests()
        helper.setUp()
        self.addCleanup(helper.tearDown)
        self.root = helper.root

    def gate_available_to_other_thread(self):
        def probe():
            acquired = rds_mutation._gate.acquire(blocking=False)
            if acquired:
                rds_mutation._gate.release()
            return acquired
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(probe).result(timeout=3)

    def test_campaign_writer_keeps_mutation_gate_through_transaction(self):
        with campaign._database(self.root, write=True):
            self.assertFalse(self.gate_available_to_other_thread())

    def test_campaign_readonly_context_does_not_hold_mutation_gate(self):
        with campaign._database(self.root):
            self.assertTrue(self.gate_available_to_other_thread())


if __name__ == '__main__':
    unittest.main()
