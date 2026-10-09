"""Issue #277: public workflow modes and same-ledger Advisor activation.

Finite synthetic CPU fixtures check accounting, original receipt/contract
identity and admission. They do not establish scientific gain or autonomy.
The existing assembly fixture is composed, never inherited or rediscovered.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

import test_rds_project_assembly as fixture_module

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_project_assembly as assembly
from rds_project import ProjectStore, canonical, digest
import rds_project_lifecycle as lifecycle


class ProjectLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-lifecycle-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.store = ProjectStore(self.root)
        # Reuse declarations/source only; no old test methods or setUp are run.
        self.fixture = fixture_module.ProjectAssemblyTests('test_compile_preserves_declared_semantics_and_original_contract_shape')
        self.fixture.root = self.root
        self.fixture.store = self.store
        for name, raw in [('code.py', fixture_module.SCRIPT), ('config.json', '{}'),
                          ('data.json', '[1]'), ('evaluator.json', '{}')]:
            self.fixture.write(name, raw)
        self.recipe = self.fixture.make_recipe()
        self.env = {**os.environ, 'RDS_USAGE_DB': str(self.root / 'usage.sqlite3'),
                    'PYTHONIOENCODING': 'utf-8'}
        self.trace = []
        self.addCleanup(self.export_evidence)

    def export_evidence(self):
        destination = os.environ.get('RDS_TEST_EVIDENCE_ROOT')
        if destination:
            target = Path(destination) / self._testMethodName
            target.mkdir(parents=True, exist_ok=False)
            (target / 'cli-transcript.json').write_text(json.dumps(self.trace, ensure_ascii=False, indent=2), encoding='utf-8')
            shutil.copytree(self.root, target / 'original-workspace')

    def write_json(self, name, value, *, root=None):
        path = (root or self.root) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fixture_module.canonical_bytes(value) + b'\n')
        return path

    def call(self, *args, root=None, ok=True):
        result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'),
                                 '--root', str(root or self.root), *args],
                                capture_output=True, text=True, encoding='utf-8', timeout=40, env=self.env)
        self.trace.append({'root': str(root or self.root), 'argv': list(args),
                           'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        if ok:
            self.assertEqual(result.returncode, 0, (result.stdout + result.stderr)[-6000:])
        return result

    def output(self, *args, root=None):
        return json.loads(self.call(*args, root=root).stdout)

    def prepare_contract(self, *, owned=False, source=None):
        if source is not None:
            self.fixture.write('code.py', source)
        contract, protocol_raw, _ = assembly.compile_recipe(self.store, self.recipe)
        self.fixture.write('protocol.json', protocol_raw)
        self.policy = deepcopy(contract['advisor_policy'])
        self.manifests = {r['manifest']['id']: deepcopy(r['manifest']) for r in self.policy['routes']}
        self.contract = deepcopy(contract)
        if not owned:
            self.contract.pop('advisor_policy')
        self.contract_path = self.write_json('contract.json', self.contract)
        self.policy_path = self.write_json('policy.json', self.policy)
        return self.contract_path

    def init_quick(self, *, source=None):
        self.prepare_contract(source=source)
        return self.output('project', 'init', '--contract', str(self.contract_path), '--mode', 'quick')

    def rows(self, table):
        if not self.store.path.is_file():
            return []  # A rejected new declaration correctly creates no ledger.
        with self.store._db(True) as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
            return [tuple(row) for row in db.execute('SELECT * FROM ' + table + ' ORDER BY rowid')] if exists else []

    def originals(self):
        return {table: self.rows(table) for table in ('contract', 'budget', 'runs', 'receipts', 'checkpoints', 'exposures')}

    def activations(self):
        with self.store._db(True) as db:
            return [json.loads(row['body']) for row in db.execute(
                "SELECT body FROM events WHERE json_extract(body,'$.kind')='ADVISOR_POLICY_ENABLED' ORDER BY id")]

    def preview(self, policy=None):
        path = self.policy_path if policy is None else self.write_json('proposal-policy.json', policy)
        return self.output('project', 'enable-advisor', '--policy', str(path))

    def apply(self, preview, *, policy_path=None, ok=True):
        return self.call('project', 'enable-advisor', '--policy', str(policy_path or self.policy_path),
                         '--apply', '--expected-snapshot', preview['snapshot_sha256'], ok=ok)

    def create(self, ident):
        return self.output('project', 'create', '--manifest',
                           str(self.write_json(ident + '-manifest.json', self.manifests[ident])))

    def starts(self):
        path = self.root / 'outputs/launches.txt'
        return path.read_text(encoding='utf-8').splitlines() if path.exists() else []

    def receipt(self, result, ident):
        value = result.get('receipt', result)
        self.assertEqual(value['run_id'], ident)
        self.assertEqual(value['sha256'], digest({k: v for k, v in value.items() if k != 'sha256'}))
        self.assertNotIn('workflow', value)
        saved = next(r for r in self.store.snapshot()['receipts'] if r['run_id'] == ident)
        self.assertEqual(saved, value)
        return value

    def test_new_default_full_requires_policy_and_quick_is_explicit(self):
        self.prepare_contract()
        rejected = self.call('project', 'init', '--contract', str(self.contract_path), ok=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('FULL', rejected.stderr)
        self.assertEqual(self.rows('contract'), [])
        quick = self.call('project', 'init', '--contract', str(self.contract_path), '--mode', 'quick')
        self.assertEqual(json.loads(quick.stdout)['workflow']['mode'], 'QUICK')
        self.assertIn('[RDS] mode=QUICK advisor=NOT_PROGRAM_OWNED', quick.stderr)
        self.assertEqual(self.starts(), [])

    def test_recipe_defaults_full_and_brief_exposes_mode_without_dispatch(self):
        path = self.write_json('recipe.json', self.recipe)
        initialized = self.call('project', 'init', '--recipe', str(path))
        self.assertEqual(json.loads(initialized.stdout)['workflow']['mode'], 'FULL')
        self.assertIn('[RDS] mode=FULL advisor=PROGRAM_OWNED', initialized.stderr)
        brief = self.output('project', 'next', '--brief')
        self.assertEqual(brief['workflow']['mode'], 'FULL')
        self.assertEqual(brief['workflow']['advisor'], 'PROGRAM_OWNED')
        self.assertEqual(self.store.snapshot()['runs'], [])
        self.assertEqual(self.starts(), [])
        rejected = self.call('project', 'init', '--recipe', str(path), '--mode', 'quick', ok=False)
        self.assertNotEqual(rejected.returncode, 0)

    def test_multiple_runs_share_full_ledger_costs_and_receipts_are_not_decorated(self):
        self.prepare_contract(owned=True)
        self.output('project', 'init', '--contract', str(self.contract_path))
        first = self.receipt(self.output('project', 'advance'), 'baseline')
        second = self.receipt(self.output('project', 'advance'), 'repair')
        self.assertEqual(first['run_status'], 'SUCCEEDED')
        self.assertEqual(second['run_status'], 'SUCCEEDED')
        self.assertNotEqual(first['attempt_id'], second['attempt_id'])
        state = self.store.snapshot()
        self.assertEqual(len(state['runs']), 2)
        self.assertEqual(len(state['receipts']), 2)
        self.assertEqual(state['budget']['cpu_seconds']['charged_estimate'], 2)
        self.assertEqual(state['budget']['cpu_seconds']['reserved'], 0)
        self.assertEqual(state['budget']['cpu_seconds']['cap'], 10)
        self.assertEqual(self.starts(), ['baseline', 'repair'])
        before = self.originals()
        self.output('project', 'recover', '--id', 'baseline')
        self.output('project', 'recover', '--id', 'repair')
        self.output('project', 'advance', '--brief')
        self.assertEqual(self.originals(), before)
        self.assertEqual(self.starts(), ['baseline', 'repair'])

    def test_activation_preserves_genesis_originals_costs_and_old_receipt(self):
        self.init_quick()
        self.create('baseline')
        old_receipt = self.receipt(self.output('project', 'execute', '--id', 'baseline'), 'baseline')
        self.output('checkpoint', 'save', '--id', 'legacy-before-enable')
        before = self.originals()
        before_events = self.rows('events')
        preview = self.preview()
        self.assertEqual(preview['status'], 'ADVISOR_ACTIVATION_PREVIEW')
        self.assertFalse(preview['execution_started'])
        self.assertEqual(preview['authorization'], 'UNCHANGED')
        self.assertEqual(preview['parent_contract_sha256'], digest(self.contract))
        self.assertEqual(self.originals(), before)
        self.assertEqual(self.rows('events'), before_events)
        applied = json.loads(self.apply(preview).stdout)
        self.assertEqual(applied['status'], 'ADVISOR_ENABLED')
        self.assertEqual(self.originals(), before)
        self.assertEqual(len(self.activations()), 1)
        self.assertEqual(self.activations()[0]['genesis_sha256'], digest(self.contract))
        retry = json.loads(self.apply(preview).stdout)
        self.assertEqual(retry['status'], 'ALREADY_ENABLED')
        self.assertEqual(len(self.activations()), 1)
        self.assertEqual(self.originals(), before)
        genesis_retry = self.output('project', 'init', '--contract', str(self.contract_path))
        self.assertEqual(genesis_retry['workflow']['mode'], 'FULL')
        self.assertEqual(self.originals(), before)
        report = self.output('project', 'next')
        self.assertEqual(report['context']['facts']['baseline.score']['value'], -1)
        self.assertEqual(report['selected_run'], 'repair')
        self.assertEqual(report['workflow']['mode'], 'FULL')
        finished = self.receipt(self.output('project', 'advance'), 'repair')
        self.assertEqual(finished['run_status'], 'SUCCEEDED')
        saved = next(r for r in self.store.snapshot()['receipts'] if r['run_id'] == 'baseline')
        self.assertEqual(saved, old_receipt)
        self.assertEqual(self.rows('contract'), before['contract'])
        self.assertEqual(self.store.snapshot()['budget']['cpu_seconds']['charged_estimate'], 2)
        self.assertEqual(self.starts(), ['baseline', 'repair'])

    def test_missing_old_output_remains_unknown_after_activation(self):
        source = fixture_module.SCRIPT.replace(
            "pathlib.Path(output).write_text(json.dumps({'score': int(score), 'run_id': run_id}), encoding='utf-8')",
            "if run_id != 'baseline':\n    pathlib.Path(output).write_text(json.dumps({'score': int(score), 'run_id': run_id}), encoding='utf-8')")
        self.init_quick(source=source)
        self.create('baseline')
        failed = self.call('project', 'execute', '--id', 'baseline', ok=False)
        self.assertNotEqual(failed.returncode, 0)
        receipt = self.receipt(json.loads(failed.stdout), 'baseline')
        self.assertEqual(receipt['run_status'], 'FAILED')
        before = self.originals()
        preview = self.preview()
        self.apply(preview)
        report = self.output('project', 'next')
        fact = report['context']['facts']['baseline.score']
        self.assertIsNone(fact['value'])
        self.assertFalse(fact['reliable'])
        self.assertIsNone(report['selected_run'])
        self.assertEqual(report['scientific_support'], 'UNKNOWN')
        self.assertEqual(self.originals(), before)
        self.assertEqual(self.starts(), ['baseline'])

    def test_stale_preview_cannot_apply_or_reset_state(self):
        self.init_quick()
        preview = self.preview()
        self.output('checkpoint', 'save', '--id', 'change-after-preview')
        before = self.originals()
        rejected = self.apply(preview, ok=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertEqual(self.activations(), [])
        self.assertEqual(self.originals(), before)
        self.assertEqual(self.starts(), [])

    def test_reserved_and_running_attempts_block_activation_without_refund(self):
        hold = "import time\n" + fixture_module.SCRIPT.replace(
            "run_id, score, output = sys.argv[1:]",
            "run_id, score, output = sys.argv[1:]\nwhile not pathlib.Path('outputs/release').exists():\n    time.sleep(.02)")
        self.recipe['routes'][0]['run']['timeout_seconds'] = 10
        self.recipe['routes'][0]['run']['resource_estimates']['wall_seconds'] = 10
        self.init_quick(source=hold)
        self.create('baseline')
        reserved = self.originals()
        result = self.call('project', 'enable-advisor', '--policy', str(self.policy_path), ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.originals(), reserved)
        process = subprocess.Popen([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'),
                                    '--root', str(self.root), 'project', 'execute', '--id', 'baseline'],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   encoding='utf-8', env=self.env)
        try:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                run = self.store.snapshot()['runs'][0]
                if run['status'] == 'RUNNING':
                    break
                if process.poll() is not None:
                    self.fail('Execution ended before a RUNNING state could be inspected')
                time.sleep(.03)
            else:
                self.fail('No RUNNING attempt observed within the bounded wait')
            rejected = self.call('project', 'enable-advisor', '--policy', str(self.policy_path), ok=False)
            self.assertNotEqual(rejected.returncode, 0)
            self.assertEqual(self.activations(), [])
            state = self.store.snapshot()
            self.assertEqual(state['budget']['wall_seconds']['reserved'], 10)
            self.assertEqual(state['runs'][0]['attempt_id'], run['attempt_id'])
        finally:
            self.fixture.write('outputs/release', 'release synthetic worker\n')
            stdout, stderr = process.communicate(timeout=35)
            self.trace.append({'argv': ['project', 'execute', '--id', 'baseline'],
                               'returncode': process.returncode, 'stdout': stdout, 'stderr': stderr})
        self.assertEqual(process.returncode, 0, (stdout + stderr)[-6000:])
        self.assertEqual(self.store.snapshot()['budget']['wall_seconds']['reserved'], 0)
        self.assertEqual(self.starts(), ['baseline'])

    def test_conflicting_policy_and_extra_authority_are_rejected(self):
        self.init_quick()
        for field in ('autonomy', 'tool_bindings', 'confirmation', 'feasibility'):
            with self.subTest(extra=field):
                altered = {**deepcopy(self.policy), field: {}}
                path = self.write_json('extra-policy.json', altered)
                result = self.call('project', 'enable-advisor', '--policy', str(path), ok=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.activations(), [])
        preview = self.preview()
        self.apply(preview)
        before = self.originals()
        changed = deepcopy(self.policy)
        changed['context']['decision']['goal_conditions'][0]['value'] = 99
        result = self.apply(preview, policy_path=self.write_json('conflict-policy.json', changed), ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(self.activations()), 1)
        self.assertEqual(self.originals(), before)

    def test_concurrent_activation_appends_once_without_changing_originals(self):
        self.init_quick()
        before = self.originals()
        preview = self.preview()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: json.loads(self.apply(preview).stdout), range(2)))
        self.assertEqual(sorted(r['status'] for r in results), ['ADVISOR_ENABLED', 'ALREADY_ENABLED'])
        self.assertEqual(len(self.activations()), 1)
        self.assertEqual(self.originals(), before)
        self.assertEqual(self.starts(), [])

    def test_unsupported_goal_operator_cannot_be_frozen_by_activation(self):
        self.init_quick()
        before = self.originals()
        for operator in ('gtt', [], True):
            with self.subTest(operator=operator):
                changed = deepcopy(self.policy)
                changed['context']['decision']['goal_conditions'][0]['op'] = operator
                result = self.call('project', 'enable-advisor', '--policy',
                                   str(self.write_json('invalid-policy.json', changed)), ok=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Invalid owned goal condition', result.stderr)
                self.assertEqual(self.activations(), [])
                self.assertEqual(self.originals(), before)

    def test_activation_cannot_change_existing_manifest_even_with_authorized_command(self):
        self.init_quick()
        self.create('baseline')
        self.output('project', 'execute', '--id', 'baseline')
        changed = deepcopy(self.policy)
        changed['routes'][0]['manifest']['timeout_seconds'] = 4
        changed['routes'][0]['manifest']['resource_estimates']['wall_seconds'] = 4
        before = self.originals()
        path = self.write_json('changed-manifest-policy.json', changed)
        result = self.call('project', 'enable-advisor', '--policy', str(path), ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.activations(), [])
        self.assertEqual(self.originals(), before)
        self.assertEqual(self.starts(), ['baseline'])

    def test_discovery_is_read_only_and_finds_current_or_nearest_ancestor(self):
        self.prepare_contract(owned=True)
        self.output('project', 'init', '--contract', str(self.contract_path))
        child = self.root / 'analysis' / 'nested'
        child.mkdir(parents=True)
        before = self.originals()
        current = self.output('project', 'discover')
        ancestor = self.output('project', 'discover', root=child)
        self.assertEqual(current['relation'], 'CURRENT')
        self.assertEqual(ancestor['relation'], 'ANCESTOR')
        self.assertEqual(ancestor['project_root'], str(self.root))
        self.assertEqual(ancestor['workflow']['mode'], 'FULL')
        self.assertFalse(ancestor['execution_started'])
        self.assertFalse(ancestor['sibling_projects_searched'])
        self.assertEqual(self.originals(), before)
        self.assertEqual(list(child.iterdir()), [])

    def test_nested_init_requires_explicit_separate_reason_and_exec_cannot_bypass(self):
        self.prepare_contract(owned=True)
        self.output('project', 'init', '--contract', str(self.contract_path))
        child = self.root / 'independent'
        child.mkdir()
        for binding in self.contract['bindings']:
            shutil.copyfile(self.root / binding['path'], child / binding['path'])
        path = self.write_json('contract.json', self.contract, root=child)
        before = self.originals()
        result = self.call('project', 'init', '--contract', str(path), root=child, ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((child / '.rds/project.sqlite3').exists())
        blocked = self.call('exec', '--timeout', '5', '--', sys.executable, '-c',
                            "from pathlib import Path; Path('bypass-marker').write_text('unexpected')",
                            root=child, ok=False)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertFalse((child / 'bypass-marker').exists())
        self.assertFalse((child / '.rds/project.sqlite3').exists())
        reason = 'Independent labelled software fixture with separately declared budget'
        self.output('project', 'init', '--contract', str(path), '--separate-project', reason, root=child)
        separate = ProjectStore(child)
        with separate._db(True) as db:
            declarations = [json.loads(row['body']) for row in db.execute(
                "SELECT body FROM events WHERE json_extract(body,'$.kind')='PROJECT_SCOPE_DECLARED'")]
        self.assertEqual(len(declarations), 1)
        self.assertEqual(declarations[0]['reason'], reason)
        self.assertEqual(declarations[0]['ancestor_root'], str(self.root))
        child_before = separate.snapshot()
        with separate._db(True) as db:
            child_events = [tuple(row) for row in db.execute('SELECT * FROM events ORDER BY id')]
        self.output('project', 'init', '--contract', str(path), '--separate-project', reason, root=child)
        self.assertEqual(separate.snapshot(), child_before)
        changed = self.call('project', 'init', '--contract', str(path),
                            '--separate-project', 'A changed independence claim', root=child, ok=False)
        self.assertNotEqual(changed.returncode, 0)
        with separate._db(True) as db:
            self.assertEqual([tuple(row) for row in db.execute('SELECT * FROM events ORDER BY id')], child_events)
        self.assertEqual(self.originals(), before)
        self.assertEqual(self.starts(), [])

    def test_scope_cannot_be_added_after_original_failure_and_checkpoint(self):
        self.init_quick(source=fixture_module.SCRIPT + '\nraise SystemExit(3)\n')
        self.create('baseline')
        result = self.call('project', 'execute', '--id', 'baseline', ok=False)
        receipt = self.receipt(json.loads(result.stdout), 'baseline')
        self.assertEqual(receipt['run_status'], 'FAILED')
        self.output('checkpoint', 'save', '--id', 'failed-original')
        before, before_events = self.originals(), self.rows('events')
        rejected = self.call('project', 'init', '--contract', str(self.contract_path), '--mode', 'quick',
                             '--separate-project', 'Posthoc independent experiment', ok=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('cannot be added after initialization', rejected.stderr)
        self.assertEqual(self.originals(), before)
        self.assertEqual(self.rows('events'), before_events)
        self.assertEqual(self.starts(), ['baseline'])
        self.output('project', 'init', '--contract', str(self.contract_path), '--mode', 'quick')
        self.assertEqual(self.originals(), before)
        self.assertEqual(self.rows('events'), before_events)

    def test_legacy_exact_retry_retains_quick_mode_without_new_contract_or_budget(self):
        self.prepare_contract()
        # Seed through the unchanged low-level API to model a pre-mode ledger.
        self.store.initialize(self.contract)
        before = self.originals()
        retry = self.call('project', 'init', '--contract', str(self.contract_path))
        self.assertEqual(json.loads(retry.stdout)['workflow']['mode'], 'QUICK')
        self.assertIn('[RDS] mode=QUICK advisor=NOT_PROGRAM_OWNED', retry.stderr)
        self.assertEqual(self.originals(), before)
        wrong = self.call('project', 'init', '--contract', str(self.contract_path), '--mode', 'full', ok=False)
        self.assertNotEqual(wrong.returncode, 0)
        self.assertEqual(self.originals(), before)


    def retained_child(self, root, *, register=True, finish=False):
        root.mkdir(parents=True, exist_ok=True)
        for binding in self.contract['bindings']:
            path = root / binding['path']
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.root / binding['path'], path)
        child = ProjectStore(root)
        child.initialize(self.contract)
        if register:
            child.register(deepcopy(self.manifests['baseline']))
        if finish:
            child.execute('baseline')
        return child

    def retained_pointer(self, owner, child, *, kind='EXTERNAL_RUN_ALLOWANCE', request_sha=None):
        event = {'kind': kind, 'job_root': str(child),
                 'request_sha256': request_sha or digest({'fixture': str(child)}),
                 'resource': 'wall_seconds', 'amount': 1,
                 'accounting': 'CONSERVATIVE_ALLOWANCE', 'execution_authority': 'UNCHANGED'}
        with owner._db() as db:
            db.execute('INSERT INTO events(body) VALUES (?)', (canonical(event),))

    def test_supersedes_must_name_the_discovered_parent_or_declare_separation(self):
        self.init_quick()
        unrelated = self.retained_child(self.root / 'unrelated', register=False)
        child_root = self.root / 'experiment'
        child_root.mkdir()
        for binding in self.contract['bindings']:
            shutil.copyfile(self.root / binding['path'], child_root / binding['path'])
        path = self.write_json('contract.json', self.contract, root=child_root)
        rejected = self.call('project', 'init', '--contract', str(path), '--mode', 'quick',
                             '--supersedes', str(unrelated.root), root=child_root, ok=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('--separate-project', rejected.stderr)
        self.assertFalse(ProjectStore(child_root).path.exists())
        inherited = self.output('project', 'init', '--contract', str(path), '--mode', 'quick',
                                '--supersedes', str(self.root), root=child_root)
        self.assertEqual(inherited['workflow']['mode'], 'QUICK')
        self.assertEqual(ProjectStore(child_root)._predecessor()['root_path'], '..')

    def test_retained_reserved_child_blocks_preview_and_stale_apply_without_refund(self):
        source = fixture_module.SCRIPT + "\nimport time\nwhile not pathlib.Path('outputs/release').exists(): time.sleep(0.01)\n"
        self.init_quick(source=source)
        preview = self.preview()
        child = self.retained_child(self.root / '.rds/exec/pending')
        self.retained_pointer(self.store, child.root, kind='QUICK_JOB_ADMITTED')
        before = child.snapshot(), self.originals()
        for apply in (False, True):
            with self.assertRaisesRegex(ValueError, 'retained|Retained'):
                lifecycle.enable_advisor(self.store, self.policy, apply=apply,
                                         expected_snapshot=preview['snapshot_sha256'])
        self.assertEqual((child.snapshot(), self.originals()), before)
        self.assertEqual(self.activations(), [])
        with ThreadPoolExecutor(max_workers=1) as pool:
            execution = pool.submit(child.execute, 'baseline')
            try:
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    run = child.snapshot()['runs'][0]
                    if run['status'] == 'RUNNING' and run['pid'] is not None:
                        break
                    time.sleep(0.01)
                self.assertEqual(run['status'], 'RUNNING')
                self.assertIsNotNone(run['pid'])
                with self.assertRaisesRegex(ValueError, 'reserved/running'):
                    lifecycle.enable_advisor(self.store, self.policy, apply=True,
                                             expected_snapshot=preview['snapshot_sha256'])
                self.assertEqual(self.activations(), [])
            finally:
                (child.root / 'outputs/release').write_text('finish original run', encoding='utf-8')
                execution.result(timeout=15)
        self.assertEqual(child.snapshot()['budget']['wall_seconds']['reserved'], 0)
        self.assertEqual(lifecycle.enable_advisor(self.store, self.policy)['status'], 'ADVISOR_ACTIVATION_PREVIEW')

    def test_retained_missing_and_empty_preparation_are_not_terminal_evidence(self):
        self.init_quick()
        missing = self.root / '.rds/exec/preparing'
        self.retained_pointer(self.store, missing)
        with self.assertRaisesRegex(ValueError, 'retained|Retained'):
            lifecycle.enable_advisor(self.store, self.policy)
        child = self.retained_child(missing, register=False)
        with self.assertRaisesRegex(ValueError, 'retained|Retained'):
            lifecycle.enable_advisor(self.store, self.policy)
        self.assertEqual(child.snapshot()['runs'], [])
        self.assertEqual(self.activations(), [])

    def test_recursive_retained_children_and_terminal_receipt_identity_are_checked(self):
        self.init_quick()
        child = self.retained_child(self.root / '.rds/exec/finished', finish=True)
        grandchild = self.retained_child(child.root / '.rds/exec/nested')
        self.retained_pointer(self.store, child.root)
        self.retained_pointer(child, grandchild.root)
        with self.assertRaisesRegex(ValueError, 'retained|Retained'):
            lifecycle.enable_advisor(self.store, self.policy)
        grandchild.execute('baseline')
        preview = lifecycle.enable_advisor(self.store, self.policy)
        with grandchild._db() as db:
            receipt = grandchild._receipt(db.execute('SELECT run_id,sha256,body FROM receipts').fetchone())
            receipt['attempt_id'] = 'different-original-attempt'
            receipt['sha256'] = digest({k: v for k, v in receipt.items() if k != 'sha256'})
            # Explicit retained-damage fixture; supported writes remain append-only.
            db.execute('DROP TRIGGER receipts_no_update')
            db.execute('UPDATE receipts SET body=?,sha256=?', (canonical(receipt), receipt['sha256']))
        with self.assertRaisesRegex(ValueError, 'receipt|Receipt'):
            lifecycle.enable_advisor(self.store, self.policy, apply=True,
                                     expected_snapshot=preview['snapshot_sha256'])
        self.assertEqual(self.activations(), [])

    def test_completed_legacy_tool_allowance_is_redirected_only_by_original_identity(self):
        self.init_quick()
        token = 'a' * 32
        workspace = self.root / '.rds/rsi/tool-checks' / token
        child = self.retained_child(workspace / '.rds/exec/tool-check', finish=True)
        self.retained_pointer(self.store, workspace, request_sha=digest({'tool_validation': token}))
        # Duplicate original pointer must not count as another child operation.
        self.retained_pointer(self.store, child.root, kind='QUICK_JOB_ADMITTED')
        preview = lifecycle.enable_advisor(self.store, self.policy)
        self.assertEqual(preview['status'], 'ADVISOR_ACTIVATION_PREVIEW')
        self.retained_pointer(self.store, workspace, request_sha=digest({'tool_validation': 'b' * 32}))
        with self.assertRaisesRegex(ValueError, 'retained|Retained'):
            lifecycle.enable_advisor(self.store, self.policy)

    def test_completed_child_snapshot_changes_invalidate_activation_preview(self):
        self.init_quick()
        child = self.retained_child(self.root / '.rds/exec/finished', finish=True)
        self.retained_pointer(self.store, child.root)
        preview = lifecycle.enable_advisor(self.store, self.policy)
        with child._db() as db:
            db.execute('INSERT INTO events(body) VALUES (?)', (canonical({'kind': 'RETAINED_OBSERVATION'}),))
        with self.assertRaisesRegex(ValueError, 'changed since preview'):
            lifecycle.enable_advisor(self.store, self.policy, apply=True,
                                     expected_snapshot=preview['snapshot_sha256'])
        self.assertEqual(self.activations(), [])

    def test_retained_inventory_deduplicates_children_and_rejects_incomplete_bounds(self):
        self.init_quick()
        child = self.retained_child(self.root / '.rds/exec/finished', finish=True)
        for kind in ('EXTERNAL_RUN_ALLOWANCE', 'QUICK_JOB_ADMITTED'):
            self.retained_pointer(self.store, child.root, kind=kind)
        with patch.object(lifecycle, 'MAX_RETAINED_JOBS', 1):
            self.assertEqual(lifecycle.enable_advisor(self.store, self.policy)['status'], 'ADVISOR_ACTIVATION_PREVIEW')
        with patch.object(lifecycle, 'MAX_RETAINED_EVENTS', 1):
            with self.assertRaisesRegex(ValueError, 'bound exceeded'):
                lifecycle.enable_advisor(self.store, self.policy)
        self.assertEqual(self.activations(), [])

    def test_activation_holds_checked_child_writer_lock_until_parent_commit(self):
        self.init_quick()
        child = self.retained_child(self.root / '.rds/exec/finished', finish=True)
        self.retained_pointer(self.store, child.root)
        preview = lifecycle.enable_advisor(self.store, self.policy)
        checked, release, writing, committed = Event(), Event(), Event(), Event()
        original = lifecycle._activation_children

        def pause_after_check(*args, **kwargs):
            result = original(*args, **kwargs)
            checked.set()
            if not release.wait(5):
                raise AssertionError('Fixture activation was not released')
            return result

        def child_writer():
            # The real writer enters the global mutation gate before opening
            # its DB. Signal this attempt before either global or child lock;
            # signalling inside _db would wait for activation to finish first.
            writing.set()
            with child._db() as db:
                db.execute('BEGIN IMMEDIATE')
                # This assertion runs only after the original root commit has
                # released its held child lock, never on an injected snapshot.
                with self.store._db(True) as parent_db:
                    self.assertIn('advisor_policy', self.store._contract(parent_db))
                db.execute('INSERT INTO events(body) VALUES (?)', (canonical({'kind': 'AFTER_ACTIVATION'}),))
            committed.set()

        with ThreadPoolExecutor(max_workers=2) as pool, patch.object(lifecycle, '_activation_children', pause_after_check):
            activation = pool.submit(lifecycle.enable_advisor, self.store, self.policy,
                                     apply=True, expected_snapshot=preview['snapshot_sha256'])
            self.assertTrue(checked.wait(5))
            writer = pool.submit(child_writer)
            try:
                self.assertTrue(writing.wait(5))
                self.assertFalse(committed.wait(0.2))
            finally:
                release.set()
            self.assertEqual(activation.result(timeout=10)['status'], 'ADVISOR_ENABLED')
            writer.result(timeout=10)
        self.assertTrue(committed.is_set())
        self.assertEqual(len(self.activations()), 1)
        with child._db(True) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE "
                "json_extract(body,'$.kind')='AFTER_ACTIVATION'").fetchone()[0], 1)

    def test_contended_child_lock_fails_closed_without_activation(self):
        self.init_quick()
        child = self.retained_child(self.root / '.rds/exec/finished', finish=True)
        self.retained_pointer(self.store, child.root)
        preview = lifecycle.enable_advisor(self.store, self.policy)
        with child._db() as busy:
            busy.execute('BEGIN IMMEDIATE')
            with self.assertRaisesRegex(ValueError, 'lock is unavailable.*incomplete'):
                lifecycle.enable_advisor(self.store, self.policy, apply=True,
                                         expected_snapshot=preview['snapshot_sha256'])
        self.assertEqual(self.activations(), [])
        self.assertEqual(child.snapshot()['runs'][0]['status'], 'COMPLETED')

    def native_scope(self, child, kind):
        child.mkdir(parents=True, exist_ok=True)
        if kind == 'tms':
            spec = {'schema': 1, 'nodes': [], 'hyperedges': [], 'goals': []}
            path = self.write_json('map.json', spec, root=child)
            self.output('hypergraph', '--input', str(path), '--json', root=child)
            return child / '.rds/project.sqlite3'
        shutil.copytree(ROOT / 'examples/reference-run', child, dirs_exist_ok=True)
        self.output('init', '--contract', str(child / 'contract.json'), root=child)
        return child / '.rds/state.sqlite3'

    def test_native_child_without_project_contract_cannot_hide_initialized_ancestor(self):
        self.init_quick()
        before = self.originals()
        for kind in ('tms', 'reference'):
            with self.subTest(kind=kind):
                child = self.root / kind
                native = self.native_scope(child, kind)
                native_sha = hashlib.sha256(native.read_bytes()).hexdigest()
                for binding in self.contract['bindings']:
                    shutil.copyfile(self.root / binding['path'], child / binding['path'])
                path = self.write_json('execution-contract.json', self.contract, root=child)
                found = self.output('project', 'discover', root=child)
                self.assertEqual(found['relation'], 'ANCESTOR')
                self.assertEqual(found['project_root'], str(self.root))
                rejected = self.call('project', 'init', '--contract', str(path), '--mode', 'quick',
                                     root=child, ok=False)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertIn('--separate-project', rejected.stderr)
                self.assertEqual(hashlib.sha256(native.read_bytes()).hexdigest(), native_sha)
                independent = self.output('project', 'init', '--contract', str(path), '--mode', 'quick',
                                          '--separate-project', 'Separate declared ' + kind + ' fixture', root=child)
                self.assertEqual(independent['workflow']['mode'], 'QUICK')
                with ProjectStore(child)._db(True) as db:
                    declaration = json.loads(db.execute("SELECT body FROM events WHERE "
                        "json_extract(body,'$.kind')='PROJECT_SCOPE_DECLARED'").fetchone()['body'])
                self.assertEqual(declaration['ancestor_root'], str(self.root))
        self.assertEqual(self.originals(), before)

    def test_native_scope_without_initialized_ancestor_retains_original_discovery(self):
        for kind in ('tms', 'reference'):
            with self.subTest(kind=kind):
                child = self.root / kind
                native = self.native_scope(child, kind)
                before = hashlib.sha256(native.read_bytes()).hexdigest()
                found = self.output('project', 'discover', root=child)
                self.assertEqual(found['relation'], 'CURRENT')
                self.assertEqual(found['project_root'], str(child))
                self.assertEqual(found['workflow']['mode'], 'UNINITIALIZED')
                self.assertEqual(hashlib.sha256(native.read_bytes()).hexdigest(), before)

    def test_corrupt_native_child_is_rejected_instead_of_hidden_by_ancestor(self):
        self.init_quick()
        for kind in ('tms', 'reference'):
            with self.subTest(kind=kind):
                child = self.root / ('corrupt-' + kind)
                (child / '.rds').mkdir(parents=True)
                path = child / '.rds' / ('project.sqlite3' if kind == 'tms' else 'state.sqlite3')
                path.write_bytes(b'not a sqlite database')
                rejected = self.call('project', 'discover', root=child, ok=False)
                self.assertNotEqual(rejected.returncode, 0)
                self.assertEqual(path.read_bytes(), b'not a sqlite database')


if __name__ == '__main__':
    unittest.main()
