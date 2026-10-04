"""Prospective local tool comparisons using original native validation receipts.

One wall measurement per tool is descriptive evidence only. No automatic adoption,
statistical claim, general correctness certificate or research-policy gain.
"""
import math
from pathlib import Path
import re
import time

from rds_math import blob, get, put, read_bytes, records
from rds_project import canonical, digest, file_sha, number, require
from rds_quick import cas_bytes
import rds_tools as tools


def _known_context(value):
    fields = ('host_sha256', 'system', 'release', 'machine', 'processor', 'python',
              'implementation', 'executable_sha256')
    return (isinstance(value, dict) and value.get('schema') == 'rds-local-runtime-v1'
            and all(isinstance(value.get(k), str) and value[k] for k in fields)
            and type(value.get('logical_cpus')) is int and value['logical_cpus'] > 0)


def _wall(receipt):
    cost = receipt.get('resources', {}).get('wall_seconds', {})
    measured = cost.get('measured')
    return measured if (cost.get('unit') == 'seconds' and cost.get('unknown') is False
                        and type(measured) in (int, float) and math.isfinite(measured) and measured > 0) else None


def _observation(root, candidate, validation, plan):
    if validation.get('id') is None:
        return {'validation_id': None, 'validation_sha256': None, 'receipt_sha256': None,
                'job_root': validation.get('job_root'), 'run_status': 'UNRESOLVED', 'timeout': None,
                'exit_code': None, 'execution_errors': [], 'correctness': 'UNKNOWN',
                'wall_seconds': None, 'started_at': None, 'ended_at': None, 'measurement_context': None}
    value = get(root, validation['id'])
    require(value['data'].get('comparison') == {'id': plan['id'], 'sha256': digest(plan)}
            and value['data']['cases_sha256'] == plan['data']['request']['cases']['sha256'],
            'Validation differs from its prospective comparison')
    receipt = tools._validation_receipt(root, value, candidate)
    if value['data']['status'] == 'LOCAL_CASES_PASSED':
        tools._check_validation(root, value, candidate)
        correctness = 'PASS'
    else:
        correctness = 'UNKNOWN'
        result_file = Path(root).resolve() / value['data']['job_root'] / 'outputs' / 'result.json'
        if receipt['run_status'] == 'FAILED' and result_file.is_file():
            from rds_cli import strict_json
            report = strict_json(read_bytes(result_file).decode('utf-8-sig'))
            rows = report.get('cases') if isinstance(report, dict) else None
            count = plan['data']['request']['case_count']
            if (isinstance(report, dict) and set(report) == {'status', 'case_count', 'cases'}
                    and report['status'] == 'FAIL' and report['case_count'] == count
                    and isinstance(rows, list) and len(rows) == count
                    and all(isinstance(row, dict) and set(row) == {'case', 'passed'}
                            and type(row['case']) is int and row['case'] == i
                            and type(row['passed']) is bool for i, row in enumerate(rows))
                    and any(not row['passed'] for row in rows)):
                correctness = 'FAIL'
    return {'validation_id': value['id'], 'validation_sha256': digest(value),
            'receipt_sha256': digest(receipt), 'run_status': receipt['run_status'],
            'timeout': receipt.get('timeout'), 'exit_code': receipt.get('exit_code'),
            'execution_errors': receipt.get('errors', []),
            'correctness': correctness, 'wall_seconds': _wall(receipt),
            'started_at': receipt.get('started_at'), 'ended_at': receipt.get('ended_at'),
            'measurement_context': value['data'].get('measurement_context')}


def assess(plan, baseline, candidate):
    """Interpret already checked observations; missing costs never become zero."""
    expected = plan['data']['request']['measurement_context']
    contexts = [expected, baseline['measurement_context'], candidate['measurement_context']]
    comparable = all(_known_context(c) for c in contexts) and contexts[0] == contexts[1] == contexts[2]
    reasons = []
    if not comparable:
        reasons.append('MISSING_OR_DIFFERENT_RUNTIME_CONTEXT')
    verdicts = [baseline['correctness'], candidate['correctness']]
    correctness = 'FAIL' if 'FAIL' in verdicts else 'PASS' if verdicts == ['PASS', 'PASS'] else 'UNKNOWN'
    if correctness != 'PASS':
        reasons.append('BOTH_LOCAL_VALIDATIONS_MUST_PASS')
    times = [baseline['wall_seconds'], candidate['wall_seconds']]
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in times):
        reasons.append('MISSING_POSITIVE_MEASURED_WALL_COST')
    for observation in (baseline, candidate):
        started = observation['started_at']
        if type(started) not in (int, float) or not math.isfinite(started) or started < plan['data']['declared_at']:
            reasons.append('PROSPECTIVE_EXECUTION_ORDER_UNCONFIRMED')
            break
    ratio = times[0] / times[1] if not reasons else None
    if ratio is not None and not math.isfinite(ratio):
        reasons.append('NONFINITE_WALL_RATIO')
        ratio = None
    status = ('UNKNOWN' if reasons else 'OBSERVED_SPEEDUP' if ratio >= plan['data']['request']['min_speedup']
              else 'NO_OBSERVED_SPEEDUP')
    return {'status': status, 'correctness': correctness, 'comparable_context': comparable,
            'speedup_ratio': ratio, 'reasons': reasons}


