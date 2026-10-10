"""Optional continuation view over owned advice and verified existing evidence.

This projection has no selector, execution permission, model call or memory DB.
Large details stay in the existing CAS. Structure reads never use metered APIs.
"""
from copy import deepcopy

from rds_project import digest, canonical, require
from rds_quick import cas_json

ITEM_LIMIT = 4
DETAIL_BYTES = 2048


def _compact(store, value):
    if len(canonical(value).encode('utf-8')) <= DETAIL_BYTES:
        return deepcopy(value)
    # Do not silently cut predicates or exact identities into altered meanings.
    identity = {k: value[k] for k in ('id', 'status', 'observation', 'feedback_sha256',
                'proposal_sha256', 'hypothesis_key', 'selected_run') if isinstance(value, dict) and k in value}
    result = {**identity, 'details_omitted': True, 'original': cas_json(store.root, value)}
    for key in ('original_observation', 'goal_status', 'trigger', 'allowed_trigger_purposes',
                'blocked_hypothesis_key', 'discriminator', 'next_move', 'selection_basis', 'summary', 'comparison'):
        if isinstance(value, dict) and key in value:
            candidate = {**result, key: deepcopy(value[key])}
            if len(canonical(candidate).encode('utf-8')) <= DETAIL_BYTES:
                result = candidate
    return result


def _section(store, rows):
    result = {'items': [_compact(store, row) for row in rows[:ITEM_LIMIT]],
              'total': len(rows), 'omitted': max(0, len(rows) - ITEM_LIMIT)}
    if result['omitted']:
        result['original'] = cas_json(store.root, rows)
    return result


def _structure(store, state, saved):
    from rds_structure import (_binding_scope, _events, _find,
                               _verified_feedback, _feedback_view, _route_constraint)
    from rds_discrimination import hypothesis_key
    events = _events(store)
    scope = digest(_binding_scope({'contract_sha256': digest(state['contract']), **state}, saved))
    observed = _verified_feedback(store, scope)  # Replays original measurements and hashes.
    feedback_ids = {r['id'] for r in observed}
    receipts = {r['run_id']: r for r in state['receipts']}
    refs = {(e['kind'], e.get('id')): e.get('record') for e in events}
    feedback, hypotheses = [], []
    for original in reversed(observed):
        proposal = _find(store, 'PROPOSAL', original['id'])
        effective = _feedback_view(proposal, original)
        value = proposal.get('discriminator')
        observation = effective.get('observation', 'UNKNOWN')
        originals = []
        for manifest in proposal['proposal']['experiment']['runs']:
            receipt = receipts.get(manifest['id'])
            if receipt:
                originals.extend({'run_id': manifest['id'], 'receipt_sha256': receipt['sha256'],
                                  **{k: a[k] for k in ('path', 'sha256', 'size') if k in a}}
                                 for a in receipt['artifacts'] if a.get('kind') == 'project_output')
        feedback.append({'id': original['id'], 'scope_sha256': scope,
            'feedback_sha256': digest(original), 'record': refs['STRUCTURE_FEEDBACK', original['id']],
            'observation': observation, 'original_observation': original.get('observation'),
            'status': effective['status'], 'original_status': original['status'],
            'goal_status': effective['goal_status'], 'reason': effective.get('reason'),
            'discriminator': value, 'measurement': original.get('discrimination'),
            'originals': originals, 'next_decision': effective['next_decision'],
            'trigger': {'proposal_id': original['id'], 'feedback_sha256': digest(original),
                        'observation': observation},
            'allowed_trigger_purposes': ['EVIDENCE'] if observation == 'UNKNOWN' else ['ALTERNATIVE', 'EVIDENCE'],
            'blocked_hypothesis_key': hypothesis_key(value) if value and observation == 'REFUTE' else None})
    outside_scope = 0
    for event in events:
        if event['kind'] != 'STRUCTURE_PROPOSAL':
            continue
        row = _find(store, 'PROPOSAL', event['id'])
        if row['scope_sha256'] != scope:
            outside_scope += 1
            continue
        effective = next((r for r in feedback if r['id'] == row['id']), None)
        if row['id'] in feedback_ids and effective['observation'] != 'UNKNOWN':
            continue
        value = row.get('discriminator')
        hypotheses.append({'id': row['id'], 'proposal_sha256': row['proposal_sha256'],
            'record': event['record'], 'discriminator': value,
            'hypothesis_key': hypothesis_key(value) if value else None,
            'observation': effective['observation'] if effective else 'NOT_MEASURED',
            'constraint': _route_constraint(store, row),
            'unknown_premises': row['proposal'].get('exploration', {}).get('unknown_premises', [])})
    return scope, feedback, hypotheses, outside_scope, digest(events)


