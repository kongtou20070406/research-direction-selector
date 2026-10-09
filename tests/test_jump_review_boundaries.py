"""Regression boundaries over real local workers and native project records."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'scripts'))
import rds_jump
import rds_structure as structure
from rds_project import ProjectStore, canonical, digest, file_sha
from rds_tms_store import current, save

spec = importlib.util.spec_from_file_location('review_jump_example', REPO / 'examples/jump-generation/run.py')
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


class JumpOriginalBoundaryTests(unittest.TestCase):
    def test_four_generated_proposals_finish_under_one_original_controller_grant(self):
        with tempfile.TemporaryDirectory() as directory:
            initialize = ProjectStore.initialize
            def frozen(store, contract):
                worker = store.root / 'worker.py'
                raw = worker.read_text(encoding='utf-8')
                marker = "    Path(output).write_text(canonical({'schema': 1, 'kind': kind,"
                extension = """    if kind == 'synthesize' and result['proposals']:
        from copy import deepcopy
        first = result['proposals'][0]
        result['proposals'] = []
        for index in range(4):
            item = deepcopy(first)
            item['id'] += '-budget-fixture-' + str(index)
            for run in item['experiment']['runs']:
                run['id'] += '-budget-fixture-' + str(index)
            result['proposals'].append(item)
