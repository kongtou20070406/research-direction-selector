"""Opt-in, evidence-conditioned generation over existing structure records.

Slots bound proposal preparation, not model tokens, worker reservations or truth.
All feedback supplied here has been replayed by rds_structure against originals.
"""
from copy import deepcopy
from fractions import Fraction
import hashlib
import math
import re

from rds_project import canonical, digest, require
from rds_artifacts import strict_json
from rds_discrimination import hypothesis_key

POLICY_PATH = 'structure-search.json'
STRATEGY = 'bounded_feedback_v1'


def validate(policy):
    require(isinstance(policy, dict) and set(policy) == {
        'schema', 'strategy', 'metric', 'baseline_proposal_id', 'slots', 'min_repeats'},
        'Search policy requires schema, strategy, metric, baseline_proposal_id, slots and min_repeats')
    require(type(policy['schema']) is int and policy['schema'] == 1
            and policy['strategy'] == STRATEGY, 'Unsupported search allocation policy')
    require(isinstance(policy['baseline_proposal_id'], str) and
            re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', policy['baseline_proposal_id']),
            'Search policy requires a predeclared baseline proposal ID')
    metric = policy['metric']
    require(isinstance(metric, dict) and set(metric) == {'name', 'pointer', 'unit', 'direction', 'min_improvement'},
            'Search metric requires name, pointer, unit, direction and min_improvement')
    require(all(isinstance(metric[k], str) and 0 < len(metric[k]) <= 128 for k in ('name', 'unit')),
            'Invalid search metric name/unit')
    pointer = metric['pointer']
    require(isinstance(pointer, str) and pointer.startswith('/') and len(pointer) <= 2048
            and len(pointer.split('/')) <= 33
            and all(p[:1] in ('0', '1') for s in pointer.split('/') for p in s.split('~')[1:]),
            'Invalid search measurement pointer')
    require(metric['direction'] in ('min', 'max'), 'Search direction must be min or max')
    delta = metric['min_improvement']
    require(isinstance(delta, str) and re.fullmatch(r'[0-9]{1,32}(?:/[1-9][0-9]{0,31})?', delta),
            'Search minimum improvement must be a bounded nonnegative rational string')
    require(Fraction(delta) > 0, 'Search minimum improvement must be positive')
    slots = policy['slots']
    require(isinstance(slots, dict) and set(slots) == {'explore', 'evidence', 'refine'}
            and all(type(v) is int and 1 <= v <= 8 for v in slots.values()) and sum(slots.values()) <= 16,
            'Declare 1..8 slots per kind and at most 16 total; exploration/evidence floors are required')
    require(type(policy['min_repeats']) is int and 2 <= policy['min_repeats'] <= 8,
            'Search refinement requires 2..8 distinct repeated experiments')
    return policy


def load_policy(store, state):
    bindings = [b for b in state['contract']['bindings'] if b['path'] == POLICY_PATH]
    if not bindings:
        return None
    require(len(bindings) == 1 and bindings[0]['role'] == 'config',
            'structure-search.json must be a unique frozen config binding')
    path = store._path(POLICY_PATH)
    require(path.stat().st_size <= 16384, 'Search policy exceeds 16 KiB')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == bindings[0]['sha256'], 'Search policy binding changed')
    return validate(strict_json(raw.decode('utf-8-sig')))


def experiment_key(row):
    """Only declared identical interventions count as repeated observations.

    Output filenames may differ. Arbitrary argument aliases are not inferred.
    """
    run = row['proposal']['experiment']['runs'][0]
    outputs = run['outpaths']
    return digest({'hypothesis': hypothesis_key(row['discriminator']),
                   'argv': ['<output:' + str(outputs.index(a)) + '>' if a in outputs else a for a in run['argv']],
                   'protocol': run['protocol']})


