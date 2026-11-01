"""Data-only general Lean proof obligations and fail-closed audit preflight.

An audit is untrusted input, not a certificate. Even a clean audit still needs
an independent kernel replay before any theorem can be accepted.
"""
from dataclasses import dataclass
import json
import re

from rds_core import canonical, object_digest

KINDS = frozenset({'CONJECTURE', 'LEMMA', 'THEOREM'})
STANDARD_AXIOMS = frozenset({'propext', 'Quot.sound', 'Classical.choice'})
MAX_BYTES = 65536
HASH = re.compile(r'[0-9a-f]{64}')
FIELDS = frozenset({'schema_version', 'kind', 'theorem', 'informal_statement',
                    'formal_statement', 'source_sha256'})
AUDIT_FIELDS = frozenset({'schema_version', 'obligation_sha256', 'source_sha256',
                          'theorem', 'declaration_kind', 'axioms', 'remaining_goals',
                          'errors', 'warnings'})


def _require(value, message):
    if not value:
        raise ValueError(message)


def _text(value, limit):
    return isinstance(value, str) and bool(value.strip()) and len(value) <= limit


@dataclass(frozen=True, slots=True)
class ProofObligation:
    """Immutable declaration intent; creating it grants no execution authority."""
    _json: str

    @classmethod
    def from_dict(cls, value):
        _require(isinstance(value, dict) and set(value) == FIELDS,
                 'Proof obligation requires exactly the versioned declaration fields')
        _require(type(value['schema_version']) is int and value['schema_version'] == 1,
                 'Unsupported proof obligation schema_version')
        _require(isinstance(value['kind'], str) and value['kind'] in KINDS,
                 'Declaration kind must be CONJECTURE, LEMMA or THEOREM')
        _require(_text(value['theorem'], 512) and '\x00' not in value['theorem'],
                 'Theorem name must be bounded text')
        for field in ('informal_statement', 'formal_statement'):
            _require(_text(value[field], 16384), field + ' must be nonempty bounded text')
        _require(isinstance(value['source_sha256'], str) and HASH.fullmatch(value['source_sha256']),
                 'source_sha256 must bind the exact proposed source bytes')
        raw = canonical(value)
        _require(len(raw.encode('utf-8')) <= MAX_BYTES, 'Proof obligation exceeds byte limit')
        return cls(raw)

    def to_dict(self):
        return json.loads(self._json)

    @property
    def sha256(self):
        return object_digest(self.to_dict())


def review_audit(obligation, audit=None):
    """Reject invalid proof claims; never promote even a self-consistent audit.

    Only conjectures can retain unfinished proof bodies. This function does not
    execute Lean, validate source semantics or register a kernel-checked claim.
    """
    obligation = ProofObligation.from_dict(
        obligation.to_dict() if isinstance(obligation, ProofObligation) else obligation)
    request = obligation.to_dict()
    result = {'schema_version': 1, 'obligation_sha256': obligation.sha256,
              'kind': request['kind'], 'theorem': request['theorem'],
              'status': 'PROOF_REQUIRED', 'assurance': 'NONE',
              'scientific_support': 'UNKNOWN', 'usable_as_proved_premise': False,
              'formal_alignment': 'NOT_CHECKED', 'reasons': []}
    if audit is None:
        if request['kind'] == 'CONJECTURE':
            result['status'] = 'CONJECTURE'
            result['reasons'] = ['Unproved conjecture retained; not a proved premise']
        else:
            result['reasons'] = ['An independently checked proof is required']
        return result
    _require(isinstance(audit, dict) and set(audit) == AUDIT_FIELDS,
             'Unsupported or incomplete Lean audit fields')
    _require(type(audit['schema_version']) is int and audit['schema_version'] == 1,
             'Unsupported Lean audit schema_version')
    _require(len(canonical(audit).encode('utf-8')) <= MAX_BYTES, 'Lean audit exceeds byte limit')
    for key in ('axioms', 'remaining_goals', 'errors', 'warnings'):
        _require(isinstance(audit[key], list) and len(audit[key]) <= 256 and
                 all(_text(item, 4096) for item in audit[key]), key + ' must be bounded text records')
    _require(isinstance(audit['declaration_kind'], str) and
             audit['declaration_kind'] in {'theorem', 'axiom', 'definition', 'unknown'},
             'Unknown Lean declaration kind')
    bindings = {'obligation_sha256': obligation.sha256,
                'source_sha256': request['source_sha256'], 'theorem': request['theorem']}
    if any(audit[key] != value for key, value in bindings.items()):
        return {**result, 'status': 'REJECTED', 'reasons': ['STALE or mismatched proof audit binding']}
    if request['kind'] == 'CONJECTURE':
        return {**result, 'status': 'CONJECTURE',
                'reasons': ['Draft placeholders are allowed only as unproved conjecture; no promotion']}
    reasons = []
    if audit['declaration_kind'] != 'theorem':
        reasons.append('A lemma or theorem cannot be replaced by an axiom or definition')
    unexpected = sorted(set(audit['axioms']) - STANDARD_AXIOMS)
    if unexpected:
        reasons.append('Unapproved axiom dependencies: ' + ', '.join(unexpected))
    if audit['remaining_goals']:
        reasons.append('Unfinished proof goals remain')
    if audit['errors'] or audit['warnings']:
        reasons.append('Lean errors or warnings require resolution')
    if reasons:
        return {**result, 'status': 'REJECTED', 'reasons': reasons}
    return {**result, 'status': 'REPLAY_REQUIRED',
            'reasons': ['Audit preflight is clean, but untrusted JSON cannot establish a proof; '
                        'independent kernel replay and formal-statement alignment are required']}
