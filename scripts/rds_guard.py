"""Explicit comparable-result guards and declared negative domains, not new proofs."""
from fractions import Fraction
import hashlib
from pathlib import Path
import re
import time

from rds_project import require, file_sha
from rds_guard_values import MAX_BYTES, exact, validate_domain, in_domain


def read(path):
    path = Path(path)
    require(path.is_file(), 'Guard input must be an existing regular file')
    with path.open('rb') as handle:
        raw = handle.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, 'Guard input exceeds 2 MiB')
    from rds_artifacts import strict_json
    return strict_json(raw.decode('utf-8-sig')), hashlib.sha256(raw).hexdigest()


def same_family(first, second, parameter_names):
    from rds_advisor import _loop_route
    def family(candidate):
        action = candidate.get('action')
        if not isinstance(action, dict):
            return None
        parameters = action.get('parameters', {})
        if not isinstance(parameters, dict):
            return None
        return _loop_route({**candidate, 'action': {**action, 'parameters': {k: v for k, v in parameters.items() if k not in parameter_names}}})
    identity = family(first)
    return identity is not None and identity == family(second)


def policy_inputs(path, source_root):
    """List frozen dependencies; a future candidate output is deliberately absent."""
    policy, _ = read(path)
    require(isinstance(policy, dict) and set(policy) <= {'schema', 'comparison', 'metrics', 'milestones', 'wall_seconds'}
            and type(policy.get('schema')) is int and policy['schema'] == 1, 'Expected guard policy schema 1')
    metrics, milestones = policy.get('metrics', []), policy.get('milestones', [])
    require(isinstance(metrics, list) and isinstance(milestones, list) and 0 < len(metrics) + len(milestones) <= 32, 'Declare 1–32 metric or milestone checks')
    if metrics:
        comparison = policy.get('comparison')
        require(isinstance(comparison, dict) and all(comparison.get(k) for k in ('question_id', 'goal_revision', 'scope', 'metric_definition', 'unit', 'cohort', 'protocol')), 'Comparable metrics need question/revision/scope/definition/unit/cohort/protocol')
    require(0 < exact(policy.get('wall_seconds', 30)) <= 60, 'Guard wall cap must be bounded to 60 seconds')
    paths = [Path(path).resolve()]
    base, root = Path(path).resolve().parent, Path(source_root).resolve()
    require(paths[0].is_relative_to(root), 'Guard policy must stay inside the source root')
    for row in metrics:
        require(isinstance(row, dict) and isinstance(row.get('name'), str) and row.get('direction') in {'min', 'max'} and isinstance(row.get('pointer'), str), 'Metric needs name, min|max and JSON pointer')
        require(isinstance(row.get('candidate'), str) and row['candidate'], 'Metric needs a relative candidate output path')
        output = Path(row['candidate'])
        require(not output.is_absolute() and not output.drive and ':' not in row['candidate']
                and (root / output).resolve().is_relative_to(root), 'Metric candidate path must stay inside the source root')
    for row in milestones:
        require(isinstance(row, dict) and isinstance(row.get('id'), str) and row['id'], 'Milestone needs an ID')
        expected = row.get('expected', {})
        require(isinstance(expected, dict) and set(expected) == {'status', 'backend', 'assurance'} and expected['status'] == 'PASS'
                and isinstance(expected['backend'], str) and expected['backend']
                and expected['assurance'] in {'CERTIFICATE_CHECKED', 'LEAN_KERNEL_CHECKED'}, 'Milestone needs exact PASS/backend/assurance expectations')
    for kind, row in [('metric', row) for row in metrics] + [('milestone', row) for row in milestones]:
        refs = [row.get('baseline')] if kind == 'metric' else [row.get('spec'), row.get('certificate')]
        for ref in refs:
            require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'} and isinstance(ref['path'], str)
                    and isinstance(ref['sha256'], str) and re.fullmatch('[0-9a-f]{64}', ref['sha256']), 'Frozen guard input needs path and SHA-256')
            require(not Path(ref['path']).is_absolute() and not Path(ref['path']).drive and ':' not in ref['path'], 'Guard dependencies must use relative paths')
            original = (base / ref['path']).resolve()
            require(original.is_relative_to(root), 'Guard inputs must stay inside the source root')
            _, actual = read(original)
            require(actual == ref['sha256'], 'Guard input binding changed: ' + str(original))
            paths.append(original)
    return policy, list(dict.fromkeys(paths))


