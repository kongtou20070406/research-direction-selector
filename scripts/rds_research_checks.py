"""Compose existing checkers; replay never generates evidence or runs a job."""
import hashlib
from pathlib import Path

from rds_project import ProjectStore, require


def obligation(proposal, name):
    items = [o for o in proposal['obligations'] if o['id'] == name]
    require(len(items) == 1, 'Unknown obligation: ' + str(name))
    return items[0]


def run(root, item, evidence=None):
    tool, spec = item['tool'], item['spec']
    require((evidence is not None) == (tool == 'observation'),
            'Only an observation requires --evidence; checked tools use their frozen spec')
    if tool == 'formal':
        from rds_verify import verify
        return verify(spec)
    if tool == 'math':
        from rds_math_router import solve_and_check
        return solve_and_check(spec)
    if tool == 'structure':
        from rds_structure import feedback
        require(set(spec) == {'proposal_id'}, 'Structure spec needs proposal_id')
        return feedback(root, spec['proposal_id'])
    from rds_cas import cas_bytes
    from rds_math import read_bytes
    ref = cas_bytes(root, read_bytes(evidence))
    return {'status': 'INPUT_REPORTED', 'original': {'sha256': ref['sha256'], 'bytes': ref['bytes']}}


def replay(root, proposal, saved):
    item = obligation(proposal, saved['obligation'])
    require(saved['request'] == item, 'Check is bound to a different obligation')
    raw, tool, spec = saved['result'], item['tool'], item['spec']
    if tool == 'formal':
        from rds_verify import checked_result
        result = checked_result(spec, raw.get('certificate'))
    elif tool == 'math':
        from rds_math_router import check_certificate
        result = ({'status': 'PASS', 'assurance': 'CERTIFICATE_CHECKED',
                   'certificate': raw['certificate']}
                  if check_certificate(spec, raw.get('certificate')) else
                  {'status': 'UNKNOWN', 'assurance': 'NONE', 'reason': raw.get('reason', 'No replayable certificate')})
    elif tool == 'structure':
        from rds_structure import _find, _check_feedback, _feedback_view
        store = ProjectStore(root)
        original = _find(store, 'PROPOSAL', spec['proposal_id'])
        feedback = _find(store, 'FEEDBACK', spec['proposal_id'])
        require(original is not None and feedback is not None, 'Missing original structure feedback')
        _check_feedback(store, original, feedback)
        current = _feedback_view(original, feedback)
        require(current == raw, 'Structure feedback changed; retain the original and inspect its scope')
        result = {'status': {'SUPPORT': 'PASS', 'REFUTE': 'FAIL'}.get(current['observation'], 'UNKNOWN'),
                  'assurance': 'SCOPED_EMPIRICAL_DISCRIMINATOR', 'feedback': current}
    else:
        ref = raw['original']
        sha = ref['sha256']
        require(isinstance(sha, str) and len(sha) == 64 and all(c in '0123456789abcdef' for c in sha),
                'Invalid observation source')
        path = Path(root) / '.rds' / 'cas' / (sha + '.bin')
        require(path.resolve().is_relative_to(Path(root).resolve()), 'Observation escapes project root')
        from rds_math import read_bytes
        content = read_bytes(path)
        require(hashlib.sha256(content).hexdigest() == sha and len(content) == ref['bytes'], 'Observation integrity failure')
        result = {'status': 'INPUT_REPORTED', 'assurance': 'NONE', 'original': ref}
    # Keep full proof/counterexample bytes in the bound check note; the context
    # carries only the scoped conclusion and a record id for reopening them.
    return {k: v for k, v in result.items() if k not in {'certificate', 'theorems'}}
