"""Bounded domain checks over original owned evidence; no scientific self-certification.

This module neither launches workers nor creates a second research ledger. It
replays exact certificates, finite integer oracles, bounded numeric expressions,
or CPU tensor inference.
"""
from copy import deepcopy
import math
from pathlib import Path
import re

from rds_artifacts import strict_json
from rds_project import ProjectStore, file_sha, require

MAX_BYTES = 2 * 1024 * 1024
DOMAINS = {'mathematics': 'exact_certificate', 'algorithms': 'integer_sum_squares',
           'deep_learning': 'torch_linear_regression', 'continuous': 'numerical_expression_evaluation'}


def goal_conditions(policy):
    """Keep the original predicates and enforce the declared confirmation gate."""
    conditions = deepcopy(policy['context']['decision']['goal_conditions'])
    if 'confirmation' in policy:
        conditions.append({'fact': 'confirmation.task_status', 'op': 'eq', 'value': 'PASS'})
    return conditions


def _ref(store, contract, ref, role):
    require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'} and
            isinstance(ref['sha256'], str) and re.fullmatch('[a-f0-9]{64}', ref['sha256']),
            'Invalid domain confirmation file reference')
    path = store._path(ref['path'])
    matches = [b for b in contract['bindings'] if store._path(b['path']) == path]
    require(matches and {b['role'] for b in matches} == {role} and
            all(b['sha256'] == ref['sha256'] for b in matches),
            'Domain confirmation file must be exclusively frozen as ' + role)
    return path


def _json(path, expected, *, max_bytes=MAX_BYTES):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= max_bytes,
            'Domain confirmation input missing, linked, or too large')
    raw = path.read_bytes()
    import hashlib
    require(hashlib.sha256(raw).hexdigest() == expected, 'Domain confirmation input changed')
    return strict_json(raw.decode('utf-8-sig'))


def validate_policy(store, contract, policy):
    """Validate optional protected declaration without launching or writing."""
    value = policy.get('confirmation')
    if value is None:
        return None
    require(isinstance(value, dict) and set(value) == {'schema', 'domain', 'candidate_runs',
            'confirmation_runs', 'scope', 'claim', 'evaluator', 'data', 'rules'} and
            type(value['schema']) is int and value['schema'] == 1 and isinstance(value['domain'], str) and value['domain'] in DOMAINS,
            'Invalid domain confirmation schema')
    require(isinstance(value['scope'], str) and 1 <= len(value['scope'].strip()) <= 1024,
            'Domain confirmation needs an explicit bounded scope')
    routes = {r['manifest']['id']: r['manifest'] for r in policy['routes']}
    for field in ('candidate_runs', 'confirmation_runs'):
        ids = value[field]
        require(isinstance(ids, list) and 1 <= len(ids) <= 16 and
                all(isinstance(r, str) and r in routes for r in ids) and len(set(ids)) == len(ids),
                'Domain confirmation needs distinct declared route IDs')
    require(len(value['candidate_runs']) == 1 and len(value['confirmation_runs']) == 1 and
            not set(value['candidate_runs']).intersection(value['confirmation_runs']),
            'Current domain confirmation supports one candidate and one independent confirmation route')
    _ref(store, contract, value['claim'], 'config')
    evaluator = _ref(store, contract, value['evaluator'], 'evaluator')
    require(isinstance(value['data'], list) and 1 <= len(value['data']) <= 16 and
            all(isinstance(r, dict) and isinstance(r.get('path'), str) for r in value['data']) and
            len({r['path'] for r in value['data']}) == len(value['data']), 'Invalid confirmation data bindings')
    for ref in value['data']:
        _ref(store, contract, ref, 'data')
    rules = value['rules']
    fields = {'kind', 'candidate_output', 'confirmation_output'}
    if value['domain'] == 'algorithms':
        fields |= {'baseline_run', 'baseline_output'}
    kinds = {'exact_certificate', 'polynomial_rational_evaluation'} if value['domain'] == 'mathematics' else {DOMAINS[value['domain']]}
    if value['domain'] == 'deep_learning':
        kinds.add('torch_postcommit_mlp')
    require(isinstance(rules, dict) and set(rules) == fields and rules['kind'] in kinds,
             'Unsupported domain confirmation rules')
    if rules['kind'] == 'polynomial_rational_evaluation':
        require(len(value['data']) == 1, 'Polynomial confirmation requires one frozen data file')
    if rules['kind'] == 'numerical_expression_evaluation':
        from rds_continuous_confirmation import validate_claim
        require(len(value['data']) == 2, 'Continuous confirmation requires separate frozen features and labels')
        require(_ref(store, contract, value['data'][0], 'data') !=
                _ref(store, contract, value['data'][1], 'data'), 'Continuous features and labels must be distinct')
        validate_claim(_json(_ref(store, contract, value['claim'], 'config'), value['claim']['sha256'],
                             max_bytes=1024 * 1024))
    if rules['kind'] == 'torch_postcommit_mlp':
        from rds_postcommit_confirmation import validate_claim
        validate_claim(_json(_ref(store, contract, value['claim'], 'config'), value['claim']['sha256']))
    candidate, confirmation = value['candidate_runs'][0], value['confirmation_runs'][0]
    for rid, key in ((candidate, 'candidate_output'), (confirmation, 'confirmation_output')):
        require(rules[key] in routes[rid]['outpaths'], 'Domain evidence must be an owned declared output')
    argv = routes[confirmation]['argv']
    index = 2 if len(argv) > 1 and argv[1] == '-B' else 1
    require(len(argv) > index and not argv[index].startswith('-') and
            (store.root / argv[index]).resolve() == evaluator and
            Path(argv[0]).stem.lower().startswith('python'),
            'Confirmation must directly execute its frozen evaluator script')
    if value['domain'] == 'algorithms':
        rid = rules['baseline_run']
        require(rid in routes and rid not in {candidate, confirmation} and
                rules['baseline_output'] in routes[rid]['outpaths'], 'Invalid independent baseline route')
    return deepcopy(value)


