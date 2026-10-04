"""Original baseline before material repair, verified by actual owned CLI."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
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
                p = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(root), *args],
                                   cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=50,
                                   env={**os.environ, 'RDS_USAGE_DB': str(root / '.rds/usage.sqlite3')})
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
            completed = cli('project', 'drive', '--max-steps', '5')
            self.assertEqual(completed['status'], 'GOAL_CONFIRMED', completed)
            state = store.snapshot()
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
            self.assertEqual(after['budget']['wall_seconds']['charged_estimate'], state['budget']['wall_seconds']['charged_estimate'])
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