def _measurement(policy, row, observed):
    measured = observed.get('discrimination', {}).get('measured')
    discriminator = row.get('discriminator')
    if observed.get('observation') not in ('SUPPORT', 'REFUTE') or not discriminator or not measured:
        return None, None, 'UNKNOWN_OR_MISSING_MEASUREMENT'
    definition = discriminator['measurement']
    metric = policy['metric']
    if definition['name'] != metric['name'] or definition['pointer'] != metric['pointer']:
        return None, None, 'INCOMPARABLE_MEASUREMENT_DEFINITION'
    value = measured.get('value')
    if not (type(value) is int and value.bit_length() <= 1024 or type(value) is float and math.isfinite(value)):
        return None, None, 'NONNUMERIC_MEASUREMENT'
    verifier = row['proposal']['experiment']['runs'][1]
    candidate = row['proposal']['experiment']['runs'][0]
    locators = {row['id']: '<proposal-id>', candidate['id']: '<candidate-run-id>',
                row['proposal']['experiment']['candidate_output']: '<candidate-output>'}
    locators.update({path: '<verifier-output:' + str(i) + '>' for i, path in enumerate(verifier['outpaths'])})
    # One frozen scope binds the evaluator/code/data; protocol also binds split,
    # measurement schedule and numeric conditions. Only declared result locators
    # and standalone experiment IDs are normalized; evaluator options stay bound.
    comparable = digest({'scope': row['scope_sha256'], 'metric': metric,
                         'conditions': sorted(discriminator['conditions'], key=lambda x: x['path']),
                         'protocol': verifier['protocol'],
                         'evaluator_argv': [locators.get(a, a) for a in verifier['argv']]})
    return Fraction(value), comparable, None


def build(policy, rows, feedback, state, *, scope_feedback=None):
    """Pure bounded heuristic: conservative repeated gain versus frozen baseline."""
    if policy is None:
        return None
    # Positive allocation is goal-local, but REFUTE has the same hypothesis
    # scope as the existing admission gate, independent of candidate command.
    hypotheses = {ident: hypothesis_key(row['discriminator']) for ident, row in rows.items()
                  if row.get('discriminator')}
    refutations = [{'hypothesis_sha256': hypotheses[r['id']], 'proposal_id': r['id'],
                    'feedback_sha256': digest(r), 'scope_sha256': rows[r['id']]['scope_sha256'],
                    'receipts': list(r['receipts'])}
                   for r in (feedback if scope_feedback is None else scope_feedback)
                   if r.get('observation') == 'REFUTE' and r['id'] in hypotheses]
    refuted = {r['hypothesis_sha256'] for r in refutations}

    def parent_eligible(ident):
        return ident in hypotheses and hypotheses[ident] not in refuted

    evidence, usable, by_id = [], {}, {}
    for observed in feedback:
        row = rows[observed['id']]
        value, identity, reason = _measurement(policy, row, observed)
        entry = {'proposal_id': observed['id'], 'feedback_sha256': digest(observed),
                 'observation': observed.get('observation'), 'reason': reason,
                 'measured': deepcopy(observed.get('discrimination', {}).get('measured')),
                 'comparison_sha256': identity, 'improvement': None,
                 'scope_sha256': row['scope_sha256'], 'receipts': list(observed['receipts']),
                 'claim_assurance': 'SCOPED_MEASUREMENT_NOT_SCIENTIFIC_SUPPORT'}
        evidence.append(entry)
        by_id[row['id']] = entry
        if value is not None:
            usable[row['id']] = (value, identity)
    baseline_id = policy['baseline_proposal_id']
    baseline = usable.get(baseline_id)
    groups = {}
    for ident, (value, identity) in usable.items():
        entry = by_id[ident]
        if ident == baseline_id:
            entry['reason'] = 'PREDECLARED_BASELINE'
            continue
        if baseline is None:
            entry['reason'] = 'BASELINE_UNAVAILABLE'
            continue
        if identity != baseline[1]:
            entry['reason'] = 'INCOMPARABLE_BASELINE'
            continue
        gain = baseline[0] - value if policy['metric']['direction'] == 'min' else value - baseline[0]
        entry['improvement'] = str(gain)
        entry['reason'] = 'MEASURED_IMPROVEMENT' if gain >= Fraction(policy['metric']['min_improvement']) else 'NO_USEFUL_IMPROVEMENT'
        if entry['observation'] == 'REFUTE':
            entry['reason'] = 'SCOPED_HYPOTHESIS_REFUTED'
            continue
        key = experiment_key(rows[ident])
        groups.setdefault(key, []).append((gain, ident))
    excluded = {experiment_key(rows[e['proposal_id']]) for e in evidence
                if e['observation'] in ('UNKNOWN', 'REFUTE') and rows[e['proposal_id']].get('discriminator')}
    qualified, promising = [], []
    for key, measurements in groups.items():
        worst = min(gain for gain, _ in measurements)
        # Count original candidate runs, not renamed proposals sharing receipts.
        runs = {rows[ident]['proposal']['experiment']['runs'][0]['id'] for _, ident in measurements}
        parent = measurements[-1][1]
        if worst >= Fraction(policy['metric']['min_improvement']) and key not in excluded and parent_eligible(parent):
            target = qualified if len(runs) >= policy['min_repeats'] else promising
            target.append((worst, key, parent, len(runs)))
    qualified.sort(key=lambda x: (-x[0], x[1]))
    promising.sort(key=lambda x: (-x[0], x[1]))
    selected_parent = qualified[0][2] if qualified else None
    unknowns = [e['proposal_id'] for e in evidence if e['observation'] == 'UNKNOWN'
                and parent_eligible(e['proposal_id'])]
    evidence_parent = (unknowns[-1] if unknowns else promising[0][2] if promising
                       else selected_parent or (baseline_id if baseline and by_id[baseline_id]['observation'] == 'SUPPORT'
                                                and parent_eligible(baseline_id) else None))
    slots, withheld = [], []
    for kind, parent in [('explore', None), ('evidence', evidence_parent), ('refine', selected_parent)]:
        for _ in range(policy['slots'][kind]):
            if kind != 'explore' and parent is None:
                withheld.append({'kind': kind, 'reason': 'NO_EVIDENCE_TARGET' if kind == 'evidence' else 'NO_ELIGIBLE_REPEATED_COMPARABLE_IMPROVEMENT'})
                continue
            source = by_id.get(parent)
            slots.append({'slot': len(slots), 'kind': kind, 'parent_proposal_id': parent,
                          'trigger': {'proposal_id': parent, 'feedback_sha256': source['feedback_sha256'],
                                      'observation': source['observation'],
                                      'purpose': 'EVIDENCE' if kind == 'evidence' else 'ALTERNATIVE'} if source else None})
    result = {'schema': 1, 'strategy': STRATEGY, 'assurance': 'HEURISTIC',
              'policy_sha256': digest(policy), 'baseline_proposal_id': baseline_id,
              'selected_parent': selected_parent, 'slots': slots, 'withheld': withheld,
              'limits': deepcopy(policy['slots']), 'comparisons': evidence, 'scoped_refutations': refutations,
              'qualified': [{'experiment_sha256': key, 'parent_proposal_id': parent,
                             'distinct_runs': count, 'worst_improvement': str(gain)}
                            for gain, key, parent, count in qualified],
              'budget': deepcopy(state['budget']), 'resources_reserved': False,
              'instruction': 'Use one slot per proposal. Explore may introduce unlisted concepts. Evidence repeats or diagnoses the bound parent. '
                             'Refine starts from the selected original result. New constraints remain proposals; original gates and total budget apply.',
              'scientific_support': 'UNKNOWN', 'research_policy_gain_measured': False}
    return {**result, 'sha256': digest(result)}


