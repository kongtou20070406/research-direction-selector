"""Recover only the original prospective completion after a terminal receipt."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
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
        self.assertEqual(decision['execution'], {'job_root': str(workspace),
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
