"""Public CLI acceptance for low-friction entry, ambiguity and preserved evidence."""
from contextlib import closing
import copy
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import ProjectStore


class QuickTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {**os.environ, 'RDS_USAGE_DB': str(self.root / 'usage.sqlite3')}

    def call(self, *args, root=None, ok=True):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(root or self.root), *args],
                                capture_output=True, text=True, encoding='utf-8', env=self.env, timeout=25)
        if ok:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def script(self, text='print("value=" + "x" * 20000)\n'):
        (self.root / 'probe.py').write_text(text, encoding='utf-8')

    def job(self, name='probe', ok=True, *options):
        return self.call('exec', '--name', name, '--timeout', '5', *options, '--', sys.executable, '-B', 'probe.py', ok=ok)

    def initialize_ledger(self):
        self.script('print("ledger")\n')
        self.job('ledger', True, '--timeout', '30')
        self.ledger = self.root / '.rds/exec/ledger'
        self.context = {'decision': {'id': 'choose-next', 'goal_revision': 'fixed-objective', 'scope': {'domain': 'software-fixture'}},
                        'facts': {'x': {'value': False, 'source': 'reported-fixture.json'},
                                  'route-done': {'value': False, 'source': 'reported-fixture.json'}}}
        self.graph = {'nodes': [{'id': 'route', 'sources': ['fixture'], 'executable': {
            'decisions': ['choose-next'], 'preconditions': [{'fact': 'x', 'value': False}],
            'satisfied_when': [{'fact': 'route-done', 'value': True}],
            'action': {'id': 'inspect-x', 'kind': 'PAIRED_TEST', 'description': 'Inspect a reported x', 'operation': 'inspect', 'target': 'x',
                       'competing_explanations': ['first', 'second'], 'required_observables': ['x'],
                       'outcomes': [{'observation': 'first', 'next_decision': 'review first'},
                                    {'observation': 'second', 'next_decision': 'review second'}]}}}], 'edges': []}
        self.context_path = self.root / 'context.json'
        self.graph_path = self.root / 'graph.json'
        self.context_path.write_text(json.dumps(self.context), encoding='utf-8')
        self.graph_path.write_text(json.dumps(self.graph), encoding='utf-8')

    def initialize_policy_ledger(self, max_attempts=1):
        self.initialize_ledger()
        old = self.ledger
        contract = ProjectStore(old).snapshot()['contract']
        self.ledger = self.root / 'policy-ledger'
        self.ledger.mkdir()
        for binding in contract['bindings']:
            target = self.ledger / binding['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(old / binding['path'], target)
        contract['execution_policy'] = {'schema': 1, 'max_attempts': max_attempts}
        ProjectStore(self.ledger).initialize(contract)

    def test_prospective_exec_binds_native_goal_without_manual_goal_link_flag(self):
        from rds_math import bind_objective
        from test_rds_native_research import GOAL
        bind_objective(self.root, json.dumps(GOAL).encode('utf-8'))
        self.initialize_ledger()
        self.context['decision']['scope'] = copy.deepcopy(GOAL['scope'])
        self.context_path.write_text(json.dumps(self.context), encoding='utf-8')
        original = self.context_path.read_bytes()
        self.assertNotIn('objective_binding', self.context)
        self.assertNotIn('require_goal_link', self.context)
        self.script('print("bounded local check")\n')
        result = json.loads(self.job('native-local', True, *self.policy_options()).stdout)
        full = json.loads(Path(result['record']).read_text(encoding='utf-8'))
        self.assertEqual(full['receipt']['run_status'], 'SUCCEEDED')
        self.assertEqual(self.context_path.read_bytes(), original)
        before = ProjectStore(self.ledger).snapshot()['budget']
        retried = json.loads(self.job('native-local', True, *self.policy_options()).stdout)
        self.assertFalse(json.loads(Path(retried['record']).read_text(encoding='utf-8'))['execution_started'])
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before)

    def policy_options(self):
        return ['--context', str(self.context_path), '--graph', str(self.graph_path), '--ledger', str(self.ledger)]

    def test_policy_failed_dispatch_cannot_reset_with_names_facts_ids_or_timeout(self):
        self.initialize_policy_ledger()
        marker = self.root / 'launch-marker'
        self.script(f'from pathlib import Path\nPath({str(marker)!r}).write_text("started")\nraise SystemExit(7)\n')
        self.call('checkpoint', 'save', '--id', 'before-policy', root=self.ledger)
        failed = self.job('failed-one', False, *self.policy_options())
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(marker.read_text(), 'started')
        before = ProjectStore(self.ledger).snapshot()['budget']
        marker.unlink()
        self.context['decision']['id'] = 'renamed-decision'
        self.context['decision']['goal_revision'] = 'unverified-renamed-revision'
        self.context['facts']['x']['note'] = 'claimed new evidence without changed bound bytes'
        self.graph['nodes'][0]['id'] = 'renamed-node'
        self.graph['nodes'][0]['executable']['decisions'] = ['renamed-decision']
        self.graph['nodes'][0]['executable']['action']['id'] = 'renamed-action'
        self.graph['nodes'][0]['executable']['action']['operation'] = 'renamed-operation-with-identical-execution'
        self.graph['nodes'][0]['executable']['action']['target'] = 'renamed-target-with-identical-execution'
        self.context_path.write_text(json.dumps(self.context), encoding='utf-8')
        self.graph_path.write_text(json.dumps(self.graph), encoding='utf-8')
        blocked = self.job('renamed-two', False, *self.policy_options(), '--timeout', '3')
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn('max_attempts', blocked.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / '.rds/exec/renamed-two').exists())
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before)
        self.call('checkpoint', 'restore', '--id', 'before-policy', root=self.ledger)
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before)

    def test_policy_explicit_two_attempts_keep_both_failures_and_refuse_the_third(self):
        self.initialize_policy_ledger(max_attempts=2)
        marker = self.root / 'two-attempts-marker'
        self.script(f'from pathlib import Path\nwith Path({str(marker)!r}).open("a") as f: f.write("launch\\n")\nraise SystemExit(7)\n')
        before = ProjectStore(self.ledger).snapshot()['budget']['wall_seconds']['charged_estimate']
        for name in ('allowed-one', 'allowed-two'):
            result = self.job(name, False, *self.policy_options())
            self.assertNotEqual(result.returncode, 0)
            saved = json.loads(Path(json.loads(result.stdout)['record']).read_text(encoding='utf-8'))
            self.assertEqual(saved['receipt']['run_status'], 'FAILED')
        self.assertEqual(marker.read_text().splitlines(), ['launch', 'launch'])
        charged = ProjectStore(self.ledger).snapshot()['budget']
        self.assertEqual(charged['wall_seconds']['charged_estimate'], before + 10)
        rejected = self.job('third-refused', False, *self.policy_options())
        self.assertIn('max_attempts', rejected.stderr)
        self.assertEqual(marker.read_text().splitlines(), ['launch', 'launch'])
        self.assertFalse((self.root / '.rds/exec/third-refused').exists())
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], charged)

    def test_policy_same_content_script_rename_does_not_reopen_failure(self):
        self.initialize_policy_ledger()
        marker = self.root / 'alias-marker'
        self.script(f'from pathlib import Path\nPath({str(marker)!r}).write_text("first")\nraise SystemExit(7)\n')
        self.job('original-script', False, *self.policy_options())
        marker.unlink()
        (self.root / 'same-code.py').write_bytes((self.root / 'probe.py').read_bytes())
        before = ProjectStore(self.ledger).snapshot()['budget']
        failed = self.call('exec', '--name', 'script-renamed', '--timeout', '5', *self.policy_options(),
                           '--', sys.executable, '-B', 'same-code.py', ok=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('max_attempts', failed.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before)

    def test_policy_new_actual_bound_evidence_bytes_reopen(self):
        self.initialize_policy_ledger()
        marker = self.root / 'evidence-marker'
        self.script(f'from pathlib import Path\nPath({str(marker)!r}).write_text(Path("evidence.json").read_text())\n')
        evidence = self.root / 'evidence.json'
        evidence.write_text('first')
        options = self.policy_options() + ['--bind', 'data=evidence.json']
        self.job('evidence-one', True, *options)
        before = ProjectStore(self.ledger).snapshot()['budget']['wall_seconds']['charged_estimate']
        evidence.write_text('second')
        self.job('evidence-two', True, *options)
        self.assertEqual(marker.read_text(), 'second')
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget']['wall_seconds']['charged_estimate'], before + 5)

    def test_policy_concurrent_duplicate_cli_only_launches_and_charges_once(self):
        self.initialize_policy_ledger()
        marker = self.root / 'concurrent-marker'
        self.script(f'from pathlib import Path\nimport time\nwith Path({str(marker)!r}).open("a") as f: f.write("launch\\n")\ntime.sleep(0.2)\n')
        before = ProjectStore(self.ledger).snapshot()['budget']['wall_seconds']['charged_estimate']
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda name: self.job(name, False, *self.policy_options()), ('race-one', 'race-two')))
        self.assertEqual(marker.read_text().splitlines(), ['launch'])
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget']['wall_seconds']['charged_estimate'], before + 5)
        with ProjectStore(self.ledger)._db(True) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE'").fetchone()[0], 1)

    def test_policy_success_alias_observes_verified_output_and_missing_output_refuses(self):
        self.initialize_policy_ledger()
        marker = self.root / 'success-marker'
        self.script(f'from pathlib import Path\nPath({str(marker)!r}).write_text("launched")\nPath("outputs/result").write_text("original")\n')
        options = self.policy_options() + ['--output', 'outputs/result']
        first = self.job('success-one', True, *options)
        job_root = Path(json.loads(Path(json.loads(first.stdout)['record']).read_text())['job_root'])
        before = ProjectStore(self.ledger).snapshot()['budget']
        marker.unlink()
        second = self.job('success-renamed', True, *options)
        self.assertEqual(json.loads(second.stdout)['status'], 'EXISTING_JOB')
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / '.rds/exec/success-renamed').exists())
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before)
        (job_root / 'outputs/result').unlink()
        rejected = self.job('missing-output-reuse', False, *options)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('output unavailable', rejected.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before)

    def test_policy_same_name_recovery_requires_exact_parent_allowance(self):
        self.initialize_policy_ledger()
        marker = self.root / 'owned-child-marker'
        self.script(f'from pathlib import Path\nPath({str(marker)!r}).write_text("launched")\n')
        first = self.job('owned-child', True, *self.policy_options())
        saved = json.loads(Path(json.loads(first.stdout)['record']).read_text(encoding='utf-8'))
        before = ProjectStore(self.ledger).snapshot()['budget']
        marker.unlink()
        repeated = self.job('owned-child', True, *self.policy_options())
        retained = json.loads(Path(json.loads(repeated.stdout)['record']).read_text(encoding='utf-8'))
        self.assertEqual(retained['receipt']['sha256'], saved['receipt']['sha256'])
        self.assertFalse(marker.exists())
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before)
        copied_root = self.root / 'other-source'
        copied_root.mkdir()
        shutil.copyfile(self.root / 'probe.py', copied_root / 'probe.py')
        copied_child = copied_root / '.rds/exec/owned-child'
        shutil.copytree(saved['job_root'], copied_child)
        refused = self.call('exec', '--name', 'owned-child', '--timeout', '5', *self.policy_options(),
                            '--', sys.executable, '-B', 'probe.py', root=copied_root, ok=False)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('no matching parent allowance', refused.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before)

    def test_policy_same_name_failure_is_observed_without_another_charge(self):
        self.initialize_policy_ledger()
        marker = self.root / 'failed-child-marker'
        self.script(f'from pathlib import Path\nPath({str(marker)!r}).write_text("launched")\nraise SystemExit(7)\n')
        first = self.job('retained-failure', False, *self.policy_options())
        saved = json.loads(Path(json.loads(first.stdout)['record']).read_text(encoding='utf-8'))
        marker.unlink()
        before = ProjectStore(self.ledger).snapshot()['budget']
        repeated = self.job('retained-failure', False, *self.policy_options())
        retained = json.loads(Path(json.loads(repeated.stdout)['record']).read_text(encoding='utf-8'))
        self.assertNotEqual(repeated.returncode, 0)
        self.assertEqual(retained['receipt']['sha256'], saved['receipt']['sha256'])
        self.assertFalse(marker.exists())
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before)

    def test_policy_executor_changed_after_charge_is_refused_before_launch(self):
        self.initialize_policy_ledger()
        runtime = self.root / 'disposable-runtime'
        runtime.mkdir()
        executor = runtime / Path(sys.executable).name
        if os.name == 'nt':
            shutil.copy2(sys.executable, executor)
            for dll in Path(sys.executable).parent.glob('python*.dll'):
                shutil.copyfile(dll, runtime / dll.name)
        else:
            executor.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
            executor.chmod(0o700)
        original_executor = executor.read_bytes()
        expected = hashlib.sha256(original_executor).hexdigest()
        env = {**self.env, 'PYTHONHOME': sys.base_prefix}
        runnable = subprocess.run([str(executor), '-B', '-c', 'print("disposable executor")'],
                                  capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(runnable.returncode, 0, runnable.stderr)
        marker = self.root / 'executor-marker'
        self.script(f'from pathlib import Path\nPath({str(marker)!r}).write_text("launched")\n')
        before = ProjectStore(self.ledger).snapshot()['budget']['wall_seconds']['charged_estimate']
        harness = '''import pathlib, sys
sys.path.insert(0, sys.argv.pop(1))
import rds_cli, rds_quick
charge = rds_quick._charge_ledger
def changed_after_charge(*args, **kwargs):
    result = charge(*args, **kwargs)
    if result is None and kwargs.get('dispatch', True):
        with pathlib.Path(args[2]['argv'][0]).open('ab') as handle:
            handle.write(b'actual-executor-change-after-charge')
    return result
rds_quick._charge_ledger = changed_after_charge
sys.argv[0] = rds_cli.__file__
raise SystemExit(rds_cli.main())
'''
        refused = subprocess.run([sys.executable, '-B', '-c', harness, str(ROOT / 'scripts'),
                                 '--root', str(self.root), 'exec', '--name', 'changed-executor', '--timeout', '5',
                                 *self.policy_options(), '--', str(executor), '-B', 'probe.py'],
                                capture_output=True, text=True, encoding='utf-8', env=env, timeout=25)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn('changed before registration', refused.stderr)
        self.assertNotEqual(hashlib.sha256(executor.read_bytes()).hexdigest(), expected)
        self.assertFalse(marker.exists())
        state = ProjectStore(self.root / '.rds/exec/changed-executor').snapshot()
        self.assertFalse(state['runs'])
        self.assertEqual(state['budget']['wall_seconds']['reserved'], 0)
        charged = ProjectStore(self.ledger).snapshot()['budget']
        self.assertEqual(charged['wall_seconds']['charged_estimate'], before + 5)
        # Restore the original route; the incomplete charged child must still block it.
        executor.write_bytes(original_executor)
        repeated = self.call('exec', '--name', 'after-executor-crash', '--timeout', '5', *self.policy_options(),
                             '--', str(executor), '-B', 'probe.py', ok=False)
        self.assertIn('incomplete or ambiguous run', repeated.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], charged)

    def test_policy_charged_crash_without_child_does_not_refund_or_relaunch(self):
        from rds_advisor import _loop_route
        from rds_project import file_sha
        from rds_quick import _charge_ledger, choice
        self.initialize_policy_ledger()
        self.script('print("not launched")\n')
        selected = choice(json.loads(self.advise().stdout), self.context)
        request = {'argv': [sys.executable, '-B', 'probe.py'], 'inputs': [
            {'path': 'probe.py', 'roles': ['code'], 'sha256': file_sha(self.root / 'probe.py')}], 'outputs': [], 'timeout': 5}
        _charge_ledger(self.ledger, self.root / '.rds/exec/crashed-child', request, 5,
                       route=_loop_route(selected['candidate']), source_root=self.root)
        before = ProjectStore(self.ledger).snapshot()['budget']
        failed = self.job('after-charge-crash', False, *self.policy_options())
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn('charged child state is unavailable', failed.stderr)
        self.assertFalse((self.root / '.rds/exec/after-charge-crash').exists())
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before)

    def test_policy_guarded_success_reuses_original_review_and_missing_review_refuses(self):
        self.initialize_policy_ledger()
        marker = self.root / 'guard-launch-marker'
        comparison = {'question_id': 'fixture', 'goal_revision': 'fixed', 'scope': {'domain': 'fixture'},
                      'metric_definition': 'score', 'unit': 'ratio', 'cohort': 'fixed', 'protocol': 'fixed'}
        baseline = self.root / 'baseline.json'
        baseline.write_text(json.dumps({'status': 'PASS', 'comparison': comparison, 'score': '1'}), encoding='utf-8')
        policy = {'schema': 1, 'wall_seconds': 1, 'comparison': comparison, 'metrics': [
            {'name': 'score', 'direction': 'max', 'pointer': '/score', 'candidate': 'outputs/candidate.json',
             'baseline': {'path': 'baseline.json', 'sha256': hashlib.sha256(baseline.read_bytes()).hexdigest()}}]}
        (self.root / 'guard.json').write_text(json.dumps(policy), encoding='utf-8')
        self.script(f'from pathlib import Path\nPath({str(marker)!r}).write_text("launched")\n'
                    'Path("outputs/candidate.json").write_bytes(Path("baseline.json").read_bytes())\n')
        options = self.policy_options() + ['--guard', 'guard.json', '--output', 'outputs/candidate.json']
        first = self.job('guard-one', True, *options)
        saved = json.loads(Path(json.loads(first.stdout)['record']).read_text(encoding='utf-8'))
        child = ProjectStore(saved['job_root'])
        before = ProjectStore(self.ledger).snapshot()['budget']
        marker.unlink()
        second = self.job('guard-renamed', True, *options)
        reused = json.loads(Path(json.loads(second.stdout)['record']).read_text(encoding='utf-8'))
        self.assertEqual(reused['regression_review'], saved['regression_review'])
        self.assertEqual(reused['receipt']['sha256'], saved['receipt']['sha256'])
        self.assertFalse(marker.exists())
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before)
        with child._db() as db:
            # Model the post-command/pre-review crash window in this disposable ledger.
            db.execute('DROP TRIGGER events_no_delete')
            db.execute("DELETE FROM events WHERE json_extract(body,'$.kind')='QUICK_EXEC_REGRESSION_REVIEW'")
        rejected = self.job('guard-unfinished', False, *options)
        self.assertIn('Guard review is unfinished', rejected.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / '.rds/exec/guard-unfinished').exists())
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before)

    def test_policy_source_owner_applies_without_context_and_cannot_be_replaced(self):
        self.initialize_policy_ledger()
        marker = self.root / 'raw-owner-marker'
        (self.ledger / 'raw-fail.py').write_text(
            f'from pathlib import Path\nPath({str(marker)!r}).write_text("launched")\nraise SystemExit(7)\n', encoding='utf-8')
        first = self.call('exec', '--name', 'raw-one', '--timeout', '5', '--', sys.executable, '-B', 'raw-fail.py',
                          root=self.ledger, ok=False)
        self.assertNotEqual(first.returncode, 0)
        self.assertEqual(marker.read_text(), 'launched')
        marker.unlink()
        before = ProjectStore(self.ledger).snapshot()['budget']
        second = self.call('exec', '--name', 'raw-renamed', '--timeout', '3', '--', sys.executable, '-B', 'raw-fail.py',
                           root=self.ledger, ok=False)
        self.assertIn('max_attempts', second.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse((self.ledger / '.rds/exec/raw-renamed').exists())
        different = self.root / '.rds/exec/ledger'
        switched = self.call('exec', '--name', 'switched-owner', '--timeout', '3', '--context', str(self.context_path),
                            '--graph', str(self.graph_path), '--ledger', str(different), '--', sys.executable, '-B', 'raw-fail.py',
                            root=self.ledger, ok=False)
        self.assertIn('cannot be replaced', switched.stderr)
        self.assertFalse(marker.exists())
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before)

    def advise(self, *tail, ok=True):
        return self.call('advise', '--context', str(self.context_path), '--graph', str(self.graph_path), *tail, root=self.ledger, ok=ok)

    def test_root_level_file_output_is_authorized_at_its_own_path(self):
        # Issue #46: a valid-looking root-level filename must not be rejected by
        # inferring the file itself as a directory root. It keeps exact identity.
        self.script('from pathlib import Path\nPath("result.json").write_text(\'{"ok":true}\', encoding="utf-8")\n')
        result = json.loads(self.job('root-file', True, '--output', 'result.json').stdout)
        self.assertEqual(result['run_status'], 'SUCCEEDED')
        self.assertEqual(json.loads((Path(result['job_root']) / 'result.json').read_text(encoding='utf-8')), {'ok': True})

    def test_root_level_output_does_not_widen_the_boundary(self):
        # Declaring one root-level file must not authorize traversal or overwrite a
        # bound input; both fail before any execution is dispatched.
        self.script('print("noop")\n')
        traversal = self.job('traverse', False, '--output', '../escape.json')
        self.assertNotEqual(traversal.returncode, 0)
        self.assertIn('escapes project root', traversal.stderr)
        collision = self.job('collide', False, '--output', 'probe.py')
        self.assertNotEqual(collision.returncode, 0)
        self.assertIn('overwrites bound input', collision.stderr)

    def test_invalid_output_binding_is_rejected_before_parent_charge_or_child_creation(self):
        self.initialize_policy_ledger()
        marker = self.root / 'collision-launched'
        self.script(f'from pathlib import Path\nPath({str(marker)!r}).write_text("launched")\n')
        before_budget = ProjectStore(self.ledger).snapshot()['budget']
        before_children = set((self.root / '.rds/exec').iterdir())

        for name, output in [('collision-retry', 'probe.py'), ('nested-collision-retry', 'probe.py/child.json'), ('traversal-retry', '../escape.json')]:
            first = self.job(name, False, *self.policy_options(), '--output', output)
            second = self.job(name, False, *self.policy_options(), '--output', output)
            self.assertNotEqual(first.returncode, 0)
            self.assertNotEqual(second.returncode, 0)
            self.assertFalse((self.root / '.rds/exec' / name).exists())

        self.assertEqual(set((self.root / '.rds/exec').iterdir()), before_children)
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before_budget)
        self.assertFalse(marker.exists())
    def test_native_objective_without_operational_contract_keeps_quick_compatibility(self):
        from rds_math import bind_objective
        from test_rds_native_research import GOAL
        goal = bind_objective(self.root, json.dumps(GOAL).encode('utf-8'))
        self.script('print("native objective, no operational owner yet")\n')
        result = json.loads(self.job('objective-only').stdout)
        self.assertEqual(result['run_status'], 'SUCCEEDED')
        saved = json.loads(Path(result['record']).read_text(encoding='utf-8'))
        state = ProjectStore(saved['job_root']).snapshot(check_bindings=True)
        self.assertFalse(state['binding_check']['errors'])
        self.assertEqual(state['contract']['objective_sha256'], goal['asset']['sha256'])
        self.assertEqual(saved['scientific_support'], 'UNKNOWN')

    def test_prospective_theory_choice_preserves_outputs_without_claiming_proof(self):
        self.initialize_ledger()
        self.context['research_mode'] = 'theory'
        self.context_path.write_text(json.dumps(self.context), encoding='utf-8')
        action = self.graph['nodes'][0]['executable']['action']
        action.update(kind='OBLIGATION_CHECK', claim='Every rational square is nonnegative.',
            outcomes=[{'observation': label, 'next_decision': 'review ' + label}
                      for label in ('verified', 'counterexample', 'unresolved')])
        action.pop('competing_explanations')
        self.graph_path.write_text(json.dumps(self.graph), encoding='utf-8')
        self.script('from pathlib import Path\nPath("outputs/result.json").write_text(\'{"claim_status":"unresolved"}\')\n')
        result = json.loads(self.job('theory', True, '--context', str(self.context_path),
            '--graph', str(self.graph_path), '--ledger', str(self.ledger), '--output', 'outputs/result.json').stdout)
        self.assertEqual(result['run_status'], 'SUCCEEDED')
        self.assertEqual(result['ledger_checkpoints'], 2)
        report = json.loads(Path(result['record']).read_text(encoding='utf-8'))
        self.assertIn('UNKNOWN', report['scientific_support'])
        self.assertEqual(json.loads((Path(report['job_root']) / 'outputs/result.json').read_text()),
                         {'claim_status': 'unresolved'})

    def test_theory_with_missing_domain_premise_stops_before_budget_charge(self):
        self.initialize_ledger()
        self.context['research_mode'] = 'theory'
        self.context_path.write_text(json.dumps(self.context), encoding='utf-8')
        config = self.graph['nodes'][0]['executable']
        config['preconditions'] = [{'fact': 'domain-exhaustive', 'value': True}]
        config['action'].update(kind='OBLIGATION_CHECK', claim='Every case is covered.',
            outcomes=[{'observation': label, 'next_decision': 'review ' + label}
                      for label in ('verified', 'counterexample', 'unresolved')])
        config['action'].pop('competing_explanations')
        self.graph_path.write_text(json.dumps(self.graph), encoding='utf-8')
        before = ProjectStore(self.ledger).snapshot()['budget']
        failed = self.job('missing-proof-premise', False, '--context', str(self.context_path),
            '--graph', str(self.graph_path), '--ledger', str(self.ledger))
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before)
        self.assertFalse((self.root / '.rds/exec/missing-proof-premise').exists())

    def test_discarded_obligation_cli_explains_missing_outcome_before_any_job_or_ledger_write(self):
        self.initialize_ledger()
        action = self.graph['nodes'][0]['executable']['action']
        action.update(kind='OBLIGATION_CHECK', claim='Synthetic scoped claim.',
            outcomes=[{'observation': label, 'next_decision': 'review ' + label}
                      for label in ('verified', 'unresolved')])
        action.pop('competing_explanations')
        self.graph_path.write_text(json.dumps(self.graph), encoding='utf-8')
        inputs = {p: p.read_bytes() for p in (self.context_path, self.graph_path, self.root / 'probe.py')}
        before = ProjectStore(self.ledger).snapshot()
        with closing(sqlite3.connect(self.ledger / '.rds/project.sqlite3')) as db:
            ledger_before = list(db.iterdump())
        for index, selected in enumerate(('route:inspect-x', 'inspect-x', None)):
            with self.subTest(selected=selected):
                name = 'missing-outcome-' + str(index)
                options = ['--context', str(self.context_path), '--graph', str(self.graph_path), '--ledger', str(self.ledger)]
                if selected is not None:
                    options += ['--choose', selected]
                failed = self.job(name, False, *options)
                self.assertNotEqual(failed.returncode, 0)
                self.assertIn('obligation outcomes must distinguish verified, counterexample and unresolved', failed.stderr)
                self.assertNotIn('missing, pruned or ambiguous', failed.stderr)
                self.assertFalse((self.root / '.rds/exec' / name).exists())
                self.assertEqual(ProjectStore(self.ledger).snapshot(), before)
        with closing(sqlite3.connect(self.ledger / '.rds/project.sqlite3')) as db:
            self.assertEqual(list(db.iterdump()), ledger_before)
        self.assertTrue(all(p.read_bytes() == raw for p, raw in inputs.items()))

    def test_discarded_diagnostics_use_exact_identity_and_do_not_overshadow_ready_choice(self):
        from rds_advisor_search import search_directions
        from rds_quick import choice
        self.initialize_ledger()
        invalid = self.graph['nodes'][0]
        ready = copy.deepcopy(invalid)
        ready['id'] = 'healthy'
        complete = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH',
            'search': search_directions({'nodes': [ready], 'edges': []}, self.context)}]}
        self.assertTrue(complete['recommendations'][0]['search']['analysis_coverage']['full'])
        complete_original = copy.deepcopy((complete, self.context))
        for selected in ('healthy:inspect-x', 'inspect-x', None):
            self.assertEqual(choice(complete, self.context, selected)['candidate']['id'], 'healthy:inspect-x')
        for selected in ('nonexistent', 'route', 'route:inspect', 'inspect'):
            with self.subTest(selected=selected), self.assertRaisesRegex(ValueError, 'missing, pruned or ambiguous candidate'):
                choice(complete, self.context, selected)
        self.assertEqual((complete, self.context), complete_original)
        # Once an invalid primary is declared, even the healthy route cannot
        # be selected until complete analysis is restored. Exact diagnostic
        # lookup remains read-only and never licenses execution.
        invalid['executable']['action']['required_observables'] = []
        self.graph['nodes'].append(ready)
        advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH',
            'search': search_directions(self.graph, self.context)}]}
        original = copy.deepcopy((advice, self.context))
        self.assertFalse(advice['recommendations'][0]['search']['analysis_coverage']['full'])
        for selected in ('healthy:inspect-x', 'inspect-x', None):
            with self.subTest(selected=selected), self.assertRaisesRegex(ValueError, 'Complete graph analysis required'):
                choice(advice, self.context, selected)
        with self.assertRaisesRegex(ValueError, 'Selected candidate was discarded.*missing required observables'):
            choice(advice, self.context, 'route:inspect-x')
        for selected in ('nonexistent', 'route', 'route:inspect', 'inspect'):
            with self.subTest(selected=selected), self.assertRaisesRegex(ValueError, 'Complete graph analysis required') as error:
                choice(advice, self.context, selected)
            self.assertNotIn('missing required observables', str(error.exception))
        self.assertEqual((advice, self.context), original)

    def test_choice_ambiguity_does_not_attribute_one_discarded_reason(self):
        from rds_advisor_search import search_directions
        from rds_quick import choice
        self.initialize_ledger()
        other = copy.deepcopy(self.graph['nodes'][0])
        other['id'] = 'other'
        self.graph['nodes'].append(other)
        def advice():
            return {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH',
                'search': search_directions(self.graph, self.context)}]}
        self.assertTrue(advice()['recommendations'][0]['search']['analysis_coverage']['full'])
        with self.assertRaisesRegex(ValueError, 'ambiguous candidate') as error:
            choice(advice(), self.context, 'inspect-x')
        self.assertNotIn('missing required observables', str(error.exception))
        invalid = copy.deepcopy(other)
        invalid['id'] = 'invalid'
        invalid['executable']['action']['required_observables'] = []
        self.graph['nodes'].append(invalid)
        self.assertFalse(advice()['recommendations'][0]['search']['analysis_coverage']['full'])
        with self.assertRaisesRegex(ValueError, 'Complete graph analysis required') as error:
            choice(advice(), self.context, 'inspect-x')
        self.assertNotIn('missing required observables', str(error.exception))
        with self.assertRaisesRegex(ValueError, 'Selected candidate was discarded.*missing required observables'):
            choice(advice(), self.context, 'invalid:inspect-x')
        self.graph['nodes'][0]['executable']['action']['outcomes'] = []
        self.graph['nodes'][1]['executable']['action']['required_observables'] = []
        with self.assertRaisesRegex(ValueError, 'Complete graph analysis required') as error:
            choice(advice(), self.context, 'inspect-x')
        self.assertNotIn('no outcome', str(error.exception))
        with self.assertRaisesRegex(ValueError, 'no outcome can distinguish next decisions'):
            choice(advice(), self.context, 'route:inspect-x')

    def test_discard_hints_escape_and_bound_text_while_cas_preserves_original_reasons(self):
        from rds_quick import cas_json, choice
        context = {'decision': {'id': 'd', 'goal_revision': 'g', 'scope': {'domain': 'synthetic'}}}
        identity = 'rule:\x1b\n\r\t\x00\x7f\u202e' + 'i' * 1000
        reason = 'invalid\x1b\n\r\t\x00\x7f\u202e' + 'r' * 10000
        discarded = [{'id': identity, 'action_id': 'a', 'reason': reason}]
        discarded += [{'id': 'extra-' + str(i), 'reason': 'extra reason'} for i in range(9)]
        advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH',
            'search': {'candidates': [], 'discarded_candidates': discarded}}]}
        original = copy.deepcopy(advice)
        with self.assertRaisesRegex(ValueError, 'Selected candidate was discarded') as error:
            choice(advice, context, identity)
        text = str(error.exception)
        self.assertLess(len(text), 500)
        self.assertTrue(all(ord(char) >= 32 and ord(char) != 127 for char in text))
        self.assertNotIn('\u202e', text)
        self.assertIn('\\u001b', text)
        with self.assertRaisesRegex(ValueError, 'No READY candidate') as error:
            choice(advice, context)
        self.assertLess(len(str(error.exception)), 1100)
        self.assertIn('7 more discarded actions', str(error.exception))
        self.assertNotIn('extra-2', str(error.exception))
        self.assertEqual(advice, original)
        ref = cas_json(self.root, advice)
        self.assertEqual(json.loads(Path(ref['path']).read_text(encoding='utf-8')), original)

    def test_missing_action_identity_is_not_guessed_from_rule_prefix(self):
        from rds_advisor_search import search_directions
        from rds_quick import choice
        self.initialize_ledger()
        self.graph['nodes'][0]['executable']['action'] = None
        advice = {'recommendations': [{'type': 'EXECUTABLE_DIRECTION_SEARCH',
            'search': search_directions(self.graph, self.context)}]}
        search = advice['recommendations'][0]['search']
        self.assertFalse(search['analysis_coverage']['full'])
        self.assertEqual(search['discarded_candidates'], [{'rule_id': 'route', 'reason': 'missing action identity'}])
        with self.assertRaisesRegex(ValueError, 'Complete graph analysis required'):
            choice(advice, self.context, 'route:inspect-x')
        with self.assertRaisesRegex(ValueError, 'No READY candidate.*missing action identity'):
            choice(advice, self.context)

    def test_one_call_completes_identity_preserves_output_and_never_claims_scientific_pass(self):
        self.script()
        result = json.loads(self.job().stdout)
        self.assertEqual(result['run_status'], 'SUCCEEDED')
        self.assertNotIn('badge', result)
        self.assertEqual(result['ledger_checkpoints'], 0)
        self.assertLess(len(json.dumps(result)), 1300)
        record = Path(result['record']).read_bytes()
        self.assertEqual(hashlib.sha256(record).hexdigest(), result['sha256'])
        report = json.loads(record)
        store = ProjectStore(report['job_root'])
        state = store.snapshot(check_bindings=True)
        self.assertFalse(state['binding_check']['errors'])
        receipt = state['receipts'][0]
        self.assertEqual(receipt['assessment']['task_gain'], 'UNKNOWN')
        logs = [a for a in receipt['artifacts'] if a['kind'] == 'stdout.bin']
        self.assertGreater((Path(report['job_root']) / logs[0]['path']).stat().st_size, 20000)
        self.assertEqual(state['runs'][0]['protocol']['seed'], 'UNKNOWN')

    def test_existing_job_is_not_reexecuted_and_changed_dependency_needs_new_identity(self):
        (self.root / 'helper.py').write_text('value = 3\n', encoding='utf-8')
        self.script('import helper\nprint(helper.value)\n')
        first = json.loads(self.job().stdout)
        second = json.loads(self.job().stdout)
        full = json.loads(Path(second['record']).read_text(encoding='utf-8'))
        self.assertFalse(full['execution_started'])
        self.assertEqual(full['receipt']['sha256'], json.loads(Path(first['record']).read_text(encoding='utf-8'))['receipt']['sha256'])
        (self.root / 'helper.py').write_text('value = 4\n', encoding='utf-8')
        bad = self.job(ok=False)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn('new --name', bad.stderr)

    def test_failed_command_preserves_receipt_without_automatic_scientific_rejection(self):
        self.script('raise RuntimeError("preserved failure")\n')
        result = self.job(ok=False)
        self.assertEqual(result.returncode, 1)
        digest = json.loads(result.stdout)
        report = json.loads(Path(digest['record']).read_text(encoding='utf-8'))
        self.assertEqual(report['receipt']['run_status'], 'FAILED')
        self.assertEqual(report['receipt']['assessment']['mechanism'], 'UNKNOWN')
        self.assertFalse((Path(report['job_root']) / '.rds/project.sqlite3').is_symlink())
        status = self.call('project', 'status', '--brief', root=report['job_root'])
        summary = json.loads(status.stdout)
        self.assertIn('run_states', summary, status.stdout)
        self.assertEqual(summary['status'], 'RECORDED')
        self.assertEqual(summary['run_states'], {'FAILED': 1})
        self.assertEqual((summary['runs'], summary['receipts']), (1, 1))
        receipt = summary['latest_receipt']
        self.assertEqual((receipt['run_id'], receipt['run_status'], receipt['exit_code']),
                         (report['receipt']['run_id'], 'FAILED', 1))
        stderr = Path(receipt['stderr_path'])
        self.assertTrue(stderr.is_absolute())
        self.assertIn('preserved failure', stderr.read_text(encoding='utf-8'))
        raw = Path(summary['record']).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), summary['sha256'])
        full = json.loads(raw)
        self.assertEqual(full['receipts'][0]['assessment'], {'task_gain': 'UNKNOWN', 'mechanism': 'UNKNOWN'})
        self.assertNotIn('preserved failure', status.stdout)

    def test_exit_zero_with_missing_output_exposes_receipt_errors_in_status(self):
        self.script('print("PASS")\n')
        result = self.job('missing-output', False, '--output', 'outputs/required.json')
        self.assertEqual(result.returncode, 1)
        report = json.loads(Path(json.loads(result.stdout)['record']).read_text(encoding='utf-8'))
        receipt = report['receipt']
        self.assertEqual((receipt['exit_code'], receipt['run_status']), (0, 'FAILED'))
        self.assertTrue(any('Missing output:' in error for error in receipt['errors']))
        self.assertEqual(receipt['assessment'], {'task_gain': 'UNKNOWN', 'mechanism': 'UNKNOWN'})
        self.assertTrue(any(artifact['kind'] == 'stderr.bin' and artifact['size'] == 0
                            for artifact in receipt['artifacts']))
        stdout = next(artifact for artifact in receipt['artifacts'] if artifact['kind'] == 'stdout.bin')
        self.assertEqual((Path(receipt['cwd']) / stdout['path']).read_text(encoding='utf-8').strip(), 'PASS')
        status = self.call('project', 'status', '--brief', root=report['job_root'])
        summary = json.loads(status.stdout)
        latest = summary['latest_receipt']
        self.assertIn('errors', latest, 'Actual project status --brief: ' + status.stdout)
        self.assertEqual(latest['error_count'], len(receipt['errors']))
        self.assertEqual(latest['errors'], receipt['errors'][:1])
        self.assertEqual((latest['run_status'], latest['exit_code']), ('FAILED', 0))
        self.assertNotIn('stderr_path', latest)
        self.assertEqual(summary['status'], 'RECORDED')
        self.assertEqual(summary['run_states'], {'FAILED': 1})

    def test_project_brief_bounds_errors_and_preserves_full_cas(self):
        from rds_quick import brief
        errors = ['Missing output: ' + 'a' * 300, 'second error: ' + 'b' * 300]
        value = {'runs': [{'status': 'FAILED'}], 'receipts': [
            {'run_id': 'missing-output', 'run_status': 'FAILED', 'exit_code': 0,
             'ended_at': 10.0, 'cwd': str(self.root), 'errors': errors}]}
        summary = brief(self.root, value, 'test')
        latest = summary['latest_receipt']
        self.assertEqual(latest['error_count'], 2)
        self.assertEqual(latest['errors'], [errors[0][:197] + '...'])
        self.assertLessEqual(len(latest['errors'][0]), 200)
        self.assertEqual(json.loads(Path(summary['record']).read_text(encoding='utf-8')), value)
        self.assertEqual(value['receipts'][0]['errors'], errors)

    def test_project_brief_distinguishes_live_states_and_latest_finished_receipt(self):
        from rds_quick import brief
        runs = [{'status': 'RUNNING', 'run_status': 'RUNNING'},
                {'status': 'RESERVED', 'run_status': 'RESERVED'},
                {'status': 'COMPLETED', 'run_status': 'SUCCEEDED'}]
        value = {'runs': runs, 'receipts': []}
        summary = brief(self.root, value, 'test')
        self.assertEqual(summary['status'], 'RECORDED')
        self.assertEqual(summary['run_states'], {'RUNNING': 1, 'RESERVED': 1, 'COMPLETED': 1})
        self.assertNotIn('latest_receipt', summary)
        # Snapshot receipt ordering is by run ID, not finishing time.
        value['receipts'] = [
            {'run_id': 'a-new', 'run_status': 'FAILED', 'exit_code': 7, 'ended_at': 20.0,
             'cwd': str(self.root), 'errors': [],
             'artifacts': [{'kind': 'stderr.bin', 'path': 'error.bin', 'size': 0}]},
            {'run_id': 'z-old', 'run_status': 'SUCCEEDED', 'exit_code': 0, 'ended_at': 10.0,
             'cwd': str(self.root), 'artifacts': []},
            {'run_id': 'missing-time', 'cwd': str(self.root)},
            {'run_id': 'missing-cwd', 'ended_at': 30.0}]
        summary = brief(self.root, value, 'test')
        self.assertEqual(summary['latest_receipt'], {'run_id': 'a-new', 'run_status': 'FAILED', 'exit_code': 7})
        self.assertEqual(summary['run_states'], {'RUNNING': 1, 'RESERVED': 1, 'COMPLETED': 1})
        self.assertNotIn('run_status', summary)

    def test_aliases_and_unique_prefixes_preserve_command_tail_and_values(self):
        from rds_cli import parser
        from rds_usage import _label
        self.assertEqual(_label(['--workspace', 'project', 'advisor', '--brief']), ('advise', 'command'))
        self.assertEqual(_label(['execute', '--name', 'x', '--', 'python', 'usage']), ('exec', 'command'))
        self.assertEqual(_label(['execute', '--name', 'x', '--', 'python', '--help']), ('exec', 'command'))
        self.assertEqual(_label(['misspelled', '--', 'usage']), ('other', 'command'))
        args = parser().parse_args(['proj', 'stat', '--workspace', str(self.root), '--digest'])
        self.assertEqual((args.command, args.action, args.root), ('project', 'status', str(self.root)))
        args = parser().parse_args(['deny', '--route', 'r', '--reason=--root', '--evidence', 'f'])
        self.assertEqual(args.reason, '--root')
        args = parser().parse_args(['exe', '--name', 'same', '--', 'python', 'probe.py', '--workspace', 'unchanged'])
        self.assertEqual(args.argv, ['--', 'python', 'probe.py', '--workspace', 'unchanged'])

    def test_ambiguous_prefix_cannot_mutate_or_execute(self):
        bad = self.call('adv', ok=False)
        self.assertEqual(bad.returncode, 2)
        self.assertIn('advise, advancement', bad.stderr)
        self.assertFalse((self.root / '.rds').exists())

    def test_scoped_choice_and_reject_fields_are_completed_and_consumed_by_next_advice(self):
        self.initialize_ledger()
        output = json.loads(self.advise().stdout)
        candidate = next(r for r in output['recommendations'] if r.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')['search']['candidates'][0]
        recorded = json.loads(self.advise('--choose', candidate['id'], '--record', 'before', '--brief').stdout)
        self.assertEqual(recorded['checkpoint'], 'before')
        witness = self.root / 'witness.json'
        witness.write_text('{"counterexample":"declared software fixture"}', encoding='utf-8')
        result = json.loads(self.call('reject', '--route', candidate['id'], '--reason', 'scoped fixture rejection',
                                     '--evidence', str(witness), root=self.ledger).stdout)
        self.assertEqual(result['status'], 'RECORDED_REJECTION')
        after = json.loads(self.advise().stdout)
        search = next(r for r in after['recommendations'] if r.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')['search']
        self.assertEqual(search['candidates'], [])
        self.assertEqual(search['blocked_candidates'][-1]['loop_review']['kind'], 'REPEAT_REJECTED_ROUTE')
        self.assertEqual(search['selection_review']['ready_graph_directions'], 0)
        self.assertEqual(search['selection_review']['basis'], 'NO_READY_DIRECTION')
        self.context['facts']['x']['source'] = {'path': 'new-witness.json', 'sha256': 'f' * 64}
        self.context_path.write_text(json.dumps(self.context), encoding='utf-8')
        reopened = json.loads(self.advise().stdout)
        search = next(r for r in reopened['recommendations'] if r.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')['search']
        self.assertEqual(len(search['candidates']), 1)

    def test_prospective_exec_records_before_execution_and_unchanged_rejection_blocks_new_job(self):
        self.initialize_ledger()
        output = json.loads(self.advise().stdout)
        candidate = next(r for r in output['recommendations'] if r.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')['search']['candidates'][0]
        self.script('print("actual bounded probe")\n')
        options = ['--context', str(self.context_path), '--graph', str(self.graph_path), '--choose', candidate['id'], '--ledger', str(self.ledger)]
        first = json.loads(self.job('first', True, *options).stdout)
        self.assertEqual(first['ledger_checkpoints'], 2)
        with closing(sqlite3.connect(self.ledger / '.rds/project.sqlite3')) as db:
            rows = db.execute('SELECT id,body FROM checkpoints ORDER BY rowid').fetchall()
        self.assertEqual([r[0] for r in rows], ['exec-before-first', 'exec-after-first'])
        self.assertNotIn('execution', json.loads(rows[0][1])['decision'])
        self.assertEqual(json.loads(rows[0][1])['decision']['selection_review']['basis'], 'REVIEW_ONLY')
        self.assertEqual(json.loads(rows[1][1])['decision']['execution']['run_status'], 'SUCCEEDED')
        witness = self.root / 'witness.json'
        witness.write_text('{}', encoding='utf-8')
        self.call('reject', '--route', candidate['id'], '--reason', 'fixture', '--evidence', str(witness), root=self.ledger)
        bad = self.job('second', False, *options)
        self.assertNotEqual(bad.returncode, 0)
        self.assertFalse((self.root / '.rds/exec/second').exists())

    def test_missing_scope_or_unrecorded_route_cannot_be_auto_invented(self):
        self.initialize_ledger()
        witness = self.root / 'witness.json'
        witness.write_text('{}', encoding='utf-8')
        bad = self.call('reject', '--route', 'unknown', '--reason', 'test', '--evidence', str(witness), root=self.ledger, ok=False)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn('No decision context', bad.stderr)

    def test_two_field_array_scope_stops_before_launch_or_charge_and_atoms_still_execute(self):
        self.initialize_ledger()
        marker = self.root / 'scope-process-started'
        self.script('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_text("started")\n')
        self.context['decision']['scope'] = {'N_range': [30, 40], 'purpose': 'synthetic scope regression'}
        self.context_path.write_text(json.dumps(self.context), encoding='utf-8')
        original = self.context_path.read_bytes()
        before = ProjectStore(self.ledger).snapshot()['budget']
        options = ['--context', str(self.context_path), '--graph', str(self.graph_path), '--ledger', str(self.ledger)]
        bad = self.job('array-scope', False, *options)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn('scope/parameters field "N_range" must be a JSON atom; got array (list)', bad.stderr)
        self.assertIn('structured original input', bad.stderr)
        self.assertIn('explicit source binding', bad.stderr)
        self.assertNotIn('at most 16', bad.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse((self.root / '.rds/exec/array-scope').exists())
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before)
        self.assertEqual(self.context_path.read_bytes(), original)
        self.context['decision']['scope'] = {'N': 30, 'purpose': 'synthetic scope regression'}
        self.context_path.write_text(json.dumps(self.context), encoding='utf-8')
        good = json.loads(self.job('atomic-scope', True, *options).stdout)
        self.assertEqual(good['run_status'], 'SUCCEEDED')
        self.assertEqual(good['ledger_checkpoints'], 2)
        self.assertEqual(marker.read_text(), 'started')

    def test_unresolved_method_scope_never_launches_or_charges_and_confirmation_is_recorded(self):
        self.initialize_ledger()
        self.context['method_constraints'] = [{'id': 'search-scope', 'quote': 'No numerical search',
            'source': 'user:fixture', 'status': 'UNRESOLVED', 'when': {'purpose': 'proof'},
            'question': 'May a certified exact proof use branch and bound?'}]
        self.graph['nodes'][0]['executable']['action']['methods'] = {
            'purpose': 'proof', 'technique': 'certifying_branch_bound', 'device': 'cpu'}
        self.context_path.write_text(json.dumps(self.context), encoding='utf-8')
        self.graph_path.write_text(json.dumps(self.graph), encoding='utf-8')
        options = ['--context', str(self.context_path), '--graph', str(self.graph_path),
                   '--choose', 'route:inspect-x', '--ledger', str(self.ledger)]
        before = ProjectStore(self.ledger).snapshot()['budget']
        for tail in (options, [x for x in options if x not in ('--choose', 'route:inspect-x')]):
            bad = self.job('unclear', False, *tail)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn('Method scope', bad.stderr)
            self.assertFalse((self.root / '.rds/exec/unclear').exists())
        self.assertEqual(ProjectStore(self.ledger).snapshot()['budget'], before)
        self.context['method_constraints'][0].update(status='CONFIRMED', when={}, require={'device': 'cpu'},
            confirmation={'quote': 'Strict assisted proof is allowed on CPU', 'source': 'user-confirmation:fixture'})
        self.context_path.write_text(json.dumps(self.context), encoding='utf-8')
        good = json.loads(self.job('confirmed', True, *options).stdout)
        self.assertEqual(good['run_status'], 'SUCCEEDED')
        from rds_quick import latest_decision
        decision, _ = latest_decision(self.ledger)
        self.assertEqual(decision['method_constraints'], self.context['method_constraints'])
        self.assertEqual(decision['candidate']['method_review']['status'], 'COMPATIBLE')
        self.assertEqual(decision['scientific_support'], 'UNKNOWN')

    def test_timeout_keeps_failure_logs_and_budget(self):
        self.script('import time\nprint("before timeout", flush=True)\ntime.sleep(20)\n')
        bad = self.call('exec', '--name', 'timed', '--timeout', '0.15', '--', sys.executable, '-B', 'probe.py', ok=False)
        self.assertNotEqual(bad.returncode, 0)
        report = json.loads(Path(json.loads(bad.stdout)['record']).read_text(encoding='utf-8'))
        state = ProjectStore(report['job_root']).snapshot()
        self.assertEqual(state['runs'][0]['status'], 'FAILED')
        self.assertEqual(state['budget']['wall_seconds']['reserved'], 0)
        self.assertGreater(state['budget']['wall_seconds']['spent_measured'], 0)
        self.assertIn('UNKNOWN', report['scientific_support'])

    def test_tampered_cas_cannot_be_reused(self):
        from rds_quick import cas_json
        value = {'status': 'UNKNOWN'}
        ref = cas_json(self.root, value)
        Path(ref['path']).write_bytes(b'false proof')
        with self.assertRaisesRegex(ValueError, 'CAS integrity'):
            cas_json(self.root, value)

    def test_shell_and_outside_input_are_rejected_before_launch(self):
        self.script()
        bad = self.call('exec', '--name', 'shell', '--', 'cmd' if os.name == 'nt' else 'sh', ok=False)
        self.assertNotEqual(bad.returncode, 0)
        self.assertFalse((self.root / '.rds/exec/shell').exists())
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / 'outside.py'
            outside.write_text('print("outside")', encoding='utf-8')
            bad = self.call('exec', '--name', 'outside', '--', sys.executable, str(outside), ok=False)
            self.assertNotEqual(bad.returncode, 0)
            self.assertFalse((self.root / '.rds/exec/outside').exists())

    def test_unique_candidate_and_python_witness_completion_share_the_real_ledger(self):
        from rds_quick import record_falsification
        self.initialize_ledger()
        result = json.loads(self.advise('--record', 'unique-choice', '--brief').stdout)
        self.assertEqual(result['checkpoint'], 'unique-choice')
        self.assertEqual(result['ledger_checkpoints'], 1)
        rejected = record_falsification(self.ledger, witness={'declared_bad_case': 'synthetic'}, reason='scoped fixture')
        self.assertEqual(rejected['status'], 'RECORDED_REJECTION')
        output = json.loads(self.advise('--brief').stdout)
        self.assertIn('REPEAT_REJECTED_ROUTE', output['flags'])

    def test_rejections_recorded_for_the_same_goal_survive_renamed_questions(self):
        from rds_checkpoints import save_checkpoint
        self.initialize_ledger()
        goal = [{'fact': 'long_gain', 'op': 'gte', 'value': 0.05}]
        witness = self.root / 'witness.json'
        witness.write_text('{"long_gain": -0.6}', encoding='utf-8')

        def write(question, parameters, goal_conditions=goal, scope=None, kind='PAIRED_TEST', operation='train', revision='growth-v1'):
            self.context = {'research_mode': 'theory' if kind == 'OBLIGATION_CHECK' else 'empirical',
                            'decision': {'id': question, 'goal_revision': revision, 'goal_conditions': goal_conditions,
                                         'scope': scope or {'domain': 'synthetic'}},
                            'facts': {'long_gain': {'value': None, 'source': 'unmeasured-current-scope.json'}}}
            if goal_conditions is None:
                self.context['decision'].pop('goal_conditions')
            action = {'id': 'recipe', 'kind': kind, 'description': 'Train a scoped recipe and measure the declared gain',
                      'operation': operation, 'target': 'Measure long gain', 'parameters': parameters,
                      'goal_contribution': {'target': 'long_gain', 'path': ['Measure long gain'], 'source': 'protocol.json'},
                      'competing_explanations': ['recipe-capacity', 'training-premise'], 'required_observables': ['long_gain'],
                      'outcomes': [{'observation': 'gain', 'next_decision': 'confirm'},
                                   {'observation': 'no gain', 'next_decision': 'revise'}]}
            if kind == 'OBLIGATION_CHECK':
                action.pop('competing_explanations')
                action.update(claim='A scoped implication', outcomes=[{'observation': label, 'next_decision': label}
                              for label in ('verified', 'counterexample', 'unresolved')])
            self.graph = {'nodes': [{'id': 'route', 'sources': ['fixture'], 'executable': {
                'decisions': [question], 'preconditions': [], 'action': action}}], 'edges': []}
            self.context_path.write_text(json.dumps(self.context), encoding='utf-8')
            self.graph_path.write_text(json.dumps(self.graph), encoding='utf-8')

        def full():
            advice = json.loads(self.advise().stdout)
            search = next(r['search'] for r in advice['recommendations'] if r.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
            flags = {f['kind']: f for f in search.get('loop_review', {}).get('flags', [])}
            return search, flags, search['selection_review'].get('next_move', {}).get('kind')

        write('round-1', {'gain': 16})
        first = json.loads(self.advise('--brief').stdout)
        self.assertEqual(first['next_move'], 'RESOLVE_PREMISE')  # A first unmeasured attempt need not jump.
        self.advise('--record', 'round-1-choice')
        self.call('reject', '--reason', 'locked final gain failed', '--evidence', str(witness), root=self.ledger)

        # A renamed question that only adds a knob: still allowed, but the recorded rejection reaches the next move.
        write('round-2', {'gain': 16, 'tau': 0.5})
        before = ProjectStore(self.ledger).snapshot()
        search, flags, move = full()
        self.assertEqual(ProjectStore(self.ledger).snapshot(), before)
        self.assertEqual([c['status'] for c in search['candidates']], ['READY'])
        self.assertEqual(flags['GOAL_ROUTES_REJECTED']['rejected_routes'], 1)
        self.assertEqual(flags['GOAL_ROUTES_REJECTED']['question_ids'], ['round-1'])
        self.assertEqual(flags['GOAL_ROUTES_REJECTED']['candidate_ids'], [search['candidates'][0]['id']])
        self.assertEqual(move, 'REFORMULATE')
        reason = search['selection_review']['next_move']['reason']
        self.assertIn('changes only parameters', reason)
        self.assertIn('not a capacity bound', reason)
        self.assertEqual(search['selection_review']['next_move']['authorization'], 'UNCHANGED')
        brief = json.loads(self.advise('--brief').stdout)
        self.assertEqual(brief['next_move'], 'REFORMULATE')
        self.assertIn('GOAL_ROUTES_REJECTED', brief['flags'])
        self.assertEqual(json.loads(self.advise('--record', 'round-2-choice', '--brief').stdout)['checkpoint'], 'round-2-choice')

        # Renaming the question does not reopen the unchanged rejected route.
        write('round-3', {'gain': 16})
        search, flags, move = full()
        self.assertEqual(search['candidates'], [])
        self.assertEqual(search['blocked_candidates'][0]['status'], 'BLOCKED_REJECTED_ROUTE')
        self.assertEqual(flags['REPEAT_REJECTED_ROUTE']['recorded_question_id'], 'round-1')
        self.assertNotIn('GOAL_ROUTES_REJECTED', flags)

        # A changed scope reopens the same route for review; it is not called another variant.
        write('round-3', {'gain': 16}, scope={'domain': 'synthetic', 'data': 'wider'})
        search, flags, move = full()
        self.assertEqual(search['candidates'][0]['loop_review']['kind'], 'REOPEN_REVIEW')
        self.assertNotIn('GOAL_ROUTES_REJECTED', flags)
        self.assertEqual(move, 'RESOLVE_PREMISE')

        # A changed operation, other predicates, another revision or no predicates share no variant history.
        for options in ({'operation': 'distill'}, {'goal_conditions': [{'fact': 'long_gain', 'op': 'gte', 'value': 0.1}]},
                        {'revision': 'growth-v2'}, {'goal_conditions': None}):
            with self.subTest(options=options):
                write('round-4', {'gain': 16, 'tau': 0.9}, **options)
                search, flags, move = full()
                self.assertEqual([c['status'] for c in search['candidates']], ['READY'])
                self.assertNotIn('GOAL_ROUTES_REJECTED', flags)
                self.assertNotEqual(move, 'REFORMULATE')

        # Predicate order does not change goal identity.
        write('round-4', {'gain': 8}, goal_conditions=[{'value': 0.05, 'op': 'gte', 'fact': 'long_gain'}])
        self.assertIn('GOAL_ROUTES_REJECTED', full()[1])

        # A healthy scoped obligation check is not interrupted by the history prompt.
        write('round-5', {'gain': 32}, kind='OBLIGATION_CHECK')
        rejected_obligation = {'question_id': 'round-0', 'goal_revision': 'growth-v1', 'goal_conditions': goal,
            'scope': {'domain': 'synthetic'}, 'candidate': {'id': 'route:recipe', 'status': 'READY',
            'action': {**self.graph['nodes'][0]['executable']['action'], 'parameters': {'gain': 4}}},
            'outcome': 'rejected', 'evidence': copy.deepcopy(self.context['facts'])}  # Same relevant evidence as this check.
        save_checkpoint(self.ledger, 'rejected-obligation', ProjectStore(self.ledger).snapshot(check_bindings=True),
                        kind='project', decision=rejected_obligation)
        search, flags, move = full()
        self.assertIn('GOAL_ROUTES_REJECTED', flags)
        self.assertEqual(move, 'RESOLVE_PREMISE')

        # Another question's malformed record cannot block this question, and partial same-goal history is not applied.
        write('round-6', {'gain': 16, 'tau': 0.7})
        self.assertEqual(full()[2], 'REFORMULATE')
        # save_checkpoint now refuses this record (#153); plant it as a record saved before that check.
        from rds_checkpoints import SCHEMA, _raw, _sha
        snapshot = ProjectStore(self.ledger).snapshot(check_bindings=True)
        raw = _raw({'schema': SCHEMA, 'id': 'malformed-other', 'kind': 'project', 'created_ns': 1,
                    'contract_sha256': _sha(snapshot['contract']), 'snapshot': snapshot,
                    'decision': {**rejected_obligation, 'question_id': 'other', 'outcome': 'unknown-label'},
                    'decision_assurance': 'RECORDED_INPUT_NOT_SCIENTIFIC_VERIFICATION'})
        with closing(sqlite3.connect(self.ledger / '.rds/project.sqlite3')) as db:
            db.execute('INSERT INTO checkpoints VALUES (?,?,?)', ('malformed-other', hashlib.sha256(raw.encode('utf-8')).hexdigest(), raw))
            db.commit()
        search, flags, move = full()
        self.assertNotIn('LOOP_HISTORY_REVIEW_ERROR', flags)
        self.assertEqual(flags['GOAL_HISTORY_SKIPPED']['checkpoint_ids'], ['malformed-other'])
        self.assertNotIn('GOAL_ROUTES_REJECTED', flags)
        self.assertEqual(move, 'RESOLVE_PREMISE')
        self.assertIn('GOAL_HISTORY_SKIPPED', json.loads(self.advise('--brief').stdout)['flags'])
        self.assertEqual(json.loads(self.advise('--record', 'round-6-choice', '--brief').stdout)['checkpoint'], 'round-6-choice')

    def test_latest_same_goal_choice_supersedes_a_recorded_rejection(self):
        from test_rds_advisor import LedgerLoopTests
        from rds_checkpoints import save_checkpoint
        helper = LedgerLoopTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        context = copy.deepcopy(helper.context)
        context['decision']['goal_conditions'] = [{'fact': 'goal', 'value': True}]
        context['facts']['goal'] = {'value': None, 'source': 'unmeasured.json'}
        graph = copy.deepcopy(helper.graph)
        graph['nodes'][0]['executable']['action']['parameters'] = {'p': 2}
        variant = helper.search(context=context, graph=graph)['search']['candidates'][0]
        rejected = copy.deepcopy(variant)
        rejected['action']['parameters'] = {'p': 1}

        def save(identity, outcome):
            decision = context['decision']
            save_checkpoint(helper.root, identity, helper.store.snapshot(), kind='project', decision={
                'question_id': 'earlier-question', 'goal_revision': decision['goal_revision'],
                'goal_conditions': decision['goal_conditions'], 'scope': decision['scope'], 'candidate': rejected,
                'outcome': outcome, 'evidence': context['facts']})

        save('earlier-rejection', 'rejected')
        search = helper.search(context=context, graph=graph)['search']
        self.assertEqual([c['status'] for c in search['candidates']], ['READY'])
        self.assertIn('GOAL_ROUTES_REJECTED', {f['kind'] for f in search['loop_review']['flags']})
        # History lifts the unknown-goal pause; this undeclared action then needs its goal link first.
        self.assertEqual(search['selection_review']['next_move']['kind'], 'REVIEW_GOAL_LINK')
        for outcome in ('plan_locked', 'accepted'):
            with self.subTest(outcome=outcome):
                save('later-' + outcome, outcome)
                search = helper.search(context=context, graph=graph)['search']
                self.assertEqual([c['status'] for c in search['candidates']], ['READY'])
                self.assertNotIn('GOAL_ROUTES_REJECTED', {f['kind'] for f in search['loop_review']['flags']})
                self.assertEqual(search['selection_review']['next_move']['kind'], 'RESOLVE_PREMISE')

    def goal_ledger(self):
        from test_rds_advisor import LedgerLoopTests
        from rds_checkpoints import save_checkpoint
        helper = LedgerLoopTests()
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        context = copy.deepcopy(helper.context)
        context['decision']['goal_conditions'] = [{'fact': 'goal', 'value': True}]
        context['facts']['goal'] = {'value': None, 'source': 'unmeasured.json'}
        graph = copy.deepcopy(helper.graph)
        graph['nodes'][0]['executable']['action']['parameters'] = {'p': 1}

        def save(identity, question, candidate, outcome, scope=None):
            decision = context['decision']
            save_checkpoint(helper.root, identity, helper.store.snapshot(), kind='project', decision={
                'question_id': question, 'goal_revision': decision['goal_revision'],
                'goal_conditions': decision['goal_conditions'], 'scope': scope or decision['scope'],
                'candidate': candidate, 'outcome': outcome, 'evidence': context['facts']})

        def advise(context=context, graph=graph):
            search = helper.search(context=context, graph=graph)['search']
            return search, {f['kind']: f for f in search['loop_review']['flags']}
        return helper.search(context=context, graph=graph)['search']['candidates'][0], context, graph, save, advise

    def test_latest_same_goal_decision_wins_across_current_and_renamed_questions(self):
        # Returning to an older question cannot bypass a later rejection recorded under a renamed one.
        candidate, context, graph, save, advise = self.goal_ledger()
        save('current-accepted', 'choose', candidate, 'accepted')
        save('renamed-rejected', 'renamed', candidate, 'rejected')
        search, flags = advise()
        self.assertEqual(search['candidates'], [])
        self.assertEqual([c['status'] for c in search['blocked_candidates']], ['BLOCKED_REJECTED_ROUTE'])
        self.assertEqual(flags['REPEAT_REJECTED_ROUTE']['checkpoint_id'], 'renamed-rejected')
        self.assertEqual(flags['REPEAT_REJECTED_ROUTE']['recorded_question_id'], 'renamed')
        # The existing scope and evidence reopening rules still apply to that later rejection.
        moved = copy.deepcopy(context)
        moved['decision']['scope'] = {'dataset': 'independent-new-scope'}
        changed = copy.deepcopy(context)
        changed['facts']['x']['binding'] = {'run_id': 'new-run'}
        for variant in (moved, changed):
            with self.subTest(variant=variant['decision']['scope']):
                search, flags = advise(context=variant)
                self.assertEqual([c['status'] for c in search['candidates']], ['READY'])
                self.assertEqual(flags['REOPEN_REVIEW']['checkpoint_id'], 'renamed-rejected')
                self.assertNotIn('REPEAT_REJECTED_ROUTE', flags)

        # A later acceptance under the renamed question supersedes this question's older rejection.
        candidate, context, graph, save, advise = self.goal_ledger()
        save('current-rejected', 'choose', candidate, 'rejected')
        save('renamed-accepted', 'renamed', candidate, 'accepted')
        search, flags = advise()
        self.assertEqual([c['status'] for c in search['candidates']], ['READY'])
        self.assertEqual(search.get('blocked_candidates', []), [])
        self.assertNotIn('REPEAT_REJECTED_ROUTE', flags)
        self.assertNotIn('REOPEN_REVIEW', flags)

    def test_parameter_variant_hint_applies_only_in_the_rejected_scope_and_evidence(self):
        candidate, context, graph, save, advise = self.goal_ledger()
        save('earlier-rejection', 'earlier', candidate, 'rejected')
        variant_graph = copy.deepcopy(graph)
        variant_graph['nodes'][0]['executable']['action']['parameters'] = {'p': 2}
        search, flags = advise(graph=variant_graph)
        self.assertEqual([c['status'] for c in search['candidates']], ['READY'])
        self.assertEqual(flags['GOAL_ROUTES_REJECTED']['checkpoint_ids'], ['earlier-rejection'])
        unrelated = copy.deepcopy(context)
        unrelated['facts']['unrelated'] = {'value': 3, 'source': 'other.json'}
        self.assertIn('GOAL_ROUTES_REJECTED', advise(context=unrelated, graph=variant_graph)[1])

        # The new scope or relevant evidence has not failed: no stagnation hint and no forced reformulation.
        moved = copy.deepcopy(context)
        moved['decision']['scope'] = {'dataset': 'independent-new-scope'}
        changed = copy.deepcopy(context)
        changed['facts']['x']['binding'] = {'run_id': 'new-run'}
        for variant in (moved, changed):
            with self.subTest(variant=variant['decision']['scope']):
                search, flags = advise(context=variant, graph=variant_graph)
                self.assertEqual([c['status'] for c in search['candidates']], ['READY'])
                self.assertNotIn('GOAL_ROUTES_REJECTED', flags)
                self.assertEqual(search['selection_review']['next_move']['kind'], 'RESOLVE_PREMISE')

        # A later acceptance recorded in another scope does not supersede the rejection in this scope.
        save('other-scope-accepted', 'earlier', candidate, 'accepted', scope={'dataset': 'independent-new-scope'})
        self.assertEqual(advise(graph=variant_graph)[1]['GOAL_ROUTES_REJECTED']['checkpoint_ids'], ['earlier-rejection'])

    def test_prospective_job_cannot_reset_the_parent_budget(self):
        self.initialize_ledger()
        options = ['--context', str(self.context_path), '--graph', str(self.graph_path), '--ledger', str(self.ledger), '--timeout', '31']
        bad = self.job('over-budget', False, *options)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn('Insufficient parent ledger', bad.stderr)
        self.assertFalse((self.root / '.rds/exec/over-budget').exists())

    def test_ambiguous_candidate_is_not_completed(self):
        import copy
        self.initialize_ledger()
        other = copy.deepcopy(self.graph['nodes'][0])
        other['id'] = 'other-route'
        other['executable']['action']['id'] = 'inspect-y'
        self.graph['nodes'].append(other)
        self.graph_path.write_text(json.dumps(self.graph), encoding='utf-8')
        bad = self.advise('--record', 'ambiguous', ok=False)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn('ambiguous candidate', bad.stderr)


    def test_goal_link_guard_blocks_before_parent_charge_then_allows_a_real_open_obligation(self):
        self.initialize_ledger()
        self.context['require_goal_link'] = True
        self.context_path.write_text(json.dumps(self.context))
        before = ProjectStore(self.ledger).snapshot()
        bad = self.job('missing-goal-link', False, '--context', str(self.context_path),
                       '--graph', str(self.graph_path), '--ledger', str(self.ledger))
        self.assertIn('Goal-link guard', bad.stderr)
        self.assertFalse((self.root / '.rds/exec/missing-goal-link').exists())
        self.assertEqual(ProjectStore(self.ledger).snapshot(), before)
        self.context['dependency_map'] = json.loads((ROOT / 'examples/goal-linked-hypergraph.json').read_text())
        self.context['decision']['goal_conditions'] = [{'fact': 'completion_standard', 'value': True}]
        self.context['facts']['completion_standard'] = {'value': False, 'source': 'synthetic original contract'}
        self.context['research_mode'] = 'theory'
        action = self.graph['nodes'][0]['executable']['action']
        action.update(kind='OBLIGATION_CHECK', target='unrestricted_lower', claim='An unrestricted bound',
            outcomes=[{'observation': label, 'next_decision': label}
                      for label in ('verified', 'counterexample', 'unresolved')],
            goal_contribution={'target': 'completion_standard',
                'path': ['unrestricted_lower', 'completion_standard'], 'source': 'synthetic original contract'})
        action.pop('competing_explanations')
        self.context_path.write_text(json.dumps(self.context))
        self.graph_path.write_text(json.dumps(self.graph))
        result = json.loads(self.job('open-goal-obligation', True, '--context', str(self.context_path),
            '--graph', str(self.graph_path), '--ledger', str(self.ledger)).stdout)
        self.assertEqual(result['run_status'], 'SUCCEEDED')
        self.assertEqual(result['ledger_checkpoints'], 2)

if __name__ == '__main__':
    unittest.main()