def validate_reply(store, req, proposal, state, feedback, policy):
    allocation = req.get('search_allocation')
    if policy is None:
        require('search' not in proposal, 'Search slots require a frozen search policy')
        return None
    require(allocation is not None and allocation['policy_sha256'] == digest(policy), 'Search policy/request mismatch')
    require(allocation['sha256'] == digest({k: v for k, v in allocation.items() if k != 'sha256'}),
            'Search allocation identity changed')
    require(req['receipts'] == [r['sha256'] for r in state['receipts']]
            and req['feedback_sha256'] == [digest(r) for r in feedback],
            'Stale search evidence; request current feedback before generating a proposal')
    search = proposal.get('search')
    require(isinstance(search, dict) and set(search) == {'slot'} and type(search['slot']) is int,
            'Search proposal must name one integer slot')
    slot = next((s for s in allocation['slots'] if s['slot'] == search['slot']), None)
    require(slot is not None, 'Unknown or withheld search slot')
    if slot['trigger'] is not None:
        require(proposal.get('trigger') == slot['trigger'], 'Search proposal must consume its exact allocated parent feedback')
    return {**deepcopy(slot), 'allocation_sha256': allocation['sha256'], 'policy_sha256': digest(policy)}


def claim(events, row, read_ref):
    """Called inside the existing structure insertion transaction."""
    search = row.get('search_allocation')
    if search is None:
        return
    for event in events:
        if event['kind'] != 'STRUCTURE_PROPOSAL':
            continue
        old = read_ref(event['record'])
        if old['request_id'] == row['request_id'] and old.get('search_allocation', {}).get('slot') == search['slot']:
            raise ValueError('Search slot already consumed by proposal ' + old['id'])
