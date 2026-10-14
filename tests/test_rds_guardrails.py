"""Comparable promotion gates, preserved failures and scoped interval rejections."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_guard import evaluate, exact, policy_inputs, validate_domain
from rds_project import ProjectStore


class GuardTests(unittest.TestCase):
    def test_guard_cold_import_and_route_check_do_not_load_advisor(self):
        code = ("import sys; sys.path.insert(0,sys.argv[1]); import rds_guard; "
                "assert not rds_guard.same_family({}, {}, set()); "
                "assert 'rds_advisor' not in sys.modules; "
                "import rds_advisor; "
                "assert rds_advisor._bounded_theory_gate is rds_guard._bounded_theory_gate")
        result = subprocess.run([sys.executable, '-I', '-B', '-c', code, str(ROOT / 'scripts')],
                                capture_output=True, text=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.comparison = {'question_id': 'fixed-metric', 'goal_revision': 'v1', 'scope': {'domain': 'software-fixture'},
                           'metric_definition': 'reported-score', 'unit': 'ratio', 'cohort': 'fixed', 'protocol': 'same-v1'}
        self.write('baseline.json', self.observation('0.1129'))
        self.write('outputs/candidate.json', self.observation('0.1128'))
        self.policy = {'schema': 1, 'wall_seconds': 2, 'comparison': self.comparison, 'metrics': [
            {'name': 'score', 'direction': 'max', 'pointer': '/score', 'baseline': self.ref('baseline.json'),
             'candidate': 'outputs/candidate.json'}]}
        self.path = self.write('guard.json', self.policy)

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding='utf-8')
        return path

    def ref(self, name):
        return {'path': name, 'sha256': hashlib.sha256((self.root / name).read_bytes()).hexdigest()}

    def observation(self, score, **changes):
        return {'status': 'PASS', 'comparison': self.comparison, 'score': score, **changes}

    def check(self):
        self.write('guard.json', self.policy)
        return evaluate(self.path, self.root)

    def cli(self, *args):
        return subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '-d', str(self.root), *args],
                              encoding='utf-8', text=True, capture_output=True, timeout=20,
                              env={**os.environ, 'RDS_USAGE_DB': str(self.root / 'usage.sqlite3')})

    def test_decrease_blocks_promotion_but_keeps_original_observation(self):
        report = self.check()
        self.assertEqual(report['status'], 'FAIL')
        self.assertFalse(report['promotion_eligible'])
        self.assertEqual(report['scientific_support'], 'UNKNOWN')
        self.assertEqual(json.loads((self.root / 'outputs/candidate.json').read_text())['score'], '0.1128')

    def test_equal_rational_and_improved_metrics_pass_with_exact_comparison(self):
        for direction, score in [('max', '1129/10000'), ('max', '0.1130'), ('min', '0.1128')]:
            with self.subTest(direction=direction, score=score):
                self.policy['metrics'][0]['direction'] = direction
                self.write('outputs/candidate.json', self.observation(score))
                self.assertEqual(self.check()['status'], 'PASS')

    def test_unknown_and_incompatible_records_cannot_pass(self):
        for changes in [{'status': 'UNKNOWN'}, {'withdrawn': True}, {'comparison': {**self.comparison, 'protocol': 'changed'}},
                        {'score': True}, {'score': 'nan'}, {'score': '1/0'}, {'score': '1e10000'}]:
            with self.subTest(changes=changes):
                self.write('outputs/candidate.json', {**self.observation('0.1130'), **changes})
                report = self.check()
                self.assertEqual(report['status'], 'UNKNOWN')
                self.assertFalse(report['promotion_eligible'])

    def test_withdrawn_or_changed_baseline_has_no_admission_authority(self):
        self.write('baseline.json', self.observation('0.1129', withdrawn=True))
        with self.assertRaisesRegex(ValueError, 'binding changed'):
            self.check()
        self.policy['metrics'][0]['baseline'] = self.ref('baseline.json')
        self.assertEqual(self.check()['status'], 'UNKNOWN')

    def test_missing_candidate_is_unknown_and_escaped_dependency_is_rejected(self):
        (self.root / 'outputs/candidate.json').unlink()
        self.assertEqual(self.check()['status'], 'UNKNOWN')
        self.policy['metrics'][0]['baseline']['path'] = '../outside.json'
        with self.assertRaisesRegex(ValueError, 'inside the source root'):
            self.check()

    def test_malformed_unbounded_and_nonfinite_policies_are_rejected_before_check(self):
        for changed in [{**self.policy, 'schema': True}, {**self.policy, 'metrics': []},
                        {**self.policy, 'wall_seconds': 61}, {**self.policy, 'wall_seconds': '1/0'},
                        {**self.policy, 'milestones': [{'id': 'missing-expectation'}]}]:
            with self.subTest(changed=changed):
                self.write('bad.json', changed)
                with self.assertRaises(ValueError):
                    policy_inputs(self.root / 'bad.json', self.root)
        with self.assertRaises(ValueError):
            exact(float('inf'))

    def milestone(self):
        from rds_verify import LeanFormalEngine
        spec = {'schema': 1, 'kind': 'lean_obligation', 'relation': 'lt', 'left': '1/2', 'right': '3/4'}
        checked = LeanFormalEngine().verify(spec, ['rational'])
        self.assertEqual(checked['status'], 'PASS')
        self.write('spec.json', spec)
        self.write('certificate.json', checked)
        self.policy = {'schema': 1, 'wall_seconds': 3, 'milestones': [{'id': 'closed-rational', 'spec': self.ref('spec.json'),
            'certificate': self.ref('certificate.json'), 'expected': {key: checked[key] for key in ('status', 'backend', 'assurance')}}]}

    def test_milestone_replays_original_certificate_with_actual_backend(self):
        self.milestone()
        result = self.check()
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['checks'][0]['actual']['assurance'], 'CERTIFICATE_CHECKED')
        self.assertTrue(result['checks'][0]['original_worker']['stdout']['base64'])

    def test_python_fallback_cannot_replace_a_native_milestone(self):
        self.milestone()
        self.policy['milestones'][0]['expected'].update(backend='lean4_closed_rational', assurance='LEAN_KERNEL_CHECKED')
        self.assertEqual(self.check()['status'], 'UNKNOWN')

    def test_corrupt_certificate_is_unknown_and_shared_deadline_is_bounded(self):
        self.milestone()
        self.write('certificate.json', {'status': 'PASS', 'certificate': {'verdict': 'PASS'}})
        self.policy['milestones'][0]['certificate'] = self.ref('certificate.json')
        self.assertEqual(self.check()['status'], 'UNKNOWN')
        self.policy['wall_seconds'] = '0.0001'
        self.assertEqual(self.check()['status'], 'UNKNOWN')

    def tool(self, score='0.1128'):
        (self.root / 'probe.py').write_text("from pathlib import Path\nimport json\np=json.loads(Path('baseline.json').read_text())\n"
            + "p['score']=" + repr(score) + "\nPath('outputs/candidate.json').write_text(json.dumps(p))\nprint('original run output')\n", encoding='utf-8')

    def exec_guard(self):
        return self.cli('test', '-t', '5', '-o', 'outputs/candidate.json', '--guard', 'guard.json', 'probe.py')

    def test_guarded_exec_freezes_dependencies_retains_success_and_reuses_failure(self):
        self.tool()
        first = self.exec_guard()
        self.assertEqual(first.returncode, 1, first.stderr)
        digest = json.loads(first.stdout)
        self.assertEqual(digest['run_status'], 'SUCCEEDED')
        self.assertEqual(digest['regression_status'], 'FAIL')
        self.assertFalse(digest['promotion_eligible'])
        self.assertNotIn('badge', digest)
        report = json.loads(Path(digest['record']).read_text())
        state = ProjectStore(report['job_root']).snapshot(check_bindings=True)
        self.assertFalse(state['binding_check']['errors'])
        self.assertEqual(state['runs'][0]['manifest']['timeout_seconds'], 3)
        self.assertEqual(state['budget']['wall_seconds']['charged_estimate'], 2)
        self.assertEqual(state['receipts'][0]['assessment']['task_gain'], 'UNKNOWN')
        repeated = self.exec_guard()
        self.assertEqual(repeated.returncode, 1, repeated.stderr)
        saved = json.loads(Path(json.loads(repeated.stdout)['record']).read_text())
        self.assertFalse(saved['execution_started'])
        self.assertEqual(saved['receipt']['sha256'], report['receipt']['sha256'])

    def test_guard_passes_and_unknown_candidate_blocks_promotion(self):
        for score, code in [('0.1130', 0), ('unknown', 2)]:
            with self.subTest(score=score):
                self.tool(score)
                result = self.exec_guard()
                self.assertEqual(result.returncode, code, result.stderr)

    def test_tampered_stored_guard_report_cannot_return_a_previous_pass(self):
        self.tool('0.1130')
        result = self.exec_guard()
        self.assertEqual(result.returncode, 0, result.stderr)
        job = Path(json.loads(result.stdout)['job_root'])
        store = ProjectStore(job)
        with store._db(True) as db:
            raw = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='QUICK_EXEC_REGRESSION_REVIEW'").fetchone()['body']
        Path(json.loads(raw)['report']['path']).write_text('{"status":"PASS"}')
        second = self.exec_guard()
        self.assertNotEqual(second.returncode, 0)
        self.assertIn('CAS integrity', second.stderr)

    def test_invalid_guard_and_background_combination_do_not_create_jobs(self):
        self.tool()
        for options in [('-t', '1'), ('--background',), ('--guard', 'missing.json')]:
            with self.subTest(options=options):
                result = self.cli('exec', '-t', '5', '--guard', 'guard.json', '-o', 'outputs/candidate.json', *options, 'probe.py')
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.root / '.rds/exec').exists())

    def test_standalone_guard_preserves_default_digest_and_json_status(self):
        first = self.cli('回退检查', '--policy', str(self.path))
        self.assertEqual(first.returncode, 1, first.stderr)
        brief = json.loads(first.stdout)
        self.assertNotIn('badge', brief)
        full = self.cli('guard', '--policy', str(self.path), '--json')
        self.assertEqual(full.returncode, 1)
        self.assertEqual(json.loads(full.stdout)['status'], 'FAIL')


class EntryTests(unittest.TestCase):
    def test_hypergraph_cli_retains_full_record_and_reports_bounded_truncation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = {'schema': 1, 'nodes': [{'id': name, 'status': status, 'source': 'declared fixture'}
                for name, status in [('A', 'SUPPORTED'), ('B', 'UNKNOWN'), ('C', 'UNKNOWN')]],
                'hyperedges': [{'id': 'ab', 'premises': ['A', 'B'], 'conclusion': 'C', 'status': 'SUPPORTED', 'source': 'declared fixture'}],
                'goals': ['C'], 'limits': {}}
            path = root / 'graph.json'
            path.write_text(json.dumps(spec))
            def call():
                return subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '--root', str(root), '超图', '-i', str(path)],
                    capture_output=True, text=True, encoding='utf-8', timeout=15, env={**os.environ, 'RDS_USAGE_DB': str(root / 'usage.sqlite3')})
            first = call()
            self.assertEqual(first.returncode, 0, first.stderr)
            digest = json.loads(first.stdout)
            report = json.loads(Path(digest['record']).read_text())
            self.assertEqual(report['goals']['C']['minimal_missing_evidence_sets'], [['node:B']])
            self.assertNotIn('badge', digest)
            spec['limits'] = {'max_combinations': 1}
            path.write_text(json.dumps(spec))
            second = call()
            self.assertEqual(second.returncode, 2, second.stderr)
            result = json.loads(Path(json.loads(second.stdout)['record']).read_text())
            self.assertTrue(result['truncated'])
            self.assertFalse(result['goals']['C']['blocker_sets_complete'])

    def test_exact_commands_win_and_macros_expand_without_normalizing_values(self):
        from rds_cli import parser
        from rds_usage import _label
        for spelling in ('run', 'plan'):
            self.assertEqual(_label([spelling]), (spelling, 'command'))
        args = parser().parse_args(['证明', '--spec', 'unchanged', '--tactics', 'rational'])
        self.assertEqual((args.command, args.action, args.spec), ('formal', 'verify', 'unchanged'))
        args = parser().parse_args(['snapshot', '--id', 'unchanged'])
        self.assertEqual((args.command, args.action, args.id), ('checkpoint', 'save', 'unchanged'))

    def test_implicit_child_boundary_preserves_root_flags_and_help(self):
        from rds_cli import parser
        from rds_usage import _label
        tokens = ['测试', '-w', 'project', '-t', '3', 'probe.py', '--root', 'child-only', '--help']
        args = parser().parse_args(tokens)
        self.assertEqual((args.root, args.timeout), ('project', 3))
        self.assertEqual(args.argv, ['--', 'probe.py', '--root', 'child-only', '--help'])
        self.assertEqual(_label(tokens), ('exec', 'command'))
        self.assertEqual(_label(['exec', '-t', '3', '--help']), ('exec', 'help'))

    def test_auto_identity_changes_on_input_changes_and_child_output_dirs_are_created(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            probe = root / 'probe.py'
            code = "from pathlib import Path\nPath('outputs/nested/result.json').write_text('value')\n"
            probe.write_text(code)
            command = [sys.executable, '-B', str(ROOT / 'scripts/rds_cli.py'), '-d', str(root), 'exec', '-t', '3', '-o',
                       'outputs/nested/result.json', 'probe.py']
            def run():
                result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=15,
                    env={**os.environ, 'RDS_USAGE_DB': str(root / 'usage.sqlite3')})
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(Path(json.loads(result.stdout)['record']).read_text())
            first, repeated = run(), run()
            self.assertFalse(repeated['execution_started'])
            self.assertEqual(first['receipt']['sha256'], repeated['receipt']['sha256'])
            self.assertTrue((Path(first['job_root']) / 'outputs/nested/result.json').is_file())
            probe.write_text(code + 'print("changed")\n')
            self.assertNotEqual(first['job_root'], run()['job_root'])

    def test_typo_is_actionable_and_ambiguous_prefix_never_executes(self):
        from rds_cli import parser
        with self.assertRaises(SystemExit) as exc:
            parser().parse_args(['adv'])
        self.assertEqual(exc.exception.code, 2)


class DomainTests(unittest.TestCase):
    # Reuse acceptance fixtures without rediscovering their entire test class.
    import test_rds_quick as fixture
    setUp = fixture.QuickTests.setUp
    call = fixture.QuickTests.call
    script = fixture.QuickTests.script
    job = fixture.QuickTests.job
    initialize_ledger = fixture.QuickTests.initialize_ledger
    advise = fixture.QuickTests.advise

    def prepare(self):
        from rds_quick import record_falsification
        self.initialize_ledger()
        self.graph['nodes'][0]['executable']['action']['parameters'] = {'c': '21/100', 'method': 'proof-family'}
        self.graph_path.write_text(json.dumps(self.graph), encoding='utf-8')
        self.advise('--record', 'selected')
        self.domain = {'parameters': {'c': {'min': '1/5', 'max': '22/100'}},
                       'justification': 'Synthetic fixture: declared witness is assumed to apply throughout this interval; not a verified theorem.'}
        return record_falsification(self.ledger, witness={'fixture': 'declared universal witness'}, reason='declared bounded exclusion', domain=self.domain)

    def search(self, **parameters):
        self.graph['nodes'][0]['executable']['action']['parameters'].update(parameters)
        self.graph_path.write_text(json.dumps(self.graph), encoding='utf-8')
        result = json.loads(self.advise().stdout)
        return next(r for r in result['recommendations'] if r.get('type') == 'EXECUTABLE_DIRECTION_SEARCH')['search']

    def test_small_parameter_change_cannot_escape_an_explicit_scoped_interval(self):
        self.prepare()
        result = self.search(c='0.210001')
        self.assertEqual(result['candidates'], [])
        self.assertEqual(result['blocked_candidates'][0]['status'], 'BLOCKED_DECLARED_DOMAIN')
        self.assertEqual(result['blocked_candidates'][0]['loop_review']['kind'], 'REPEAT_DECLARED_REJECTED_DOMAIN')

    def test_outside_interval_and_undeclared_parameter_changes_remain_available(self):
        self.prepare()
        self.assertEqual(len(self.search(c='23/100')['candidates']), 1)
        self.assertEqual(len(self.search(c='0.210001', method='other-family')['candidates']), 1)

    def test_changed_facts_or_scope_reopens_review_without_scientific_promotion(self):
        self.prepare()
        self.context['facts']['x']['source'] = {'sha256': 'a' * 64, 'path': 'changed-original.json'}
        self.context_path.write_text(json.dumps(self.context), encoding='utf-8')
        result = self.search(c='0.210001')
        self.assertEqual(len(result['candidates']), 1)
        self.assertEqual(result['candidates'][0]['loop_review']['kind'], 'REOPEN_REVIEW')
        self.context['decision']['scope'] = {'domain': 'different-fixture'}
        self.context_path.write_text(json.dumps(self.context), encoding='utf-8')
        self.assertEqual(len(self.search(c='0.210001')['candidates']), 1)

    def test_domain_witness_tampering_blocks_prospective_admission(self):
        rejected = self.prepare()
        Path(rejected['evidence']['path']).write_bytes(b'tampered witness')
        review = self.search(c='0.210001')
        self.assertTrue(any(f['kind'] == 'LOOP_HISTORY_REVIEW_ERROR' for f in review['loop_review']['flags']))
        self.script('print("must not launch")\n')
        result = self.job('blocked', False, '--context', str(self.context_path), '--graph', str(self.graph_path), '--ledger', str(self.ledger))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Repair checkpoint integrity before executing a candidate: Declared-domain witness CAS integrity failure',
                      result.stdout + result.stderr)
        self.assertFalse((self.root / '.rds/exec/blocked').exists())

    def test_relative_domain_witness_path_is_refused_at_save(self):
        # #153: review resolves the witness from its own working directory, so a relative path saved from the
        # project root would later block the question from anywhere else.
        import os, sqlite3
        from rds_checkpoints import save_checkpoint
        from rds_project import ProjectStore
        self.prepare()
        db = sqlite3.connect(self.ledger / '.rds/project.sqlite3')
        try:
            decision = json.loads(db.execute('SELECT body FROM checkpoints ORDER BY rowid DESC LIMIT 1').fetchone()[0])['decision']
            count = db.execute('SELECT count(*) FROM checkpoints').fetchone()[0]
        finally:
            db.close()
        witness = Path(decision['falsification']['path'])
        decision['falsification']['path'] = str(witness.relative_to(self.ledger.resolve()))
        previous = os.getcwd()
        os.chdir(self.ledger)
        self.addCleanup(os.chdir, previous)
        with self.assertRaisesRegex(ValueError, 'Domain witness path must be absolute'):
            save_checkpoint(self.ledger, 'relative-witness', ProjectStore(self.ledger).snapshot(), kind='project', decision=decision)
        db = sqlite3.connect(self.ledger / '.rds/project.sqlite3')
        try:
            self.assertEqual(db.execute('SELECT count(*) FROM checkpoints').fetchone()[0], count)
        finally:
            db.close()

    def test_invalid_or_inferred_domains_cannot_be_recorded_as_universal_falsification(self):
        candidate = {'action': {'parameters': {'c': '21/100'}}}
        for domain in [{'parameters': {'c': {'min': '23/100'}}, 'justification': 'outside'},
                       {'parameters': {'c': {'min': '1/5', 'max': '1/10'}}, 'justification': 'reversed'},
                       {'parameters': {'c': {'min': '1/5'}}},
                       {'parameters': {'c': {'eq': True}}, 'justification': 'boolean'}]:
            with self.subTest(domain=domain), self.assertRaises(ValueError):
                validate_domain(domain, candidate)


if __name__ == '__main__':
    unittest.main()
