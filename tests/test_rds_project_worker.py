"""The worker runs the one claimed attempt it was given; a failure is logged, never retried."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_project_worker as worker


class FakeStore:
    def __init__(self, artifact_dir, failure=None, runs=None):
        self.artifact_dir = artifact_dir
        self.failure = failure
        self.runs = runs or {}
        self.claims = []

    def _execute_claim(self, run_id, attempt_id):
        self.claims.append((run_id, attempt_id))
        if self.failure is not None:
            raise self.failure

    def run(self, run_id):
        if run_id not in self.runs:
            raise ValueError('Unknown run')
        return self.runs[run_id]


class ProjectWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rds-worker-')
        self.addCleanup(self.temp.cleanup)
        self.artifacts = Path(self.temp.name) / 'artifacts'
        self.artifacts.mkdir()

    def main(self, store, run_id='run-1', attempt_id='attempt-1'):
        argv = ['rds_project_worker.py', '--root', self.temp.name, '--run-id', run_id, '--attempt-id', attempt_id]
        with patch.object(worker, 'open_store', return_value=store) as opened, patch.object(sys, 'argv', argv):
            try:
                worker.main()
            finally:
                opened.assert_called_once_with(self.temp.name)

    def test_executes_exactly_the_claimed_attempt_once(self):
        store = FakeStore(self.artifacts)
        self.main(store)
        self.assertEqual(store.claims, [('run-1', 'attempt-1')])
        self.assertEqual(list(self.artifacts.iterdir()), [])

    def test_failure_is_logged_under_the_run_and_reraised_without_a_retry(self):
        store = FakeStore(self.artifacts, failure=RuntimeError('workload crashed'), runs={'run-1': {'id': 'run-1'}})
        with self.assertRaisesRegex(RuntimeError, 'workload crashed'):
            self.main(store)
        self.assertEqual(store.claims, [('run-1', 'attempt-1')])
        log = (self.artifacts / 'run-1' / 'worker.log').read_text(encoding='utf-8')
        self.assertIn('RuntimeError: workload crashed', log)

    def test_an_unknown_run_id_never_chooses_a_log_directory(self):
        store = FakeStore(self.artifacts, failure=RuntimeError('workload crashed'))
        with self.assertRaisesRegex(ValueError, 'Unknown run'):
            self.main(store, run_id='../outside')
        self.assertEqual(store.claims, [('../outside', 'attempt-1')])
        self.assertEqual(list(Path(self.temp.name).iterdir()), [self.artifacts])
        self.assertEqual(list(self.artifacts.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
