"""Original baseline before material repair, verified by actual owned CLI."""
from contextlib import closing
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sqlite3
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import ProjectStore
from rds_domain_confirmation import inspect_confirmation


class BaselineLineageCLITests(unittest.TestCase):
    def test_paid_baseline_before_repair_is_consumed_without_rerun(self):
        with tempfile.TemporaryDirectory(prefix='rds-baseline-lineage-') as tmp:
            root = Path(tmp) / 'campaign'
            spec = importlib.util.spec_from_file_location('autonomy_example', ROOT / 'examples/autonomy/run.py')
            example = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(example)
            contract = example.build(root, 'algorithms')
            nodes = contract['advisor_policy']['graph']['nodes']
            node = lambda rid: next(n for n in nodes if n['id'] == rid)
            baseline = node('baseline')
            baseline['executable']['preconditions'] = []
            node('failed-pilot')['executable']['preconditions'] = [
                {'fact': 'run.baseline.succeeded', 'op': 'eq', 'value': True}]
            node('confirmation')['executable']['preconditions'].append(
                {'fact': 'run.candidate.succeeded', 'op': 'eq', 'value': True})
            contract['advisor_policy']['graph']['nodes'] = [baseline] + [n for n in nodes if n['id'] != 'baseline']
            (root / 'contract.json').write_text(json.dumps(contract), encoding='utf-8')
            trace = []
            def cli(*args):
                # The controller allowance excludes worker time. Wait for the
                # original frozen campaign window, without changing any native
                # budget, worker timeout, step limit, or recovery assertion.
                timeout = contract['stop_policy']['wall_seconds'] if args[:2] == ('project', 'drive') else 50
                try:
                    p = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(root), *args],
                                       cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=timeout,
                                       env={**os.environ, 'RDS_USAGE_DB': str(root / '.rds/usage.sqlite3')})
                except subprocess.TimeoutExpired as exc:
                    # subprocess.run has killed/reaped the parent CLI here;
                    # inspect only, never recover or launch another controller.
                    captured = {}
                    failure = {'argv': list(args), 'status': 'HARNESS_TIMEOUT', 'timeout_seconds': timeout,
                               'exception': str(exc)[:2048], 'child_settlement': 'UNKNOWN'}
                    for name, value in (('stdout', exc.stdout), ('stderr', exc.stderr)):
                        raw = value.encode('utf-8') if isinstance(value, str) else value or b''
                        captured[name] = raw
                        failure[name] = {'captured_bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
                                         'tail_utf8': raw[-4096:].decode('utf-8', errors='replace'),
                                         'omitted_bytes': max(0, len(raw) - 4096)}
                    database = root / '.rds/project.sqlite3'
                    failure['ledger'] = {'status': 'MISSING'}
                    if database.is_file():
                        try:
                            with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=1)) as db:
                                db.row_factory = sqlite3.Row
                                deadline = time.monotonic() + 1
                                db.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
                                db.execute('BEGIN')
                                failure['ledger'] = {
                                    'status': 'READ_ONLY_PARTIAL', 'row_limit': 10,
                                    'runs': [dict(row) for row in db.execute(
                                        "SELECT id,status,json_extract(body,'$.attempt_id') AS attempt_id,"
                                        "json_extract(body,'$.pid') AS pid FROM runs ORDER BY id LIMIT 10")],
                                    'receipts': [dict(row) for row in db.execute(
                                        'SELECT run_id,sha256 FROM receipts ORDER BY run_id LIMIT 10')],
                                    'budget': [dict(row) for row in db.execute(
                                        'SELECT * FROM budget ORDER BY resource LIMIT 10')],
                                    'event_tail': [dict(row) for row in db.execute(
                                        'SELECT id,length(body) AS original_chars,substr(body,1,1024) AS body_head '
                                        'FROM events ORDER BY id DESC LIMIT 8')]}
                        except (sqlite3.Error, OSError, ValueError) as diagnostic_error:
                            failure['ledger'] = {'status': 'UNKNOWN', 'read_error': str(diagnostic_error)[:1024]}
                    trace.append(failure)
                    evidence = os.environ.get('RDS_AUTONOMY_EVIDENCE')
                    if evidence:
                        try:
                            directory = Path(evidence) / self._testMethodName
                            directory.mkdir(parents=True, exist_ok=True)
                            for name, raw in captured.items():
                                (directory / ('timeout-' + name + '.bin')).write_bytes(raw)
                            (directory / 'cli-transcript.json').write_text(json.dumps(trace), encoding='utf-8')
                        except OSError as diagnostic_error:
                            failure['evidence_write_error'] = str(diagnostic_error)[:1024]
                    self.fail('CLI harness deadline exceeded; retained attempts must be inspected, not rerun: '
                              + json.dumps(failure, ensure_ascii=True))
                trace.append({'argv': list(args), 'returncode': p.returncode, 'stdout': p.stdout, 'stderr': p.stderr})
                self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
                return json.loads(p.stdout)
            cli('project', 'init', '--contract', 'contract.json')
            cli('project', 'drive', '--max-steps', '1')
            store = ProjectStore(root)
            paid = store.snapshot()
            baseline_receipt = paid['receipts'][0]
            self.assertEqual(baseline_receipt['run_id'], 'baseline')
            self.assertEqual(baseline_receipt['run_status'], 'SUCCEEDED')
            retained = {baseline_receipt['run_id']: baseline_receipt}
            executed = []
            for _ in range(5):
                completed = cli('project', 'drive', '--max-steps', '5')
                current = store.snapshot()
                for receipt in current['receipts']:
                    if receipt['run_id'] in retained:
                        self.assertEqual(receipt, retained[receipt['run_id']])
                    retained[receipt['run_id']] = receipt
                for run in completed['executed']:
                    self.assertNotIn(run['run_id'], [r['run_id'] for r in executed])
                    executed.append(run)
                if completed['status'] == 'GOAL_CONFIRMED':
                    break
                self.assertEqual(completed['status'], 'HANDOFF_REQUIRED', completed)
                self.assertEqual(completed['reason'], 'CONTROLLER_WALL_ALLOWANCE_EXHAUSTED', completed)
            self.assertEqual(completed['status'], 'GOAL_CONFIRMED', completed)
            self.assertEqual([r['run_id'] for r in executed], ['failed-pilot', 'repair', 'candidate', 'confirmation'])
            state = store.snapshot()
            with store._db(True) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM events WHERE json_extract(body,'$.kind')='AUTONOMY_MODEL_REQUESTED'").fetchone()[0], 1)
                started = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='CAMPAIGN_STARTED'").fetchone()[0]
            candidate = next(r for r in state['receipts'] if r['run_id'] == 'candidate')
            self.assertNotEqual(candidate['effective_contract_sha256'], baseline_receipt['effective_contract_sha256'])
            self.assertEqual(next(r for r in state['receipts'] if r['run_id'] == 'baseline'), baseline_receipt)
            self.assertEqual(len(state['runs']), 5)
            self.assertEqual(len(state['contract_history']), 2)
            self.assertEqual(state['budget']['wall_seconds']['reserved'], 0)
            cli('project', 'recover', '--id', 'baseline')
            resumed = cli('project', 'drive', '--max-steps', '5')
            self.assertEqual(resumed['status'], 'GOAL_CONFIRMED', resumed)
            after = store.snapshot()
            self.assertEqual(after['runs'], state['runs'])
            self.assertEqual(after['receipts'], state['receipts'])
            self.assertEqual(after['exposures'], state['exposures'])
            with store._db(True) as db:
                events = [json.loads(r['body']) for r in db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind') IN ('AUTONOMY_DRIVE_CLAIMED','AUTONOMY_DRIVE_RELEASED') ORDER BY id")]
                self.assertEqual(db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='CAMPAIGN_STARTED'").fetchone()[0], started)
            claims = {e['owner']: e['controller_reservation'] for e in events if e['kind'] == 'AUTONOMY_DRIVE_CLAIMED'}
            workers = [r['resources']['wall_seconds'] for r in after['receipts']]
            spent = sum(r['measured'] or 0 for r in workers)
            charged = sum(r['charged_estimate'] for r in workers)
            for event in events:
                if event['kind'] == 'AUTONOMY_DRIVE_RELEASED':
                    wall = event['controller_wall_seconds']
                    spent += min(wall, claims[event['owner']])
                    charged += max(0, wall - claims[event['owner']])
            self.assertAlmostEqual(after['budget']['wall_seconds']['spent_measured'], spent, places=6)
            self.assertAlmostEqual(after['budget']['wall_seconds']['charged_estimate'], charged, places=6)
            self.assertEqual(after['budget']['wall_seconds']['cap'], paid['budget']['wall_seconds']['cap'])
            # Original byte damage must remain UNKNOWN, even if supplied history
            # claims that an arbitrary ancestor is valid.
            output = root / 'outputs/baseline.json'
            original = output.read_bytes()
            output.write_bytes(original.replace(b'0', b'1', 1))
            foreign = dict(after, contract_history=[{'sha256': 'f'*64, 'contract': after['contract']}])
            unknown = inspect_confirmation(store, after['contract'], foreign)
            self.assertEqual(unknown['task_confirmation'], 'UNKNOWN', unknown)
            output.write_bytes(original)
            self.assertEqual(inspect_confirmation(store, after['contract'], foreign)['task_confirmation'], 'PASS')
            self.assertEqual(store.snapshot()['receipts'], state['receipts'])
            evidence = os.environ.get('RDS_AUTONOMY_EVIDENCE')
            if evidence:
                directory = Path(evidence) / self._testMethodName
                directory.mkdir(parents=True, exist_ok=True)
                (directory / 'cli-transcript.json').write_text(json.dumps(trace), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
