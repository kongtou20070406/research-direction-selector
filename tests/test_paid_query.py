"""The shared paid-query shim charges before it queries and never spends past its quota."""
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import paid_query


class PaidQueryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rds-paid-query-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = self.root / 'scripts' / 'credits.json'
        self.log = self.root / 'scripts' / 'query_log.jsonl'

    def query(self, *args, quota=2):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = paid_query.main(['--root', str(self.root), '--quota', str(quota), '--latency', '0', *args])
        return code, json.loads(out.getvalue())

    def used(self):
        return json.loads(self.ledger.read_text(encoding='utf-8'))['used']

    def logged(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding='utf-8').splitlines()]

    def test_each_query_is_charged_and_logged_once(self):
        code, result = self.query('--arm', 'B', '--batch', '1')
        self.assertEqual(code, 0)
        self.assertEqual((result['mean'], result['n']), (round(paid_query.BASELINE + paid_query.EFFECT[1], 6),
                                                         paid_query.DEFAULT_N))
        self.assertEqual((result['credits_used'], result['credits_remaining']), (1, 1))
        self.assertEqual(self.used(), 1)
        self.assertEqual([(r['arm'], r['batch'], r['credits_remaining']) for r in self.logged()], [('B', 1, 1)])

    def test_an_exhausted_quota_refuses_without_charging(self):
        self.assertEqual(self.query('--arm', 'A', '--batch', '1')[0], 0)
        self.assertEqual(self.query('--arm', 'A', '--batch', '2')[0], 0)
        code, result = self.query('--arm', 'A', '--batch', '3')
        self.assertEqual(code, 2)
        self.assertEqual(result, {'error': 'credit exhausted', 'used': 2, 'quota': 2})
        self.assertEqual(self.used(), 2)
        self.assertEqual(len(self.logged()), 2)

    def test_a_failed_query_stays_charged_and_unlogged(self):
        # Charge-then-query is the protocol: a credit spent on a failed query is not refunded.
        data = self.root / 'data.db'
        with contextlib.closing(sqlite3.connect(data)) as db:
            db.execute('CREATE TABLE scores (arm TEXT, batch INTEGER, score REAL)')
            db.commit()
        code, result = self.query('--arm', 'A', '--batch', '1', '--backend', 'sqlite', '--data', str(data))
        self.assertEqual(code, 5)
        self.assertEqual((result['error'], result['credits_used']), ('query failed', 1))
        self.assertEqual(self.used(), 1)
        self.assertEqual(self.logged(), [])

    def test_an_unreadable_ledger_is_never_reset(self):
        self.ledger.parent.mkdir(parents=True)
        for raw in ('not json', '{"used": -1}', '{"used": true}', '[]'):
            with self.subTest(raw=raw):
                self.ledger.write_text(raw, encoding='utf-8')
                code, result = self.query('--arm', 'A', '--batch', '1')
                self.assertEqual((code, result['error']), (4, 'unreadable ledger'))
                self.assertEqual(self.ledger.read_text(encoding='utf-8'), raw)
        self.assertEqual(self.logged(), [])

    def test_missing_data_or_a_non_positive_quota_spends_nothing(self):
        code, result = self.query('--arm', 'A', '--batch', '1', '--backend', 'sqlite',
                                  '--data', str(self.root / 'absent.db'))
        self.assertEqual((code, result['error']), (5, 'data not found'))
        code, result = self.query('--arm', 'A', '--batch', '1', quota=0)
        self.assertEqual((code, result['error']), (2, 'quota must be positive'))
        self.assertFalse(self.ledger.exists())


if __name__ == '__main__':
    unittest.main()
