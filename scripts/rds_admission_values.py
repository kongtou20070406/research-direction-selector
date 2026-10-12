"""Admission data predicates; no storage, clock, callbacks or execution authority.

The caller retains coherent locked reads and original artifact verification.
Telemetry reconciliation intentionally retains updates preceding a later error.
"""
from rds_project_values import digest, number, require


def fingerprint(state):
    """Bind admission state, excluding only a running worker's elapsed telemetry."""
    projected = {**state, 'runs': [
        {k: v for k, v in run.items() if k != 'observed_wall_seconds'}
        if run['status'] == 'RUNNING' else run for run in state['runs']]}
    return digest(projected)


def telemetry(state, now):
    return {'snapshot_at': now, 'checked_at': now, 'running': [
        {'run_id': run['id'], 'attempt_id': run['attempt_id'],
         'lower_bound_seconds': run['observed_wall_seconds'],
         'latest_seconds': run['observed_wall_seconds']}
        for run in state['runs'] if run['status'] == 'RUNNING']}


def reconcile(state, expected_fingerprint, window, reason):
    """Reconcile samples; the caller stamps checked_at only after success."""
    require(fingerprint(state) == expected_fingerprint, reason)
    require(isinstance(window, dict) and isinstance(window.get('running'), list),
            'Owned admission telemetry is missing or malformed')
    active = {run['id']: run for run in state['runs'] if run['status'] == 'RUNNING'}
    require(len(window['running']) == len(active), 'Owned admission telemetry coverage differs')
    seen = set()
    for sample in window['running']:
        require(isinstance(sample, dict) and sample.get('run_id') in active
                and sample['run_id'] not in seen, 'Owned admission telemetry identity differs')
        seen.add(sample['run_id'])
        run = active[sample['run_id']]
        require(sample.get('attempt_id') == run['attempt_id'], 'Owned admission telemetry attempt differs')
        lower = number(sample.get('lower_bound_seconds'), 'telemetry lower bound')
        latest = number(sample.get('latest_seconds'), 'telemetry latest bound')
        require(lower <= latest <= run['observed_wall_seconds'],
                'Owned running telemetry regressed; inspect retained state')
        sample['latest_seconds'] = run['observed_wall_seconds']


def check_manifest(spec, token):
    require(isinstance(token, dict) and token.get('selected_run') == spec.get('id')
            and token.get('analysis_coverage', {}).get('full') is True
            and token.get('manifest_sha256') == digest(spec), 'Owned admission manifest binding mismatch')


def check_snapshot_identity(snapshot, token):
    require(snapshot is not None and snapshot['sha256'] == token['snapshot_sha256'],
            'Owned dependency snapshot changed after Advisor selection; retry admission')


def check_snapshot_digest(saved, snapshot):
    require(digest(saved) == snapshot['sha256'], 'Owned dependency snapshot integrity failure')
