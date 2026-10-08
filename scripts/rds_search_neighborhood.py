"""Discrete preparation-lineage feedback, not a derivative or causal estimate.

All inputs come from search_allocation after replay of original feedback. No
persisted controller state: original proposals, receipts and policy own history.
"""
from fractions import Fraction

from rds_project import digest
from rds_search_allocation import experiment_key


def review(policy, rows, evidence, usable, eligible):
    by_id = {e['proposal_id']: e for e in evidence}
    keys = {i: experiment_key(rows[i]) for i in by_id if rows[i].get('discriminator')}
    baseline = policy['baseline_proposal_id']
    threshold = Fraction(policy['metric']['min_improvement'])
    anchors, local_steps = {}, {}

    def anchor(ident, visiting):
        if ident in anchors:
            return anchors[ident]
        if ident not in by_id or ident in visiting:
            return None
        allocation = rows[ident].get('search_allocation', {})
        parent = allocation.get('parent_proposal_id')
        kind = allocation.get('kind')
        if kind == 'refine':
            result = parent
            local_steps[ident] = True
        elif kind == 'evidence':
            result = (anchor(parent, visiting | {ident}) if parent in keys
                      and keys.get(ident) == keys[parent] else None)
            local_steps[ident] = local_steps.get(parent, False)
        else:
            # An ALTERNATIVE trigger alone does not establish a neighborhood.
            result = allocation.get('comparison_parent_proposal_id', baseline)
            local_steps[ident] = 'comparison_parent_proposal_id' in allocation
        anchors[ident] = result
        return result

    groups, directions = {}, []
    for entry in evidence:
        ident = entry['proposal_id']
        parent = anchor(ident, set())
        source = by_id.get(parent)
        local = local_steps.get(ident, False)
        item = {'proposal_id': ident, 'parent_proposal_id': parent,
                'basis': 'DECLARED_LINEAGE' if local else 'PREDECLARED_BASELINE',
                'baseline_improvement': entry['improvement'], 'parent_improvement': None,
                'parent_feedback_sha256': source['feedback_sha256'] if source else None,
                'feedback_sha256': entry['feedback_sha256'], 'reason': 'UNKNOWN_COMPARISON'}
        directions.append(item)
        if ident == baseline:
            item['reason'] = 'PREDECLARED_BASELINE'
            continue
        if ident not in usable or parent not in usable or parent not in keys or ident not in keys:
            continue
        if usable[ident][1] != usable[parent][1]:
            item['reason'] = 'INCOMPARABLE_PARENT'
            continue
        gain = usable[parent][0] - usable[ident][0]
        if policy['metric']['direction'] == 'max':
            gain = -gain
        item.update(parent_improvement=str(gain), reason='OBSERVED_LOCAL_GAIN' if gain >= threshold else 'NO_USEFUL_LOCAL_GAIN')
        key = digest({'parent': keys[parent], 'child': keys[ident], 'comparison': usable[ident][1]})
        groups.setdefault(key, []).append((ident, parent, gain, local))

    # Any unresolved/incompatible repeat prevents a favorable or stagnant label.
    unresolved = {keys[i] for i in keys if i not in usable}
    qualified, promising, trials, diagnosis = [], [], [], []
    positions = {e['proposal_id']: i for i, e in enumerate(evidence)}
    for key, samples in groups.items():
        ident, parent, _, local = samples[-1]
        child_key = keys[ident]
        runs = {rows[i]['proposal']['experiment']['runs'][0]['id'] for i, _, _, _ in samples}
        gains = [gain for _, _, gain, _ in samples]
        comparable_repeats = all(i in usable and usable[i][1] == usable[ident][1]
                                 for i in keys if keys[i] in (child_key, keys[parent]))
        parent_values = [usable[i][0] for i in keys if keys[i] == keys[parent]
                         and i in usable and usable[i][1] == usable[ident][1]]
        child_values = [usable[i][0] for i in keys if keys[i] == child_key
                        and i in usable and usable[i][1] == usable[ident][1]]
        conservative_gain = (min(parent_values) - max(child_values) if policy['metric']['direction'] == 'min'
                             else min(child_values) - max(parent_values))
        possible_gain = (max(parent_values) - min(child_values) if policy['metric']['direction'] == 'min'
                         else max(child_values) - min(parent_values))
        certain = child_key not in unresolved and keys[parent] not in unresolved and comparable_repeats
        good = certain and all(eligible(i) and by_id[i]['observation'] == 'SUPPORT' for i, _, _, _ in samples)
        if conservative_gain >= threshold and good:
            target = qualified if len(runs) >= policy['min_repeats'] else promising
            target.append((conservative_gain, key, ident, len(runs)))
        if local and eligible(ident) and (len(runs) < policy['min_repeats'] or not certain
                                         or conservative_gain < threshold <= possible_gain):
            diagnosis.append(ident)
        if local and certain and len(runs) >= policy['min_repeats']:
            # Mixed threshold-crossing observations remain unresolved, not failure.
            outcome = ('GAIN' if conservative_gain >= threshold and good
                       else 'STAGNANT' if possible_gain < threshold else 'UNKNOWN')
            trials.append({'parent_experiment_sha256': keys[parent], 'child_experiment_sha256': child_key,
                           'proposal_ids': [i for i, _, _, _ in samples], 'outcome': outcome,
                           'distinct_runs': len(runs)})

    # A limited set of distinct, repeatedly unproductive interventions pauses
    # this anchor only. A new observed gain or different anchor can reopen it.
    paused = []
    for parent_key in sorted({t['parent_experiment_sha256'] for t in trials}):
        current = sorted((t for t in trials if t['parent_experiment_sha256'] == parent_key),
                         key=lambda t: max(positions[i] for i in t['proposal_ids']))
        stagnant = set()
        for trial in current:
            if trial['outcome'] != 'STAGNANT':
                stagnant.clear()
            else:
                run = rows[trial['proposal_ids'][-1]]['proposal']['experiment']['runs'][0]
                # Renaming a hypothesis cannot mint a distinct computation.
                stagnant.add(digest({'argv': ['<output:' + str(run['outpaths'].index(a)) + '>'
                                             if a in run['outpaths'] else a for a in run['argv']],
                                     'protocol': run['protocol']}))
        if len(stagnant) >= policy['stagnation_trials']:
            paused.append(parent_key)
    qualified = [q for q in qualified if keys[q[2]] not in paused]
    qualified.sort(key=lambda x: (-x[0], x[1]))
    promising.sort(key=lambda x: (-x[0], x[1]))
    return qualified, promising, {'comparisons': directions, 'trials': trials, 'paused_parents': paused,
        'evidence_parent': max(diagnosis, key=positions.get) if diagnosis else None,
        'stagnation_trials': policy['stagnation_trials'], 'step_semantics': 'PROPOSAL_ALLOCATION_NOT_GRAPH_DISTANCE',
        'assurance': 'OBSERVED_DIFFERENCES_NOT_CAUSAL_OR_STATISTICAL_GRADIENT'}