def _receipt(store, state, rid):
    rows = [r for r in state['receipts'] if r['run_id'] == rid]
    require(len(rows) <= 1, 'Duplicate domain receipt')
    if not rows:
        return None
    receipt = rows[0]
    # Reuse canonical retained-receipt validation even for supplied snapshots.
    from rds_project import canonical
    receipt = ProjectStore._receipt({'run_id': rid, 'sha256': receipt['sha256'], 'body': canonical(receipt)})
    with store._db(True) as db:
        db.execute('BEGIN')
        row = db.execute('SELECT run_id,sha256,body FROM receipts WHERE run_id=?', (rid,)).fetchone()
        require(row is not None and ProjectStore._receipt(row) == receipt,
                'Supplied domain receipt is not the retained original')
        require(db.execute("SELECT 1 FROM events WHERE json_extract(body,'$.kind')='ATTEMPT_FINISHED' "
                           "AND json_extract(body,'$.run_id')=? AND json_extract(body,'$.sha256')=?",
                           (rid, receipt['sha256'])).fetchone(), 'Domain receipt lacks original completion event')
    run = next((r for r in state['runs'] if r['id'] == rid), None)
    require(run is not None and run['attempt_id'] == receipt['attempt_id'] and
            run['manifest_sha256'] == receipt['manifest_sha256'] and run['status'] == receipt['process_status'],
            'Domain receipt differs from its original run')
    return receipt


def _matched_baseline(store, state, contract, value):
    """Reuse a paid baseline only through this ledger's verified ancestors."""
    from rds_method_revision import contract_history
    rid = value['rules']['baseline_run']
    receipt = _receipt(store, state, rid)
    require(receipt is not None and receipt['run_status'] == 'SUCCEEDED' and receipt.get('process_started') is True,
            'Matched baseline evidence is unavailable')
    with store._db(True) as db:
        db.execute('BEGIN')
        run = store._run(db, rid)
        require(store._observe(db, run) == receipt, 'Baseline differs from its retained operation')
        require(receipt.get('effective_contract_sha256') == run.get('effective_contract_sha256'),
                'Baseline run/receipt contract identities differ')
        history = contract_history(db)
        require(history[-1]['contract'] == contract, 'Current baseline comparison contract is stale')
        ancestor = next((h['contract'] for h in history if h['sha256'] == receipt.get('effective_contract_sha256')), None)
        require(ancestor is not None, 'Baseline contract is outside verified method lineage')
        require(receipt['bindings_before'] == receipt['bindings_after'] == ancestor['bindings'] and
                ancestor['advisor_policy']['confirmation'] == value and
                ancestor['advisor_policy']['context'] == contract['advisor_policy']['context'],
                'Baseline comparison declaration or original bindings differ')
        original = next((r['manifest'] for r in ancestor['advisor_policy']['routes'] if r['manifest']['id'] == rid), None)
        require(original == run['manifest'], 'Baseline operation is not its ancestor-authorized route')
        require({(b['path'], b['role'], b['sha256']) for b in ancestor['bindings'] if b['role'] in {'config', 'data', 'evaluator'}} ==
                {(b['path'], b['role'], b['sha256']) for b in contract['bindings'] if b['role'] in {'config', 'data', 'evaluator'}},
                'Baseline data, claim or evaluator differs')
    return receipt