def evaluate(path, candidate_root=None):
    """Compare matching observations; replay milestones with the existing strict gate."""
    path = Path(path).resolve()
    policy, _ = policy_inputs(path, path.parent if candidate_root is None else candidate_root)
    root = Path(candidate_root).resolve() if candidate_root is not None else path.parent
    deadline = time.monotonic() + float(exact(policy.get('wall_seconds', 30)))
    results = []
    from rds_artifacts import _pointer
    for row in policy.get('metrics', []):
        result = {'name': row['name'], 'kind': 'metric', 'status': 'UNKNOWN'}
        try:
            baseline, baseline_sha = read(path.parent / row['baseline']['path'])
            candidate_path = (root / row['candidate']).resolve()
            require(candidate_path.is_relative_to(root), 'Candidate output escapes root')
            candidate, candidate_sha = read(candidate_path)
            result.update(baseline_sha256=baseline_sha, candidate_sha256=candidate_sha)
            require(all(isinstance(record, dict) and record.get('comparison') == policy['comparison'] and record.get('status') == 'PASS' and record.get('withdrawn') is not True
                        for record in (baseline, candidate)), 'Scope/protocol/status mismatch or withdrawn baseline; comparison is UNKNOWN')
            old, new = exact(_pointer(baseline, row['pointer'])), exact(_pointer(candidate, row['pointer']))
            good = new >= old if row['direction'] == 'max' else new <= old
            result.update(status='PASS' if good else 'FAIL', baseline=str(old), candidate=str(new), direction=row['direction'],
                          assurance='HASH_BOUND_OBSERVATION_COMPARISON')
        except (OSError, ValueError, TypeError, KeyError, ZeroDivisionError) as exc:
            result['reason'] = str(exc)
        results.append(result)
    from rds_advisor import _bounded_theory_gate
    for row in policy.get('milestones', []):
        try:
            spec, _ = read(path.parent / row['spec']['path'])
            artifact, _ = read(path.parent / row['certificate']['path'])
            require(isinstance(artifact, dict), 'Certificate artifact must be a JSON object')
            certificate = artifact.get('certificate', artifact)
            remaining = deadline - time.monotonic()
            gate, worker = (_bounded_theory_gate({'formal': {'kind': 'declarative', 'statement': spec},
                                                'committed': {'certificate': certificate}}, remaining)
                            if remaining > 0 else ({'status': 'UNKNOWN', 'assurance': 'NONE', 'reason': 'Shared guard deadline exceeded'}, {}))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            gate, worker = {'status': 'UNKNOWN', 'assurance': 'NONE', 'reason': str(exc)}, {}
        expected = row['expected']
        match = all(gate.get(key) == value for key, value in expected.items())
        results.append({'name': row.get('id'), 'kind': 'milestone', 'status': 'PASS' if match else 'FAIL' if gate.get('status') == 'FAIL' else 'UNKNOWN',
                        'expected': expected, 'actual': gate, 'original_worker': worker})
    if time.monotonic() > deadline:
        results.append({'kind': 'budget', 'status': 'UNKNOWN', 'reason': 'Shared guard deadline exceeded'})
    status = 'FAIL' if any(r['status'] == 'FAIL' for r in results) else 'UNKNOWN' if any(r['status'] == 'UNKNOWN' for r in results) else 'PASS'
    return {'status': status, 'promotion_eligible': status == 'PASS', 'policy_sha256': file_sha(path), 'checks': results,
            'scientific_support': 'UNKNOWN', 'assurance': 'SCOPED_REGRESSION_REVIEW',
            'authorization': 'UNCHANGED', 'note': 'Retains all observations; never promotes a process exit code to scientific truth.'}
