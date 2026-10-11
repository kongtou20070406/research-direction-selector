"""Invalid frozen layouts must not consume prospective choices or allowances."""
import json
from pathlib import Path
import sys
import unittest

import test_rds_quick as fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import ProjectStore


class QuickPreparationTests(unittest.TestCase):
    def test_rejected_layout_keeps_ledger_and_allows_corrected_same_name(self):
        f = fixture.QuickTests(methodName='runTest')
        f.setUp()
        self.addCleanup(f.doCleanups)
        f.initialize_policy_ledger()
        marker = f.root / 'worker-started'
        f.script(f'from pathlib import Path\nPath({str(marker)!r}).write_text("started")\n')
        (f.root / 'rds-exec-extra.dat').write_text('bound input')
        nested = f.root / 'rds-exec-metadata.json' / 'data.txt'
        nested.parent.mkdir()
        nested.write_text('bound nested input')
        store = ProjectStore(f.ledger)
        before = store.snapshot()
        with store._db(True) as db:
            before_events = [row[0] for row in db.execute('SELECT body FROM events ORDER BY rowid')]
        cases = [(['--bind', 'data=rds-exec-extra.dat'], 'reserved'),
                 (['--bind', 'data=rds-exec-metadata.json/data.txt'], 'generated exec file'),
                 (['--output', 'rds-exec-protocol.json/result.txt'], 'generated exec file'),
                 (['--output', 'outputs/value', '--output', 'outputs/value/child'], 'overlap each other')]
        for options, error in cases:
            with self.subTest(options=options):
                result = f.job('retry-preparation', False, *f.policy_options(), *options)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(error, result.stderr)
                self.assertFalse(marker.exists())
                self.assertFalse((f.root / '.rds/exec/retry-preparation').exists())
                self.assertEqual(store.snapshot(), before)
                with store._db(True) as db:
                    self.assertEqual([row[0] for row in db.execute('SELECT body FROM events ORDER BY rowid')],
                                     before_events)
                    if db.execute("SELECT 1 FROM sqlite_master WHERE name='checkpoints'").fetchone():
                        self.assertEqual(db.execute('SELECT count(*) FROM checkpoints').fetchone()[0], 0)
        completed = f.job('retry-preparation', True, *f.policy_options())
        result = json.loads(Path(json.loads(completed.stdout)['record']).read_text(encoding='utf-8'))
        self.assertEqual(result['receipt']['run_status'], 'SUCCEEDED')
        self.assertEqual(marker.read_text(), 'started')
        with store._db(True) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM checkpoints').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
