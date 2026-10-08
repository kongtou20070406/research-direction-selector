"""Synthetic agent transport, incremental state and real CLI continuation checks."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_hypergraph import review_hypergraph
from rds_project import canonical
from rds_tms_store import SnapshotConflict, current, maintain, save, tms_tool, with_saved_dependencies


def declaration():
    return {'claims': {'a': {'status': 'supported', 'source': 'synthetic observation'}},
            'rules': [{'from': 'a', 'to': 'g', 'status': 'supported', 'source': 'synthetic implication'}],
            'goal': 'g'}


class TMSLoopTests(unittest.TestCase):
    def test_record_metadata_and_non_inference_relations_survive_saved_cli_round_trip(self):
        spec = {'schema': 1, 'nodes': [
            {'id': 'opaque-run', 'status': 'SUPPORTED', 'source': 'synthetic run',
             'record_kind': 'run', 'run_id': 'example'},
            {'id': 'opaque-fact', 'status': 'UNKNOWN', 'source': 'synthetic lifecycle',
             'record_kind': 'lifecycle_fact', 'run_id': 'example'}],
            'hyperedges': [], 'goals': ['opaque-fact']}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def cli(*arguments):
                result = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'),
                    '--root', str(root), 'hypergraph', '--json', *arguments], capture_output=True, text=True,
                    encoding='utf-8', timeout=15, env={**os.environ, 'RDS_USAGE_DB': str(root / 'usage.sqlite3')})
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)
            first = cli('--declare', canonical(spec))
            saved = current(root)
            self.assertEqual(saved['dependency_map'], spec)
            self.assertEqual(first['record_relations'][0]['kind'], 'run_lifecycle_fact')
            self.assertEqual(first['goals']['opaque-fact']['status'], 'UNKNOWN')
            repeated = cli()
            self.assertEqual(repeated['record_relations'], first['record_relations'])
            self.assertEqual(repeated['record_topology'], first['record_topology'])
            self.assertEqual(current(root)['sha256'], saved['sha256'])
            changed = cli('--retract-node', 'opaque-run')
            self.assertEqual(changed['record_relations'], first['record_relations'])
            self.assertEqual(changed['declared_supported_closure'], [])
            self.assertEqual(current(root)['dependency_map']['nodes'][0]['run_id'], 'example')
            self.assertNotEqual(current(root)['sha256'], saved['sha256'])

    def test_scripted_agent_loop_carries_only_changes_and_small_observations(self):
        with tempfile.TemporaryDirectory() as root:
            # This is an application transport simulation, not a live LLM eval.
            calls = [{'declaration': declaration()}, {'retract_nodes': ['a']},
                     {'declaration': {'claims': {'a': {'status': 'SUPPORTED', 'source': 'new observation'}}}}]
            history, statuses = [], []
            for args in calls:
                history.append({'role': 'assistant', 'tool_call': args})
                observation = tms_tool(root, **args)
                history.append({'role': 'toolResult', 'content': canonical(observation)})
                statuses.append(observation['goals']['g'])
                for field in ('dependency_map', 'reported_nodes', 'reported_hyperedges', 'declared_supported_closure'):
                    self.assertNotIn(field, observation)
                self.assertEqual(observation['authorization'], 'UNCHANGED')
            self.assertEqual(statuses, ['DECLARED_SUPPORTED', 'UNKNOWN', 'DECLARED_SUPPORTED'])
            self.assertEqual(len(history), 2 * len(calls))
            self.assertNotIn('dependency_map', canonical(history))
            # A new model context needs no old messages, record path or map.
            self.assertEqual(tms_tool(root)['goals']['g'], statuses[-1])
            self.assertEqual(current(root)['dependency_map']['goals'], ['g'])

    def test_incremental_rules_preserve_existing_observations_and_metadata(self):
        value = declaration()
        value['claims']['a']['origin_metadata'] = {'receipt_id': 'reported only'}
        first = review_hypergraph(value)
        update = {'rules': [{'from': 'a', 'to': 'h', 'status': 'supported', 'source': 'second implication'}], 'goal': 'h'}
        result = review_hypergraph(first, updates=[update])
        node = next(n for n in result['dependency_map']['nodes'] if n['id'] == 'a')
        self.assertEqual(node['status'], 'SUPPORTED')
        self.assertEqual(node['origin_metadata'], value['claims']['a']['origin_metadata'])
        self.assertEqual(result['declared_supported_closure'], ['a', 'g', 'h'])
        rejected = review_hypergraph(first, updates=[update], refute_nodes=['missing'])
        self.assertEqual(rejected['dependency_map'], first['dependency_map'])
        self.assertEqual(rejected['status'], 'UNKNOWN')

    def test_declared_scope_and_limits_cannot_be_silently_changed_by_updates(self):
        value = declaration()
        value['goal_revision'], value['scope'] = 'rev1', {'domain': 'one'}
        first = review_hypergraph(value)
        for fragment in ({'goal_revision': 'rev2'}, {'scope': {'domain': 'two'}}, {'limits': {'max_nodes': 4096}}):
            with self.subTest(fragment=fragment):
                rejected = review_hypergraph(first, updates=[fragment])
                self.assertEqual(rejected['status'], 'UNKNOWN')
                self.assertEqual(rejected['dependency_map'], first['dependency_map'])

    def test_snapshot_history_retains_original_source_and_change_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'observation.bin'
            source.write_bytes(b'synthetic evidence bytes')
            binding = {'locator': 'fixture observation', 'file': 'observation.bin',
                       'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
            value = declaration()
            value['claims']['a']['source'] = binding
            first = maintain(root, updates=[value], audit_files=True)
            old = current(root)
            old_bytes = (root / old['map']['path']).read_bytes()
            second = maintain(root, retract_nodes=['a'], change_source='new scoped observation', audit_files=True)
            saved = current(root)
            self.assertEqual(saved['parent'], first['snapshot_sha256'])
            self.assertEqual(saved['revision']['changes'][0]['previous_source'], binding)
            self.assertEqual(saved['revision']['changes'][0]['source'], 'new scoped observation')
            self.assertEqual(saved['dependency_map']['nodes'][0]['source'], binding)
            self.assertTrue(second['source_file_audit']['all_requested_files_match'])
            from rds_advisor_search import _dependency_review
            context = with_saved_dependencies(root, {'decision': 'next'})
            audit = _dependency_review(context, audit_files=True)['source_file_audit']
            self.assertTrue(audit['all_requested_files_match'])
            self.assertEqual((root / old['map']['path']).read_bytes(), old_bytes)
            # Re-importing the old snapshot records a restoration, preserving history.
            restored = maintain(root, initial=old)
            self.assertEqual(restored['goals']['g']['status'], 'DECLARED_SUPPORTED')
            self.assertEqual(current(root)['parent'], saved['sha256'])

    def test_file_hash_source_without_locator_is_repaired_without_losing_binding(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'evidence.txt'
            source.write_bytes(b'synthetic')
            value = declaration()
            binding = {'file': 'evidence.txt', 'sha256': hashlib.sha256(source.read_bytes()).hexdigest()}
            value['claims']['a']['source'] = binding
            first = maintain(root, updates=[value], audit_files=True)
            self.assertTrue(first['source_file_audit']['all_requested_files_match'])
            self.assertEqual(first['dependency_map']['nodes'][0]['source'], {**binding, 'locator': 'evidence.txt'})
            second = maintain(root, retract_nodes=['a'], audit_files=True)
            self.assertEqual(second['dependency_map']['nodes'][0]['source'], first['dependency_map']['nodes'][0]['source'])
            self.assertTrue(second['source_file_audit']['all_requested_files_match'])
            broken = maintain(root, updates=[{'claims': {'a': {'status': 'supported', 'source': {'file': 'evidence.txt', 'sha256': 'invalid'}}}}])
            self.assertEqual(broken['status'], 'UNKNOWN')
            self.assertEqual(current(root)['sha256'], second['snapshot_sha256'])

    def test_repeated_same_refutation_reuses_snapshot_but_new_source_is_recorded(self):
        with tempfile.TemporaryDirectory() as root:
            maintain(root, updates=[declaration()])
            first = maintain(root, refute_nodes=['a'], change_source='one observation')
            second = maintain(root, refute_nodes=['a'], change_source='one observation')
            self.assertEqual(second['snapshot_sha256'], first['snapshot_sha256'])
            third = maintain(root, refute_nodes=['a'], change_source='another observation')
            self.assertNotEqual(third['snapshot_sha256'], second['snapshot_sha256'])
            self.assertEqual(current(root)['revision']['changes'][0]['source'], 'another observation')

    def test_receipt_audit_delta_and_support_cone_share_current_grounded_semantics(self):
        from test_hypergraph_evidence import ledger as receipt_ledger, spec_with, RECEIPT_SHA
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            owner = receipt_ledger(root / 'receipt-owner')
            spec = spec_with(node_evidence={'receipt': {'project_root': str(owner), 'sha256': RECEIPT_SHA}})
            first = maintain(root, initial=spec, audit_receipts_enabled=True, trace='D')
            self.assertTrue(first['support_cone']['supported'])
            second = maintain(root, refute_nodes=['A'], audit_receipts_enabled=True, trace='D')
            self.assertEqual(second['declared_supported_closure'], ['B', 'C', 'D'])
            self.assertEqual(second['revision']['lost_support'], ['A'])
            self.assertEqual(second['support_cone']['support_cone_nodes'], ['B', 'C', 'D'])
            self.assertEqual(maintain(root)['declared_supported_closure'], [])
            self.assertEqual(maintain(root, audit_receipts_enabled=True)['declared_supported_closure'], ['B', 'C', 'D'])

    def test_missing_receipts_table_is_unavailable_evidence_not_a_tool_crash(self):
        with tempfile.TemporaryDirectory() as root:
            maintain(root, updates=[declaration()])
            value = {'claims': {'a': {'status': 'supported', 'source': 'unresolved receipt',
                     'evidence': {'receipt': {'project_root': root, 'sha256': 'a' * 64}}}}}
            result = maintain(root, updates=[value], audit_receipts_enabled=True)
            self.assertEqual(result['declared_supported_closure'], [])
            self.assertEqual(result['receipt_audit']['audits'][0]['status'], 'LEDGER_UNAVAILABLE')

    def test_invalid_declarations_do_not_move_current_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            maintain(root, updates=[declaration()])
            before = current(root)
            result = maintain(root, updates=[{'rules': [{'to': 'x'}, {'from': 'y'}]}], retract_nodes=['a'])
            self.assertEqual(result['status'], 'UNKNOWN')
            self.assertEqual(len(result['input_review']['errors']), 2)
            self.assertEqual(current(root), before)
            self.assertNotIn('revision', result)

    def test_read_reuses_snapshot_and_different_roots_do_not_share_maps(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as other:
            first = maintain(root, updates=[declaration()])
            second = maintain(root)
            self.assertEqual(second['snapshot_sha256'], first['snapshot_sha256'])
            self.assertIsNone(current(other))
            self.assertEqual(maintain(other)['goals'], {})

    def test_append_only_snapshots_and_blob_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            maintain(root, updates=[declaration()])
            state = current(root)
            with closing(sqlite3.connect(root / '.rds' / 'project.sqlite3')) as db, db:
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'append-only'):
                    db.execute('UPDATE dependency_snapshots SET body=?', ('{}',))
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'append-only'):
                    db.execute('DELETE FROM dependency_snapshots')
            (root / state['map']['path']).write_bytes(b'changed bytes')
            with self.assertRaisesRegex(ValueError, 'integrity'):
                current(root)

    def test_concurrent_writers_cannot_lose_an_accepted_update(self):
        with tempfile.TemporaryDirectory() as root:
            maintain(root, updates=[declaration()])
            old = current(root)
            barrier = threading.Barrier(2)

            def writer(name):
                spec = review_hypergraph(old, updates=[{'claims': [name]}])['dependency_map']
                barrier.wait(timeout=10)
                try:
                    save(root, spec, expected=old['sha256'])
                    return name
                except SnapshotConflict:
                    return 'conflict'

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(writer, ('b', 'c')))
            self.assertEqual(results.count('conflict'), 1)
            winner = next(value for value in results if value != 'conflict')
            ids = {n['id'] for n in current(root)['dependency_map']['nodes']}
            self.assertIn(winner, ids)
            self.assertNotIn('c' if winner == 'b' else 'b', ids)

    def test_conflict_observation_does_not_report_stale_supported_goals(self):
        with tempfile.TemporaryDirectory() as root:
            with patch('rds_tms_store.save', side_effect=SnapshotConflict('newer snapshot')):
                result = maintain(root, updates=[declaration()])
            self.assertEqual(result['status'], 'CONFLICT')
            self.assertNotIn('goals', result)
            self.assertNotIn('declared_supported_closure', result)
            self.assertIsNone(result['dependency_map'])

    def test_real_cli_continues_without_a_map_or_previous_record_argument(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def cli(*args):
                run = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'),
                    '--root', str(root), 'hypergraph', *args], capture_output=True, text=True,
                    encoding='utf-8', timeout=15, env={**os.environ, 'RDS_USAGE_DB': str(root / 'usage.sqlite3')})
                self.assertEqual(run.returncode, 0, run.stderr)
                return json.loads(run.stdout)

            first = cli('--declare', canonical(declaration()))
            self.assertEqual(first['goals']['g'], 'DECLARED_SUPPORTED')
            second = cli('--retract-node', 'a', '--change-source', 'later observation')
            self.assertEqual(second['goals']['g'], 'UNKNOWN')
            self.assertEqual(cli()['goals']['g'], 'UNKNOWN')
            fragment = root / 'one-change.json'
            fragment.write_text('```json\n' + canonical({'claims': {'a': {'state': 'supported', 'source': 'restoration'}}})[:-1]
                                + ',}\n```', encoding='utf-8')
            self.assertEqual(cli('--update', str(fragment))['goals']['g'], 'DECLARED_SUPPORTED')

    def test_advice_and_exec_load_saved_map_outside_model_context(self):
        from test_rds_goal_dependencies import fixture
        import contextlib
        import io
        import rds_cli
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            graph, context = fixture()
            dep = context.pop('dependency_map')
            maintain(root, initial=dep)
            context_path, graph_path = root / 'context.json', root / 'graph.json'
            context_path.write_text(canonical(context), encoding='utf-8')
            graph_path.write_text(canonical(graph), encoding='utf-8')
            raw = context_path.read_bytes()
            command = [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(root),
                       'advise', '-c', str(context_path), '--graph', str(graph_path), '--saved-dependencies']
            run = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=15,
                                 env={**os.environ, 'RDS_USAGE_DB': str(root / 'usage.sqlite3')})
            self.assertEqual(run.returncode, 0, run.stderr)
            advice = json.loads(run.stdout)
            search = next(row['search'] for row in advice['recommendations'] if row['type'] == 'EXECUTABLE_DIRECTION_SEARCH')
            self.assertEqual(search['selection_review']['dependency_review']['goals']['completion_standard']['status'], 'UNKNOWN')
            args = ['--root', str(root), 'exec', '--timeout', '1', '--context', str(context_path),
                    '--graph', str(graph_path), '--ledger', str(root), '--saved-dependencies', '--json',
                    '--', sys.executable, '-c', 'print("synthetic")']
            # Check the real entry handoff before execution; no scientific run is needed.
            with patch('rds_quick.execute', return_value={'status': 'ENTRY_CHECK'}) as execute, \
                    patch.object(sys, 'argv', ['rds_cli.py', *args]), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(rds_cli._main(), 0)
            supplied = execute.call_args.kwargs['review'][1]['dependency_map']
            self.assertEqual(supplied, current(root)['dependency_map'])
            self.assertEqual(context_path.read_bytes(), raw)


    def test_neutral_repairs_and_explicit_unknowns_keep_advisor_evidence_boundary(self):
        from rds_advisor_search import _dependency_review
        compact = {'rules': [{'from': 'unmeasured', 'to': 'g'}], 'goal': 'g'}
        result = _dependency_review({'dependency_map': compact})
        self.assertEqual(result['status'], 'ANALYZED')
        self.assertEqual(result['goals']['g']['status'], 'UNKNOWN')
        self.assertEqual(result['declared_supported_closure'], [])
        self.assertIn('dependency_map_sha256', result)
        self.assertEqual(_dependency_review({'dependency_map': {}})['status'], 'UNKNOWN')


if __name__ == '__main__':
    unittest.main()
