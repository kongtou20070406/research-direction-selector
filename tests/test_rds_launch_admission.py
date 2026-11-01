"""Revalidate claimed attempts without repeating execution or weakening fences."""
import json
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'scripts'), str(ROOT / 'tests')]
from rds_admission_read import StaleAdmission
from rds_project import ProjectStore
import test_rds_owned_advisor as fixtures


WRAPPER = r'''
import json, pathlib, sys, time
role, source, project, gate, phase = sys.argv[1:]
sys.path.insert(0, str(pathlib.Path(source) / 'scripts'))
import rds_cli, rds_project
gate = pathlib.Path(gate)
original = rds_project.ProjectStore._advisor_prepare_run
calls = 0
def prepare(store, run_id, **kwargs):
    global calls
    token = original(store, run_id, **kwargs)
    calls += 1
    if role == 'A' and calls == int(phase):
        with store._db(True) as db:
            run = store._run(db, run_id)
            reviews = db.execute("SELECT count(*) FROM events").fetchone()[0]
        (gate / 'identity.json').write_text(json.dumps(dict(
            attempt=run['attempt_id'], status=run['status'], reviews=reviews)))
        (gate / 'ready').touch()
        deadline = time.monotonic() + 25
        while not (gate / 'released').exists():
            if time.monotonic() >= deadline:
                raise TimeoutError('competing advance did not release claim')
            time.sleep(.01)
    return token
rds_project.ProjectStore._advisor_prepare_run = prepare
sys.argv = ['rds_cli.py', '--root', project, 'project', 'advance']
raise SystemExit(rds_cli.main())
'''


class LaunchAdmissionTests(unittest.TestCase):
    def fixture(self):
        case = fixtures.OwnedAdvisorCLITests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.initialize('negative', mutate_policy=lambda p: p['context'].update(max_candidates=2))
        return case, ProjectStore(case.root)

    def queued(self):
        case, store = self.fixture()
        store.register(case.manifests['baseline'])
        with store._db() as db:
            run = store._run(db, 'baseline')
            run['attempt_id'] = 'frozen-claim'
            store._save(db, run)
        return case, store

    def test_competing_real_cli_review_cannot_strand_claim_or_double_charge(self):
        for phase in (2, 3):
            with self.subTest(phase=phase):
                case, store = self.fixture()
                gate = case.root / 'gate'
                gate.mkdir()
                wrapper = gate / 'advance.py'
                wrapper.write_text(WRAPPER, encoding='utf-8')
                owner = subprocess.Popen(
                    [sys.executable, '-X', 'utf8', '-B', str(wrapper), 'A',
                     str(ROOT), str(case.root), str(gate), str(phase)],
                    env=case.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding='utf-8')
                try:
                    deadline = time.monotonic() + 30
                    while not (gate / 'ready').exists():
                        self.assertIsNone(owner.poll(), 'owner exited before native admission')
                        self.assertLess(time.monotonic(), deadline, 'admission gate timed out')
                        time.sleep(.02)
                    identity = json.loads((gate / 'identity.json').read_text())
                    contender = case.call('project', 'advance', ok=False)
                    with store._db(True) as db:
                        self.assertGreater(db.execute('SELECT count(*) FROM events').fetchone()[0],
                                           identity['reviews'])
                    (gate / 'released').touch()
                    out, err = owner.communicate(timeout=45)
                finally:
                    if owner.poll() is None:
                        owner.kill()
                    owner.communicate(timeout=5)
                self.assertEqual(owner.returncode, 0, (out + err)[-6000:])
                self.assertEqual(contender.returncode, 1, contender.stdout + contender.stderr)
                self.assertIn('Run already dispatched', contender.stderr)
                self.assertEqual(identity['status'], 'RESERVED' if phase == 2 else 'RUNNING')
                self.assertEqual(case.starts(), ['baseline'])
                state = store.snapshot()
                self.assertEqual(len(state['receipts']), 1)
                self.assertEqual(state['receipts'][0]['attempt_id'], identity['attempt'])
                self.assertEqual(state['budget']['cpu_seconds']['charged_estimate'], 1)
                self.assertEqual(state['budget']['cpu_seconds']['reserved'], 0)
                store.recover('baseline')
                self.assertEqual(store.snapshot()['budget'], state['budget'])
                self.assertEqual(case.starts(), ['baseline'])

    def test_second_stale_preserves_attempt_and_reservation(self):
        case, store = self.queued()
        before = store.snapshot()
        prepare, calls = store._advisor_prepare_run, []

        def intervening_review(*args, **kwargs):
            token = prepare(*args, **kwargs)
            calls.append(token)
            from rds_owned_advisor import review
            review(store)
            return token

        with patch.object(store, '_advisor_prepare_run', side_effect=intervening_review):
            with self.assertRaises(StaleAdmission):
                store._execute_claim('baseline', 'frozen-claim')
        self.assertEqual(len(calls), 2)
        state = store.snapshot()
        self.assertEqual(state['budget'], before['budget'])
        self.assertEqual(state['receipts'], [])
        self.assertEqual(state['runs'][0]['attempt_id'], 'frozen-claim')
        self.assertEqual(state['runs'][0]['status'], 'RESERVED')
        self.assertEqual(case.starts(), [])

    def test_changed_claim_is_not_refreshed(self):
        case, store = self.queued()
        prepare, calls = store._advisor_prepare_run, []

        def change_claim(*args, **kwargs):
            token = prepare(*args, **kwargs)
            calls.append(token)
            with store._db() as db:
                run = store._run(db, 'baseline')
                run['worker_pid'] = 12345
                store._save(db, run)
            return token

        with patch.object(store, '_advisor_prepare_run', side_effect=change_claim):
            with self.assertRaisesRegex(StaleAdmission, 'Claim or contract changed'):
                store._execute_claim('baseline', 'frozen-claim')
        self.assertEqual(len(calls), 1)
        self.assertEqual(case.starts(), [])
        self.assertEqual(store.snapshot()['receipts'], [])

    def test_current_policy_refusal_after_stale_is_not_retried_again(self):
        case, store = self.queued()
        prepare, calls = store._advisor_prepare_run, []

        def revised_policy(*args, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise ValueError('current policy rejects original route')
            token = prepare(*args, **kwargs)
            from rds_owned_advisor import review
            review(store)
            return token

        with patch.object(store, '_advisor_prepare_run', side_effect=revised_policy):
            with self.assertRaisesRegex(ValueError, 'current policy rejects'):
                store._execute_claim('baseline', 'frozen-claim')
        self.assertEqual(len(calls), 2)
        self.assertEqual(case.starts(), [])
        self.assertEqual(store.snapshot()['receipts'], [])


if __name__ == '__main__':
    unittest.main()