def build(store, report):
    """Project only a coherent final report; diagnostics cannot grant admission."""
    from rds_owned_advisor import _state, _fingerprint
    from rds_tms_store import current
    source = cas_json(store.root, report)
    result = {'schema': 1, 'status': 'UNAVAILABLE', 'source_report': source,
              'authorization': 'UNCHANGED', 'scientific_support': 'UNKNOWN',
              'research_benefit': 'UNMEASURED', 'execution_started': False,
              'limits': {'items_per_section': ITEM_LIMIT, 'detail_bytes': DETAIL_BYTES}}
    try:
        require(report.get('status') == 'REVIEWED', 'Owned evidence collection is not complete')
        with store._db(True) as db:
            db.execute('BEGIN')
            state = _state(store, db)
        require(_fingerprint(state) == report['fingerprint'], 'Owned report is stale; retry advise')
        saved = current(store.root)
        require(saved is not None and saved['sha256'] == report['snapshot_sha256'],
                'Dependency snapshot changed; retry advise')
        scope, feedback, hypotheses, outside_scope, event_sha = _structure(store, state, saved)
        context = report['context']
        from rds_advisor_search import evaluate_condition
        goals = [{'condition': c, 'evaluation': evaluate_condition(c, context['facts']),
                  'evidence': deepcopy(context['facts'].get(c['fact']))}
                 for c in context['decision']['goal_conditions']]
        unresolved = [{k: node[k] for k in ('id', 'status', 'kind', 'source') if k in node}
                      for node in saved['dependency_map']['nodes'] if node['status'] != 'SUPPORTED']
        history = [flag for r in report['recommendations'] if r.get('type') == 'RESEARCH_LOOP_REVIEW'
                   for flag in r['review']['flags']]
        operational = [{'run_id': r['run_id'], 'receipt_sha256': r['sha256'],
                        'run_status': r['run_status'], 'timeout': r['timeout'],
                        'exit_code': r['exit_code'], 'scientific_support': 'UNKNOWN'}
                       for r in state['receipts']]
        operational.sort(key=lambda r: (r['run_status'] == 'SUCCEEDED', r['run_id']))
        from rds_jump import packet as jump_packet
        jumps = jump_packet(store)
        budget = {b['resource']: {'cap': b['cap'], 'spent_measured': b['spent'],
                  'charged_estimate': b['charged'], 'reserved': b['reserved'],
                  'remaining': b['cap'] - b['spent'] - b['charged'] - b['reserved'],
                  'unit': 'seconds' if b['resource'].endswith('_seconds') else b['resource']}
                  for b in state['budget']}
        # Detect concurrent execution or structure changes after replay. No
        # partial projection is presented as current if this cut moved.
        from rds_structure import _events
        with store._db(True) as db:
            db.execute('BEGIN')
            require(_fingerprint(_state(store, db)) == report['fingerprint'], 'Owned state changed; retry advise')
            require(digest(_events(store, db)) == event_sha, 'Structure evidence changed; retry advise')
        require(current(store.root)['sha256'] == saved['sha256'], 'Dependency snapshot changed; retry advise')
        result.update(status='CURRENT', scope=_compact(store, {
            'decision': context['decision'], 'contract_sha256': digest(state['contract']),
            'scope_sha256': scope, 'snapshot_sha256': saved['sha256'], 'fingerprint': report['fingerprint']}),
            selection=_compact(store, {k: report.get(k) for k in
                            ('status', 'selected_run', 'next_move', 'selection_basis')}),
            budget=_compact(store, budget), goals=_section(store, goals),
            unresolved_hypotheses=_section(store, hypotheses), unresolved_dependencies=_section(store, unresolved),
            scoped_feedback=_section(store, feedback), route_review=_section(store, history),
            operational_results=_section(store, operational), outside_scope_proposals=outside_scope,
            shadow_plan=_compact(store, report.get('shadow_plan', {})),
            continuation={'command': 'project next', 'structure_command': 'structure request',
                'instruction': 'Consume final owned next_move. For structure exploration, use exact feedback trigger and declared conditions; UNKNOWN requires EVIDENCE. Reopen route review only through existing goal/scope/relevant-evidence gates. Read omitted originals before a consequential choice.'})
        if jumps is not None:
            result['jump_packet'] = jumps
    except (ValueError, KeyError, TypeError, OSError) as exc:
        result['diagnostic'] = str(exc)[:512]
    return result
