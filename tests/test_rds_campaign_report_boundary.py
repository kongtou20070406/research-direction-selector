"""Retained report size is the CAS read bound, without a success promotion."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import rds_campaign as campaign
from rds_dialogue import _report
from rds_project import ProjectStore
from rds_quick import cas_json
import test_rds_campaign_tail_settlement as tails


class CampaignReportBoundaryTests(unittest.TestCase):
    def test_large_original_cas_report_matches_dialogue_without_changing_status(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            value = {'status':'INCOMPLETE_ANALYSIS', 'scientific_support':'UNKNOWN',
                     'padding':'x' * (8 * 1024 * 1024 + 1)}
            ref = cas_json(root, value)
            self.assertGreater(ref['bytes'], 8 * 1024 * 1024)
            before = (root / ref['path']).read_bytes()
            self.assertEqual(campaign._json_cas(root, ref), value)
            self.assertEqual(_report(ProjectStore(root), ref), value)
            self.assertEqual((root / ref['path']).read_bytes(), before)

    def test_invalid_sizes_paths_hashes_and_strict_json_remain_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            ref = cas_json(root, {'status':'COLLECTION_FAILED'})
            for size in (False, 0, -1, ref['bytes'] - 1, ref['bytes'] + 1, 10**100):
                with self.subTest(size=size), self.assertRaises(ValueError):
                    campaign._json_cas(root, {**ref, 'bytes':size})
            with self.assertRaises(ValueError):
                campaign._json_cas(root, {**ref, 'path':'outside.json'})
            path = root / ref['path']
            path.write_bytes(b'x' * ref['bytes'])
            with self.assertRaisesRegex(ValueError, 'integrity'):
                campaign._json_cas(root, ref)
            for raw in (b'{"status":1,"status":2}', b'{"n":NaN}'):
                sha = hashlib.sha256(raw).hexdigest()
                path = root / '.rds/cas' / (sha + '.json')
                path.write_bytes(raw)
                with self.assertRaises(ValueError):
                    campaign._json_cas(root, {'path':str(path), 'sha256':sha, 'bytes':len(raw)})

    def test_post_stat_growth_is_detected_by_recorded_size_plus_one_read(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            ref = cas_json(root, {'status':'UNKNOWN'})
            path = (root / ref['path']).resolve()
            original_open = Path.open
            def grow(target, *args, **kwargs):
                if target == path and args == ('rb',):
                    with original_open(target, 'ab') as stream:
                        stream.write(b' ')
                return original_open(target, *args, **kwargs)
            with patch.object(Path, 'open', grow), self.assertRaisesRegex(ValueError, 'integrity'):
                campaign._json_cas(root, ref)

    def test_actual_foreign_non_success_settlement_accepts_large_original_report_without_rerun(self):
        helper = tails.CampaignTailSettlementTests('runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        import rds_owned_advisor as owned
        import rds_quick
        original_cas = rds_quick.cas_json
        def large(root, report):
            report = deepcopy(report)
            report['retained_detail'] = 'x' * (8 * 1024 * 1024 + 1)
            return original_cas(root, report)
        with patch.object(rds_quick, 'cas_json', large):
            foreign, store, receipt = helper.foreign_owned(mode='badjson')
        self.assertEqual(store.last_advisor_review['status'], 'COLLECTION_FAILED')
        before = store.snapshot()
        campaign.bind(helper.f.store, helper.f.workspace)
        after = store.snapshot()
        self.assertEqual(after['receipts'], before['receipts'])
        self.assertEqual(after['budget'], before['budget'])
        self.assertEqual(foreign.starts(), ['baseline'])
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        self.assertEqual(store.last_advisor_review['scientific_support'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