def compare(root, baseline, candidate, case_file, precision_key, precision,
            timeout=10, min_speedup=1.1, ledger=None):
    baseline, candidate = tools._name(baseline), tools._name(candidate)
    require(baseline != candidate, 'Compare two distinct tool candidates')
    require(isinstance(precision_key, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', precision_key),
            'Supply a safe explicit precision keyword')
    require(type(precision) is int and precision > 0, 'Precision must be a positive integer')
    timeout = number(timeout, 'tool timeout', True)
    require(timeout <= 60, 'Tool validation is bounded to 60 seconds')
    min_speedup = number(min_speedup, 'min_speedup', True)
    require(min_speedup > 1, 'min_speedup must be greater than one')
    candidates = [get(root, 'tool:' + name) for name in (baseline, candidate)]
    require(all(v and v['kind'] == 'tool' for v in candidates), 'Extract both named tool candidates first')
    require(candidates[0]['asset']['sha256'] != candidates[1]['asset']['sha256'], 'Tool sources are identical')
    raw = read_bytes(case_file)
    cases = tools._cases(raw)
    require(all(type(c.get('kwargs', {}).get(precision_key)) is int
                and c['kwargs'][precision_key] == precision for c in cases),
            'Every case must declare the same fixed precision keyword and value')
    case_ref = cas_bytes(root, raw)
    case_ref['path'] = Path(case_ref['path']).relative_to(Path(root).resolve()).as_posix()
    request = {'baseline': {'id': candidates[0]['id'], 'sha256': digest(candidates[0])},
               'candidate': {'id': candidates[1]['id'], 'sha256': digest(candidates[1])},
               'cases': case_ref, 'case_count': len(cases), 'precision_key': precision_key, 'precision': precision,
               'timeout': timeout, 'min_speedup': min_speedup, 'order': 'baseline_then_candidate',
               'samples_per_tool': 1, 'measurement_context': tools._validation_context(),
               'controller_sha256': file_sha(__file__), 'validator_sha256': file_sha(tools.__file__),
               'ledger': str(Path(ledger).resolve()) if ledger else None}
    token = digest(request)[:32]
    plan_id = 'comparison-plan:' + token
    plan = get(root, plan_id)
    if plan is None:
        plan_data = {'type': 'tool-comparison-plan', 'request': request, 'declared_at': time.time()}
        plan = put(root, plan_id, 'note', canonical(plan_data).encode('utf-8'),
                   dependencies=[v['id'] for v in candidates], data=plan_data)
    require(plan['kind'] == 'note' and plan['data'].get('request') == request
            and blob(root, plan['asset']) == canonical(plan['data']).encode('utf-8'),
            'Comparison declaration changed')
    targets = {v['data'].get('target') for v in records(root) if v['kind'] == 'refutation'}
    require(not targets.intersection({plan_id, 'comparison:' + token, 'objective', *[v['id'] for v in candidates]}),
            'A declared refutation requires comparison review')
    # Both receive the exact original case bytes retained before either dispatch.
    frozen_cases = Path(root).resolve() / request['cases']['path']
    require(blob(root, request['cases']) == raw, 'Comparison cases changed')
    validations, observations = [], []
    for name, tool in zip((baseline, candidate), candidates):
        validation = tools.validate(root, name, frozen_cases, timeout, ledger, comparison=plan)
        validations.append(validation)
        observations.append(_observation(root, tool, validation, plan))
        if validation['id'] is None:
            break  # Inspect/recover the retained attempt before another launch.
    if len(observations) == 1:
        observation = _observation(root, candidates[1], {'id': None}, plan)
        observation['run_status'] = 'NOT_STARTED'
        observations.append(observation)
    result = {**assess(plan, *observations), 'type': 'tool-comparison-result', 'plan_id': plan_id,
              'baseline': observations[0], 'candidate': observations[1], 'precision': precision,
              'precision_key': precision_key, 'case_count': len(cases), 'min_speedup': min_speedup,
              'samples_per_tool': 1, 'order': 'baseline_then_candidate',
              'assurance': 'LOCAL_CASES_AND_SINGLE_WALL_OBSERVATIONS', 'variability': 'NOT_MEASURED',
              'cpu_seconds': None, 'peak_memory_bytes': None, 'automatic_adoption': False,
              'total_budget': 'CHARGED_PARENT_LEDGER' if ledger else 'UNKNOWN',
              'mathematical_status': 'UNKNOWN', 'research_policy_gain_measured': False}
    record = (put(root, 'comparison:' + token, 'note', canonical(result).encode('utf-8'),
                  dependencies=[plan_id, *[v['id'] for v in validations]], data=result)
              if len(validations) == 2 and all(v['id'] is not None for v in validations) else None)
    started = {baseline: False, candidate: False}
    started.update({name: v['execution_started'] for name, v in zip((baseline, candidate), validations)})
    return {**result, 'id': record['id'] if record else None, 'execution_started': started}
