"""Real CLI read-only dialogue and human steering regression boundaries."""
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_rds_steering as steering_fixture
from rds_project import ProjectStore, canonical


class DialogueCLITests(unittest.TestCase):
    setUp = steering_fixture.SteeringCLITests.setUp
    call = steering_fixture.SteeringCLITests.call
    output = steering_fixture.SteeringCLITests.output
    write_json = steering_fixture.SteeringCLITests.write_json
    initialize = steering_fixture.SteeringCLITests.initialize
    create = steering_fixture.SteeringCLITests.create
    execute = steering_fixture.SteeringCLITests.execute
    starts = steering_fixture.SteeringCLITests.starts
    snapshot = steering_fixture.SteeringCLITests.snapshot
    request = steering_fixture.SteeringCLITests.request
    steer = steering_fixture.SteeringCLITests.steer

    def dialogue(self):
        return self.output('project', 'plan', '--dialogue')['dialogue']

    def event_count(self):
        with ProjectStore(self.root)._db(True) as db:
            return db.execute('SELECT COUNT(*) FROM events').fetchone()[0]

    def test_new_fixed_task_and_existing_missing_advice_remain_unknown(self):
        intent = self.write_json('intent.json', {'goal': 'one exact proof', 'fixed_task': True})
        result = self.output('project', 'plan', '--dialogue', '--intent', intent)['dialogue']
        self.assertEqual(result['status'], 'NO_CURRENT_ADVICE')
        self.assertTrue(result['fixed_task'])
        self.assertEqual(result['missing'], ['evaluation', 'budget'])
        self.assertIsNone(result['selected_run'])
        self.assertNotIn('candidates', result)
        self.assertFalse(ProjectStore(self.root).path.exists())
        self.initialize()
        self.assertEqual(self.dialogue()['status'], 'NO_CURRENT_ADVICE')
        self.assertNotIn('dialogue', self.output('project', 'plan'))

    def test_actual_selection_is_distinct_from_advisory_candidates_and_reads_do_not_mutate(self):
        self.initialize(mutate_policy=steering_fixture.SteeringCLITests.independent_routes)
        advice = self.output('project', 'next')
        before = self.snapshot()
        count = self.event_count()
        cas = sorted(p.name for p in (self.root / '.rds/cas').iterdir())
        first = self.dialogue()
        second = self.dialogue()
        self.assertEqual(first, second)
        self.assertEqual(first['status'], 'COMPATIBLE_RECORDED_ADVICE')
        self.assertEqual(first['selected_run'], advice['selected_run'])
        rows = first['candidates']['items']
        self.assertEqual(len([r for r in rows if r['is_recorded_selection']]), 1)
        self.assertEqual(rows[0]['run_id'], advice['selected_run'])
        self.assertTrue(rows[0]['observation_to_next_decision'])
        self.assertEqual(first['admission'], 'RECHECK_REQUIRED_BEFORE_DISPATCH')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.event_count(), count)
        self.assertEqual(sorted(p.name for p in (self.root / '.rds/cas').iterdir()), cas)
        self.assertEqual(self.starts(), [])

    def test_changed_frozen_input_is_not_current_even_if_receipts_unchanged(self):
        self.initialize()
        self.output('project', 'next')
        (self.root / 'config.json').write_text('{"changed":true}', encoding='utf-8')
        result = self.dialogue()
        self.assertEqual(result['status'], 'STALE_OR_UNAVAILABLE')
        self.assertIsNone(result['selected_run'])
        self.assertEqual(result['historical_selection']['selected_run'], 'baseline')
        self.assertIn('Binding changed', result['reason'])

    def test_changed_original_measurement_and_corrupt_report_are_unavailable(self):
        self.initialize()
        self.create()
        self.execute()
        self.output('project', 'next')
        result = self.dialogue()
        self.assertEqual(result['status'], 'COMPATIBLE_RECORDED_ADVICE')
        item = result['evidence']['items'][0]
        path = self.root / item['path']
        path.write_bytes(b'changed original')
        result = self.dialogue()
        self.assertEqual(result['status'], 'STALE_OR_UNAVAILABLE')
        self.assertIsNone(result['selected_run'])
        report = result['source_report']
        from pathlib import Path
        Path(report['path']).write_text('{"selected_run":"invented"}', encoding='utf-8')
        self.assertIn('integrity failure', self.dialogue()['reason'])

    def test_failed_collection_never_becomes_current_advice(self):
        self.initialize('nan')
        self.create()
        executed = self.execute(ok=False)
        self.assertEqual(executed.returncode, 2)
        self.assertEqual(self.snapshot()['runs'][0]['status'], 'COMPLETED')
        report = self.output('project', 'next', status_codes=(2,))
        self.assertEqual(report['status'], 'COLLECTION_FAILED')
        before, count = self.snapshot(), self.event_count()
        result = self.dialogue()
        self.assertEqual(result['status'], 'STALE_OR_UNAVAILABLE')
        self.assertEqual(result['historical_selection']['status'], 'COLLECTION_FAILED')
        self.assertIn('COLLECTION_FAILED', result['reason'])
        self.assertIsNone(result['selected_run'])
        self.assertEqual(result['recommendation'], 'UNKNOWN')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.event_count(), count)
        self.assertEqual(self.starts(), ['baseline'])

    def test_retained_pause_precedes_hypothesis_and_revision_continuations(self):
        self.initialize()
        self.steer(self.request('pause', ident='retained-pause'))
        before = self.snapshot()['budget']
        for kind in ('hypothesis', 'change_request'):
            with self.subTest(kind=kind):
                request = self.request(kind, ident='paused-' + kind)
                received = self.output('project', 'steer', '--request', self.write_json('request.json', request),
                                       '--user-directed', '--source', 'current-user:paused', '--dialogue')
                result = received['dialogue']['interaction']
                self.assertEqual(result['continuation'], 'NEW_DISPATCH_PAUSED')
                self.assertTrue(result['instruction']['paused'])
                self.assertEqual(result['instruction']['kind'], kind)
                self.assertEqual(result['instruction_source']['request_artifact'], received['request_artifact'])
                self.assertNotEqual(self.create(ok=False).returncode, 0)
                self.assertEqual(self.snapshot()['budget'], before)
                self.assertEqual(self.snapshot()['runs'], [])
                self.assertEqual(self.starts(), [])

    def test_paused_legacy_reservation_has_actual_run_provenance(self):
        self.initialize(include_policy=False)
        self.create()
        received = self.steer(self.request('pause'))
        before, count = self.snapshot(), self.event_count()
        result = self.dialogue()
        interaction = result['interaction']
        self.assertEqual(interaction['continuation'], 'NEW_DISPATCH_PAUSED')
        affected = interaction['affected_routes']
        self.assertEqual(affected['items'], ['baseline'])
        self.assertEqual(affected['locator'], '/runs')
        self.assertEqual(affected['original'], {'command': 'project status',
                                               'contract_sha256': before['contract_sha256']})
        self.assertEqual(interaction['instruction_source']['request_artifact'], received['request_artifact'])
        self.assertEqual(interaction['active_work']['items'][0]['disposition'], 'UNSTARTED_RESERVATION_HELD')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.event_count(), count)
        self.assertNotEqual(self.execute(ok=False).returncode, 0)
        self.assertEqual(self.snapshot()['budget'], before['budget'])
        self.assertEqual(self.starts(), [])

    def assert_plan_keeps_snapshot(self, *, with_advice):
        self.initialize()
        if with_advice:
            self.output('project', 'next')
        from pathlib import Path
        from rds_checkpoints import read_checkpoint
        from rds_source_documents import strict_json
        from rds_steering import plan, view
        store = ProjectStore(self.root)
        before = store.snapshot()
        snapshot = store.snapshot

        def snapshot_then_change(*args, **kwargs):
            saved = snapshot(*args, **kwargs)
            # Commit real run, steering and accounting changes at precisely the
            # plan/dialogue boundary; the returned draft must keep one read.
            self.create()
            self.steer(self.request('pause'))
            with store._db() as db:
                db.execute("UPDATE budget SET charged=charged+0.5 WHERE resource='wall_seconds'")
            return saved

        with patch.object(store, 'snapshot', side_effect=snapshot_then_change):
            result = plan(store, dialogue=True, save_as='coherent-plan')
        current = snapshot()
        self.assertTrue(current['steering']['paused'])
        self.assertNotEqual(current['budget'], before['budget'])
        self.assertEqual(len(current['runs']), 1)
        self.assertEqual(result['steering'], before.get('steering', view(None)))
        self.assertEqual(result['budget'], before['budget'])
        self.assertEqual(result['active_work'], [])
        dialogue = result['dialogue']
        self.assertEqual(dialogue['budget'], result['budget'])
        self.assertEqual(dialogue['interaction']['instruction'], result['steering'])
        self.assertIsNone(dialogue['interaction']['instruction_source']['request_artifact'])
        self.assertEqual(dialogue['interaction']['active_work']['items'], [])
        self.assertEqual(dialogue['interaction']['continuation'], 'CONTINUE_WITH_NORMAL_ADMISSION')
        self.assertEqual(dialogue['status'], 'STALE_OR_UNAVAILABLE' if with_advice else 'NO_CURRENT_ADVICE')
        retained = strict_json(Path(result['artifact']['path']).read_text(encoding='utf-8'))
        self.assertEqual(retained['dialogue'], dialogue)
        self.assertEqual(retained['budget'], before['budget'])
        self.assertEqual(retained['snapshot_sha256'], result['snapshot_sha256'])
        with store._db(True) as db:
            checkpoint = read_checkpoint(db, 'coherent-plan', root=self.root)['record']
        self.assertEqual(checkpoint['snapshot'], before)
        self.assertEqual(checkpoint['decision']['plan_draft'], result['artifact'])
        self.assertEqual(self.starts(), [])

    def test_plan_dialogue_keeps_snapshot_without_owned_advice(self):
        self.assert_plan_keeps_snapshot(with_advice=False)

    def test_plan_dialogue_keeps_snapshot_with_stale_owned_advice(self):
        self.assert_plan_keeps_snapshot(with_advice=True)

    def test_inherited_redirect_effects_use_current_revision_not_latest_request(self):
        self.initialize(mutate_policy=steering_fixture.SteeringCLITests.independent_routes)
        self.steer(self.request('redirect', ident='initial-routes', withdraw=['baseline'], prefer=['repair']))
        for kind in ('hypothesis', 'redirect'):
            with self.subTest(kind=kind):
                request = self.request(kind, ident='inherit-' + kind,
                                       **({'prefer': ['repair']} if kind == 'redirect' else {}))
                received = self.steer(request)
                before, count = self.snapshot(), self.event_count()
                interaction = self.dialogue()['interaction']
                source = {'command': 'project steering', 'revision': received['received_revision']}
                self.assertEqual(interaction.get('dispatch_source'), source)
                self.assertEqual(interaction['affected_routes']['original'], source)
                self.assertEqual(interaction['affected_routes']['items'], ['baseline', 'repair'])
                self.assertEqual(interaction['instruction']['withdrawn_runs'], ['baseline'])
                self.assertEqual(interaction['instruction']['preferred_runs'], ['repair'])
                self.assertEqual(interaction['instruction_source']['request_artifact_scope'], 'LATEST_REQUEST_ONLY')
                latest = received['request_artifact']
                from pathlib import Path
                from rds_source_documents import strict_json
                self.assertNotIn('withdraw', strict_json(Path(latest['path']).read_text(encoding='utf-8'))['request'])
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.event_count(), count)
                self.assertEqual(self.starts(), [])

    def test_blocked_candidate_retains_frozen_action_and_its_original_source(self):
        self.initialize('positive')
        self.create()
        self.execute()
        self.output('project', 'next', '--brief')
        before, count = self.snapshot(), self.event_count()
        result = self.dialogue()
        blocked = next(row for row in result['candidates']['items'] if row['run_id'] == 'repair')
        action = self.policy['graph']['nodes'][1]['executable']['action']
        self.assertEqual(blocked['status'], 'BLOCKED_PREREQUISITE')
        self.assertEqual(blocked['description'], action['description'])
        self.assertEqual(blocked['explanations'], action['competing_explanations'])
        self.assertEqual(blocked['observation_to_next_decision'], action['outcomes'])
        self.assertEqual(blocked['action_source'], {'command': 'project status',
            'contract_sha256': before['contract_sha256'],
            'locator': '/contract/advisor_policy/graph/nodes/1/executable/action'})
        self.assertIn('/blocked_candidates/', blocked['evidence_locator']['locator'])
        self.assertIsNone(result['selected_run'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.event_count(), count)
        self.assertEqual(self.starts(), ['baseline'])

    def test_large_owned_report_uses_recorded_size_and_rejects_invalid_sizes(self):
        self.initialize()
        self.output('project', 'next', '--brief')
        from rds_tms_store import current, save
        from rds_dialogue import _report
        from rds_math import MAX_BYTES
        store = ProjectStore(self.root)
        saved = current(self.root)
        spec = saved['dependency_map']
        # Supported near-limit dependency metadata survives the actual owned
        # collector and makes its CAS report larger than the asset/map cap.
        spec['synthetic_padding'] = 'x' * (MAX_BYTES - len(canonical(spec).encode('utf-8')) - 1024)
        self.assertLess(len(canonical(spec).encode('utf-8')), MAX_BYTES)
        save(self.root, spec, expected=saved['sha256'], source_base=self.root)
        reviewed = self.output('project', 'next', '--brief', status_codes=(2,))
        self.assertEqual(reviewed['status'], 'INCOMPLETE_ANALYSIS')
        before, count = self.snapshot(), self.event_count()
        cas = sorted(p.name for p in (self.root / '.rds/cas').iterdir())
        result = self.dialogue()
        self.assertGreater(result['source_report']['bytes'], MAX_BYTES)
        self.assertEqual(result['status'], 'STALE_OR_UNAVAILABLE')
        self.assertIsNone(result['selected_run'])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.event_count(), count)
        self.assertEqual(sorted(p.name for p in (self.root / '.rds/cas').iterdir()), cas)
        self.assertEqual(self.starts(), [])
        ref = result['source_report']
        self.assertEqual(_report(store, ref)['status'], 'INCOMPLETE_ANALYSIS')
        for size in (True, 0, -1, '8', 10 ** 100, ref['bytes'] - 1, ref['bytes'] + 1):
            with self.subTest(size=size), self.assertRaises(ValueError):
                _report(store, {**ref, 'bytes': size})

    def test_current_pause_redirect_and_hypothesis_effects_precede_old_advice(self):
        self.initialize(mutate_policy=steering_fixture.SteeringCLITests.independent_routes)
        self.output('project', 'next')
        self.create()
        request = self.request('pause', ident='pause-1')
        received = self.output('project', 'steer', '--request', self.write_json('request.json', request),
                               '--user-directed', '--source', 'current-user:1', '--dialogue')
        result = received['dialogue']
        self.assertEqual(result['status'], 'STALE_OR_UNAVAILABLE')
        self.assertIsNone(result['selected_run'])
        self.assertEqual(result['interaction']['continuation'], 'NEW_DISPATCH_PAUSED')
        self.assertEqual(result['interaction']['instruction_source']['request_artifact'], received['request_artifact'])
        self.assertEqual(result['interaction']['active_work']['items'][0]['disposition'], 'UNSTARTED_RESERVATION_HELD')
        self.assertNotEqual(self.create('repair', ok=False).returncode, 0)
        self.steer(self.request('redirect', ident='redirect-1', withdraw=['baseline'], prefer=['repair']))
        result = self.dialogue()
        self.assertEqual(result['interaction']['affected_routes']['items'], ['baseline', 'repair'])
        self.assertEqual(result['interaction']['instruction']['preferred_runs'], ['repair'])
        self.steer(self.request('hypothesis', ident='hypothesis-1'))
        result = self.dialogue()
        self.assertEqual(result['interaction']['continuation'], 'RETAIN_UNVERIFIED_HYPOTHESIS_AND_DESIGN_CHECK')
        self.assertEqual(result['interaction']['scientific_support'], 'UNKNOWN')
        self.assertFalse(result['interaction']['creates_new_approval_gate'])
        self.assertEqual(self.starts(), [])

    def test_search_truncation_unknown_and_detail_omission_have_original_locators(self):
        self.initialize()
        from rds_steering import plan
        from rds_owned_advisor import review
        from rds_quick import cas_json
        store = ProjectStore(self.root)
        report = review(store)
        search = next(r['search'] for r in report['recommendations'] if r.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
        search['truncation']['truncated'] = True
        search['candidates'][0]['incremental_cost'] = {'status': 'UNKNOWN'}
        report['warnings'] = [{'kind': 'CONFLICT', 'reason': 'x' * 3000}] * 6
        report['next_move'] = {'kind': 'REVIEW', 'reason': 'x' * 3000}
        ref = cas_json(store.root, report)
        # Controlled synthetic retained report, bound through the same event/CAS
        # path. Production accepts no externally supplied report argument.
        with store._db() as db:
            db.execute('INSERT INTO events(body) VALUES (?)', (canonical({'kind': 'OWNED_ADVISOR_REVIEW',
                **{k: report[k] for k in ('fingerprint', 'status', 'selected_run', 'snapshot_sha256')}, 'report': ref}),))
        result = self.dialogue()
        self.assertTrue(result['search_scope']['items'][0]['truncation']['truncated'])
        self.assertEqual(result['candidates']['items'][0]['cost']['status'], 'UNKNOWN')
        self.assertEqual(result['warnings']['omitted'], 2)
        self.assertTrue(result['warnings']['items'][0]['details_omitted'])
        self.assertEqual(result['warnings']['items'][0]['original'], ref)
        self.assertTrue(result['next_move']['details_omitted'])
        self.assertEqual(result['next_move']['locator'], '/next_move')
        self.assertTrue(result['historical_selection']['next_move']['details_omitted'])
        # Opt-in separation: ordinary draft never hashes originals or imports the
        # dialogue module's projection; no requirement to call it every round.
        with patch.object(store, '_bindings', side_effect=AssertionError('unexpected audit')):
            self.assertNotIn('dialogue', plan(store))

    def test_postcommit_dialogue_failure_preserves_submission_and_idempotent_replay(self):
        self.initialize()
        request = self.request('pause')
        args = SimpleNamespace(root=str(self.root), action='steer', request=self.write_json('request.json', request),
                               user_directed=True, source='current-user:display-failure', dialogue=True)
        from rds_cli import cmd_project
        with patch('rds_steering.plan', side_effect=sqlite3.OperationalError('controlled read failure')):
            received = cmd_project(args)
            count = self.event_count()
            replay = cmd_project(args)
        self.assertEqual(received['status'], 'RECEIVED')
        self.assertEqual(received['dialogue']['status'], 'UNAVAILABLE')
        self.assertEqual(replay['status'], 'ALREADY_RECEIVED')
        self.assertEqual(replay['received_revision'], received['received_revision'])
        self.assertEqual(self.event_count(), count)
        self.assertTrue(self.output('project', 'steering')['steering']['paused'])

    def test_optional_owned_context_failure_preserves_legacy_plan_snapshot(self):
        self.initialize(include_policy=False)
        self.create()
        self.steer(self.request('pause', ident='legacy-pause'))
        from rds_steering import plan
        store = ProjectStore(self.root)
        before, count = self.snapshot(), self.event_count()
        ordinary = plan(store)
        with patch('rds_owned_advisor._state', side_effect=ValueError('Owned history exceeds 128-run coverage')):
            displayed = plan(store, dialogue=True)
        self.assertEqual({k: v for k, v in displayed.items() if k != 'dialogue'}, ordinary)
        view = displayed['dialogue']
        self.assertEqual(view['status'], 'STALE_OR_UNAVAILABLE')
        self.assertIn('128-run', view['reason'])
        self.assertEqual(view['budget'], ordinary['budget'])
        self.assertEqual(view['interaction']['instruction'], ordinary['steering'])
        self.assertEqual(view['interaction']['affected_routes']['items'], ['baseline'])
        self.assertEqual(view['interaction']['affected_routes']['locator'], '/runs')
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.event_count(), count)
        self.assertEqual(self.starts(), [])

    def test_budget_and_dependency_snapshot_changes_invalidate_old_advice(self):
        self.initialize()
        self.output('project', 'next')
        store = ProjectStore(self.root)
        with store._db() as db:
            db.execute("UPDATE budget SET charged=charged+0.5 WHERE resource='wall_seconds'")
        self.assertIn('budget', self.dialogue()['reason'])
        self.output('project', 'next')
        from rds_tms_store import current, save
        saved = current(self.root)
        save(self.root, saved['dependency_map'], expected=saved['sha256'],
             source_base=self.root, revision={'changes': [{'kind': 'test', 'id': 'changed-snapshot'}]})
        result = self.dialogue()
        self.assertEqual(result['status'], 'STALE_OR_UNAVAILABLE')
        self.assertIn('snapshot changed', result['reason'])


if __name__ == '__main__':
    unittest.main()
