import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_lean_obligation import ProofObligation
import rds_lean_research as lean_research


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

    def test_feedback_displays_subgoals_without_proof_promotion_or_writes(self):
        source = self.root / 'Draft.lean'
        source.write_bytes(b'theorem pending : True := by\n')
        log = self.root / 'diagnostics.jsonl'
        log.write_text(json.dumps({'severity': 'error', 'kind': 'Tactic.unsolvedGoals',
                                  'data': 'unsolved goals', 'pos': {'line': 1, 'column': 0}}))
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        run = self.command('feedback', '--source', str(source), '--diagnostics', str(log))
        self.assertEqual(run.returncode, 0, run.stderr)
        result = json.loads(run.stdout)
        self.assertEqual(result['unresolved_subgoals'], ['unsolved goals'])
        self.assertEqual(result['proof_status'], 'UNKNOWN')
        self.assertFalse(result['usable_as_proved_premise'])
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir()}, before)

    def test_bad_feedback_is_not_complete(self):
        source, log = self.root / 'Draft.lean', self.root / 'diagnostics.jsonl'
        source.write_bytes(b'-- draft')
        log.write_bytes(b'not json')
        run = self.command('feedback', '--source', str(source), '--diagnostics', str(log))
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertEqual(json.loads(run.stdout)['parse_status'], 'UNKNOWN')

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


class LeanResearchRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='rds-lean-request-')
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'request.json'

    def test_non_finite_constants_are_rejected(self):
        for constant in ('NaN', 'Infinity', '-Infinity'):
            with self.subTest(constant=constant):
                self.path.write_text('{"kind": "CONJECTURE", "weight": %s}' % constant, encoding='utf-8')
                with self.assertRaises(ValueError):
                    lean_research._read_json(self.path)

    def test_request_file_is_bounded_before_parsing(self):
        self.path.write_text('{"kind": "CONJECTURE"}', encoding='utf-8')
        size = self.path.stat().st_size
        with patch.object(lean_research, 'MAX_BYTES', size):
            self.assertEqual(lean_research._read_json(self.path), {'kind': 'CONJECTURE'})
        with patch.object(lean_research, 'MAX_BYTES', size - 1):
            with self.assertRaisesRegex(ValueError, 'exceeds byte limit'):
                lean_research._read_json(self.path)

    def test_only_pass_or_conjecture_exits_zero(self):
        self.path.write_text('{"kind": "CONJECTURE"}', encoding='utf-8')
        for status, code in (('PASS', 0), ('CONJECTURE', 0), ('PROOF_REQUIRED', 2), (None, 2)):
            app = SimpleNamespace(review_lean_obligation=lambda request, audit, status=status: {'status': status})
            with self.subTest(status=status), patch.object(lean_research, 'create_app', return_value=app), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(lean_research.main(['--root', self.temp.name, 'review',
                                                     '--obligation', str(self.path)]), code)


if __name__ == '__main__':
    unittest.main()