"""
                self.assertEqual(raw.count(marker), 1)
                worker.write_text(raw.replace(marker, extension + marker), encoding='utf-8')
                next(b for b in contract['bindings'] if b['path'] == 'worker.py')['sha256'] = file_sha(worker)
                protocol_path = store.root / 'protocol.json'
                protocol = json.loads(protocol_path.read_text())
                protocol['code_sha256'] = ProjectStore._role_sha(contract, 'code')
                protocol_path.write_text(canonical(protocol), encoding='utf-8')
                next(b for b in contract['bindings'] if b['role'] == 'protocol')['sha256'] = file_sha(protocol_path)
                (store.root / 'contract.json').write_text(canonical(contract), encoding='utf-8')
                return initialize(store, contract)
            with patch.object(ProjectStore, 'initialize', frozen):
                root, store = example.build(Path(directory) / 'project')
            generated = rds_jump.generate(root, 3)
            self.assertEqual(generated['status'], 'JUMP_PROPOSED')
            self.assertEqual(len(set(generated['proposal_ids'])), 4)
            events = structure._events(store)
            parent = next(e for e in events if e.get('operation') == 'jump-stage-synthesize')
            self.assertEqual(parent['cap'], 14)
            children = [e for e in events if e['kind'] == 'STRUCTURE_CONTROL_STARTED'
                        and e.get('reservation_owner') == parent['id']]
            self.assertEqual(sum(e['cap'] for e in children), 14)
            self.assertEqual(len(store.snapshot()['receipts']), 3)
            self.assertEqual(store.snapshot()['budget']['wall_seconds']['reserved'], 0)
            before = store.snapshot()
            self.assertEqual(rds_jump.generate(root, 3)['id'], generated['id'])
            self.assertEqual(store.snapshot(), before)

    def test_changed_frozen_generator_helper_cannot_become_compatible_history(self):
        spec = importlib.util.spec_from_file_location('review_jump_lineage_fixture', REPO / 'tests/test_jump_lineage.py')
        lineage = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lineage)
        case = lineage.JumpLineageTests()
        case.setUp()
        try:
            initialize = ProjectStore.initialize
            def allow_revision(store, contract):
                contract['method_evolution']['code_paths'].append('instrument.py')
                (store.root / 'contract.json').write_text(canonical(contract), encoding='utf-8')
                return initialize(store, contract)
            with patch.object(ProjectStore, 'initialize', allow_revision):
                _, store = case.build()
            case.complete(store)
            originals = store.snapshot()['receipts']
            replacement = store.root / 'replacement-helper.py'
            replacement.write_bytes((store.root / 'instrument.py').read_bytes()
                + b'\ndef revised_probe(inputs):\n    return measure(inputs)\n')
            import rds_method_revision as revision
            revision.apply(store, {'id':'changed-generator-helper','parent_sha256':store.snapshot()['contract_sha256'],
                'reason':'Synthetic incompatibility regression over a real method revision',
                'policy':deepcopy(store.snapshot()['contract']['advisor_policy']),
                'code_replacements':[{'path':'instrument.py','source':'replacement-helper.py','sha256':file_sha(replacement)}]})
            before = store.snapshot()
            unavailable = rds_jump.packet(store)
            self.assertEqual(unavailable['status'], 'UNAVAILABLE')
            self.assertIn('generator code compatibility changed', unavailable['diagnostic'])
            with self.assertRaisesRegex(ValueError, 'generator code compatibility changed'):
                rds_jump.generate(store.root, 3)
            self.assertEqual(store.snapshot(), before)
            self.assertEqual(store.snapshot()['receipts'], originals)
        finally:
            case.doCleanups()

    def test_controller_headroom_is_reserved_before_any_worker_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            stage = rds_jump.load_plan(store, store.snapshot())['stages'][0]['run']
            rds_jump.prepare_owned(store, stage)
            with store._db() as db:
                db.execute("UPDATE budget SET charged=cap-spent-reserved-10.5 WHERE resource='wall_seconds'")
            with self.assertRaisesRegex(ValueError, 'Insufficient'):
                rds_jump.generate(root)
            state = store.snapshot()
            self.assertEqual(state['runs'], [])
            self.assertEqual(state['receipts'], [])
            self.assertEqual(state['budget']['wall_seconds']['reserved'], 0)
            self.assertGreater(state['budget']['wall_seconds']['spent_measured'], 0)

    def test_registration_race_reuses_only_the_exact_original_manifest(self):
        for same in (True, False):
            with self.subTest(same=same), tempfile.TemporaryDirectory() as directory:
                root, store = example.build(Path(directory) / 'project')
                original = ProjectStore.register
                competed = []
                def register(caller, manifest, **kwargs):
                    if not competed:
                        competing = deepcopy(manifest)
                        if not same:
                            competing['description'] = 'Distinct competing manifest with the same ID'
                        competed.append(original(ProjectStore(root), competing))
                    return original(caller, manifest, **kwargs)
                with patch.object(ProjectStore, 'register', register):
                    if same:
                        self.assertEqual(rds_jump.generate(root)['status'], 'JUMP_STEP_LIMIT')
                    else:
                        with self.assertRaisesRegex(ValueError, 'another manifest'):
                            rds_jump.generate(root)
                state = store.snapshot()
                self.assertEqual(len(state['runs']), 1)
                self.assertEqual(len(state['receipts']), 1 if same else 0)
                self.assertEqual(bool(state['runs'][0]['attempt_id']), same)

    def test_completed_execute_race_retains_one_attempt_and_one_receipt(self):
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            original = ProjectStore.execute
            competed = []
            def execute(caller, ident, **kwargs):
                if not competed:
                    competed.append(original(ProjectStore(root), ident))
                return original(caller, ident, **kwargs)
            with patch.object(ProjectStore, 'execute', execute):
                self.assertEqual(rds_jump.generate(root)['status'], 'JUMP_STEP_LIMIT')
            state = store.snapshot()
            self.assertEqual(len(state['runs']), 1)
            self.assertEqual(len(state['receipts']), 1)
            self.assertEqual(state['receipts'][0]['attempt_id'], competed[0]['attempt_id'])
            self.assertEqual(state['receipts'][0]['sha256'], competed[0]['sha256'])

    def test_completed_registration_race_and_unowned_outputs_remain_distinct(self):
        for owned in (True, False):
            with self.subTest(owned=owned), tempfile.TemporaryDirectory() as directory:
                root, store = example.build(Path(directory) / 'project')
                original_register, original_execute = ProjectStore.register, ProjectStore.execute
                competed = []
                def register(caller, manifest, **kwargs):
                    if not competed:
                        if owned:
                            other = ProjectStore(root)
                            original_register(other, manifest)
                            competed.append(original_execute(other, manifest['id']))
                        else:
                            (root / manifest['outpaths'][0]).write_text('Unowned output', encoding='utf-8')
                            competed.append(None)
                    return original_register(caller, manifest, **kwargs)
                with patch.object(ProjectStore, 'register', register):
                    if owned:
                        self.assertEqual(rds_jump.generate(root)['status'], 'JUMP_STEP_LIMIT')
                    else:
                        with self.assertRaisesRegex(ValueError, 'Outputs must be unique and absent'):
                            rds_jump.generate(root)
                self.assertEqual(len(store.snapshot()['receipts']), int(owned))
                self.assertEqual(len(store.snapshot()['runs']), int(owned))

    def test_policy_execute_observes_a_real_active_attempt_without_rerun(self):
        with tempfile.TemporaryDirectory() as directory:
            initialize = ProjectStore.initialize
            def frozen(store, contract):
                worker = store.root / 'worker.py'
                worker.write_text('import time\ntime.sleep(1)\n' + worker.read_text(encoding='utf-8'), encoding='utf-8')
                next(b for b in contract['bindings'] if b['path'] == 'worker.py')['sha256'] = file_sha(worker)
                protocol_path = store.root / 'protocol.json'
                protocol = json.loads(protocol_path.read_text())
                protocol['code_sha256'] = ProjectStore._role_sha(contract, 'code')
                protocol_path.write_text(canonical(protocol), encoding='utf-8')
                next(b for b in contract['bindings'] if b['role'] == 'protocol')['sha256'] = file_sha(protocol_path)
                # Real method policy enables the kernel's non-exception observation branch.
                contract['method_evolution'] = {'schema': 1, 'max_revisions': 1, 'code_paths': ['worker.py']}
                (store.root / 'contract.json').write_text(canonical(contract), encoding='utf-8')
                return initialize(store, contract)
            with patch.object(ProjectStore, 'initialize', frozen):
                root, store = example.build(Path(directory) / 'project')
            original = ProjectStore.execute
            results, errors, threads = [], [], []
            def run_other(ident):
                try:
                    results.append(original(ProjectStore(root), ident))
                except BaseException as exc:
                    errors.append(exc)
            def execute(caller, ident, **kwargs):
                thread = threading.Thread(target=run_other, args=(ident,))
                threads.append(thread)
                thread.start()
                end = time.monotonic() + 5
                while time.monotonic() < end:
                    observed = next(r for r in store.snapshot()['runs'] if r['id'] == ident)
                    if observed['status'] == 'RUNNING':
                        break
                    time.sleep(.01)
                self.assertEqual(observed['status'], 'RUNNING')
                return original(caller, ident, **kwargs)
            try:
                with patch.object(ProjectStore, 'execute', execute):
                    self.assertEqual(rds_jump.generate(root)['status'], 'RECOVERY_REQUIRED')
            finally:
                for thread in threads:
                    thread.join(12)
            self.assertFalse(errors)
            self.assertEqual(len(results), 1)
            self.assertEqual(len(store.snapshot()['receipts']), 1)
            self.assertEqual(len({r['attempt_id'] for r in store.snapshot()['receipts']}), 1)
            self.assertEqual(rds_jump.generate(root)['status'], 'JUMP_STEP_LIMIT')

    def test_wrong_initial_stage_or_manifest_cannot_create_a_jump_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            plan = rds_jump.load_plan(store, store.snapshot())
            wrong = deepcopy(plan['stages'][0]['run'])
            wrong['description'] = 'Changed admission identity'
            before = store.snapshot()
            events = structure._events(store)
            for selected in (plan['stages'][1]['run'], wrong):
                result = rds_jump.prepare_owned(store, selected)
                self.assertEqual(result['status'], 'JUMP_WAITING_ADMISSION')
                self.assertEqual(result['selected_manifest'], plan['stages'][0]['run'])
                self.assertFalse(result['changed'])
                self.assertEqual(store.snapshot(), before)
                self.assertEqual(structure._events(store), events)
            self.assertIsNone(rds_jump.prepare_owned(store, {'id': 'unrelated-route'}))

    def test_dependency_declaration_handles_aliases_and_legacy_conservatively(self):
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            contract = store.snapshot()['contract']
            plan = rds_jump.load_plan(store, store.snapshot())
            # These are declaration checks over actual frozen files. They do
            # not claim execution of an arbitrary wrapper or import closure.
            for argument in ('./worker.py', '--script=worker.py', '-script=worker.py',
                             str((root / 'worker.py').resolve())):
                with self.subTest(argument=argument):
                    aliased = deepcopy(plan)
                    aliased['stages'][0]['run']['argv'][2] = argument
                    self.assertEqual(len(rds_jump._generator_bindings(store, contract, aliased)), 3)
                    aliased['generator_code_paths'].remove('worker.py')
                    with self.assertRaisesRegex(ValueError, 'entrypoint'):
                        rds_jump._generator_bindings(store, contract, aliased)
            legacy = deepcopy(plan)
            legacy.pop('generator_code_paths')
            self.assertEqual(rds_jump._generator_bindings(store, contract, legacy),
                             [b for b in contract['bindings'] if b['role'] == 'code'])

    def test_changed_synthesis_ast_or_probe_cannot_generate_positive_feedback(self):
        for field in ('candidate', 'probe', 'restored'):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as directory:
                root, store = example.build(Path(directory) / 'project')
                generated = rds_jump.generate(root, 3)
                proposal_record = structure._find(store, 'PROPOSAL', generated['proposal_ids'][0])
                candidate_id = proposal_record['proposal']['experiment']['runs'][0]['id']
                path = root / 'out/synthesize.json'
                raw = path.read_bytes()
                doc = json.loads(raw)
                doc['result']['search']['candidate' if field == 'restored' else field] = (
                    {'const': 999} if field != 'probe' else {'x': 999, 'y': 999})
                path.write_text(json.dumps(doc), encoding='utf-8')
                if field == 'restored':
                    path.write_bytes(raw)
                before = store.snapshot()
                observed = None
                try:
                    observed = structure.advance(root, generated['proposal_ids'][0])
                except ValueError:
                    # A rejected downstream interpretation must still retain
                    # the actual failed candidate receipt, checked below.
                    if field == 'restored':
                        raise
                after = store.snapshot()
                candidate = next(r for r in after['receipts'] if r['run_id'] == candidate_id)
                self.assertEqual(candidate['run_status'], 'SUCCEEDED' if field == 'restored' else 'FAILED')
                originals = {r['run_id']: r['sha256'] for r in before['receipts']}
                self.assertEqual(originals, {r['run_id']: r['sha256'] for r in after['receipts'] if r['run_id'] in originals})
                if field == 'restored':
                    self.assertEqual((observed['observation'], observed['goal_status']), ('SUPPORT', 'PASS'))
                else:
                    self.assertFalse((root / 'out/candidate.json').exists())
                    self.assertFalse(observed and observed.get('observation') == 'SUPPORT')
                    self.assertGreater(after['budget']['wall_seconds']['spent_measured'], before['budget']['wall_seconds']['spent_measured'])

    def test_same_candidate_distinct_native_requests_get_distinct_retained_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            root, store = example.build(Path(directory) / 'project')
            generated = rds_jump.generate(root, 3)
            first = structure._find(store, 'PROPOSAL', generated['proposal_ids'][0])
            saved = current(root)
            graph = deepcopy(saved['dependency_map'])
            marker = deepcopy(graph['nodes'][0])
            marker.update(id='request-context-marker', label='Synthetic context marker; no support claim')
            graph['nodes'].append(marker)
            save(root, graph, expected=saved['sha256'])
            req = structure.request(root, 1)['tasks'][0]
            self.assertNotEqual(req['id'], first['request_id'])
            result = json.loads((root / 'out/synthesize.json').read_text())['result']['search']
            template = json.loads((root / 'template.json').read_text())
            for run in template['experiment']['runs']:
                run['id'] += '-second-request'
            worker_spec = importlib.util.spec_from_file_location('review_generated_worker', root / 'worker.py')
            worker = importlib.util.module_from_spec(worker_spec)
            with patch.object(sys, 'path', [str(root), *sys.path]):
                worker_spec.loader.exec_module(worker)
            with patch.object(worker, 'read', side_effect=lambda path: json.loads((root / path).read_text())):
                proposal = worker.proposal(req, result, deepcopy(template))
                repeated = worker.proposal(req, result, deepcopy(template))
            self.assertEqual(proposal['id'], repeated['id'])
            self.assertNotEqual(proposal['id'], first['id'])
            from rds_discrimination import hypothesis_key
            self.assertEqual(hypothesis_key(proposal['discriminator']), hypothesis_key(first['discriminator']))
            retained = structure.propose(root, proposal)
            self.assertEqual(retained['id'], proposal['id'])
            self.assertIsNotNone(structure._find(store, 'PROPOSAL', first['id']))
            self.assertEqual(len({first['id'], retained['id']}), 2)

    def test_fresh_request_cannot_reintroduce_an_actually_refuted_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            original_write = example.write
            def additive_oracle(path, value):
                if path.name == 'oracle.json':
                    value = [{'inputs': row['inputs'],
                              'value': row['inputs']['x'] + row['inputs']['y']}
                             for row in value]
                return original_write(path, value)
            # Freeze a real independent counterexample before any execution.
            with patch.object(example, 'write', additive_oracle):
                root, store = example.build(Path(directory) / 'project')
            generated = rds_jump.generate(root, 3)
            first = structure._find(store, 'PROPOSAL', generated['proposal_ids'][0])
            observed = structure.advance(root, first['id'])
            self.assertEqual(observed['observation'], 'REFUTE')
            req = structure.request(root, 1)['tasks'][0]
            self.assertNotEqual(req['id'], first['request_id'])
            result = json.loads((root / 'out/synthesize.json').read_text())['result']['search']
            template = json.loads((root / 'template.json').read_text())
            for run in template['experiment']['runs']:
                run['id'] += '-after-refutation'
            worker_spec = importlib.util.spec_from_file_location('review_refuted_worker', root / 'worker.py')
            worker = importlib.util.module_from_spec(worker_spec)
            with patch.object(sys, 'path', [str(root), *sys.path]):
                worker_spec.loader.exec_module(worker)
            with patch.object(worker, 'read', side_effect=lambda path: json.loads((root / path).read_text())):
                proposal = worker.proposal(req, result, template)
            self.assertNotEqual(proposal['id'], first['id'])
            from rds_discrimination import hypothesis_key
            self.assertEqual(hypothesis_key(proposal['discriminator']), hypothesis_key(first['discriminator']))
            before = store.snapshot()
            with self.assertRaisesRegex(ValueError, 'HYPOTHESIS_REFUTED'):
                structure.propose(root, proposal)
            after = store.snapshot()
            self.assertEqual(after['runs'], before['runs'])
            self.assertEqual(after['receipts'], before['receipts'])
            self.assertGreater(after['budget']['wall_seconds']['spent_measured'],
                               before['budget']['wall_seconds']['spent_measured'])
            self.assertIsNone(structure._find(store, 'PROPOSAL', proposal['id']))


if __name__ == '__main__':
    unittest.main()
