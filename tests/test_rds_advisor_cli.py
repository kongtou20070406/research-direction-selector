"""Check the human-facing Advisor commands without initializing a project."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_verify_types import digest


class AdvisorCLITests(unittest.TestCase):
    def call(self, project, *args):
        proc = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'),
                               '--root', str(project), 'advise', *args],
                              cwd=ROOT, capture_output=True, encoding='utf-8', timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def cost_fixture(self, project, *, conflicting=False, bind_cost=True, include_fact=False, measured=2):
        binding = {'run_id': 'run', 'code_sha256': 'a' * 64, 'config_sha256': 'b' * 64,
                   'data_sha256': 'c' * 64, 'data_split': 'development'}
        if include_fact:
            binding['metric'] = 'run_status'
        receipt = {'run_id': 'run', 'run_status': 'SUCCEEDED', 'binding': binding,
                   'resources': {'wall_seconds': {'measured': measured, 'unit': 'seconds'}}}
        receipt['sha256'] = digest(receipt)
        receipt_raw = json.dumps(receipt).encode('utf-8')
        (project / 'receipt.json').write_bytes(receipt_raw)
        graph = {'nodes': [{'id': 'inspect', 'executable': {'decisions': ['choose'],
                 'preconditions': [{'fact': 'completed', 'value': 'SUCCEEDED'}] if include_fact else [],
                 'action': {'id': 'compare', 'description': 'Inspect bounded evidence',
                 'competing_explanations': ['real improvement', 'wrong run'], 'required_observables': ['bound loss'],
                 'outcomes': [{'observation': 'improved', 'next_decision': 'continue'},
                              {'observation': 'not improved', 'next_decision': 'stop'}]}}}]}
        graph_path = project / 'graph.json'
        graph_path.write_text(json.dumps(graph), encoding='utf-8')
        manifest = {'schema': 'rds-artifact-manifest-v1', 'decision': 'choose',
                    'sources': [{'id': 'receipt', 'kind': 'receipt', 'path': 'receipt.json',
                                 'expected_sha256': digest(receipt_raw),
                                 'facts': [{'id': 'completed', 'pointer': '/run_status'}] if include_fact else [],
                                 'binding': {**binding, 'data_split': 'holdout' if conflicting else 'development'}}],
                    'cost_bindings': [{'action_id': 'compare', 'run_id': 'run', 'resource': 'wall_seconds',
                                       'comparison_group': 'same-protocol'}] if bind_cost else [],
                    'budget': {'value': 3, 'resource': 'wall_seconds', 'unit': 'seconds',
                               'comparison_group': 'same-protocol', 'source': 'predeclared test budget'}}
        manifest_path = project / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        return manifest_path, graph_path

    def test_fit_curve_protocol_reaches_diagnosis_and_does_not_create_ledger(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            curve = project / 'curve.json'
            curve.write_text(json.dumps({'train_loss_history': [3, 2, 1],
                'val_loss_history': [1, 2, 3], 'losses_comparable': True,
                'matched_checkpoints': True, 'trend_tolerance': 0.1}), encoding='utf-8')
            single = self.call(project, '--train-loss', '1', '--val-loss', '3')
            self.assertEqual(single['verdict'], 'INSUFFICIENT_EVIDENCE')
            paired = self.call(project, '--train-loss', '1', '--val-loss', '3',
                               '--fit-telemetry', str(curve))
            self.assertEqual(paired['verdict'], 'POSSIBLE_GENERALIZATION_GAP')
            self.assertFalse((project / '.rds').exists())

    def test_literature_uses_scoped_records_without_initializing_a_ledger(self):
        # The library is resolved relative to the supplied repository/project root.
        answer = self.call(ROOT, '--literature', 'lr')
        self.assertGreater(answer['matches_count'], 0)
        self.assertEqual(answer['assurance'], 'HEURISTIC_ONLY')
        self.assertTrue(all('sources' in record for record in answer['principles']))

    def test_sourced_context_works_without_a_project_and_does_not_execute(self):
        with tempfile.TemporaryDirectory() as raw:
            answer = self.call(Path(raw), '--research-context',
                str(ROOT / 'examples/advisor-search/boundary-context.json'),
                '--graph', str(ROOT / 'references/judgment-graph.yaml'))
            search = next(row['search'] for row in answer['recommendations']
                          if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
            self.assertTrue(search['blocked_candidates'])
            self.assertEqual(search['candidates'][0]['evidence_status'], 'INPUT_REPORTED')
            self.assertFalse((Path(raw) / '.rds').exists())

    def test_explanation_coverage_reaches_cli_without_overclaiming_imported_evidence(self):
        example = json.loads((ROOT / 'examples/advisor-search/discrimination-example.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            graph, context, manifest = (project / name for name in ('graph.json', 'context.json', 'manifest.json'))
            graph.write_text(json.dumps(example['graph']), encoding='utf-8')
            manifest.write_text(json.dumps({'schema': 'rds-artifact-manifest-v1', 'sources': []}), encoding='utf-8')
            for matched in (True, False):
                example['context']['facts']['protocol_matched']['value'] = matched
                context.write_text(json.dumps(example['context']), encoding='utf-8')
                for extra in ([], ['--artifacts', str(manifest)]):
                    with self.subTest(protocol_matched=matched, artifacts=bool(extra)):
                        answer = self.call(project, '--research-context', str(context), '--graph', str(graph), *extra)
                        search = next(row['search'] for row in answer['recommendations']
                                      if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
                        self.assertEqual(len(search['ranking']['pareto_front']), 2)
                        self.assertEqual(search['ranking']['dominance'], [])
                        for candidate in search['candidates']:
                            report = candidate['discrimination']
                            self.assertEqual(report['valid_prediction_support'], matched)
                            self.assertEqual(report['evidence_status'], 'INPUT_REPORTED')
                            self.assertEqual(len(report['conditional_distinguishing_pairs']), 2 if matched else 0)
                            self.assertEqual(len(report['unresolved_pairs']), 1 if matched else 3)
            self.assertFalse((project / '.rds').exists())

    def test_malformed_prediction_operator_preserves_other_cli_candidates(self):
        example = json.loads((ROOT / 'examples/advisor-search/discrimination-example.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            graph, context = (project / name for name in ('graph.json', 'context.json'))
            context.write_text(json.dumps(example['context']), encoding='utf-8')
            for operator in ([], {}, None):
                with self.subTest(operator=operator):
                    example['graph']['nodes'][0]['executable']['action']['discrimination']['conditions'][0]['op'] = operator
                    graph.write_text(json.dumps(example['graph']), encoding='utf-8')
                    answer = self.call(project, '--research-context', str(context), '--graph', str(graph))
                    search = next(row['search'] for row in answer['recommendations']
                                  if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
                    self.assertEqual(len(search['candidates']), 2)
                    reports = [candidate['discrimination'] for candidate in search['candidates']]
                    self.assertEqual(sum(report['valid_prediction_support'] for report in reports), 1)
                    unknown = next(report for report in reports if not report['valid_prediction_support'])
                    self.assertEqual(unknown['applicability'], 'UNKNOWN')
                    self.assertEqual(len(unknown['unresolved_pairs']), 3)
            self.assertFalse((project / '.rds').exists())

    def test_manual_search_limit_reaches_search_with_and_without_artifacts(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            action = {'description': 'Inspect a bounded comparison', 'competing_explanations': ['A', 'B'],
                      'required_observables': ['matched result'], 'outcomes': [
                          {'observation': 'improved', 'next_decision': 'continue'},
                          {'observation': 'unchanged', 'next_decision': 'revise'}]}
            graph = {'nodes': [{'id': name, 'executable': {'decisions': ['choose'], 'preconditions': [],
                         'action': {**action, 'id': name + '-check'}}} for name in ('first', 'second')]}
            graph_path, context, manifest = (project / name for name in ('graph.json', 'context.json', 'manifest.json'))
            graph_path.write_text(json.dumps(graph), encoding='utf-8')
            context.write_text(json.dumps({'decision': 'choose', 'max_candidates': 1}), encoding='utf-8')
            manifest.write_text(json.dumps({'schema': 'rds-artifact-manifest-v1', 'sources': []}), encoding='utf-8')
            for extra in ([], ['--artifacts', str(manifest)]):
                with self.subTest(artifacts=bool(extra)):
                    answer = self.call(project, '--research-context', str(context), '--graph', str(graph_path), *extra)
                    search = next(row['search'] for row in answer['recommendations']
                                  if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
                    self.assertEqual(len(search['candidates']), 1)
                    self.assertEqual(search['truncation']['limits']['max_candidates'], 1)
                    self.assertIn('candidate limit', search['truncation']['reasons'])
            graph['nodes'][0]['executable']['satisfied_when'] = [{'fact': 'first-checked', 'value': True}]
            graph['edges'] = [{'from': 'first', 'to': 'second', 'relation': 'prerequisite_for'}]
            graph_path.write_text(json.dumps(graph), encoding='utf-8')
            context.write_text(json.dumps({'decision': {'id': 'choose', 'target_rules': ['second']},
                'max_depth': 1, 'facts': {'first-checked': {'value': True, 'source': 'matched check'}}}), encoding='utf-8')
            for extra in ([], ['--artifacts', str(manifest)]):
                with self.subTest(depth=True, artifacts=bool(extra)):
                    answer = self.call(project, '--research-context', str(context), '--graph', str(graph_path), *extra)
                    search = next(row['search'] for row in answer['recommendations']
                                  if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
                    self.assertEqual(search['truncation']['limits']['max_depth'], 1)
                    self.assertIn('depth limit', search['truncation']['reasons'])
                    self.assertFalse(any(candidate['status'] == 'READY' for candidate in search['candidates']))
            self.assertFalse((project / '.rds').exists())

    def test_receipt_cost_identity_conflict_reaches_advisor_without_selected_facts(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            for conflicting in (False, True):
                with self.subTest(conflicting=conflicting):
                    manifest_path, graph_path = self.cost_fixture(project, conflicting=conflicting)
                    imported = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'),
                        '--root', str(project), 'artifacts', 'import', '--manifest', str(manifest_path)],
                        cwd=ROOT, capture_output=True, encoding='utf-8', timeout=10)
                    self.assertEqual(imported.returncode, 0, imported.stderr)
                    self.assertEqual(json.loads(imported.stdout)['status'], 'CONFLICT' if conflicting else 'IMPORTED')
                    answer = self.call(project, '--artifacts', str(manifest_path), '--graph', str(graph_path))
                    search = next(row['search'] for row in answer['recommendations']
                                  if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
                    candidate = search['candidates'][0]
                    cost = answer['artifact_import']['context']['costs']['compare']
                    self.assertEqual(answer['artifact_import']['facts'], {})
                    self.assertEqual(answer['artifact_import']['context']['budget']['resource'], 'wall_seconds')
                    if conflicting:
                        self.assertEqual(cost['kind'], 'UNKNOWN')
                        self.assertFalse(cost['reliable'])
                        self.assertEqual(cost['declared_value'], 2)
                        self.assertEqual(candidate['incremental_cost']['status'], 'UNKNOWN')
                        self.assertEqual(candidate['budget_status'], 'UNKNOWN')
                    else:
                        self.assertEqual(cost['value'], 2)
                        self.assertEqual(candidate['incremental_cost']['resource'], 'wall_seconds')
                        self.assertEqual(candidate['incremental_cost']['evidence_statuses'], ['ARTIFACT_OBSERVED'])
                        self.assertEqual(candidate['budget_status'], 'WITHIN_REPORTED_BUDGET')
            self.assertFalse((project / '.rds').exists())

    def test_artifact_choice_keeps_saved_dependency_identity(self):
        import test_rds_project as fixtures
        from rds_tms_store import save, current
        from rds_checkpoints import read_checkpoint
        fixture = fixtures.ProjectTests('test_real_success_receipt_and_distinct_costs')
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        project = fixture.root
        manifest, graph = self.cost_fixture(project)
        dependency = {'schema': 1, 'nodes': [
            {'id': 'goal', 'status': 'UNKNOWN', 'source': 'synthetic open obligation'}],
            'hyperedges': [], 'goals': ['goal']}
        pin = save(project, dependency, expected=None)
        context = project / 'context.json'
        context.write_text(json.dumps({'decision': {'id': 'choose', 'goal_revision': 'synthetic-v1',
                                                   'scope': {'dataset': 'synthetic'}}}), encoding='utf-8')
        answer = self.call(project, '--artifacts', str(manifest), '--graph', str(graph),
                           '--research-context', str(context), '--record', 'artifact-choice')
        search = next(row['search'] for row in answer['recommendations']
                      if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
        self.assertTrue(search['analysis_coverage']['full'])
        self.assertEqual(current(project)['sha256'], pin)
        with fixture.store._db(True) as db:
            checkpoint = read_checkpoint(db, 'artifact-choice', root=project)
        self.assertEqual(checkpoint['record']['decision']['candidate']['id'],
                         answer['checkpoint']['candidate_id'])
        # A caller cannot re-use the old pin after the saved graph advances.
        dependency['nodes'].append({'id': 'new', 'status': 'UNKNOWN', 'source': 'new synthetic obligation'})
        save(project, dependency, expected=pin)
        context.write_text(json.dumps({'dependency_snapshot_sha256': pin}), encoding='utf-8')
        rejected = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'),
            '--root', str(project), 'advise', '--artifacts', str(manifest), '--graph', str(graph),
            '--research-context', str(context), '--record', 'stale-choice'],
            cwd=ROOT, capture_output=True, encoding='utf-8', timeout=10)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn('snapshot changed', rejected.stderr)
        with fixture.store._db(True) as db:
            self.assertIsNone(read_checkpoint(db, 'stale-choice', root=project))

    def test_manual_only_cost_is_sourced_and_budgeted_alongside_imported_facts(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            manifest, graph = self.cost_fixture(project, bind_cost=False, include_fact=True)
            context = project / 'context.json'
            context.write_text(json.dumps({'costs': {'compare': {'value': 5, 'resource': 'wall_seconds',
                'unit': 'seconds', 'comparison_group': 'same-protocol', 'source': 'manual estimate:elapsed'}}}), encoding='utf-8')
            answer = self.call(project, '--artifacts', str(manifest), '--research-context', str(context), '--graph', str(graph))
            search = next(row['search'] for row in answer['recommendations']
                          if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
            self.assertEqual(answer['artifact_import']['facts']['completed']['value'], 'SUCCEEDED')
            self.assertEqual(search['queries'], [])
            self.assertEqual(search['candidates'], [])
            candidate = search['blocked_candidates'][0]
            self.assertEqual(candidate['status'], 'BLOCKED_BUDGET')
            self.assertEqual(candidate['budget_status'], 'OVER_REPORTED_BUDGET')
            self.assertEqual(candidate['incremental_cost']['value'], 5)
            self.assertEqual(candidate['incremental_cost']['evidence_statuses'], ['INPUT_REPORTED'])
            self.assertEqual(candidate['incremental_cost']['sources'], ['manual estimate:elapsed'])
            self.assertFalse((project / '.rds').exists())

    def test_compatible_manual_cost_preserves_imported_provenance(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            manifest, graph = self.cost_fixture(project)
            context = project / 'context.json'
            context.write_text(json.dumps({'costs': {'compare': {'value': 2, 'resource': 'wall_seconds',
                'unit': 'seconds', 'comparison_group': 'same-protocol', 'source': 'manual estimate:elapsed'}}}), encoding='utf-8')
            answer = self.call(project, '--artifacts', str(manifest), '--research-context', str(context), '--graph', str(graph))
            search = next(row['search'] for row in answer['recommendations']
                          if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
            candidate = search['candidates'][0]
            imported = answer['artifact_import']['context']['costs']['compare']
            self.assertEqual(answer['artifact_import']['status'], 'IMPORTED')
            self.assertTrue(imported['historical'])
            self.assertEqual(candidate['incremental_cost']['evidence_statuses'], ['ARTIFACT_OBSERVED'])
            self.assertEqual(candidate['incremental_cost']['sources'], [imported['source']])
            self.assertEqual(candidate['budget_status'], 'WITHIN_REPORTED_BUDGET')

    def test_disputed_manual_cost_is_unknown_and_never_uses_a_cheaper_estimate(self):
        disputes = ({'value': 1}, {'resource': 'cpu_seconds'}, {'unit': 'minutes'},
                    {'comparison_group': 'other-protocol'}, {'reliable': False}, {'reliability': 'UNKNOWN'},
                    {'binding': {'run_id': 'other-run'}}, {'binding': {'data_split': 'holdout'}})
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            manifest, graph = self.cost_fixture(project)
            context = project / 'context.json'
            for dispute in disputes:
                with self.subTest(dispute=dispute):
                    manual = {'value': 2, 'resource': 'wall_seconds', 'unit': 'seconds',
                              'comparison_group': 'same-protocol', 'source': 'manual estimate:elapsed', **dispute}
                    context.write_text(json.dumps({'costs': {'compare': manual}}), encoding='utf-8')
                    answer = self.call(project, '--artifacts', str(manifest), '--research-context', str(context), '--graph', str(graph))
                    search = next(row['search'] for row in answer['recommendations']
                                  if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
                    self.assertEqual(answer['artifact_import']['status'], 'CONFLICT')
                    self.assertIn('compare', json.dumps(answer['artifact_import']['conflicts']))
                    candidate = search['candidates'][0]
                    self.assertEqual(candidate['incremental_cost']['status'], 'UNKNOWN')
                    self.assertNotIn('value', candidate['incremental_cost'])
                    self.assertEqual(candidate['budget_status'], 'UNKNOWN')
                    self.assertEqual(answer['artifact_import']['context']['costs']['compare']['value'], 2)

    def test_unreliable_manual_only_cost_keeps_search_cost_unknown(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            manifest, graph = self.cost_fixture(project, bind_cost=False)
            context = project / 'context.json'
            context.write_text(json.dumps({'costs': {'compare': {'value': 1, 'resource': 'wall_seconds',
                'unit': 'seconds', 'comparison_group': 'same-protocol', 'source': 'manual estimate:elapsed',
                'reliable': False}}}), encoding='utf-8')
            answer = self.call(project, '--artifacts', str(manifest), '--research-context', str(context), '--graph', str(graph))
            search = next(row['search'] for row in answer['recommendations']
                          if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
            self.assertEqual(answer['artifact_import']['status'], 'IMPORTED')
            self.assertEqual(search['candidates'][0]['incremental_cost']['status'], 'UNKNOWN')
            self.assertEqual(search['candidates'][0]['budget_status'], 'UNKNOWN')

    def test_boolean_manual_cost_cannot_equal_a_numeric_imported_cost(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            manifest, graph = self.cost_fixture(project, measured=1)
            context = project / 'context.json'
            context.write_text(json.dumps({'costs': {'compare': {'value': True, 'resource': 'wall_seconds',
                'unit': 'seconds', 'comparison_group': 'same-protocol', 'source': 'manual estimate:elapsed'}}}), encoding='utf-8')
            answer = self.call(project, '--artifacts', str(manifest), '--research-context', str(context), '--graph', str(graph))
            search = next(row['search'] for row in answer['recommendations']
                          if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
            self.assertEqual(answer['artifact_import']['status'], 'CONFLICT')
            self.assertIn('compare', json.dumps(answer['artifact_import']['conflicts']))
            self.assertEqual(search['candidates'][0]['incremental_cost']['status'], 'UNKNOWN')
            self.assertEqual(search['candidates'][0]['budget_status'], 'UNKNOWN')

    def test_manual_estimate_cannot_replace_imported_unknown_cost(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            manifest, graph = self.cost_fixture(project, conflicting=True)
            context = project / 'context.json'
            context.write_text(json.dumps({'costs': {'compare': {'value': 1, 'resource': 'wall_seconds',
                'unit': 'seconds', 'comparison_group': 'same-protocol', 'source': 'manual estimate:elapsed'}}}), encoding='utf-8')
            answer = self.call(project, '--artifacts', str(manifest), '--research-context', str(context), '--graph', str(graph))
            search = next(row['search'] for row in answer['recommendations']
                          if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
            self.assertEqual(answer['artifact_import']['status'], 'CONFLICT')
            self.assertEqual(answer['artifact_import']['context']['costs']['compare']['kind'], 'UNKNOWN')
            self.assertEqual(search['candidates'][0]['incremental_cost']['status'], 'UNKNOWN')
            self.assertEqual(search['candidates'][0]['budget_status'], 'UNKNOWN')

    def test_malformed_manual_costs_are_rejected_with_a_concise_field_error(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            manifest, graph = self.cost_fixture(project)
            context = project / 'context.json'
            for costs in ([], {'compare': []}):
                with self.subTest(costs=costs):
                    context.write_text(json.dumps({'costs': costs}), encoding='utf-8')
                    proc = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(project),
                        'advise', '--artifacts', str(manifest), '--research-context', str(context), '--graph', str(graph)],
                        cwd=ROOT, capture_output=True, encoding='utf-8', timeout=10)
                    self.assertNotEqual(proc.returncode, 0)
                    self.assertEqual(proc.stdout, '')
                    self.assertRegex(proc.stderr, '(?i)cost')
                    self.assertIn('object', proc.stderr)
                    self.assertNotIn('Traceback', proc.stderr)
                    self.assertLess(len(proc.stderr), 200)

    def test_equal_manual_fact_with_disputed_binding_is_unknown_and_retains_original(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            manifest, graph = self.cost_fixture(project, include_fact=True)
            original = self.call(project, '--artifacts', str(manifest), '--graph', str(graph))['artifact_import']['facts']['completed']
            context = project / 'context.json'
            for field, value in (('run_id', 'other-run'), ('data_split', 'holdout'), ('metric', 'other-metric')):
                with self.subTest(field=field):
                    context.write_text(json.dumps({'facts': {'completed': {'value': 'SUCCEEDED',
                        'source': 'manual receipt interpretation', 'binding': {field: value}}}}), encoding='utf-8')
                    answer = self.call(project, '--artifacts', str(manifest), '--research-context', str(context), '--graph', str(graph))
                    report = answer['artifact_import']
                    fact = report['facts']['completed']
                    self.assertEqual(report['status'], 'CONFLICT')
                    self.assertTrue(any(row.get('fact_id') == 'completed' and field in row.get('fields', [])
                                        for row in report['conflicts']))
                    self.assertEqual(fact, original)  # Preserve the original observation in the import report.
                    self.assertEqual(fact['source'], original['source'])
                    self.assertEqual(fact['binding'], original['binding'])
                    search = next(row['search'] for row in answer['recommendations']
                                  if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
                    self.assertTrue(any(query['fact'] == 'completed' for query in search['queries']))
                    self.assertTrue(search['candidates'])
                    self.assertTrue(all(candidate['status'] != 'READY' for candidate in search['candidates']))

    def test_boolean_manual_fact_cannot_equal_an_observed_numeric_fact(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            manifest, graph = self.cost_fixture(project, measured=1)
            manifest_data = json.loads(manifest.read_text(encoding='utf-8'))
            manifest_data['sources'][0]['facts'] = [{'id': 'elapsed', 'pointer': '/resources/wall_seconds/measured'}]
            manifest.write_text(json.dumps(manifest_data), encoding='utf-8')
            graph_data = json.loads(graph.read_text(encoding='utf-8'))
            graph_data['nodes'][0]['executable']['preconditions'] = [{'fact': 'elapsed', 'value': 1}]
            graph.write_text(json.dumps(graph_data), encoding='utf-8')
            context = project / 'context.json'
            context.write_text(json.dumps({'facts': {'elapsed': {'value': True, 'source': 'manual receipt interpretation'}}}), encoding='utf-8')
            answer = self.call(project, '--artifacts', str(manifest), '--research-context', str(context), '--graph', str(graph))
            self.assertEqual(answer['artifact_import']['status'], 'CONFLICT')
            fact = answer['artifact_import']['facts']['elapsed']
            self.assertEqual((fact['value'], fact['kind']), (1, 'OBSERVED'))
            self.assertIs(type(fact['value']), int)
            search = next(row['search'] for row in answer['recommendations']
                          if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
            self.assertEqual(search['candidates'][0]['status'], 'NEEDS_EVIDENCE')
            self.assertEqual(next(row for row in search['candidates'][0]['derivation']
                                  if row.get('fact') == 'elapsed')['evidence_status'], 'UNKNOWN')
            search = next(row['search'] for row in answer['recommendations']
                          if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
            self.assertTrue(any(query['fact'] == 'elapsed' for query in search['queries']))
            self.assertTrue(all(candidate['status'] != 'READY' for candidate in search['candidates']))

    def test_embedded_templates_keep_experiment_composition_with_artifacts(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            manual = json.loads((ROOT / 'examples/experiment-templates/context.json').read_text(encoding='utf-8'))
            manual['templates'] = json.loads((ROOT / 'examples/experiment-templates/templates.json').read_text(encoding='utf-8'))
            context = project / 'context.json'
            context.write_text(json.dumps(manual), encoding='utf-8')
            manifest = project / 'manifest.json'
            manifest.write_text(json.dumps({'schema': 'rds-artifact-manifest-v1', 'sources': []}), encoding='utf-8')
            graph = ROOT / 'references/judgment-graph.yaml'
            without_artifacts = self.call(project, '--research-context', str(context), '--graph', str(graph))
            with_artifacts = self.call(project, '--artifacts', str(manifest), '--research-context', str(context), '--graph', str(graph))
            expected = next(row['search']['experiment_composition'] for row in without_artifacts['recommendations']
                            if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
            actual = next(row['search']['experiment_composition'] for row in with_artifacts['recommendations']
                          if row.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')
            self.assertTrue(actual['candidates'])
            # Importing an empty manifest adds explicit context fields; bind
            # that input identity while preserving identical computed results.
            self.assertEqual(len(actual['rule_search'].pop('context_sha256')), 64)
            self.assertEqual(len(expected['rule_search'].pop('context_sha256')), 64)
            self.assertEqual(actual, expected)
            self.assertTrue(all(not candidate['execution_authorized'] for candidate in actual['candidates']))
            self.assertFalse((project / '.rds').exists())


if __name__ == '__main__':
    unittest.main()
