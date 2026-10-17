"""Frozen 92130d1 equivalence and lower-layer boundary regressions.

The oracle is verbatim source, not reconstructed expected results.
"""
import ast
from copy import deepcopy
import hashlib
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import rds_admission_values as values
import rds_owned_advisor as owned
from rds_project_values import digest, number, require
from rds_source_documents import strict_json

FROZEN = {
'_fingerprint': '''def _fingerprint(state):
    """Bind admission state, excluding only a running worker's elapsed telemetry."""
    projected = {**state, 'runs': [
        {k: v for k, v in run.items() if k != 'observed_wall_seconds'}
        if run['status'] == 'RUNNING' else run for run in state['runs']]}
    return digest(projected)''',
'_telemetry': '''def _telemetry(state):
    now = time.time()
    return {'snapshot_at': now, 'checked_at': now, 'running': [
        {'run_id': run['id'], 'attempt_id': run['attempt_id'],
         'lower_bound_seconds': run['observed_wall_seconds'],
         'latest_seconds': run['observed_wall_seconds']}
        for run in state['runs'] if run['status'] == 'RUNNING']}''',
'_reconcile': '''def _reconcile(state, fingerprint, telemetry, reason):
    """Accept monotonic elapsed updates only; caller owns the coherent read/lock."""
    require(_fingerprint(state) == fingerprint, reason)
    require(isinstance(telemetry, dict) and isinstance(telemetry.get('running'), list),
            'Owned admission telemetry is missing or malformed')
    active = {run['id']: run for run in state['runs'] if run['status'] == 'RUNNING'}
    require(len(telemetry['running']) == len(active), 'Owned admission telemetry coverage differs')
    seen = set()
    for window in telemetry['running']:
        require(isinstance(window, dict) and window.get('run_id') in active
                and window['run_id'] not in seen, 'Owned admission telemetry identity differs')
        seen.add(window['run_id'])
        run = active[window['run_id']]
        require(window.get('attempt_id') == run['attempt_id'], 'Owned admission telemetry attempt differs')
        lower = number(window.get('lower_bound_seconds'), 'telemetry lower bound')
        latest = number(window.get('latest_seconds'), 'telemetry latest bound')
        require(lower <= latest <= run['observed_wall_seconds'],
                'Owned running telemetry regressed; inspect retained state')
        window['latest_seconds'] = run['observed_wall_seconds']
    telemetry['checked_at'] = time.time()''',
'check_admission': '''def check_admission(store, db, spec, token):
    """No nested write connection: called under the caller's BEGIN IMMEDIATE."""
    require(isinstance(token, dict) and token.get('selected_run') == spec.get('id')
            and token.get('analysis_coverage', {}).get('full') is True
            and token.get('manifest_sha256') == digest(spec), 'Owned admission manifest binding mismatch')
    _reconcile(_state(store, db), token['fingerprint'], token.get('telemetry'),
               'Owned state changed after Advisor selection; retry admission')
    snapshot = db.execute('SELECT sha256,body FROM dependency_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()
    require(snapshot is not None and snapshot['sha256'] == token['snapshot_sha256'],
            'Owned dependency snapshot changed after Advisor selection; retry admission')
    saved = strict_json(snapshot['body'])
    require(digest(saved) == snapshot['sha256'], 'Owned dependency snapshot integrity failure')
    from rds_math import blob
    blob(store.root, saved['map'])  # Hash-bound CAS read, no nested ledger connection.
    for item in token['evidence_files']:
        _read_original(store, item)
    contract = store._contract(db)
    if contract['advisor_policy'].get('tool_bindings'):
        from rds_owned_tools import applicable
        reports = applicable(store, contract, token['decision']['evidence'])
        binding = next((b for b in contract['advisor_policy']['tool_bindings'] if b['run_id'] == spec['id']), None)
        if binding is not None:
            require(next(r for r in reports if r['candidate'] == binding['candidate'])['status'] == 'APPLICABLE',
                    'Tool applicability changed after Advisor selection; inspect the original qualification')
    from rds_feasibility import check_start
    check_start(store, db, spec['id'])
    return True'''
}


def outcome(call):
    try:
        return ('returned', call())
    except Exception as exc:
        return ('raised', type(exc), str(exc))


