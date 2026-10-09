"""Real QUICK admission retains a charged job when the parent graph changes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import threading
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import rds_cli
import rds_quick
from rds_advisor_coverage import project_context
from rds_project import ProjectStore, digest
from rds_tms_store import current, save
import test_rds_quick as fixture_module


class QuickCoverageAdmissionTests(unittest.TestCase):
    def setUp(self):
        # Composition avoids collecting or rerunning QuickTests' old suite.
        self.fixture = fixture_module.QuickTests(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.initialize_ledger()

    def reviewed_job(self, name, source_root=None):
        fixture = self.fixture
        source_root = source_root or fixture.root
        (source_root / 'probe.py').write_text(
            'from pathlib import Path\nPath("launch-marker").write_text("started")\n', encoding='utf-8')
        args = rds_cli.parser().parse_args([
            '--root', str(source_root), 'exec', '--name', name, '--timeout', '5',
            '--context', str(fixture.context_path), '--graph', str(fixture.graph_path),
            '--ledger', str(fixture.ledger), '--', sys.executable, '-B', 'probe.py'])
        review_args = argparse.Namespace(root=str(fixture.ledger),
            research_context=str(fixture.context_path), graph=str(fixture.graph_path), saved_dependencies=False)
        advice = rds_cli.cmd_advise(review_args, rds_cli.RDSState(fixture.ledger))
        context = project_context(fixture.ledger, json.loads(fixture.context_path.read_text(encoding='utf-8')))
        return args, (advice, context), source_root / '.rds' / 'exec' / name

    def initialize_accounted_source(self, helper):
        helper.recipe['budget'] = {'wall_seconds': 30}
        for route in helper.recipe['routes']:
            route['run']['resource_estimates'] = {'wall_seconds': route['run']['resource_estimates']['wall_seconds']}
        helper.prepare_contract()
        helper.contract['execution_policy'] = {'schema': 1, 'max_attempts': 3}
        helper.contract_path = helper.write_json('contract.json', helper.contract)
        helper.output('project', 'init', '--contract', str(helper.contract_path), '--mode', 'quick')
        self.fixture.ledger = helper.root

    def assert_held_child_and_original_accounting(self, workspace, before, at_admission):
        child = ProjectStore(workspace).snapshot()
        self.assertEqual(len(child['runs']), 1)
        self.assertEqual(child['runs'][0]['status'], 'RESERVED')
        self.assertIsNone(child['runs'][0]['attempt_id'])
        self.assertIsNone(child['runs'][0]['started_at'])
        self.assertEqual(child['receipts'], [])
        self.assertFalse((workspace / 'launch-marker').exists())
        after = ProjectStore(self.fixture.ledger).snapshot()
        self.assertEqual(after['budget'], at_admission)
        for key in ('runs', 'receipts', 'exposures', 'contract_sha256'):
            self.assertEqual(after[key], before[key])
        original, charged = before['budget']['wall_seconds'], after['budget']['wall_seconds']
        self.assertEqual(charged['charged_estimate'], original['charged_estimate'] + 5)
        for key in ('spent_measured', 'reserved', 'cap'):
            self.assertEqual(charged[key], original[key])
        self.assertAlmostEqual(charged['remaining'], original['remaining'] - 5)

    def assert_writer_commits_after_attempt(self, name, writer_store, writer, source_root=None,
                                          finish_writer_before_claim=False):
        args, reviewed, workspace = self.reviewed_job(name, source_root)
        entered, committed = threading.Event(), threading.Event()
        observed = {}
        original_db = writer_store._db
        original_execute, original_save = ProjectStore.execute, ProjectStore._save
        original_claim = ProjectStore._execute_claim

        @contextmanager
        def observed_writer_db(readonly=False):
            if not readonly:
                entered.set()  # Legal writer is attempting the actual DB boundary.
            with original_db(readonly) as db:
                yield db

        def write_control():
            with mock.patch.object(writer_store, '_db', new=observed_writer_db):
                # initialize() acquires mutation before its DB callback. Signal
                # the actual API attempt; durability assertions below remain.
                entered.set()
                result = writer()
            committed.set()
            observed['committed_attempt'] = ProjectStore(workspace).snapshot()['runs'][0]['attempt_id']
            return result

        def record_attempt(store, db, run):
            if store.root == workspace.resolve() and run['attempt_id'] is not None and 'attempt' not in observed:
                self.assertFalse(committed.is_set())
                observed['attempt'] = run['attempt_id']
            return original_save(db, run)

        def claim_after_writer(store, *positional, **keywords):
            if finish_writer_before_claim and store.root == workspace.resolve():
                # Attempt admission is committed; the real process has not yet
                # started, so activation must consistently see RUNNING work.
                observed['future'].result(timeout=5)
            return original_claim(store, *positional, **keywords)

        with ThreadPoolExecutor(max_workers=1) as pool:
            def execute_with_writer(store, run_id, *positional, **keywords):
                self.assertEqual(store.root, workspace.resolve())
                self.assertTrue(callable(keywords.get('admission_context')))
                original_guard = keywords['admission_guard']

                def final_check_then_writer(db, run):
                    original_guard(db, run)
                    observed['future'] = pool.submit(write_control)
                    self.assertTrue(entered.wait(3), 'Writer did not reach the database')
                    self.assertFalse(committed.wait(0.2), 'Control committed before attempt admission')
                keywords['admission_guard'] = final_check_then_writer
                return original_execute(store, run_id, *positional, **keywords)

            with mock.patch.object(ProjectStore, 'execute', new=execute_with_writer), \
                    mock.patch.object(ProjectStore, '_save', new=record_attempt), \
                    mock.patch.object(ProjectStore, '_execute_claim', new=claim_after_writer):
                result = rds_quick.execute(args, review=reviewed)
            changed = observed['future'].result(timeout=5)
        self.assertTrue(committed.is_set())
        self.assertEqual(observed['committed_attempt'], observed['attempt'])
        child = ProjectStore(workspace).snapshot()
        self.assertEqual(len(child['runs']), 1)
        self.assertEqual(child['runs'][0]['attempt_id'], observed['attempt'])
        self.assertEqual(child['receipts'][0]['run_status'], 'SUCCEEDED')
        self.assertTrue((workspace / 'launch-marker').is_file())
        self.assertEqual(result['receipt']['attempt_id'], observed['attempt'])
        return changed

    def test_threaded_owner_pause_cannot_commit_between_check_and_attempt(self):
        from rds_steering import submit, current as current_steering
        parent = ProjectStore(self.fixture.ledger)
        before = parent.snapshot()

        def pause():
            return submit(parent, {'id': 'pause-after-final-check', 'kind': 'pause',
                'message': 'Synthetic current-user pause', 'expected_revision': None,
                'contract_sha256': before['contract_sha256']},
                user_directed=True, source='current-user:threaded-admission-test')
        changed = self.assert_writer_commits_after_attempt('ordered-pause', parent, pause)
        after = parent.snapshot()
        self.assertEqual(after['budget']['wall_seconds']['charged_estimate'],
                         before['budget']['wall_seconds']['charged_estimate'] + 5)
        for key in ('runs', 'receipts', 'exposures', 'contract_sha256'):
            self.assertEqual(after[key], before[key])
        with parent._db(True) as db:
            self.assertEqual(current_steering(db)['sha256'], changed['received_revision'])

    def test_threaded_source_initialization_cannot_commit_between_check_and_attempt(self):
        import test_rds_project_lifecycle as lifecycle_fixture
        helper = lifecycle_fixture.ProjectLifecycleTests(methodName='runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.prepare_contract()
        self.assertFalse(helper.store.path.exists())
        changed = self.assert_writer_commits_after_attempt('ordered-init', helper.store,
            lambda: helper.store.initialize(helper.contract), source_root=helper.root)
        self.assertEqual(changed['contract_sha256'], digest(helper.contract))
        self.assertEqual(helper.store.snapshot()['budget'], changed['budget'])
        self.assertEqual(helper.store.snapshot()['runs'], [])

    def test_threaded_source_activation_cannot_commit_between_check_and_attempt(self):
        import test_rds_project_lifecycle as lifecycle_fixture
        from rds_project_lifecycle import enable_advisor
        helper = lifecycle_fixture.ProjectLifecycleTests(methodName='runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.initialize_accounted_source(helper)
        before = helper.store.snapshot()
        preview = enable_advisor(helper.store, helper.policy)
        def refused_activation():
            with self.assertRaisesRegex(ValueError, 'Retained child has reserved/running work'):
                enable_advisor(helper.store, helper.policy, apply=True,
                               expected_snapshot=preview['snapshot_sha256'])
            return helper.store.snapshot()
        changed = self.assert_writer_commits_after_attempt('ordered-activation', helper.store,
            refused_activation, source_root=helper.root, finish_writer_before_claim=True)
        after = helper.store.snapshot()
        self.assertEqual(after, changed)
        for key in ('contract_sha256', 'runs', 'receipts', 'exposures'):
            self.assertEqual(after[key], before[key])
        self.assertEqual(after['budget']['wall_seconds']['charged_estimate'],
                         before['budget']['wall_seconds']['charged_estimate'] + 5)

    def test_empty_source_lock_anchor_is_not_a_research_project_but_tms_is(self):
        from rds_project_lifecycle import discover
        fixture = self.fixture
        source_root = fixture.root / 'fresh-source'
        source_root.mkdir()
        (source_root / 'probe.py').write_text('print("positive quick fixture")\n', encoding='utf-8')
        self.assertFalse(ProjectStore(source_root).path.exists())
        completed = json.loads(fixture.call('exec', '--name', 'empty-anchor', '--timeout', '5', '--',
            sys.executable, '-B', 'probe.py', root=source_root).stdout)
        self.assertEqual(completed['run_status'], 'SUCCEEDED')
        source = ProjectStore(source_root)
        self.assertTrue(source.path.is_file())
        with source._db(True) as db:
            self.assertEqual(db.execute("SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall(), [])
        self.assertEqual(discover(source_root)['status'], 'NO_PROJECT_FOUND')
        self.assertEqual(discover(source_root)['workflow']['mode'], 'UNINITIALIZED')
        save(source_root, {'schema': 1, 'nodes': [
            {'id': 'original-claim', 'status': 'UNKNOWN', 'source': {'locator': 'synthetic declaration'}}],
            'hyperedges': [], 'goals': []}, expected=None, source_base=source_root)
        native = discover(source_root)
        self.assertEqual(native['status'], 'EXISTING_PROJECT')
        self.assertEqual(native['relation'], 'CURRENT')
        self.assertEqual(native['project_root'], str(source_root.resolve()))

    def test_owner_pause_after_preparation_blocks_actual_attempt_without_refunding(self):
        from rds_steering import submit, current as current_steering
        args, reviewed, workspace = self.reviewed_job('pause-race')
        parent = ProjectStore(self.fixture.ledger)
        before, observed = parent.snapshot(), {}
        original_execute = ProjectStore.execute

        def pause_before_admission(store, run_id, *positional, **keywords):
            self.assertEqual(store.root, workspace.resolve())
            self.assertEqual(observed, {})
            self.assertTrue(callable(keywords.get('admission_guard')))
            observed['budget'] = parent.snapshot()['budget']
            observed['pause'] = submit(parent, {'id': 'pause-before-launch', 'kind': 'pause',
                'message': 'Current user stops new dispatch', 'expected_revision': None,
                'contract_sha256': before['contract_sha256']},
                user_directed=True, source='current-user:synthetic-admission-test')
            return original_execute(store, run_id, *positional, **keywords)

        with mock.patch.object(ProjectStore, 'execute', new=pause_before_admission):
            with self.assertRaisesRegex(ValueError, 'Human steering changed before quick admission'):
                rds_quick.execute(args, review=reviewed)
        self.assert_held_child_and_original_accounting(workspace, before, observed['budget'])
        with parent._db(True) as db:
            pause = current_steering(db)
        self.assertEqual(pause['sha256'], observed['pause']['received_revision'])
        self.assertTrue(pause['dispatch']['paused'])

    def test_new_source_quick_policies_after_preparation_block_actual_attempt(self):
        fixture = self.fixture
        parent = ProjectStore(fixture.ledger)
        for key, value in (
                ('execution_policy', {'schema': 1, 'max_attempts': 1}),
                ('stop_policy', {'schema': 1, 'wall_seconds': 10,
                                 'progress': {'window_seconds': 1, 'min_bytes': 1}})):
            with self.subTest(policy=key):
                # Copy valid original role/protocol bytes under distinct source
                # names; the late contract does not relabel the child's inputs.
                source_root = fixture.root / ('source-' + key)
                source_root.mkdir()
                declared = deepcopy(parent.snapshot()['contract'])
                for binding in declared['bindings']:
                    original_path = parent._path(binding['path'])
                    binding['path'] = 'declared-' + binding['path']
                    destination = source_root / binding['path']
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(original_path, destination)
                declared['allowed_commands'] = [[sys.executable, '-B', 'declared-probe.py']]
                declared[key] = value
                args, reviewed, workspace = self.reviewed_job('new-' + key, source_root)
                before, observed = parent.snapshot(), {}
                source = ProjectStore(source_root)
                original_execute = ProjectStore.execute

                def initialize_before_admission(store, run_id, *positional, **keywords):
                    self.assertEqual(store.root, workspace.resolve())
                    self.assertEqual(observed, {})
                    observed['budget'] = parent.snapshot()['budget']
                    observed['source'] = source.initialize(declared)
                    return original_execute(store, run_id, *positional, **keywords)

                with mock.patch.object(ProjectStore, 'execute', new=initialize_before_admission):
                    with self.assertRaisesRegex(ValueError, 'Quick parent contract changed before admission'):
                        rds_quick.execute(args, review=reviewed)
                self.assert_held_child_and_original_accounting(workspace, before, observed['budget'])
                self.assertEqual(source.snapshot(), observed['source'])
                self.assertEqual(source.snapshot()['contract_sha256'], digest(declared))

    def test_new_source_maintenance_contract_blocks_actual_attempt(self):
        import test_rds_project as project_fixture
        helper = project_fixture.StopPolicyAndMaintenanceTests(methodName='runTest')
        helper.setUp()
        self.addCleanup(helper.tearDown)
        args, reviewed, workspace = self.reviewed_job('maintenance-race', helper.root)
        parent = ProjectStore(self.fixture.ledger)
        before, observed = parent.snapshot(), {}
        original_execute = ProjectStore.execute

        def initialize_before_admission(store, run_id, *positional, **keywords):
            self.assertEqual(store.root, workspace.resolve())
            self.assertEqual(observed, {})
            observed['budget'] = parent.snapshot()['budget']
            # Reuse the established original-goal/config-bound maintenance
            # declaration; do not invent a bypassing maintenance policy shape.
            source = helper.contract_with(maintenance_allowance={
                'schema': 1, 'wall_seconds': 1, 'max_uses': 1})
            observed['source'] = source.snapshot()
            return original_execute(store, run_id, *positional, **keywords)

        with mock.patch.object(ProjectStore, 'execute', new=initialize_before_admission):
            with self.assertRaisesRegex(ValueError, 'Quick parent contract changed before admission'):
                rds_quick.execute(args, review=reviewed)
        self.assert_held_child_and_original_accounting(workspace, before, observed['budget'])
        self.assertEqual(helper.store.snapshot(), observed['source'])
        self.assertIn('maintenance_allowance', observed['source']['contract'])

    def test_source_advisor_activation_after_preparation_refuses_live_child(self):
        import test_rds_project_lifecycle as lifecycle_fixture
        from rds_project_lifecycle import enable_advisor
        helper = lifecycle_fixture.ProjectLifecycleTests(methodName='runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.initialize_accounted_source(helper)
        source = helper.store
        source_before = source.snapshot()
        args, reviewed, workspace = self.reviewed_job('activation-race', helper.root)
        parent = ProjectStore(self.fixture.ledger)
        before, observed = parent.snapshot(), {}
        original_execute = ProjectStore.execute

        def activate_before_admission(store, run_id, *positional, **keywords):
            self.assertEqual(store.root, workspace.resolve())
            self.assertEqual(observed, {})
            observed['budget'] = parent.snapshot()['budget']
            enable_advisor(source, helper.policy)
            return original_execute(store, run_id, *positional, **keywords)

        with mock.patch.object(ProjectStore, 'execute', new=activate_before_admission):
            with self.assertRaisesRegex(ValueError, 'Retained child has reserved/running work'):
                rds_quick.execute(args, review=reviewed)
        self.assert_held_child_and_original_accounting(workspace, before, observed['budget'])
        after = source.snapshot()
        self.assertEqual(after['budget'], observed['budget'])
        for key in ('contract_sha256', 'runs', 'receipts', 'exposures'):
            self.assertEqual(after[key], source_before[key])

    def test_owner_activation_after_terminal_receipt_rejects_late_checkpoint(self):
        import test_rds_project_lifecycle as lifecycle_fixture
        from rds_project_lifecycle import enable_advisor
        helper = lifecycle_fixture.ProjectLifecycleTests(methodName='runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        # QUICK's parent allowance supports an explicit wall-only ledger.
        helper.recipe['budget'] = {'wall_seconds': 30}
        for route in helper.recipe['routes']:
            route['run']['resource_estimates'] = {'wall_seconds': route['run']['resource_estimates']['wall_seconds']}
        helper.init_quick()
        self.fixture.ledger = helper.root
        parent = helper.store
        before = parent.snapshot()
        args, reviewed, workspace = self.reviewed_job('terminal-activation')
        original_execute = ProjectStore.execute
        observed = {}

        def activate_after_receipt(store, run_id, *positional, **keywords):
            receipt = original_execute(store, run_id, *positional, **keywords)
            self.assertEqual(receipt['run_status'], 'SUCCEEDED')
            observed['child'] = store.snapshot()
            observed['charged'] = parent.snapshot()
            preview = enable_advisor(parent, helper.policy)
            observed['activation'] = enable_advisor(parent, helper.policy, apply=True,
                                                    expected_snapshot=preview['snapshot_sha256'])
            observed['parent'] = parent.snapshot()
            return receipt

        with mock.patch.object(ProjectStore, 'execute', new=activate_after_receipt):
            with self.assertRaisesRegex(ValueError, 'Quick parent contract changed before checkpoint publication'):
                rds_quick.execute(args, review=reviewed)
        self.assertEqual(ProjectStore(workspace).snapshot(), observed['child'])
        self.assertEqual(parent.snapshot(), observed['parent'])
        child = observed['child']
        self.assertEqual(len(child['runs']), 1)
        self.assertIsNotNone(child['runs'][0]['attempt_id'])
        self.assertEqual(child['receipts'][0]['run_status'], 'SUCCEEDED')
        self.assertTrue((workspace / 'launch-marker').is_file())
        self.assertEqual(observed['parent']['budget'], observed['charged']['budget'])
        self.assertEqual(observed['charged']['budget']['wall_seconds']['charged_estimate'],
                         before['budget']['wall_seconds']['charged_estimate'] + 5)
        self.assertNotEqual(observed['parent']['contract_sha256'], before['contract_sha256'])
        from rds_checkpoints import save_checkpoint
        # Exercise both independent checks: old live binding with an old
        # snapshot, and a stale snapshot against the new effective binding.
        for expected in (before['contract_sha256'], observed['parent']['contract_sha256']):
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(ValueError, 'Quick parent contract changed before checkpoint publication'):
                    save_checkpoint(helper.root, 'stale-identity', before, kind='project',
                                    _expected_contract_sha256=expected)
        with parent._db(True) as db:
            ids = [row[0] for row in db.execute('SELECT id FROM checkpoints ORDER BY id')]
        self.assertEqual(ids, [rds_quick._checkpoint_name('before', args.name)])

    def test_accounted_parent_pause_locks_out_child_materialization(self):
        import test_rds_project_lifecycle as lifecycle_fixture
        import rds_steering as steering
        helper = lifecycle_fixture.ProjectLifecycleTests(methodName='runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        self.initialize_accounted_source(helper)
        args, _, workspace = self.reviewed_job('preparation-pause', helper.root)
        source = helper.store
        before = source.snapshot()
        charged, held, attempting, release = (threading.Event() for _ in range(4))
        observed = {}
        original_charge, original_current = rds_quick._charge_ledger, steering.current
        original_mutation = rds_quick.mutation

        def charge_then_writer(*positional, **keywords):
            result = original_charge(*positional, **keywords)
            observed['charged'] = source.snapshot()
            charged.set()
            self.assertTrue(held.wait(5), 'Pause writer did not obtain its real transaction')
            return result

        def current_then_hold(db):
            result = original_current(db)
            if threading.get_ident() == observed.get('writer') and db.in_transaction:
                held.set()
                self.assertTrue(release.wait(5), 'Pause writer release was not signalled')
            return result

        def pause():
            observed['writer'] = threading.get_ident()
            return steering.submit(source, {'id': 'pause-preparation', 'kind': 'pause',
                'message': 'Synthetic current-user pause', 'expected_revision': None,
                'contract_sha256': before['contract_sha256']},
                user_directed=True, source='current-user:preparation-race-test')

        @contextmanager
        def observe_prepare_lock():
            if held.is_set():
                attempting.set()
            # Production takes this shared gate before any parent DB lock.
            with original_mutation():
                yield

        with ThreadPoolExecutor(max_workers=2) as pool, \
                mock.patch.object(rds_quick, '_charge_ledger', new=charge_then_writer), \
                mock.patch.object(steering, 'current', new=current_then_hold), \
                mock.patch.object(rds_quick, 'mutation', new=observe_prepare_lock):
            quick = pool.submit(rds_quick.execute, args)
            try:
                self.assertTrue(charged.wait(5), 'Original allowance was not charged')
                writer = pool.submit(pause)
                self.assertTrue(held.wait(5))
                self.assertTrue(attempting.wait(5), 'QUICK did not attempt its preparation lock')
                self.assertFalse(workspace.exists(), 'Materialization bypassed the parent writer lock')
                self.assertFalse(quick.done())
            finally:
                release.set()
            received = writer.result(timeout=5)
            with self.assertRaisesRegex(ValueError, 'Human steering changed before quick admission'):
                quick.result(timeout=5)
        self.assertFalse(workspace.exists())
        after = source.snapshot()
        self.assertEqual(after['budget'], observed['charged']['budget'])
        self.assertEqual(after['budget']['wall_seconds']['charged_estimate'],
                         before['budget']['wall_seconds']['charged_estimate'] + 5)
        for key in ('runs', 'receipts', 'exposures', 'contract_sha256'):
            self.assertEqual(after[key], before[key])
        with source._db(True) as db:
            self.assertEqual(steering.current(db)['sha256'], received['received_revision'])

    def test_plain_frozen_source_cannot_open_unaccounted_quick_or_replace_ledger(self):
        import test_rds_project_lifecycle as lifecycle_fixture
        helper = lifecycle_fixture.ProjectLifecycleTests(methodName='runTest')
        helper.setUp()
        self.addCleanup(helper.doCleanups)
        helper.init_quick()
        before, owner_before = helper.store.snapshot(), ProjectStore(self.fixture.ledger).snapshot()
        for name, options in (
                ('unaccounted', []),
                ('replacement-owner', ['--context', str(self.fixture.context_path),
                    '--graph', str(self.fixture.graph_path), '--ledger', str(self.fixture.ledger)])):
            with self.subTest(name=name):
                result = self.fixture.call('exec', '--name', name, '--timeout', '5',
                    '--bind', 'code=code.py', *options,
                    '--', sys.executable, '-B', '-c',
                    'from pathlib import Path; Path("escape-marker").write_text("started")',
                    root=helper.root, ok=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('Frozen source project requires project create/execute', result.stderr)
                self.assertFalse((helper.root / '.rds/exec' / name).exists())
                self.assertFalse((helper.root / 'escape-marker').exists())
                self.assertEqual(helper.store.snapshot(), before)
                self.assertEqual(ProjectStore(self.fixture.ledger).snapshot(), owner_before)

    def test_record_choice_rejects_live_tms_change_in_checkpoint_transaction(self):
        parent = ProjectStore(self.fixture.ledger)
        initial = {'schema': 1, 'nodes': [], 'hyperedges': [], 'goals': []}
        for index in range(2):
            with self.subTest(original_head=index):
                original = current(self.fixture.ledger)
                if index:
                    self.assertIsNotNone(original)
                else:
                    self.assertIsNone(original)
                args, reviewed, _ = self.reviewed_job('stale-selection-' + str(index))
                before = parent.snapshot()
                changed = deepcopy(initial)
                changed['nodes'].append({'id': 'new-' + str(index), 'status': 'UNKNOWN',
                    'source': {'locator': 'concurrent synthetic declaration'}})
                new_sha = save(self.fixture.ledger, changed,
                               expected=original['sha256'] if original else None,
                               source_base=self.fixture.ledger)
                checkpoint = 'stale-choice-' + str(index)
                with self.assertRaisesRegex(ValueError, 'Dependency snapshot changed before checkpoint publication'):
                    rds_quick.record_choice(self.fixture.ledger, reviewed[0], reviewed[1], None, checkpoint)
                self.assertEqual(parent.snapshot(), before)
                self.assertEqual(current(self.fixture.ledger)['sha256'], new_sha)
                with parent._db(True) as db:
                    exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='checkpoints'").fetchone()
                    if exists:
                        self.assertIsNone(db.execute('SELECT 1 FROM checkpoints WHERE id=?', (checkpoint,)).fetchone())
        _, fresh, _ = self.reviewed_job('fresh-selection')
        saved = rds_quick.record_choice(self.fixture.ledger, fresh[0], fresh[1], None, 'fresh-choice')
        self.assertEqual(saved['status'], 'SAVED')

    def test_terminal_receipt_history_retains_old_decision_after_tms_update(self):
        args, reviewed, workspace = self.reviewed_job('terminal-map-update')
        original_execute = ProjectStore.execute
        observed = {}

        def update_after_receipt(store, run_id, *positional, **keywords):
            receipt = original_execute(store, run_id, *positional, **keywords)
            observed['child'] = store.snapshot()
            observed['map_sha'] = save(self.fixture.ledger, {'schema': 1, 'nodes': [
                {'id': 'new-evidence', 'status': 'UNKNOWN',
                 'source': {'locator': 'synthetic declaration after original execution'}}],
                'hyperedges': [], 'goals': []}, expected=None, source_base=self.fixture.ledger)
            return receipt

        with mock.patch.object(ProjectStore, 'execute', new=update_after_receipt):
            result = rds_quick.execute(args, review=reviewed)
        self.assertEqual(ProjectStore(workspace).snapshot(), observed['child'])
        self.assertEqual(result['receipt']['run_status'], 'SUCCEEDED')
        self.assertEqual(current(self.fixture.ledger)['sha256'], observed['map_sha'])
        decision, _ = rds_quick.latest_decision(self.fixture.ledger,
                                               rds_quick._checkpoint_name('after', args.name))
        self.assertEqual(decision['execution']['receipt_sha256'], result['receipt']['sha256'])
        self.assertEqual(decision['scientific_support'], 'UNKNOWN')

    def test_tms_change_after_final_choice_rejects_actual_attempt_admission(self):
        fixture = self.fixture
        fixture.script('from pathlib import Path\nPath("launch-marker").write_text("started")\n')
        initial = {'schema': 1, 'nodes': [
            {'id': 'declared', 'status': 'SUPPORTED', 'source': {'locator': 'synthetic declared premise'}}],
            'hyperedges': [], 'goals': []}
        initial_sha = save(fixture.ledger, initial, expected=None, source_base=fixture.ledger)
        args = rds_cli.parser().parse_args([
            '--root', str(fixture.root), 'exec', '--name', 'coverage-race', '--timeout', '5',
            '--context', str(fixture.context_path), '--graph', str(fixture.graph_path),
            '--ledger', str(fixture.ledger), '--', sys.executable, '-B', 'probe.py'])
        review_args = argparse.Namespace(root=str(fixture.ledger),
            research_context=str(fixture.context_path), graph=str(fixture.graph_path), saved_dependencies=False)
        advice = rds_cli.cmd_advise(review_args, rds_cli.RDSState(fixture.ledger))
        context = project_context(fixture.ledger, json.loads(fixture.context_path.read_text(encoding='utf-8')))
        search = next(row['search'] for row in advice['recommendations']
                      if row['type'] == 'EXECUTABLE_DIRECTION_SEARCH')
        self.assertTrue(search['analysis_coverage']['full'])
        self.assertEqual(context['dependency_snapshot_sha256'], initial_sha)

        parent = ProjectStore(fixture.ledger)
        before = parent.snapshot()
        workspace = fixture.root / '.rds' / 'exec' / 'coverage-race'
        original_execute = ProjectStore.execute
        observed = {}

        def update_before_real_admission(store, run_id, *positional, **keywords):
            # This is after quick.execute's final outer choice, but before the
            # parent admission locks and real child registration. Commit a
            # legitimate new map here; inspect the actual registered run only
            # when the original execute reaches its admission callback.
            self.assertEqual(store.root, workspace.resolve())
            self.assertEqual(run_id, 'coverage-race')
            self.assertTrue(callable(keywords.get('admission_guard')))
            self.assertEqual(observed, {})
            observed['budget'] = parent.snapshot()['budget']
            changed = deepcopy(initial)
            changed['nodes'].append({'id': 'new-premise', 'status': 'UNKNOWN',
                                     'source': {'locator': 'concurrent synthetic declaration'}})
            observed['map'] = changed
            observed['sha256'] = save(fixture.ledger, changed, expected=initial_sha,
                                     source_base=fixture.ledger)
            original_guard = keywords['admission_guard']

            def observe_registered_admission(db, run):
                actual = store._run(db, run_id)
                self.assertEqual(actual, run)
                self.assertEqual(actual['status'], 'RESERVED')
                self.assertIsNone(actual['attempt_id'])
                self.assertIsNone(actual['started_at'])
                observed['registered'] = actual['id']
                return original_guard(db, run)

            keywords['admission_guard'] = observe_registered_admission
            return original_execute(store, run_id, *positional, **keywords)

        with mock.patch.object(ProjectStore, 'execute', new=update_before_real_admission):
            with self.assertRaisesRegex(ValueError, 'Dependency snapshot changed after analysis'):
                rds_quick.execute(args, review=(advice, context))

        self.assertNotEqual(observed['sha256'], initial_sha)
        self.assertEqual(observed['registered'], 'coverage-race')
        retained = ProjectStore(workspace).snapshot()
        self.assertEqual(len(retained['runs']), 1)
        self.assertEqual(retained['runs'][0]['status'], 'RESERVED')
        self.assertIsNone(retained['runs'][0]['attempt_id'])
        self.assertIsNone(retained['runs'][0]['started_at'])
        self.assertEqual(retained['receipts'], [])
        self.assertFalse((workspace / 'launch-marker').exists())
        after = parent.snapshot()
        self.assertEqual(after['budget'], observed['budget'])
        self.assertEqual(after['runs'], before['runs'])
        self.assertEqual(after['receipts'], before['receipts'])
        self.assertEqual(after['contract_sha256'], before['contract_sha256'])
        previous = before['budget']['wall_seconds']
        charged = after['budget']['wall_seconds']
        self.assertEqual(charged['charged_estimate'], previous['charged_estimate'] + 5)
        self.assertEqual(charged['spent_measured'], previous['spent_measured'])
        self.assertEqual(charged['reserved'], previous['reserved'])
        self.assertEqual(charged['cap'], previous['cap'])
        self.assertAlmostEqual(charged['remaining'], previous['remaining'] - 5)
        latest = current(fixture.ledger)
        self.assertEqual(latest['sha256'], observed['sha256'])
        self.assertEqual(latest['dependency_map'], observed['map'])


if __name__ == '__main__':
    unittest.main()
