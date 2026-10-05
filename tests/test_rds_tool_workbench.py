"""Editable candidates preserve current code, genesis authority and original evidence."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from rds_project import ProjectStore, canonical, file_sha
import rds_tool_workbench as workbench
import rds_method_revision as revision

spec = importlib.util.spec_from_file_location('_workbench_fixture', ROOT / 'examples/predictive-feasibility/prepare.py')
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


class ToolWorkbenchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='rds-workbench-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.contract = fixture.prepare(self.root)
        self.store = ProjectStore(self.root)

    def initialize(self, mutate=None):
        if mutate:
            mutate(self.contract)
        self.store.initialize(self.contract)

    def prepare(self, identifier='improve-solver', code_path='solver.py'):
        return workbench.prepare(self.store, code_path, identifier)

    def read(self, path):
        return json.loads(Path(path).read_text(encoding='utf-8'))

    def event_count(self):
        with self.store._db(True) as db:
            return db.execute("SELECT count(*) FROM events WHERE json_extract(body,'$.kind')=?", (workbench.PREPARED,)).fetchone()[0]

    def test_prepare_real_contract_frozen_request_and_no_live_code_mutation(self):
        self.initialize()
        before = self.store.snapshot()
        live_bytes = (self.root / 'solver.py').read_bytes()
        result = self.prepare()
        request = self.read(result['request'])
        proposal = self.read(result['proposal'])
        self.assertEqual(Path(result['candidate_source']).read_bytes(), live_bytes)
        self.assertEqual((self.root / 'solver.py').read_bytes(), live_bytes)
        self.assertEqual(request['preserved']['goal_conditions'], self.contract['advisor_policy']['context']['decision']['goal_conditions'])
        self.assertEqual(request['preserved']['budget'], self.contract['budget'])
        self.assertEqual(request['preserved']['allowed_commands'], self.contract['allowed_commands'])
        self.assertEqual(proposal['policy'], self.contract['advisor_policy'])
        self.assertEqual(result['pilot_run_ids'], ['pilot-slow', 'pilot-fast', 'pilot-verify'])
        self.assertEqual(result['goal_verification_producers'][0]['observations'][0]['run_id'], 'verify')
        self.assertIn('project revise --proposal', result['revision_command'])
        self.assertTrue(result['next_command'].endswith('project next'))
        self.assertFalse(result['execution_started'])
        after = self.store.snapshot()
        for key in ('contract', 'budget', 'runs', 'receipts', 'exposures'):
            self.assertEqual(before[key], after[key])
        self.assertEqual(self.event_count(), 1)

    def test_idempotence_edit_refresh_and_revise_requires_actual_tool_change(self):
        self.initialize()
        result = self.prepare()
        self.assertEqual(self.prepare(), result)
        request_bytes = Path(result['request']).read_bytes()
        with self.assertRaisesRegex(ValueError, 'new or improved bound tool'):
            revision.apply(self.store, self.read(result['proposal']))
        source = Path(result['candidate_source'])
        original = (self.root / 'solver.py').read_bytes()
        source.write_bytes(source.read_bytes().replace(b'sum(range(100))', b'sum(i for i in range(100))'))
        refreshed = self.prepare()
        self.assertNotEqual(refreshed['candidate_sha256'], result['candidate_sha256'])
        self.assertEqual(refreshed['candidate_sha256'], file_sha(source))
        self.assertEqual(Path(result['request']).read_bytes(), request_bytes)
        self.assertEqual(self.event_count(), 1)
        self.assertEqual((self.root / 'solver.py').read_bytes(), original)
        proposal = self.read(refreshed['proposal'])
        self.assertEqual(revision.apply(self.store, proposal)['status'], 'ADOPTED')
        self.assertEqual((self.root / 'solver.py').read_bytes(), source.read_bytes())
        with self.assertRaisesRegex(ValueError, 'different code path or parent'):
            self.prepare()

    def test_genesis_path_permissions_and_pending_revision(self):
        self.initialize()
        for path in ('data.json', 'evaluator.json', '../outside.py', 'missing.py'):
            with self.assertRaisesRegex(ValueError, 'not authorized at genesis'):
                self.prepare(code_path=path)
        for identifier in ('../escape', '/absolute', 'x/y', 'x\\y', '', 'id:stream'):
            with self.assertRaisesRegex(ValueError, 'Invalid workbench ID'):
                self.prepare(identifier)
        with patch.object(workbench, 'pending_revision', return_value={'id': 'pending'}):
            with self.assertRaisesRegex(ValueError, 'pending method revision'):
                self.prepare()
        self.assertEqual(self.event_count(), 0)

    def test_missing_genesis_authority_cannot_create_bundle(self):
        self.initialize(lambda c: c.pop('method_evolution'))
        with self.assertRaisesRegex(ValueError, 'not authorized at genesis'):
            self.prepare()
        self.assertFalse((self.root / '.rds/tool-workbench').exists())

    def test_proposal_policy_edit_preserves_goals_and_arbitrary_argv_denied(self):
        self.initialize()
        result = self.prepare()
        path = Path(result['proposal'])
        original = self.read(path)
        changed = deepcopy(original)
        changed['policy']['context']['decision']['goal_conditions'][0]['value'] = False
        path.write_text(canonical(changed), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'decision, goals or scope'):
            self.prepare()
        changed = deepcopy(original)
        changed['policy']['routes'][0]['manifest']['argv'] = [sys.executable, '-c', 'print("unauthorized")']
        path.write_text(canonical(changed), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'not authorized'):
            self.prepare()
        changed = deepcopy(original)
        changed['code_replacements'][0]['source'] = '../outside.py'
        path.write_text(canonical(changed), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'target/source changed'):
            self.prepare()
        self.assertEqual(self.event_count(), 1)

    def test_model_edit_refresh_retains_permitted_policy_change(self):
        self.initialize()
        result = self.prepare()
        proposal = self.read(result['proposal'])
        proposal['policy']['feasibility']['models']['fast']['safety_factor'] = 4
        proposal['reason'] = 'Review slower-runtime model and improve its implementation'
        Path(result['proposal']).write_text(canonical(proposal), encoding='utf-8')
        self.prepare()
        self.assertEqual(self.read(result['proposal'])['policy']['feasibility']['models']['fast']['safety_factor'], 4)
        self.assertEqual(self.event_count(), 1)

    def test_existing_request_diagnostics_and_unrecorded_bundle_cannot_be_overwritten(self):
        self.initialize()
        result = self.prepare()
        for key in ('request', 'diagnostics'):
            path = Path(result[key])
            before = path.read_bytes()
            path.write_bytes(b'{}')
            with self.assertRaisesRegex(ValueError, 'binding changed'):
                self.prepare()
            self.assertEqual(path.read_bytes(), b'{}')
            path.write_bytes(before)
        orphan = self.root / '.rds/tool-workbench/orphan'
        orphan.mkdir()
        (orphan / 'candidate.py').write_text('keep me', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'refusing overwrite'):
            self.prepare('orphan')
        self.assertEqual((orphan / 'candidate.py').read_text(), 'keep me')

    def test_changed_live_code_and_candidate_limit_fail_before_refresh(self):
        self.initialize()
        result = self.prepare()
        old = (self.root / 'solver.py').read_bytes()
        (self.root / 'solver.py').write_bytes(b'print("unbound edit")')
        with self.assertRaisesRegex(ValueError, 'Binding changed'):
            self.prepare()
        (self.root / 'solver.py').write_bytes(old)
        Path(result['candidate_source']).write_bytes(b'x' * (workbench.MAX_BYTES + 1))
        with self.assertRaisesRegex(ValueError, 'exceeds byte limit'):
            self.prepare()

    def test_original_receipt_logs_verified_cas_tail_bounded_and_fraction_keeps_unknown(self):
        # Real failed attempt retains a >4 KiB stderr log, not a fabricated receipt.
        text = (self.root / 'solver.py').read_text(encoding='utf-8')
        (self.root / 'solver.py').write_text('import sys\nsys.stderr.write("e"*6000)\nraise RuntimeError("failure tail")\n' + text,
                                               encoding='utf-8')
        sha = file_sha(self.root / 'solver.py')
        for binding in self.contract['bindings']:
            if binding['role'] == 'code':
                binding['sha256'] = sha
            if binding['role'] == 'protocol':
                path = self.root / binding['path']
                protocol = self.read(path)
                protocol['code_sha256'] = sha
                path.write_text(canonical(protocol), encoding='utf-8')
                binding['sha256'] = file_sha(path)
        for route in self.contract['advisor_policy']['routes']:
            ref = route['manifest']['protocol']
            ref['sha256'] = file_sha(self.root / ref['path'])
        self.initialize()
        manifest = self.contract['advisor_policy']['routes'][0]['manifest']
        self.store.register(manifest)
        receipt = self.store.execute(manifest['id'])
        self.assertEqual(receipt['run_status'], 'FAILED')
        result = self.prepare()
        diagnostic = self.read(result['diagnostics'])
        request = self.read(result['request'])
        campaign_t0 = request['preserved']['campaign_T0']
        self.assertEqual(campaign_t0['kind'], 'CAMPAIGN_STARTED')
        before = self.store.snapshot()
        self.prepare()
        self.assertEqual(self.read(result['request'])['preserved']['campaign_T0'], campaign_t0)
        self.assertEqual(self.store.snapshot()['budget'], before['budget'])
        self.assertEqual(self.store.snapshot()['receipts'], before['receipts'])
        record = diagnostic['receipts'][0]
        self.assertEqual(record['costs'][0]['fraction_of_selected_measured_cost'], 1)
        tail = record['stderr_tails'][0]
        self.assertEqual(tail['tail']['bytes'], 4096)
        self.assertTrue(tail['truncated'])
        self.assertIn('failure tail', tail['text'])
        self.assertEqual(file_sha(tail['tail']['path']), tail['tail']['sha256'])
        self.assertEqual(diagnostic['dominant_costs'][0]['receipt_sha256'], receipt['sha256'])
        Path(tail['tail']['path']).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'CAS integrity failure'):
            self.prepare()
        # A fresh request must reread original artifact bytes, not copied diagnostic prose.
        original_stderr = self.root / tail['original']['path']
        original_stderr.write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'Artifact size changed|Artifact hash mismatch'):
            self.prepare('new-request')

    def test_receipt_without_corresponding_owned_run_is_rejected(self):
        self.initialize()
        fake = {'run_id': 'foreign-run', 'sha256': 'a' * 64, 'manifest_sha256': 'b' * 64,
                'attempt_id': 'foreign-attempt', 'process_status': 'FAILED'}
        # A forged receipt body cannot promote diagnostics to original evidence.
        with patch.object(self.store, '_receipt', return_value=fake), patch.object(self.store, '_runs', return_value=[]):
            with self.store._db() as db:
                db.execute('INSERT INTO receipts VALUES (?,?,?)', ('foreign-run', 'a' * 64, '{}'))
            with self.assertRaisesRegex(ValueError, 'differs from its owned run'):
                self.prepare()
        self.assertFalse((self.root / '.rds/tool-workbench/improve-solver').exists())

    def test_receipt_cap_and_static_hints_do_not_claim_profiling(self):
        self.initialize()
        result = self.prepare()
        hints = self.read(result['request'])['source_hints']
        self.assertIn('NOT_PROFILE', hints['assurance'])
        self.assertTrue(any(h['expression'] == 'time.sleep' for h in hints['external_calls']))
        receipt = {'run_id': 'r', 'ended_at': 0, 'sha256': 'a'*64, 'run_status': 'SUCCEEDED', 'timeout': False,
                   'resources': {'wall_seconds': {'measured': None, 'unknown': True, 'unit': 'seconds', 'charged_estimate': 3}},
                   'artifacts': []}
        records = [{**receipt, 'run_id': str(i)} for i in range(10)]
        diagnostic, _ = workbench._diagnostics(self.store, records)
        self.assertEqual(diagnostic['selected_count'], 8)
        self.assertTrue(diagnostic['truncated'])
        self.assertIsNone(diagnostic['receipts'][0]['costs'][0]['fraction_of_selected_measured_cost'])

    def test_only_ledger_bound_candidate_is_internal_revision_source(self):
        self.initialize()
        result = self.prepare()
        relative = Path(result['candidate_source']).relative_to(self.root).as_posix()
        self.assertEqual(workbench.resolve_source(self.store, relative), Path(result['candidate_source']))
        for path in ('.rds/tool-workbench/improve-solver/proposal.json', '.rds/project.sqlite3',
                     '.rds/tool-workbench/improve-solver/../candidate.py', '.rds/tool-workbench/none/candidate.py'):
            with self.assertRaises((ValueError, FileNotFoundError)):
                workbench.resolve_source(self.store, path)


if __name__ == '__main__':
    unittest.main()