def state_fixture():
    return {'runs': [
        {'id': 'a', 'status': 'RUNNING', 'attempt_id': 'a1', 'observed_wall_seconds': 2},
        {'id': 'b', 'status': 'RUNNING', 'attempt_id': 'b1', 'observed_wall_seconds': 3},
        {'id': 'c', 'status': 'COMPLETED', 'attempt_id': 'c1', 'observed_wall_seconds': 4}],
        'budget': {'cpu_seconds': {'actual': None, 'status': 'UNKNOWN'}},
        'evidence': {'status': 'UNKNOWN'}}


class AdmissionValuesTests(unittest.TestCase):
    def setUp(self):
        self.clock = Mock(return_value=123.5)
        self.old = {'digest': digest, 'number': number, 'require': require,
                    'strict_json': strict_json, 'time': SimpleNamespace(time=self.clock)}
        exec('\n\n'.join(FROZEN.values()), self.old)

    def test_frozen_oracle_identity(self):
        expected = {
            '_fingerprint': '226e4618ae1abe34784afd0fe2bc2bb89f500c986ff9a1338ffe85938f0ada17',
            '_telemetry': 'b26b10f5042721458a19f2e43644fb5d8c34dab5e20a96d3922d94db512829a7',
            '_reconcile': '17fe8d3d8f43b5e5397107be60de98d7683278bcaf596cc09854700c49f38579',
            'check_admission': '6a9026b6dd7d79578fe41b040ba840ed6c22e0d5b3231c25fb0785fa69f8cef8'}
        self.assertEqual({k: hashlib.sha256(v.encode()).hexdigest() for k, v in FROZEN.items()}, expected)

    def test_fingerprint_order_states_unknown_and_numeric_values(self):
        original = state_fixture()
        cases = [original, {**original, 'runs': list(reversed(original['runs']))}]
        for status in ('RUNNING', 'COMPLETED', 'FAILED', 'INTERRUPTED', 'RESERVED', 'UNKNOWN'):
            for value in (0, 2.5, True, None, float('nan'), float('inf'), -1):
                state = deepcopy(original)
                state['runs'][0].update(status=status, observed_wall_seconds=value)
                cases.append(state)
        for state in cases:
            with self.subTest(state=repr(state)):
                before = repr(state)
                self.assertEqual(outcome(lambda: self.old['_fingerprint'](state)),
                                 outcome(lambda: values.fingerprint(state)))
                self.assertEqual(repr(state), before)
        elapsed = deepcopy(original)
        elapsed['runs'][0]['observed_wall_seconds'] = 100
        self.assertEqual(values.fingerprint(original), values.fingerprint(elapsed))
        elapsed['budget']['cpu_seconds']['actual'] = 0
        self.assertNotEqual(values.fingerprint(original), values.fingerprint(elapsed))
        self.assertNotEqual(values.fingerprint(original), values.fingerprint(cases[1]))

    def test_telemetry_creation_and_clock_seam(self):
        states = [state_fixture(), {'runs': []}, {'runs': [{'status': 'RUNNING'}]}]
        for state in states:
            with self.subTest(state=state), patch.object(owned.time, 'time', self.clock):
                self.clock.reset_mock()
                expected = outcome(lambda: self.old['_telemetry'](deepcopy(state)))
                calls = self.clock.call_count
                self.clock.reset_mock()
                self.assertEqual(expected, outcome(lambda: owned._telemetry(deepcopy(state))))
                self.assertEqual(self.clock.call_count, calls)
                self.assertEqual(expected, outcome(lambda: values.telemetry(deepcopy(state), 123.5)))

    def compare_reconcile(self, state, telemetry, fingerprint=None):
        fingerprint = self.old['_fingerprint'](state) if fingerprint is None else fingerprint
        old_window, new_window = deepcopy(telemetry), deepcopy(telemetry)
        self.clock.reset_mock()
        expected = outcome(lambda: self.old['_reconcile'](state, fingerprint, old_window, 'changed'))
        calls = self.clock.call_count
        self.clock.reset_mock()
        with patch.object(owned.time, 'time', self.clock):
            actual = outcome(lambda: owned._reconcile(state, fingerprint, new_window, 'changed'))
        self.assertEqual(actual, expected)
        self.assertEqual(repr(new_window), repr(old_window))
        self.assertEqual(self.clock.call_count, calls)
        return new_window, actual

    def test_reconcile_coverage_identity_attempts_and_partial_failure(self):
        state = state_fixture()
        window = self.old['_telemetry'](state)
        for run in state['runs'][:2]:
            run['observed_wall_seconds'] += 5
        cases = [window, None, {}, {'running': None}, {'running': []}]
        cases += [{**window, 'running': window['running'][:1]},
                  {**window, 'running': window['running'] + [window['running'][0]]},
                  {**window, 'running': list(reversed(window['running']))},
                  {**window, 'running': [window['running'][0], window['running'][0]]}]
        for replacement in (None, {}, {'run_id': 'missing'}, {'run_id': []},
                            {**window['running'][1], 'attempt_id': 'stale'},
                            {k: v for k, v in window['running'][1].items() if k != 'attempt_id'}):
            case = deepcopy(window)
            case['running'][1] = replacement
            cases.append(case)
        for case in cases:
            with self.subTest(window=case):
                self.compare_reconcile(state, case)
        changed = deepcopy(window)
        changed['running'][1]['attempt_id'] = 'stale'
        result, failure = self.compare_reconcile(state, changed)
        self.assertEqual(failure[0], 'raised')
        self.assertEqual(result['running'][0]['latest_seconds'], 7)
        self.assertEqual(result['checked_at'], 123.5)
        self.compare_reconcile(state, None, fingerprint='stale')
        self.compare_reconcile({'runs': []}, {'running': [], 'checked_at': 1})

    def test_reconcile_numeric_errors_and_observed_value_semantics(self):
        state = state_fixture()
        window = self.old['_telemetry'](state)
        for key in ('lower_bound_seconds', 'latest_seconds'):
            for value in (None, True, False, -1, '2', float('nan'), float('inf'), float('-inf'), 0, 1, 2, 9):
                case = deepcopy(window)
                case['running'][1][key] = value
                with self.subTest(key=key, value=value):
                    self.compare_reconcile(state, case)
            case = deepcopy(window)
            del case['running'][1][key]
            self.compare_reconcile(state, case)
        # The old predicate does not number()-validate the observed run field.
        for value in (None, True, -1, '3', float('nan'), float('inf'), 0, 20):
            case = deepcopy(state)
            case['runs'][1]['observed_wall_seconds'] = value
            with self.subTest(observed=value):
                self.compare_reconcile(case, window)

    def check_case(self, spec, token, snapshot, *, state_error=False, artifact_error=False):
        def run(check, old):
            events = []
            copied = deepcopy(token)
            db = SimpleNamespace(execute=lambda sql: (
                events.append(('select', sql)) or SimpleNamespace(fetchone=lambda: snapshot)))
            store = SimpleNamespace(root=ROOT, _contract=lambda db: (
                events.append('contract') or {'advisor_policy': {'tool_bindings': [{'run_id': 'a', 'candidate': 'a'}]}}))

            def read_state(store, db):
                events.append('state')
                if state_error:
                    raise ValueError('state failure')
                return state_fixture()

            def original(store, item):
                events.append(('artifact', item))
                if artifact_error:
                    raise ValueError('artifact failure')

            def decode(raw):
                events.append('decode')
                return strict_json(raw)

            if old:
                self.old.update(_state=read_state, _read_original=original, strict_json=decode)
            with patch.object(owned, '_state', read_state), patch.object(owned, '_read_original', original), \
                    patch.object(owned, 'strict_json', decode), patch.object(owned.time, 'time', self.clock), \
                    patch('rds_math.blob', side_effect=lambda root, ref: events.append(('cas', ref))), \
                    patch('rds_owned_tools.applicable', side_effect=lambda *args: (
                        events.append('tools') or [{'candidate': 'a', 'status': 'APPLICABLE'}])), \
                    patch('rds_feasibility.check_start', side_effect=lambda *args: events.append('feasibility')):
                result = outcome(lambda: check(store, db, spec, copied))
            return result, copied, events

        expected = run(self.old['check_admission'], True)
        actual = run(owned.check_admission, False)
        self.assertEqual(actual, expected)
        return actual

    def admission_fixture(self):
        spec = {'id': 'a', 'unknown': None}
        saved = {'map': {'sha256': 'fixture', 'status': 'UNKNOWN'}}
        snapshot = {'sha256': digest(saved), 'body': '{"map":{"sha256":"fixture","status":"UNKNOWN"}}'}
        token = {'selected_run': 'a', 'analysis_coverage': {'full': True}, 'manifest_sha256': digest(spec),
                 'fingerprint': values.fingerprint(state_fixture()), 'telemetry': values.telemetry(state_fixture(), 1),
                 'snapshot_sha256': snapshot['sha256'], 'evidence_files': ['first', 'second'],
                 'decision': {'evidence': {'status': 'UNKNOWN'}}}
        return spec, token, snapshot

    def test_manifest_binding_missing_stale_and_malformed(self):
        spec, token, snapshot = self.admission_fixture()
        cases = [None, {}, [], {**token, 'selected_run': 'other'}, {**token, 'manifest_sha256': 'stale'}]
        cases += [{**token, 'analysis_coverage': value} for value in (None, [], {}, {'full': 1}, {'full': False})]
        cases += [{k: v for k, v in token.items() if k != key} for key in token]
        for case in cases:
            with self.subTest(token=case):
                self.check_case(spec, case, snapshot)
        self.check_case({**spec, 'unknown': 0}, token, snapshot)
        self.check_case(spec, token, snapshot, state_error=True)

    def test_snapshot_identity_strict_decode_digest_and_failure_order(self):
        spec, token, snapshot = self.admission_fixture()
        cases = [None, {}, {**snapshot, 'sha256': 'stale'}, {'sha256': snapshot['sha256']}]
        for body in ('{broken', '{"map":1,"map":2}', '{"map":NaN}', '{"map":1e999}', '{}', 'null', '[]'):
            cases.append({**snapshot, 'body': body})
        for case in cases:
            with self.subTest(snapshot=case):
                self.check_case(spec, token, case)
        for body in ({}, None, []):
            changed_snapshot = {'body': __import__('json').dumps(body), 'sha256': digest(body)}
            changed_token = {**token, 'snapshot_sha256': changed_snapshot['sha256']}
            self.check_case(spec, changed_token, changed_snapshot)
        self.check_case(spec, token, snapshot, artifact_error=True)

    def test_actual_caller_delegation_and_original_check_order(self):
        spec, token, snapshot = self.admission_fixture()
        names = ('fingerprint', 'telemetry', 'reconcile', 'check_manifest',
                 'check_snapshot_identity', 'check_snapshot_digest')
        from contextlib import ExitStack
        with ExitStack() as stack:
            spies = {name: stack.enter_context(patch.object(values, name, wraps=getattr(values, name)))
                     for name in names}
            owned._fingerprint(state_fixture())
            owned._telemetry(state_fixture())
            result, updated, events = self.check_case(spec, token, snapshot)
            self.assertTrue(all(spy.called for spy in spies.values()))
        self.assertEqual(result, ('returned', True))
        self.assertEqual([event[0] if isinstance(event, tuple) else event for event in events],
                         ['state', 'select', 'decode', 'cas', 'artifact', 'artifact', 'contract', 'tools', 'feasibility'])
        self.assertEqual(updated['decision']['evidence']['status'], 'UNKNOWN')

    def test_clean_process_import_and_deferred_dependency_guard(self):
        program = r'''
import ast, builtins, sys
from pathlib import Path
scripts = Path(sys.argv[1])
sys.path.insert(0, str(scripts))
allowed = {'rds_admission_values', 'rds_project_values', 'rds_core'}
original = builtins.__import__
def guarded(name, *args, **kwargs):
    if name.startswith('rds_') and name not in allowed:
        raise AssertionError('upper dependency: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = guarded
import rds_admission_values as v
state = {'runs': []}
window = v.telemetry(state, 1)
v.reconcile(state, v.fingerprint(state), window, 'changed')
spec = {'id': 'a'}
v.check_manifest(spec, {'selected_run': 'a', 'manifest_sha256': v.digest(spec), 'analysis_coverage': {'full': True}})
v.check_snapshot_identity({'sha256': 'a'}, {'snapshot_sha256': 'a'})
v.check_snapshot_digest({}, {'sha256': v.digest({})})
for name in allowed:
    tree = ast.parse((scripts / (name + '.py')).read_text(encoding='utf-8'))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(a.name in {'hashlib', 'json', 'math'} for a in node.names), ast.dump(node)
        if isinstance(node, ast.ImportFrom):
            assert node.module in allowed | {'__future__'}, ast.dump(node)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {'open', '__import__', 'eval', 'exec', 'compile'}, ast.dump(node)
assert not (set(sys.modules) - allowed) & {n for n in sys.modules if n.startswith('rds_')}
print('direct and deferred import guard passed')
'''
        result = subprocess.run([sys.executable, '-B', '-I', '-c', program, str(ROOT / 'scripts')],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
