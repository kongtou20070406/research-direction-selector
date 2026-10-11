"""Recover only the original prospective completion after a terminal receipt."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import os
import shutil
import sys
import unittest
from unittest.mock import patch

import test_rds_quick as fixture

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_cli
import rds_quick as quick
import rds_checkpoints as checkpoints
import rds_campaign as campaign
from rds_advisor_coverage import project_context
from rds_project import ProjectStore
from rds_project_lifecycle import enable_advisor
from rds_tms_store import current, save


class QuickCheckpointRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.QuickTests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.f.initialize_ledger()

    def reviewed_request(self, name):
        args = rds_cli.parser().parse_args([
            '--root', str(self.f.root), 'exec', '--name', name, '--timeout', '5',
            '--context', str(self.f.context_path), '--graph', str(self.f.graph_path),
            '--ledger', str(self.f.ledger), '--', sys.executable, '-B', 'probe.py'])
        review_args = argparse.Namespace(root=str(self.f.ledger), research_context=str(self.f.context_path),
                                        graph=str(self.f.graph_path), saved_dependencies=False)
        advice = rds_cli.cmd_advise(review_args, rds_cli.RDSState(self.f.ledger))
        context = project_context(self.f.ledger, json.loads(self.f.context_path.read_text(encoding='utf-8')))
        return args, (advice, context)

    def test_pending_allowance_before_materialization_resumes_same_choice_once(self):
        self.f.script('print("synthetic QUICK recovery")\n')
        args, review = self.reviewed_request('before-materialization')
        workspace = (self.f.root / '.rds/exec' / args.name).resolve()
        original_mkdir = Path.mkdir
        injected = {'done': False}

        def fail_at_child_materialization(path, *positional, **keywords):
            if Path(path).resolve() == workspace.resolve() and not injected['done']:
                injected['done'] = True
                raise OSError('synthetic interruption before child workspace creation')
            return original_mkdir(path, *positional, **keywords)

        with patch.dict(os.environ, self.f.env):
            with patch.object(Path, 'mkdir', autospec=True, side_effect=fail_at_child_materialization):
                with self.assertRaisesRegex(OSError, 'before child workspace creation'):
                    quick.execute(args, review=review)

            self.assertTrue(injected['done'])
            self.assertFalse(workspace.exists())
            parent = ProjectStore(self.f.ledger)
            parent_after_interruption = parent.snapshot()
            pending_before, _ = quick.latest_decision(
                self.f.ledger, quick._checkpoint_name('before', args.name))
            self.assertEqual(pending_before['candidate']['id'], args.choose)
            with parent._db(True) as db:
                allowance_rows = db.execute(
                    "SELECT body FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE' "
                    "AND json_extract(body,'$.job_root')=?", (str(workspace),)).fetchall()
                self.assertEqual(len(allowance_rows), 1)
                allowance = json.loads(allowance_rows[0]['body'])
                self.assertEqual(allowance['materialization_state'], 'PENDING')
                self.assertEqual(db.execute(
                    "SELECT count(*) FROM events WHERE json_extract(body,'$.kind')='QUICK_JOB_ADMITTED' "
                    "AND json_extract(body,'$.job_root')=?", (str(workspace),)).fetchone()[0], 0)

            with patch.object(quick, 'choice', side_effect=AssertionError('recovery must retain the saved choice')):
                recovered = quick.execute(args, review=review)
            receipt = recovered['receipt']
            self.assertEqual(receipt['run_status'], 'SUCCEEDED')
            self.assertTrue(recovered['execution_started'])
            child = ProjectStore(workspace)
            child_after_recovery = child.snapshot()
            self.assertEqual(len(child_after_recovery['receipts']), 1)
            parent_after_recovery = parent.snapshot()
            self.assertEqual(parent_after_recovery['budget'], parent_after_interruption['budget'])
            with parent._db(True) as db:
                self.assertEqual(db.execute(
                    "SELECT count(*) FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE' "
                    "AND json_extract(body,'$.job_root')=?", (str(workspace),)).fetchone()[0], 1)
                markers = db.execute(
                    "SELECT body FROM events WHERE json_extract(body,'$.kind')='QUICK_JOB_ADMITTED' "
                    "AND json_extract(body,'$.job_root')=?", (str(workspace),)).fetchall()
                self.assertEqual(len(markers), 1)
                marker = json.loads(markers[0]['body'])
                self.assertEqual(marker['materialization_state'], 'MATERIALIZED')
                self.assertEqual(marker['request_sha256'], allowance['request_sha256'])

            with (patch.object(quick, 'choice', side_effect=AssertionError('completed job must not reselect')),
                  patch.object(ProjectStore, 'execute', side_effect=AssertionError('completed job must not redispatch'))):
                repeated = quick.execute(args, review=review)
            self.assertFalse(repeated['execution_started'])
            self.assertEqual(repeated['receipt'], receipt)
            self.assertEqual(child.snapshot(), child_after_recovery)
            self.assertEqual(parent.snapshot()['budget'], parent_after_interruption['budget'])

            # If a materialized child disappears after its durable marker,
            # fail closed instead of treating it as the pre-creation gap.
            shutil.rmtree(workspace)
            with patch.object(quick, 'choice', side_effect=AssertionError('must reject before reselection')):
                with self.assertRaisesRegex(ValueError, 'previously materialized but is missing'):
                    quick.execute(args, review=review)
            self.assertEqual(parent.snapshot()['budget'], parent_after_interruption['budget'])

    def test_plain_quick_pending_allowance_resumes_without_reselection(self):
        self.f.initialize_policy_ledger()
        source_root = self.f.ledger
        parent = ProjectStore(source_root)
        args = rds_cli.parser().parse_args([
            '--root', str(source_root), 'exec', '--name', 'plain-before-materialization', '--timeout', '5',
            '--', sys.executable, '-B', 'probe.py'])
        workspace = (source_root / '.rds/exec' / args.name).resolve()
        original_mkdir = Path.mkdir
        injected = {'done': False}

        def fail_at_child_materialization(path, *positional, **keywords):
            if Path(path).resolve() == workspace.resolve() and not injected['done']:
                injected['done'] = True
                raise OSError('synthetic plain interruption before child workspace creation')
            return original_mkdir(path, *positional, **keywords)

        with patch.dict(os.environ, self.f.env):
            with patch.object(Path, 'mkdir', autospec=True, side_effect=fail_at_child_materialization):
                with self.assertRaisesRegex(OSError, 'before child workspace creation'):
                    quick.execute(args)
            self.assertTrue(injected['done'])
            self.assertFalse(workspace.exists())
            before = parent.snapshot()
            with parent._db(True) as db:
                allowance = json.loads(db.execute(
                    "SELECT body FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE' "
                    "AND json_extract(body,'$.job_root')=?", (str(workspace),)).fetchone()['body'])
            self.assertEqual(allowance['materialization_state'], 'PENDING')

            recovered = quick.execute(args)
            self.assertEqual(recovered['receipt']['run_status'], 'SUCCEEDED')
            self.assertTrue(recovered['execution_started'])
            after = parent.snapshot()
            self.assertEqual(after['budget'], before['budget'])
            with parent._db(True) as db:
                self.assertEqual(db.execute(
                    "SELECT count(*) FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE' "
                    "AND json_extract(body,'$.job_root')=?", (str(workspace),)).fetchone()[0], 1)
                marker = json.loads(db.execute(
                    "SELECT body FROM events WHERE json_extract(body,'$.kind')='QUICK_JOB_ADMITTED' "
                    "AND json_extract(body,'$.job_root')=?", (str(workspace),)).fetchone()['body'])
            self.assertEqual(marker['materialization_state'], 'MATERIALIZED')
            self.assertEqual(marker['request_sha256'], allowance['request_sha256'])

            repeated = quick.execute(args)
            self.assertFalse(repeated['execution_started'])
            self.assertEqual(repeated['receipt']['sha256'], recovered['receipt']['sha256'])
            self.assertEqual(parent.snapshot()['budget'], before['budget'])

    def test_terminal_receipt_recovers_original_after_checkpoint_without_reselection(self):
        self.f.script('from pathlib import Path\nPath("launch-marker").write_text("started")\n')
        args = rds_cli.parser().parse_args([
            '--root', str(self.f.root), 'exec', '--name', 'tail-recovery', '--timeout', '5',
            '--context', str(self.f.context_path), '--graph', str(self.f.graph_path),
            '--ledger', str(self.f.ledger), '--', sys.executable, '-B', 'probe.py'])
        review_args = argparse.Namespace(root=str(self.f.ledger), research_context=str(self.f.context_path),
                                        graph=str(self.f.graph_path), saved_dependencies=False)
        advice = rds_cli.cmd_advise(review_args, rds_cli.RDSState(self.f.ledger))
        context = project_context(self.f.ledger, json.loads(self.f.context_path.read_text(encoding='utf-8')))
        review = (advice, context)
        original_save = checkpoints.save_checkpoint
        after_id = quick._checkpoint_name('after', args.name)

        def interrupted(root, identity, *positional, **keywords):
            if identity == after_id:
                raise OSError('interrupted original after checkpoint')
            return original_save(root, identity, *positional, **keywords)

        with patch.object(checkpoints, 'save_checkpoint', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'interrupted original after checkpoint'):
                quick.execute(args, review=review)
        workspace = self.f.root / '.rds/exec' / args.name
        child = ProjectStore(workspace)
        original_child = child.snapshot()
        self.assertEqual(len(original_child['receipts']), 1)
        receipt = original_child['receipts'][0]
        self.assertEqual(receipt['run_status'], 'SUCCEEDED')
        parent = ProjectStore(self.f.ledger)
        parent_before = parent.snapshot()
        charged = parent_before['budget']
        policy_context = {'decision': deepcopy(self.f.context['decision'])}
        policy_context['decision']['goal_conditions'] = [
            {'fact': 'run.ledger.succeeded', 'op': 'eq', 'value': True}]
        policy = {'schema': 1, 'context': policy_context, 'graph': deepcopy(self.f.graph),
                  'routes': [{'candidate': 'inspect-x',
                              'manifest': deepcopy(parent_before['runs'][0]['manifest'])}],
                  'observations': []}
        before, _ = quick.latest_decision(self.f.ledger, quick._checkpoint_name('before', args.name))
        with self.assertRaisesRegex(ValueError, 'resolved prospective QUICK checkpoint settlement'):
            campaign.bind(parent, self.f.root)
        with self.assertRaisesRegex(ValueError, 'resolved prospective QUICK checkpoint settlement'):
            enable_advisor(parent, policy)
        with self.assertRaisesRegex(ValueError, 'resolved prospective QUICK checkpoint settlement'):
            enable_advisor(parent, policy, apply=True, expected_snapshot='0' * 64)
        self.assertEqual(parent.snapshot(), parent_before)
        source = self.f.root / 'probe.py'
        original_source = source.read_bytes()
        source.write_bytes(original_source + b'# changed input\n')
        with self.assertRaisesRegex(ValueError, 'Job identity is frozen'):
            quick.execute(args, review=review)
        source.write_bytes(original_source)
        self.assertEqual(parent.snapshot()['budget'], charged)
        map_sha = save(self.f.ledger, {'schema': 1, 'nodes': [], 'hyperedges': [], 'goals': []}, expected=None)
        # Public recovery needs no caller context or new graph/choice. The paid
        # receipt and frozen before/request are enough to complete its history.
        recovered_cli = self.f.call('project', 'recover', '--id', args.name, root=workspace)
        self.assertEqual(json.loads(recovered_cli.stdout), receipt)
        with patch.object(quick, 'choice', side_effect=AssertionError('recovery must not select again')), \
                patch.object(ProjectStore, 'execute', side_effect=AssertionError('recovery must not dispatch again')):
            recovered = quick.execute(args, review=review)
            decision, _ = quick.latest_decision(self.f.ledger, after_id)
            repeated = quick.execute(args, review=review)
        self.assertEqual(recovered['receipt'], receipt)
        self.assertEqual(repeated['receipt'], receipt)
        self.assertFalse(recovered['execution_started'])
        self.assertEqual(child.snapshot(), original_child)
        self.assertEqual(parent.snapshot()['budget'], charged)
        self.assertEqual(current(self.f.ledger)['sha256'], map_sha)
        for key, value in before.items():
            self.assertEqual(decision[key], value)
        self.assertEqual(decision['execution'], {'job_root': str(workspace.resolve()),
                         'receipt_sha256': receipt['sha256'], 'run_status': receipt['run_status']})
        self.assertEqual(decision['scientific_support'], 'UNKNOWN')
        with parent._db(True) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM checkpoints').fetchone()[0], 2)
        bound = campaign.bind(parent, self.f.root)
        self.assertEqual(campaign.binding(self.f.root)['binding_id'], bound['binding_id'])
        self.assertEqual(parent.snapshot()['budget'], charged)
        preview = enable_advisor(parent, policy)
        self.assertEqual(preview['status'], 'ADVISOR_ACTIVATION_PREVIEW')
        activated = enable_advisor(parent, policy, apply=True,
                                   expected_snapshot=preview['snapshot_sha256'])
        self.assertEqual(activated['status'], 'ADVISOR_ENABLED')
        self.assertFalse(activated['execution_started'])
        parent_after = parent.snapshot()
        self.assertEqual(parent_after['budget'], charged)
        self.assertEqual(parent_after['runs'], parent_before['runs'])
        self.assertEqual(parent_after['receipts'], parent_before['receipts'])
        self.assertEqual(child.snapshot(), original_child)
        self.assertEqual(quick.latest_decision(self.f.ledger, after_id)[0], decision)
        with parent._db(True) as db:
            self.assertEqual(json.loads(db.execute('SELECT body FROM contract').fetchone()[0]),
                             parent_before['contract'])


class QuickPendingCompletionTests(unittest.TestCase):
    def test_background_without_receipt_defers_terminal_validation(self):
        with patch.object(quick, '_prospective_completion', side_effect=AssertionError('not terminal')):
            self.assertIsNone(quick._complete_prospective(None, {}, {'status': 'RUNNING'}))
            self.assertIsNone(quick._recover_prospective(None, {}, {'status': 'RUNNING'}))


if __name__ == '__main__':
    unittest.main()
