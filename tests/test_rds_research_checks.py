import copy
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_research as research
from rds_research_checks import replay
from test_rds_research import proposal


class ResearchCheckTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.goal = research.start(self.root, 'Find a fixed point of 2x+1')['record_id']

    def test_failed_route_reframe_check_restart_and_reuse(self):
        old = proposal(self.goal)
        old['obligations'][0]['spec'] = {'schema': 1, 'kind': 'affine_contraction',
            'model': {'matrix': [['2']], 'bias': ['1']}, 'threshold': '1'}
        ident = research.propose(self.root, old)['record_id']
        result = research.check(self.root, ident, 'claim')
        self.assertEqual(result['context']['next_action'], 'REFORMULATE')
        new = research.propose(self.root, proposal(self.goal, ident))['record_id']
        result = research.check(self.root, new, 'claim')
        self.assertEqual(result['context']['next_action'], 'INTERPRET_CHECKED_SCOPE')
        with patch('rds_verify.verify', side_effect=AssertionError('Replay must not generate')):
            self.assertTrue(research.check(self.root, new, 'claim')['reused'])
            state = research.context(self.root)
        self.assertEqual([r['obligations'][0]['result']['status'] for r in state['routes']], ['FAIL', 'PASS'])
        self.assertEqual(state['scientific_support'], 'UNKNOWN')
        # Independently recompute every persisted source hash from ledger bytes.
        with closing(sqlite3.connect(self.root / '.rds' / 'project.sqlite3')) as db:
            sources = [json.loads(r[0])['source'] for r in db.execute('SELECT body FROM events')]
        for source in sources:
            sha = source.split(':', 1)[1]
            raw = (self.root / '.rds' / 'cas' / (sha + '.json')).read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)

    def test_unknown_and_input_reported_never_discharge(self):
        spec = proposal(self.goal)
        spec['obligations'][0]['spec']['kind'] = 'unsupported_research_claim'
        ident = research.propose(self.root, spec)['record_id']
        state = research.check(self.root, ident, 'claim')['context']
        self.assertEqual(state['routes'][0]['obligations'][0]['result']['status'], 'UNKNOWN')
        observed = proposal(self.goal)
        observed['obligations'][0].update(tool='observation', spec={'question': 'Does the run improve accuracy?'})
        ident = research.propose(self.root, observed)['record_id']
        evidence = self.root / 'output.json'
        evidence.write_text('{"status":"PASS","verified":true}', encoding='utf-8')
        state = research.check(self.root, ident, 'claim', evidence)['context']
        self.assertEqual(state['routes'][-1]['obligations'][0]['result']['status'], 'INPUT_REPORTED')
        self.assertEqual(state['next_action'], 'CHECK_OR_COLLECT_EVIDENCE')

    def test_certificate_substitution_and_cross_obligation_rejected(self):
        spec = proposal(self.goal)
        ident = research.propose(self.root, spec)['record_id']
        answer = research.check(self.root, ident, 'claim')
        saved = research._entries(self.root)[answer['record_id']]['payload']
        changed = copy.deepcopy(saved)
        changed['result']['certificate']['verdict'] = 'FAIL'
        self.assertNotEqual(replay(self.root, spec, changed)['status'], 'PASS')
        changed = copy.deepcopy(saved)
        changed['request']['spec']['point'] = ['0']
        with self.assertRaisesRegex(ValueError, 'different obligation'):
            replay(self.root, spec, changed)

    def test_exact_math_adapter_checks_its_own_request(self):
        spec = proposal(self.goal)
        spec['obligations'][0].update(tool='math', spec={
            'schema': 'rds-math-request-v1', 'kind': 'rational_linear_system',
            'domain': 'QQ', 'matrix': [['1']], 'rhs': ['2'], 'claim': 'unique_solution'})
        ident = research.propose(self.root, spec)['record_id']
        state = research.check(self.root, ident, 'claim')['context']
        result = state['routes'][-1]['obligations'][0]['result']
        # Backend availability affects generation, never permits a fake PASS.
        self.assertIn(result['status'], ('PASS', 'UNKNOWN'))
        if result['status'] == 'PASS':
            self.assertEqual(result['assurance'], 'CERTIFICATE_CHECKED')

    def test_real_cli_roundtrip(self):
        cli = [sys.executable, '-B', str(ROOT / 'scripts' / 'rds_cli.py'), '--root', str(self.root)]
        spec = self.root / 'route.json'
        spec.write_text(json.dumps(proposal(self.goal)), encoding='utf-8')
        run = subprocess.run(cli + ['research', 'propose', '--file', str(spec)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        ident = json.loads(run.stdout)['record_id']
        run = subprocess.run(cli + ['research', 'check', '--proposal', ident, '--obligation', 'claim'], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(json.loads(run.stdout)['context']['next_action'], 'INTERPRET_CHECKED_SCOPE')

    def test_repeated_proposal_and_changed_observation_do_not_rerun(self):
        spec = proposal(self.goal)
        spec['obligations'][0].update(tool='observation', spec={'question': 'What happened?'})
        ident = research.propose(self.root, spec)['record_id']
        self.assertEqual(research.propose(self.root, spec)['record_id'], ident)
        evidence = self.root / 'raw.txt'
        evidence.write_text('original failure', encoding='utf-8')
        research.check(self.root, ident, 'claim', evidence)
        evidence.write_text('different result', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'revised proposal'):
            research.check(self.root, ident, 'claim', evidence)

    def test_explicit_selection_changes_the_next_action(self):
        first = research.propose(self.root, proposal(self.goal))['record_id']
        research.check(self.root, first, 'claim')
        second = research.propose(self.root, proposal(self.goal, first))['record_id']
        state = research.select(self.root, first, 'Use the already checked route')['context']
        self.assertEqual(state['focus_route'], first)
        self.assertEqual(state['next_action'], 'INTERPRET_CHECKED_SCOPE')
        self.assertEqual(state['routes'][-1]['id'], second)

    def test_historical_check_replays_its_own_evidence_after_goal_change(self):
        spec = proposal(self.goal)
        spec['obligations'][0].update(tool='observation', spec={'question': 'What happened?'})
        ident = research.propose(self.root, spec)['record_id']
        evidence = self.root / 'raw.txt'
        evidence.write_text('original', encoding='utf-8')
        research.check(self.root, ident, 'claim', evidence)
        research.start(self.root, 'A later goal')
        self.assertEqual(research.check(self.root, ident, 'claim')['check']['status'], 'INPUT_REPORTED')
        next((self.root / '.rds' / 'cas').glob('*.bin')).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'integrity'):
            research.check(self.root, ident, 'claim')

    def test_existing_empirical_structure_runs_feed_the_same_context(self):
        from test_rds_structure import fixture
        import rds_structure as structure
        root = fixture.prepare(self.root / 'empirical', timeout=10)
        task = structure.request(root)['tasks'][0]
        original = fixture.proposal(root, task, timeout=10)
        structure.propose(root, original)
        goal = research.start(root, 'Explain the finite observed interaction')['record_id']
        spec = proposal(goal)
        spec['obligations'][0].update(tool='structure', spec={'proposal_id': original['id']})
        ident = research.propose(root, spec)['record_id']
        state = research.advance(root, ident, 'claim')['context']
        result = state['routes'][0]['obligations'][0]['result']
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['assurance'], 'SCOPED_EMPIRICAL_DISCRIMINATOR')
        from rds_project import ProjectStore
        before = ProjectStore(root).snapshot()['receipts']
        with patch('rds_structure.advance', side_effect=AssertionError('Status is read-only')):
            research.context(root)
        research.advance(root, ident, 'claim')
        self.assertEqual(ProjectStore(root).snapshot()['receipts'], before)
        # Re-reading must expose a changed original rather than trust cached PASS.
        (root / 'out' / 'candidate.json').write_text('{}', encoding='utf-8')
        with self.assertRaises(ValueError):
            research.context(root)


if __name__ == '__main__':
    unittest.main()
