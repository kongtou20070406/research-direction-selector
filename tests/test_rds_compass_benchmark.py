"""The Compass benchmark runner scores arms against sealed truth from receipts."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'benchmark' / 'compass'))
import run as bench


class CompassBenchmarkTests(unittest.TestCase):
    def test_mean_masking_trap_scores_each_arm_from_its_own_receipts(self):
        with tempfile.TemporaryDirectory() as folder:
            out = Path(folder) / 'result.json'
            with redirect_stdout(io.StringIO()):
                code = bench.main(['--work', str(Path(folder) / 'work'), '--out', str(out), '--tasks', 'mean-masking'])
            self.assertEqual(code, 0)
            body = json.loads(out.read_text(encoding='utf-8'))
        rows = {r['arm']: r for r in body['rows']}
        self.assertEqual((rows['compass']['accepted'], rows['compass']['verified_discovery']), ('variant_2', True))
        for arm in ('ratchet', 'bare_agent'):
            self.assertEqual((rows[arm]['accepted'], rows[arm]['false_certification']), ('variant_1', True))
        for row in rows.values():
            self.assertEqual(len(row['receipts']), row['experiments'])
            self.assertTrue(all(len(r['sha256']) == 64 for r in row['receipts']))
        self.assertTrue(body['arms']['provider_agent'].startswith('NOT_RUN'))  # Real-model arms are opt-in.
        self.assertEqual(body['scientific_support'], 'UNKNOWN')
        unsealed = dict(body)
        del unsealed['bundle_sha256']
        self.assertEqual(bench.digest(json.loads(json.dumps(unsealed, default=str))), body['bundle_sha256'])


if __name__ == '__main__':
    unittest.main()
