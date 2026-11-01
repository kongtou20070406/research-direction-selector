"""L3 pure, bounded feedback and version proposals; no writer or admission.

Reuse method descriptions and exact scoped predicates from rds_methods. Like
steering, feedback retains its source and never promotes a hypothesis to fact.
Version transitions are values for an existing trusted writer to validate and
commit atomically; a config flag, hash or successful preview is not authority.
"""
from copy import deepcopy
import hashlib
import json
import re

from rds_methods import declared_methods, review_methods


MAX_BYTES = 64 * 1024
MAX_RULES = 32
MAX_CANDIDATES = 128
SCHEMA = 'rds-research-policy-v1'
KINDS = {'VETO', 'FAILURE', 'HYPOTHESIS_REJECTED'}


def _require(ok, message):
    if not ok:
        raise ValueError(message)


def _text(value, limit=2048):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


def _sha(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def _fields(value):
    return (isinstance(value, dict) and 1 <= len(value) <= 8 and
            all(_text(k, 64) and _text(v, 128) for k, v in value.items()))


def _encode(value):
    try:
        raw = json.dumps(value, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=True, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError('Policy input must be finite JSON values') from exc
    _require(len(raw) <= MAX_BYTES, 'Policy input exceeds byte limit')
    return raw


def validate_policy(value):
    """Validate data only, including disabled rules; return an independent copy."""
    _require(isinstance(value, dict) and set(value) ==
             {'schema', 'parent_sha256', 'rules'}, 'Invalid policy fields')
    _require(value['schema'] == SCHEMA, 'Unknown policy schema')
    _require(value['parent_sha256'] is None or _sha(value['parent_sha256']),
             'Invalid parent identity')
    rules = value['rules']
    _require(isinstance(rules, list) and len(rules) <= MAX_RULES, 'Too many rules')
    seen = set()
    for rule in rules:
        _require(isinstance(rule, dict) and set(rule) ==
                 {'id', 'kind', 'enabled', 'scope', 'target', 'reason', 'source'},
                 'Invalid feedback rule fields')
        _require(_text(rule['id'], 128) and rule['id'] not in seen, 'Duplicate or invalid rule ID')
        seen.add(rule['id'])
        _require(isinstance(rule['kind'], str) and rule['kind'] in KINDS, 'Unknown feedback kind')
        _require(type(rule['enabled']) is bool, 'enabled must be Boolean')
        _require(_text(rule['reason']) and _text(rule['source']), 'Feedback needs reason and source')
        _require(_fields(rule['scope']) and {'project_id', 'decision_id'} <= set(rule['scope']),
                 'Feedback needs explicit project and decision scope')
        target = rule['target']
        _require(isinstance(target, dict) and len(target) == 1, 'Feedback needs one exact target')
        if 'hypothesis_id' in target:
            _require(_text(target['hypothesis_id'], 128), 'Invalid hypothesis target')
        else:
            _require(set(target) == {'methods'} and _fields(target['methods']), 'Invalid method target')
        _require(rule['kind'] != 'HYPOTHESIS_REJECTED' or 'hypothesis_id' in target,
                 'Rejected hypothesis needs a hypothesis identity')
    _encode(value)
    return deepcopy(value)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, 'Duplicate JSON key')
        result[key] = value
    return result


def load_policy(raw, expected_sha256=None):
    """Hash exact retained bytes, not a reserialization of their JSON."""
    _require(type(raw) is bytes and len(raw) <= MAX_BYTES, 'Policy needs bounded bytes')
    identity = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None:
        _require(_sha(expected_sha256) and identity == expected_sha256, 'Policy byte identity mismatch')
    try:
        value = json.loads(raw.decode('utf-8'), object_pairs_hook=_pairs)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ValueError('Invalid policy JSON: ' + str(exc)) from exc
    return {'sha256': identity, 'policy': validate_policy(value)}


def draft_policy(rules, parent_bytes=None):
    """Edits, deletions and enabled=False are explicit reversible revisions."""
    parent = load_policy(parent_bytes)['sha256'] if parent_bytes is not None else None
    return _encode(validate_policy({'schema': SCHEMA, 'parent_sha256': parent, 'rules': rules}))


def _match(expected, actual):
    if any(k in actual and actual[k] != v for k, v in expected.items()):
        return False
    return True if all(k in actual for k in expected) else None


def _target_match(rule, candidate):
    target = rule['target']
    if 'hypothesis_id' in target:
        return _match(target, candidate)
    # The existing reviewer handles multi-step methods and missing descriptors.
    review = review_methods({'method_constraints': [{
        'id': rule['id'], 'quote': rule['reason'], 'source': rule['source'],
        'status': 'CONFIRMED', 'forbid': target['methods'],
    }]}, candidate.get('methods'))
    return {'CONFLICT': True, 'UNKNOWN': None, 'COMPATIBLE': False}[review['status']]


def recommend(raw, candidates, scope):
    """Filter vetoes, defer scoped failures, preserve input order within a tier.

Only existing READY candidates participate. A recommendation is still subject
to the caller's original admission, costs and permissions. Missing matching
fields remain UNKNOWN and do not silently evade feedback.
"""
    version = load_policy(raw)
    _require(isinstance(candidates, list) and len(candidates) <= MAX_CANDIDATES,
             'Too many candidates')
    _require(isinstance(scope, dict) and len(scope) <= 8 and
             all(_text(k, 64) and _text(v, 128) for k, v in scope.items()), 'Invalid decision scope')
    _encode(candidates)
    seen, reviews, ranked = set(), [], []
    for index, candidate in enumerate(candidates):
        _require(isinstance(candidate, dict) and _text(candidate.get('id'), 128)
                 and candidate['id'] not in seen and _text(candidate.get('status'), 128),
                 'Candidates need unique IDs and existing statuses')
        seen.add(candidate['id'])
        declared_methods(candidate)
        if 'hypothesis_id' in candidate:
            _require(_text(candidate['hypothesis_id'], 128), 'Invalid candidate hypothesis')
        matches, unknown, deferred, vetoed = [], False, False, False
        for rule in version['policy']['rules']:
            if not rule['enabled']:
                continue
            applicable = _match(rule['scope'], scope)
            if applicable is False:
                continue
            targeted = _target_match(rule, candidate)
            if targeted is False:
                continue
            matched = applicable is True and targeted is True
            matches.append({'rule_id': rule['id'], 'kind': rule['kind'],
                            'status': 'MATCH' if matched else 'UNKNOWN',
                            'reason': rule['reason'], 'source': rule['source'],
                            'scope': deepcopy(rule['scope'])})
            unknown |= not matched
            vetoed |= matched and rule['kind'] == 'VETO'
            deferred |= matched and rule['kind'] in {'FAILURE', 'HYPOTHESIS_REJECTED'}
        disposition = ('VETOED' if vetoed else 'UNKNOWN' if unknown else
                       'DEFERRED' if deferred else 'UNCHANGED')
        reviews.append({'candidate_id': candidate['id'], 'disposition': disposition, 'feedback': matches})
        if candidate['status'] == 'READY' and disposition in {'UNCHANGED', 'DEFERRED'}:
            ranked.append((int(deferred), index, candidate['id']))
    ordered = [item[2] for item in sorted(ranked)]
    return {'status': 'RECOMMENDATION' if ordered else 'ABSTAIN',
            'policy_sha256': version['sha256'], 'selected_id': ordered[0] if ordered else None,
            'ordered_ids': ordered, 'reviews': reviews, 'candidates': deepcopy(candidates),
            'assurance': 'INPUT_REPORTED',
            'authorization': 'UNCHANGED', 'execution_authorized': False,
            'scientific_support': 'UNKNOWN', 'research_policy_gain_measured': False}


def _pointer(value):
    _require(isinstance(value, dict) and set(value) == {'generation', 'version_sha256'},
             'Invalid policy pointer')
    _require(type(value['generation']) is int and 0 <= value['generation'] <= 2**63 - 1,
             'Invalid policy generation')
    _require(_sha(value['version_sha256']) or
             (value['generation'] == 0 and value['version_sha256'] is None), 'Invalid active version')
    return deepcopy(value)


def _compare(current, expected):
    current, expected = _pointer(current), _pointer(expected)
    _require(current == expected, 'Stale policy pointer; compare-and-swap rejected')
    return current


def _transition(current, raw, operation):
    _require(current['generation'] < 2**63 - 1, 'Policy generation exhausted')
    identity = load_policy(raw)['sha256']
    return {'status': 'PROPOSED', 'operation': operation, 'expected': deepcopy(current),
            'replacement': {'generation': current['generation'] + 1, 'version_sha256': identity},
            'policy_bytes': raw, 'requires_trusted_writer': True, 'committed': False,
            'authorization': 'UNCHANGED', 'scientific_support': 'UNKNOWN'}


def propose_adoption(current, expected, candidate_bytes):
    """Preflight CAS; writer must recheck expected within its own transaction.

The monotonically increasing generation prevents stale adoption after A-B-A
rollback. This pure function neither authenticates a writer nor commits state.
"""
    current = _compare(current, expected)
    candidate = load_policy(candidate_bytes)
    _require(candidate['policy']['parent_sha256'] == current['version_sha256'], 'Policy parent is stale')
    return _transition(current, candidate_bytes, 'ADOPT')


def pin_version(current):
    """Return a value for the trusted reservation writer to bind to a job."""
    pointer = _pointer(current)
    _require(pointer['version_sha256'] is not None, 'No policy version to pin')
    return pointer


def load_pinned(pin, blobs):
    """Resolve only the pinned version; never fall back to the active policy."""
    identity = pin_version(pin)['version_sha256']
    _require(isinstance(blobs, dict) and identity in blobs, 'Pinned policy bytes are missing')
    raw = blobs[identity]
    load_policy(raw, identity)
    return raw


def propose_rollback(current, expected, blobs):
    """Return the verified parent's actual retained bytes, including whitespace."""
    current = _compare(current, expected)
    raw = load_pinned(current, blobs)
    parent = load_policy(raw)['policy']['parent_sha256']
    _require(parent is not None, 'Root policy has no parent')
    original = load_pinned({'generation': current['generation'], 'version_sha256': parent}, blobs)
    return _transition(current, original, 'ROLLBACK')