def _output(store, receipt, path, *, max_bytes=MAX_BYTES):
    from rds_owned_advisor import _read_original
    artifacts = [a for a in receipt['artifacts'] if a['kind'] == 'project_output' and a['path'] == path]
    require(len(artifacts) == 1, 'Domain original output missing or ambiguous')
    require(type(artifacts[0].get('size')) is int and artifacts[0]['size'] <= max_bytes,
            'Domain original exceeds bounded JSON size')
    raw = _read_original(store, artifacts[0], keep=True)
    require(raw is not None, 'Domain original exceeds bounded JSON size')
    return strict_json(raw.decode('utf-8-sig')), artifacts[0]


def _finite(value):
    return type(value) in {int, float} and math.isfinite(value) and abs(value) <= 1e9


def inspect_confirmation(store, contract, state=None):
    """Derive task evidence from retained originals, preserving failed/unknown scope."""
    value = contract.get('advisor_policy', {}).get('confirmation')
    if value is None:
        return None
    result = {'domain': value.get('domain'), 'scope': value.get('scope'), 'execution': 'PENDING',
              'task_confirmation': 'PENDING', 'assurance': 'NONE', 'scientific_support': 'UNKNOWN',
              'research_policy_gain_measured': False, 'reasons': [], 'evidence': []}
    try:
        value = validate_policy(store, contract, contract['advisor_policy'])
        if state is None:
            state = store.snapshot()
        require(state['contract'] == contract, 'Domain inspection contract differs from live state')
        candidate_id, confirmation_id = value['candidate_runs'][0], value['confirmation_runs'][0]
        candidate, confirmation = _receipt(store, state, candidate_id), _receipt(store, state, confirmation_id)
        result['execution'] = {'candidate': candidate['run_status'] if candidate else 'PENDING',
                               'confirmation': confirmation['run_status'] if confirmation else 'PENDING'}
        if candidate is None or confirmation is None:
            if any(r is not None and r['run_status'] != 'SUCCEEDED' for r in (candidate, confirmation)):
                result['task_confirmation'] = 'UNKNOWN'
                result['reasons'].append('Failed execution cannot confirm the task')
            return result
        result['execution'] = {'candidate': candidate['run_status'], 'confirmation': confirmation['run_status']}
        result['task_confirmation'] = 'UNKNOWN'
        require(candidate['run_status'] == confirmation['run_status'] == 'SUCCEEDED' and
                candidate.get('process_started') is True and confirmation.get('process_started') is True,
                'Failed or unstarted execution does not provide reliable domain confirmation')
        require(candidate['bindings_before'] == candidate['bindings_after'] == contract['bindings'] and
                confirmation['bindings_before'] == confirmation['bindings_after'] == contract['bindings'],
                'Domain input identity differs from the frozen contract')
        rules = value['rules']
        polynomial = rules['kind'] == 'polynomial_rational_evaluation'
        continuous = rules['kind'] == 'numerical_expression_evaluation'
        bound = 1024 * 1024 if polynomial or continuous else MAX_BYTES
        payload, artifact = _output(store, candidate, rules['candidate_output'], max_bytes=bound)
        checked, checked_artifact = _output(store, confirmation, rules['confirmation_output'], max_bytes=bound)
        result['evidence'] = [{'run_id': candidate_id, 'receipt_sha256': candidate['sha256'], **artifact},
                              {'run_id': confirmation_id, 'receipt_sha256': confirmation['sha256'], **checked_artifact}]
        require(isinstance(checked, dict) and checked.get('candidate_receipt_sha256') == candidate['sha256'] and
                checked.get('claim_sha256') == value['claim']['sha256'] and
                checked.get('evaluator_sha256') == value['evaluator']['sha256'],
                'Confirmation payload is not bound to its candidate/claim/evaluator')
        claim = _json(_ref(store, contract, value['claim'], 'config'), value['claim']['sha256'], max_bytes=bound)
        require(file_sha(_ref(store, contract, value['evaluator'], 'evaluator')) == value['evaluator']['sha256'],
                'Frozen evaluator changed')
        data = [_json(_ref(store, contract, ref, 'data'), ref['sha256'], max_bytes=bound) for ref in value['data']]
        verdict = 'UNKNOWN'
        if continuous:
            from rds_continuous_confirmation import check_output
            require(set(checked) == {'candidate_receipt_sha256', 'claim_sha256', 'evaluator_sha256',
                                    'inputs_sha256', 'labels_sha256', 'verdict'} and
                    checked['inputs_sha256'] == value['data'][0]['sha256'] and
                    checked['labels_sha256'] == value['data'][1]['sha256'],
                    'Continuous evaluator input identity or schema differs')
            replay = check_output(claim, data[0], data[1], payload, value['data'][0]['sha256'])
            # This gate checks finite fit only; PASS never establishes label blindness.
            verdict = replay['status']
            result.update(assurance='RECOMPUTED_FINITE_FLOAT_EXPRESSION', finite_evaluation=replay,
                          declared_statement_only=True, symbolic_identity='UNKNOWN', causal_support='UNKNOWN',
                          generalization='UNKNOWN', confirmation_independence='UNKNOWN')
            exposed = {ref['sha256'] for exposure in state.get('exposures', [])
                       if exposure.get('run_id') == candidate_id for ref in exposure.get('data', [])}
            if value['data'][1]['sha256'] in exposed:
                result['confirmation_independence'] = 'DECLARED_EXPOSED'
            if replay.get('reason'):
                result['reasons'].append(replay['reason'])
        elif polynomial:
            from rds_polynomial_confirmation import check_output
            require(checked.get('inputs_sha256') == value['data'][0]['sha256'],
                    'Polynomial evaluator input identity differs')
            replay = check_output(claim, data[0], payload, value['data'][0]['sha256'])
            verdict = replay['status']
            result.update(assurance='FINITE_EXACT_QQ_POLYNOMIAL_EVALUATION', finite_arithmetic=replay,
                          declared_statement_only=True, root_uniqueness='UNKNOWN', disk_covering='UNKNOWN',
                          global_optimality='UNKNOWN', minimal_polynomial='UNKNOWN')
        elif value['domain'] == 'mathematics':
            from rds_verify import checked_result
            require(isinstance(payload, dict), 'Original mathematical certificate must be an object')
            replay = checked_result(claim, payload.get('certificate', payload))
            require(replay is not None and replay.get('status') in {'PASS', 'FAIL'},
                    'Original certificate failed independent replay')
            if replay.get('application_status') != 'UNKNOWN' and not replay.get('conditional_statement'):
                verdict = replay['status']
            result.update(assurance=replay['assurance'], formal_semantics=replay.get('semantics'),
                          declared_statement_only=True)
        elif value['domain'] == 'algorithms':
            require(isinstance(claim, dict) and set(claim) == {'schema', 'kind'} and
                    type(claim['schema']) is int and claim['schema'] == 1 and
                    claim['kind'] == 'integer_sum_squares' and len(data) == 1,
                    'Unsupported finite integer oracle claim')
            cases = data[0]
            require(isinstance(cases, list) and 1 <= len(cases) <= 64 and
                    all(isinstance(xs, list) and len(xs) <= 64 and
                        all(type(x) is int and abs(x) <= 2 ** 63 for x in xs) for xs in cases),
                    'Integer oracle cases exceed the finite type/size bound')
            oracle = [sum(x * x for x in xs) for xs in cases]
            baseline = _matched_baseline(store, state, contract, value)
            base_values, base_artifact = _output(store, baseline, rules['baseline_output'])
            for output in (payload, base_values):
                require(isinstance(output, dict) and output.get('inputs_sha256') == value['data'][0]['sha256'] and
                        isinstance(output.get('values'), list) and all(type(x) is int for x in output['values']),
                        'Algorithm output/input/type identity differs')
            require(base_values['values'] == oracle, 'Bound baseline failed the independent oracle')
            verdict = 'PASS' if payload['values'] == oracle else 'FAIL'
            a, b = candidate['resources']['wall_seconds'], baseline['resources']['wall_seconds']
            require(_finite(a['measured']) and _finite(b['measured']) and b['measured'] > 0,
                    'Same-input measured runtime is unavailable')
            require(candidate['executor_sha256'] == baseline['executor_sha256'], 'Comparison executors differ')
            result.update(assurance='FINITE_EXACT_INTEGER_ORACLE', case_count=len(cases),
                          cost_comparison={'status': 'SINGLE_RUN_OBSERVATION', 'candidate_wall_seconds': a['measured'],
                                           'baseline_wall_seconds': b['measured'], 'ratio': a['measured'] / b['measured'],
                                           'general_speedup': 'UNKNOWN'})
            result['evidence'].append({'run_id': rules['baseline_run'], 'receipt_sha256': baseline['sha256'], **base_artifact})
        elif rules['kind'] == 'torch_postcommit_mlp':
            from rds_postcommit_confirmation import inspect as inspect_postcommit
            replay = inspect_postcommit(store, contract, candidate, confirmation, payload, checked)
            verdict = replay['task_confirmation']
            result.update(replay)
        else:
            require(isinstance(claim, dict) and set(claim) == {'schema', 'kind', 'input_dimension', 'max_mse'} and
                    type(claim['schema']) is int and claim['schema'] == 1 and claim['kind'] == 'torch_linear_regression' and
                    type(claim['input_dimension']) is int and 1 <= claim['input_dimension'] <= 16 and
                    _finite(claim['max_mse']) and claim['max_mse'] >= 0 and len(data) == 1,
                    'Unsupported bounded CPU linear-regression claim')
            inputs = data[0]
            require(isinstance(inputs, dict) and set(inputs) == {'x', 'y'} and isinstance(inputs['x'], list) and
                    1 <= len(inputs['x']) <= 256 and len(inputs['y']) == len(inputs['x']) and
                    all(isinstance(x, list) and len(x) == claim['input_dimension'] and all(_finite(v) for v in x)
                        for x in inputs['x']) and all(_finite(y) for y in inputs['y']), 'Invalid CPU confirmation dataset')
            require(isinstance(payload, dict) and set(payload) == {'weight', 'bias'} and
                    isinstance(payload['weight'], list) and len(payload['weight']) == claim['input_dimension'] and
                    all(_finite(x) for x in payload['weight']) and _finite(payload['bias']), 'Invalid original CPU weights')
            try:
                import torch
            except ImportError:
                result['reasons'].append('torch CPU dependency unavailable')
                return result
            with torch.no_grad():
                x = torch.tensor(inputs['x'], dtype=torch.float32, device='cpu')
                y = torch.tensor(inputs['y'], dtype=torch.float32, device='cpu')
                pred = x @ torch.tensor(payload['weight'], dtype=torch.float32, device='cpu') + payload['bias']
                mse = float(torch.mean((pred - y) ** 2).item())
            require(math.isfinite(mse), 'CPU confirmation metric is non-finite')
            finite_verdict = 'PASS' if mse <= claim['max_mse'] else 'FAIL'
            result.update(assurance='RECOMPUTED_CPU_FP32_FINITE_LINEAR_MODEL', measured_mse=mse,
                          finite_evaluation=finite_verdict, confirmation_independence='UNKNOWN')
            exposures = [e for e in state.get('exposures', []) if e.get('run_id') in value['candidate_runs']]
            declared = {d['sha256'] for e in exposures for d in e.get('data', [])}
            contaminated = bool(declared.intersection(ref['sha256'] for ref in value['data']))
            result['confirmation_independence'] = 'DECLARED_EXPOSED' if contaminated else 'UNKNOWN'
            result['reasons'].append('Confirmation partition was declared exposed' if contaminated else
                                     'No independently enforced unexposed confirmation partition')
            # Current shared bindings do not establish secrecy/access isolation.
            # A recomputed metric is useful, but never upgrades independence.
            verdict = 'UNKNOWN'
        require(checked.get('verdict') == verdict or (value['domain'] == 'deep_learning' and
                checked.get('verdict') == result.get('finite_evaluation')), 'Confirmation self-verdict contradicts replay')
        result['task_confirmation'] = verdict
    except (ValueError, TypeError, KeyError, OSError, StopIteration, ImportError, OverflowError, RecursionError) as exc:
        result['task_confirmation'] = 'UNKNOWN'
        result['reasons'].append(str(exc))
    return result
