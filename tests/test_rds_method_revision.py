"""Synthetic real executions across a method change in one owning ledger."""
from copy import deepcopy
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
from rds_project import ProjectStore, canonical, digest, file_sha
from rds_checkpoints import save_checkpoint, restore_checkpoint
import rds_method_revision as revision

OLD = '''import json, pathlib, sys
p = pathlib.Path(sys.argv[1])
p.parent.mkdir(parents=True, exist_ok=False)
p.write_text(json.dumps({'score': 1}), encoding='utf-8')
'''
NEW = OLD.replace('p.parent.mkdir(parents=True, exist_ok=False)', '''if p.parent.exists():
    if not p.parent.is_dir() or any(p.parent.iterdir()):
        raise ValueError('Nonempty output parent')
else:
    p.parent.mkdir(parents=True, exist_ok=False)''')


class MethodRevisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-method-revision-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        contents = {'code': ('code.py', OLD), 'config': ('config.json', '{}'),
                    'data': ('data.json', '[1]'), 'evaluator': ('evaluator.json', '{}')}
        for name, data in contents.values():
            (self.root / name).write_text(data, encoding='utf-8')
        self.protocol = {'code_sha256': file_sha(self.root / 'code.py'),
                         'config_sha256': file_sha(self.root / 'config.json'),
                         'data_sha256': file_sha(self.root / 'data.json'),
                         'data_split': 'synthetic', 'init': 'none', 'seed': 0,
                         'checkpoint': 'same-ledger', 'schedule': 'bounded-directory-repair',
                         'sample_work': {'rows': 1}, 'numeric_protocol': 'integer'}
        (self.root / 'protocol.json').write_text(json.dumps(self.protocol), encoding='utf-8')
        contents['protocol'] = ('protocol.json', '')
        specs = []
        nodes = []
        for rid in ('r1', 'r2', 'r3'):
            specs.append({'schema': 1, 'id': rid, 'arm': 'tool', 'control_id': None,
                          'argv': [sys.executable, '-B', 'code.py', f'outputs/{rid}/result.json'],
                          'outpaths': [f'outputs/{rid}/result.json'],
                          'protocol': {'path': 'protocol.json', 'sha256': file_sha(self.root / 'protocol.json')},
                          'resource_estimates': {'wall_seconds': 3}, 'timeout_seconds': 2})
            nodes.append({'id': rid, 'sources': ['synthetic-directory-fixture'], 'executable': {
                'decisions': ['repair'], 'preconditions': [], 'action': {
                    'id': rid, 'kind': 'PAIRED_TEST', 'target': 'directory-result', 'operation': 'measure-' + rid,
                    'description': 'Check the synthetic output parent behavior',
                    'competing_explanations': ['compatible parent', 'conflicting parent'],
                    'required_observables': [rid + '.score'],
                    'outcomes': [{'observation': 'positive', 'next_decision': 'accept local repair'},
                                 {'observation': 'negative', 'next_decision': 'retain failure'}]}}})
        self.policy = {'schema': 1, 'context': {'decision': {'id': 'repair', 'goal_revision': 'fixture-v1',
                       'scope': {'domain': 'software-acceptance'},
                       'goal_conditions': [{'fact': 'r2.score', 'op': 'gte', 'value': 1}]}},
                       'graph': {'nodes': nodes, 'edges': []},
                       'routes': [{'candidate': s['id'], 'manifest': s} for s in specs],
                       'observations': [{'fact': s['id'] + '.score', 'run_id': s['id'],
                                         'path': s['outpaths'][0], 'selector': {'pointer': '/score'}} for s in specs]}
        self.contract = {'schema': 1, 'bindings': [{'role': role, 'path': name, 'sha256': file_sha(self.root / name)}
                                                 for role, (name, _) in contents.items()],
                         'allowed_commands': [s['argv'] for s in specs], 'output_roots': ['outputs'],
                         'budget': {'wall_seconds': 30}, 'advisor_policy': self.policy,
                         'stop_policy': {'schema': 1, 'wall_seconds': 120, 'progress': {'window_seconds': 30, 'min_bytes': 0}},
                         'method_evolution': {'schema': 1, 'max_revisions': 8, 'code_paths': ['code.py']}}
        self.store = ProjectStore(self.root)
        self.store.initialize(self.contract)
        (self.root / 'replacement.py').write_text(NEW, encoding='utf-8')

    def proposal(self, ident='fix-parent'):
        return {'id': ident, 'parent_sha256': self.store.snapshot()['contract_sha256'],
                'reason': 'Preserve the failed preparation and accept an empty native output parent',
                'policy': deepcopy(self.store.snapshot()['contract']['advisor_policy']),
                'code_replacements': [{'path': 'code.py', 'source': 'replacement.py',
                                       'sha256': file_sha(self.root / 'replacement.py')}]}

    def execute_route(self, rid):
        route = next(r for r in self.store.snapshot()['contract']['advisor_policy']['routes'] if r['manifest']['id'] == rid)
        self.store.register(route['manifest'])
        return self.store.execute(rid)

    def test_failed_parent_repair_keeps_one_ledger_budget_deadline_and_receipt(self):
        failed = self.execute_route('r1')
        self.assertEqual(failed['run_status'], 'FAILED')
        before = self.store.snapshot()
        with self.store._db(True) as db:
            original = dict(db.execute('SELECT * FROM contract WHERE id=1').fetchone())
            deadline = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='CAMPAIGN_STARTED'").fetchone()[0]
        result = revision.apply(self.store, self.proposal())
        self.assertEqual(result['status'], 'ADOPTED')
        live = self.store.snapshot(check_bindings=True)
        self.assertEqual(live['budget'], before['budget'])
        self.assertEqual(live['exposures'], before['exposures'])
        self.assertEqual(live['receipts'], before['receipts'])
        self.assertEqual(live['runs'], before['runs'])
        self.assertEqual(len(live['contract_history']), 2)
        with self.store._db(True) as db:
            self.assertEqual(dict(db.execute('SELECT * FROM contract WHERE id=1').fetchone()), original)
            self.assertEqual(db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='CAMPAIGN_STARTED'").fetchone()[0], deadline)
        success = self.execute_route('r2')
        self.assertEqual(success['run_status'], 'SUCCEEDED')
        self.assertNotEqual(success['effective_contract_sha256'], failed['effective_contract_sha256'])
        self.assertEqual(success['runtime_fingerprint'], failed['runtime_fingerprint'])
        current_protocol = json.loads((self.root / 'protocol.json').read_text())
        self.assertEqual({k: v for k, v in current_protocol.items() if k != 'code_sha256'},
                         {k: v for k, v in self.protocol.items() if k != 'code_sha256'})

    def test_partial_copy_is_durable_resumable_and_no_run_launches(self):
        proposal = self.proposal()
        original_replace = revision._replace
        calls = []
        def interrupted(path, data):
            calls.append(path)
            if len(calls) == 2:
                raise OSError('synthetic interruption after first copy')
            original_replace(path, data)
        with patch.object(revision, '_replace', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'synthetic interruption'):
                revision.apply(self.store, proposal)
        snap = self.store.snapshot(check_bindings=True)
        self.assertEqual(snap['method_revision_pending']['id'], proposal['id'])
        self.assertEqual(snap['runs'], [])
        self.assertEqual(snap['budget']['wall_seconds']['reserved'], 0)
        with self.assertRaises(ValueError):
            self.execute_route('r1')
        (self.root / 'replacement.py').unlink()  # Resume consumes retained bytes, not mutable source.
        result = revision.apply(self.store, proposal)
        self.assertEqual(result['status'], 'ADOPTED')
        self.assertIsNone(self.store.snapshot()['method_revision_pending'])
        self.assertEqual(revision.apply(self.store, proposal)['status'], 'ALREADY_ADOPTED')
        recovered_run = self.execute_route('r1')
        self.assertEqual(recovered_run['run_status'], 'SUCCEEDED', recovered_run)

    def test_idempotence_and_same_id_changed_proposal_rejected(self):
        proposal = self.proposal()
        revision.apply(self.store, proposal)
        before = self.store.snapshot()
        self.assertEqual(revision.apply(self.store, proposal)['status'], 'ALREADY_ADOPTED')
        proposal['reason'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'ID reused'):
            revision.apply(self.store, proposal)
        self.assertEqual(self.store.snapshot(), before)

    def test_no_inplace_edits_and_hash_validation_before_bound_writes(self):
        for changes in ({'source': 'code.py'}, {'sha256': '0' * 64}, {'path': 'evaluator.json'}, {'source': '../escape.py'}):
            proposal = self.proposal()
            proposal['code_replacements'][0].update(changes)
            old = (self.root / 'code.py').read_bytes()
            with self.assertRaises((ValueError, OSError)):
                revision.apply(self.store, proposal)
            self.assertEqual((self.root / 'code.py').read_bytes(), old)
            with self.store._db(True) as db:
                self.assertEqual(db.execute("SELECT count(*) FROM events WHERE json_extract(body,'$.kind')=?", (revision.PREPARED,)).fetchone()[0], 0)

    def test_reserved_attempt_blocks_revision(self):
        self.store.register(self.policy['routes'][0]['manifest'])
        with self.assertRaisesRegex(ValueError, 'reserved or running'):
            revision.apply(self.store, self.proposal())
        self.assertEqual((self.root / 'code.py').read_text(), OLD)

    def test_context_and_registered_route_observations_are_frozen(self):
        self.execute_route('r1')
        for mutate in (lambda p: p['policy']['context']['decision'].update(goal_revision='other'),
                       lambda p: p['policy']['routes'][0]['manifest'].update(timeout_seconds=1),
                       lambda p: p['policy']['observations'][0]['selector'].update(pointer='/different')):
            proposal = self.proposal()
            mutate(proposal)
            with self.assertRaises(ValueError):
                revision.apply(self.store, proposal)
        self.assertEqual((self.root / 'code.py').read_text(), OLD)

    def test_historical_checkpoint_uses_verified_lineage_and_live_costs(self):
        save_checkpoint(self.root, 'before', self.store.snapshot(), kind='project')
        self.execute_route('r1')
        revision.apply(self.store, self.proposal())
        live = self.store.snapshot(check_bindings=True)
        recovered = restore_checkpoint(self.root, 'before', live, kind='project')
        self.assertEqual(recovered['status'], 'RESUMABLE_HANDOFF')
        self.assertEqual(recovered['live_budget'], live['budget'])
        self.assertEqual(recovered['live_exposures'], live['exposures'])
        self.assertEqual(len(recovered['interrupted_runs']), 1)
        forged = deepcopy(live)
        forged['contract']['budget']['wall_seconds'] = 300
        self.assertEqual(restore_checkpoint(self.root, 'before', forged, kind='project')['status'], 'CONFLICT')

    def test_cas_and_event_tamper_are_rejected(self):
        revision.apply(self.store, self.proposal())
        blob = next((self.root / '.rds/cas').glob('*.method-bytes'))
        blob.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'CAS integrity'):
            self.store.snapshot()

    def test_owned_cli_continues_with_checkpoints_before_and_after_revision(self):
        failed = self.execute_route('r1')
        self.assertEqual(failed['run_status'], 'FAILED')
        before = self.store.snapshot()
        save_checkpoint(self.root, 'before-tool', before, kind='project')
        revision.apply(self.store, self.proposal())
        revised = self.store.snapshot()
        save_checkpoint(self.root, 'after-tool', revised, kind='project')
        self.assertEqual(revised['budget'], before['budget'])
        self.assertEqual(revised['receipts'], before['receipts'])
        for action in ('next', 'advance'):
            completed = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'),
                                       '--root', str(self.root), 'project', action],
                                      capture_output=True, text=True, encoding='utf-8', timeout=20)
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            result = json.loads(completed.stdout)
            if action == 'next':
                self.assertEqual(result['selected_run'], 'r2')
                self.assertEqual(self.store.snapshot()['runs'], before['runs'])
            else:
                self.assertEqual(result['receipt']['run_id'], 'r2')
                self.assertEqual(result['receipt']['run_status'], 'SUCCEEDED')
                self.assertEqual(result['advisor']['status'], 'REVIEWED')
        live = self.store.snapshot()
        self.assertEqual(len(live['runs']), 2)
        self.assertEqual(live['receipts'][0], failed)
        self.assertEqual(live['budget']['wall_seconds']['cap'], 30)
        self.assertGreater(live['budget']['wall_seconds']['spent_measured'],
                           before['budget']['wall_seconds']['spent_measured'])
        self.assertEqual(live['budget']['wall_seconds']['reserved'], 0)

    def test_owned_history_rejects_self_consistent_foreign_checkpoint_contract(self):
        save_checkpoint(self.root, 'foreign-tool', self.store.snapshot(), kind='project')
        revision.apply(self.store, self.proposal())
        with self.store._db() as db:
            record = json.loads(db.execute("SELECT body FROM checkpoints WHERE id='foreign-tool'").fetchone()[0])
            record['snapshot']['contract']['budget']['wall_seconds'] = 300
            record['contract_sha256'] = digest(record['snapshot']['contract'])
            raw = canonical(record)
            # Simulate offline corruption beyond the normal append-only writer.
            db.execute('DROP TRIGGER checkpoint_no_update')
            db.execute("UPDATE checkpoints SET body=?,sha=? WHERE id='foreign-tool'", (raw, digest(record)))
        from rds_owned_advisor import review
        with self.assertRaisesRegex(ValueError, 'Checkpoint contract mismatch'):
            review(self.store)
        self.assertEqual(self.store.snapshot()['runs'], [])
        self.assertEqual(self.store.snapshot()['budget']['wall_seconds']['spent_measured'], 0)

    def test_append_only_events_reject_update(self):
        revision.apply(self.store, self.proposal())
        with self.store._db() as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE events SET body='{}'")

    def test_timeout_name_and_numeric_precision_alone_do_not_make_a_tool(self):
        proposal = self.proposal()
        proposal['code_replacements'] = []
        proposal['policy']['routes'][0]['manifest']['timeout_seconds'] = 1
        with self.assertRaisesRegex(ValueError, 'new or improved bound tool'):
            revision.apply(self.store, proposal)
        proposal = self.proposal()
        proposal['code_replacements'] = []
        action = proposal['policy']['graph']['nodes'][0]['executable']['action']
        action['operation'] = 'different-name-same-tool'
        with self.assertRaisesRegex(ValueError, 'new or improved bound tool'):
            revision.apply(self.store, proposal)
        for body in (OLD + '\n# renamed algorithm\n', OLD.replace("{'score': 1}", "{'score': 3200}")):
            (self.root / 'replacement.py').write_text(body, encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'new or improved bound tool'):
                revision.apply(self.store, self.proposal())
        self.assertEqual((self.root / 'code.py').read_text(), OLD)

    def test_stale_parent_and_second_revision_limit(self):
        import shutil
        other = self.root / 'limited'
        other.mkdir()
        for b in self.contract['bindings']:
            shutil.copyfile(self.root / b['path'], other / b['path'])
        shutil.copyfile(self.root / 'replacement.py', other / 'replacement.py')
        contract = deepcopy(self.contract)
        contract['method_evolution']['max_revisions'] = 1
        store = ProjectStore(other)
        store.initialize(contract)
        proposal = {'id': 'one', 'parent_sha256': digest(contract), 'reason': 'fix parent',
                    'policy': deepcopy(self.policy), 'code_replacements': [
                        {'path': 'code.py', 'source': 'replacement.py', 'sha256': file_sha(other / 'replacement.py')}]}
        revision.apply(store, proposal)
        second = deepcopy(proposal)
        second['id'] = 'two'
        second['parent_sha256'] = store.snapshot()['contract_sha256']
        (other / 'replacement.py').write_text(NEW + '\nprint("new observed tool")\n', encoding='utf-8')
        second['code_replacements'][0]['sha256'] = file_sha(other / 'replacement.py')
        with self.assertRaisesRegex(ValueError, 'maximum'):
            revision.apply(store, second)
        self.assertEqual(len(store.snapshot()['contract_history']), 2)
        original = self.proposal()
        revision.apply(self.store, original)
        stale = deepcopy(original)
        stale['id'] = 'stale'
        with self.assertRaisesRegex(ValueError, 'parent differs'):
            revision.apply(self.store, stale)

    def test_idempotent_adoption_rechecks_actual_bound_bytes(self):
        proposal = self.proposal()
        revision.apply(self.store, proposal)
        (self.root / 'code.py').write_text('print("unexpected in-place edit")', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Binding changed'):
            revision.apply(self.store, proposal)

    def test_real_cli_revision_entry(self):
        proposal = self.proposal()
        (self.root / 'proposal.json').write_text(canonical(proposal), encoding='utf-8')
        completed = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'),
                                    '--root', str(self.root), 'project', 'revise', '--proposal', str(self.root / 'proposal.json')],
                                   capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        self.assertEqual(json.loads(completed.stdout)['status'], 'ADOPTED')
        self.assertEqual((self.root / 'code.py').read_text(), NEW)

    def test_pure_local_rename_is_not_a_tool_improvement(self):
        import re
        (self.root / 'replacement.py').write_text(re.sub(r'\bp\b', 'renamed_output', OLD), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'new or improved bound tool'):
            revision.apply(self.store, self.proposal('rename-only'))
        self.assertEqual((self.root / 'code.py').read_text(), OLD)

    def test_successful_ancestor_is_observed_after_another_tool_revision(self):
        self.execute_route('r1')
        revision.apply(self.store, self.proposal())
        success = self.execute_route('r2')
        self.assertEqual(success['run_status'], 'SUCCEEDED')
        (self.root / 'replacement.py').write_text(NEW + '\ndef tool_hint():\n    return "revised"\n', encoding='utf-8')
        revision.apply(self.store, self.proposal('second-tool'))
        before = self.store.snapshot()
        self.assertEqual(self.store.execute('r2'), success)
        self.assertEqual(self.store.snapshot()['budget'], before['budget'])
        self.assertEqual(self.store.snapshot()['receipts'], before['receipts'])

    def test_goal_observation_cannot_be_remapped_to_solver_self_report(self):
        proposal = self.proposal()
        proposal['policy']['observations'][1]['run_id'] = 'r3'
        proposal['policy']['observations'][1]['path'] = 'outputs/r3/result.json'
        with self.assertRaisesRegex(ValueError, 'remap final goal evidence'):
            revision.apply(self.store, proposal)
        self.assertEqual((self.root / 'code.py').read_text(), OLD)


if __name__ == '__main__':
    unittest.main()
