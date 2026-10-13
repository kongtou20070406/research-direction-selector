import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_lean_obligation import ProofObligation


class LeanResearchEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rds-lean-query-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def command(self, *args):
        run = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_lean_research.py'),
                              '--root', str(self.root), *args],
                             capture_output=True, text=True, encoding='utf-8', timeout=15)
        self.assertFalse((self.root / '.rds').exists())
        return run

    def write_request(self, kind):
        req = {'schema_version': 1, 'kind': kind, 'theorem': 'Demo.example',
               'informal_statement': 'Addition commutes', 'formal_statement': 'forall a b : Nat, a + b = b + a',
               'source_sha256': 'a' * 64}
        path = self.root / 'request.json'
        path.write_text(json.dumps(req), encoding='utf-8')
        return req, path

    def test_conjecture_is_retained_but_not_proved(self):
        _, path = self.write_request('CONJECTURE')
        run = self.command('review', '--obligation', str(path))
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result['status'], 'CONJECTURE')
        self.assertFalse(result['usable_as_proved_premise'])

    def test_unproved_theorem_is_not_success(self):
        _, path = self.write_request('THEOREM')
        run = self.command('review', '--obligation', str(path))
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertEqual(json.loads(run.stdout)['status'], 'PROOF_REQUIRED')

    def test_clean_audit_still_requires_real_replay(self):
        req, path = self.write_request('THEOREM')
        audit = {'schema_version': 1, 'obligation_sha256': ProofObligation.from_dict(req).sha256,
                 'source_sha256': req['source_sha256'], 'theorem': req['theorem'],
                 'declaration_kind': 'theorem', 'axioms': [], 'remaining_goals': [],
                 'errors': [], 'warnings': []}
        evidence = self.root / 'audit.json'
        evidence.write_text(json.dumps(audit), encoding='utf-8')
        run = self.command('review', '--obligation', str(path), '--audit', str(evidence))
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertEqual(json.loads(run.stdout)['status'], 'REPLAY_REQUIRED')

    def test_duplicate_json_key_is_rejected(self):
        _, path = self.write_request('CONJECTURE')
        path.write_text('{"kind":"CONJECTURE","kind":"THEOREM"}', encoding='utf-8')
        run = self.command('review', '--obligation', str(path))
        self.assertEqual(run.returncode, 1)
        self.assertIn('Duplicate JSON key', run.stderr)

    def test_unknown_search_is_not_success(self):
        run = self.command('search', 'x' * 201)
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertEqual(json.loads(run.stdout)['status'], 'UNKNOWN')

    def test_inspection_and_search_do_not_initialize_ledger(self):
        (self.root / 'lean-toolchain').write_text('leanprover/lean4:v4.34.1\n')
        source = self.root / 'Demo.lean'
        source.write_text('theorem add_demo (n : Nat) : n + 0 = n := by simp\n')
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        run = self.command('inspect')
        self.assertEqual(run.returncode, 0, run.stderr)
        run = self.command('search', 'add_demo')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertIn('Demo.lean', run.stdout)
        self.assertNotIn('LEAN_KERNEL_CHECKED', run.stdout)
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, before)


if __name__ == '__main__':
    unittest.main()
