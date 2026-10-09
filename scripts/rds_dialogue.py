"""Opt-in, read-only conversation view of retained owned decisions.

No search, collection, selector or scientific promotion runs here. A compatible
recorded selection still needs ordinary admission checks before dispatch.
"""
from copy import deepcopy
import hashlib
import sqlite3

from rds_project import canonical, require
from rds_source_documents import strict_json

ITEMS = 4
DETAIL_BYTES = 2048


def _detail(value, source, pointer):
    if len(canonical(value).encode('utf-8')) <= DETAIL_BYTES:
        return deepcopy(value)
    identity = {k: deepcopy(value[k]) for k in ('candidate', 'run_id', 'selected_run', 'status', 'is_recorded_selection', 'kind')
                if isinstance(value, dict) and k in value}
    locator = value.get('evidence_locator', {}).get('locator', pointer) if isinstance(value, dict) else pointer
    return {**identity, 'details_omitted': True, 'original': source, 'locator': locator}


def _section(rows, source, pointer):
    return {'items': [_detail(row, source, pointer + '/' + str(i))
                      for i, row in enumerate(rows[:ITEMS])],
            'total': len(rows), 'omitted': max(0, len(rows) - ITEMS),
            'original': source, 'locator': pointer}


def _report(store, ref):
    """Only the ledger's CAS reference is accepted, never an external JSON path."""
    require(isinstance(ref, dict), 'Owned report reference missing')
    sha = ref.get('sha256')
    require(isinstance(sha, str) and len(sha) == 64 and
            all(c in '0123456789abcdef' for c in sha), 'Owned report hash invalid')
    path = (store.root / ref['path']).resolve()
    require(path.parent == (store.root / '.rds' / 'cas').resolve()
            and path.name == sha + '.json' and path.is_relative_to(store.root),
            'Owned report is outside its project CAS')
    size = ref.get('bytes')
    require(type(size) is int and size > 0, 'Owned report byte count invalid')
    # Reports embed the dependency map plus review metadata; the 8 MiB
    # research-asset cap is not their bound. Verify before allocating and read
    # at most the retained count plus one byte to detect concurrent growth.
    require(path.stat().st_size == size, 'Owned report CAS integrity failure')
    with path.open('rb') as stream:
        raw = stream.read(size + 1)
    require(len(raw) == size and hashlib.sha256(raw).hexdigest() == sha,
            'Owned report CAS integrity failure')
    value = strict_json(raw.decode('utf-8'))
    require(isinstance(value, dict), 'Owned report is not an object')
    return value


def _interaction(plan, contract=None, request_artifact=None):
    steering = plan.get('steering') or {'paused': False, 'withdrawn_runs': [], 'preferred_runs': []}
    routes = [r['manifest']['id'] for r in (contract or {}).get('advisor_policy', {}).get('routes', [])]
    affected = routes if steering['paused'] else sorted(set(steering['withdrawn_runs'] + steering['preferred_runs']))
    kind = steering.get('kind')
    instruction_source = {'command': 'project steering', 'request_artifact': request_artifact}
    affected_source = ({'command': 'project status'} if steering['paused'] else instruction_source)
    affected_pointer = '/contract/advisor_policy/routes' if steering['paused'] else '/steering'
    return {'instruction': _detail(steering, instruction_source, '/steering'),
            'instruction_source': instruction_source,
            'affected_routes': _section(affected, affected_source, affected_pointer),
            'active_work': _section(plan.get('active_work', []), 'project steering', '/active_work'),
            'continuation': ('NEW_DISPATCH_PAUSED' if steering['paused'] else
                             'REVIEW_MATERIAL_REVISION' if kind == 'change_request' else
                             'RETAIN_UNVERIFIED_HYPOTHESIS_AND_DESIGN_CHECK' if kind == 'hypothesis' else
                             'RECONCILE_ORIGINAL_ACTIVE_WORK' if plan.get('active_work') else
                             'CONTINUE_WITH_NORMAL_ADMISSION'),
            'creates_new_approval_gate': False, 'scientific_support': 'UNKNOWN',
            'judgment': 'Host resolves material missing inputs or proposed revisions with the person; retained instructions create no new resource authority.'}


def build(store, draft):
    result = {'schema': 'rds-research-dialogue-v1', 'status': 'NO_CURRENT_ADVICE',
              'interaction': _interaction(draft),
              'goal': _detail(draft.get('goal'), 'project plan', '/goal'),
              'budget': deepcopy(draft.get('budget')), 'missing': deepcopy(draft.get('missing', [])),
              'fixed_task': draft.get('fixed_task'), 'selected_run': None,
              'recommendation': 'UNKNOWN', 'next_move': _detail(draft.get('next_action'), 'project plan', '/next_action'),
              'authorization': 'UNCHANGED', 'execution_started': False,
              'scientific_support': 'UNKNOWN', 'research_benefit': 'UNMEASURED',
              'limits': {'items_per_section': ITEMS, 'detail_bytes': DETAIL_BYTES},
              'admission': 'RECHECK_REQUIRED_BEFORE_DISPATCH'}
    if draft['mode'] == 'NEW_PROJECT':
        result['reason'] = 'No initialized ledger or owned advice. Resolve only material missing inputs; fixed tasks need no invented rivals.'
        return result
    from rds_owned_advisor import _state, _fingerprint, _read_original
    from rds_tms_store import current
    try:
        with store._db(True) as db:
            db.execute('BEGIN')
            state = _state(store, db)
            from rds_steering import current as current_instruction
            retained_instruction = current_instruction(db)
            row = db.execute("SELECT id,body FROM events WHERE json_extract(body,'$.kind')='OWNED_ADVISOR_REVIEW' "
                             "ORDER BY id DESC LIMIT 1").fetchone()
        contract = state['contract']
        decision = contract.get('advisor_policy', {}).get('context', {}).get('decision')
        goal_pointer = ('/contract/advisor_policy/context/decision' if decision else
                        '/contract/description' if contract.get('description') else '/contract/objective_sha256')
        result['goal'] = _detail(draft.get('goal'), 'project status contract ' + draft['contract_sha256'],
                                 goal_pointer)
        # Use the latest coherent ledger state for human steering, even when the
        # saved advice is stale or its original evidence is unavailable.
        from rds_steering import dispositions, view
        instruction = state.get('steering', view(None))
        result['interaction'] = _interaction({'steering': instruction,
            'active_work': dispositions(state['runs'], instruction)}, contract,
            (retained_instruction or {}).get('request_artifact'))
        result['budget'] = {b['resource']: {'cap': b['cap'], 'spent_measured': b['spent'],
            'charged_estimate': b['charged'], 'reserved': b['reserved'],
            'remaining': b['cap'] - b['spent'] - b['charged'] - b['reserved']} for b in state['budget']}
        if row is None:
            result['reason'] = 'No retained owned Advisor report; project next can collect and review within existing authorization.'
            return result
        event = strict_json(row['body'])
        ref = event['report']
        result.update(status='STALE_OR_UNAVAILABLE', source_report=ref, source_event_id=row['id'])
        report = _report(store, ref)
        result['historical_selection'] = {k: _detail(report.get(k), ref, '/' + k) for k in
            ('status', 'selected_run', 'selection_basis', 'next_move', 'fingerprint', 'snapshot_sha256')}
        require(all(event.get(k) == report.get(k) for k in
            ('status', 'fingerprint', 'selected_run', 'snapshot_sha256')), 'Owned report and event identities differ')
        require(report.get('status') == 'REVIEWED',
                'Owned Advisor report is not a completed review: ' + str(report.get('status', 'UNKNOWN')))
        require(_fingerprint(state) == report['fingerprint'],
                'Owned contract, budget, steering or evidence state changed since this report')
        saved = current(store.root)
        require(saved is not None and saved['sha256'] == report['snapshot_sha256'],
                'Owned dependency snapshot changed since this report')
        _, errors = store._bindings(contract)
        require(not errors, 'Frozen input binding changed: ' + '; '.join(errors))
        seen = set()
        for item in report['evidence_files']:
            identity = (item['path'], item['sha256'], item['size'])
            if identity not in seen:
                _read_original(store, item)
                seen.add(identity)
        with store._db(True) as db:
            db.execute('BEGIN')
            require(_fingerprint(_state(store, db)) == report['fingerprint'],
                    'Owned state changed during dialogue read')
            latest = db.execute("SELECT id FROM events WHERE json_extract(body,'$.kind')='OWNED_ADVISOR_REVIEW' "
                                "ORDER BY id DESC LIMIT 1").fetchone()
            require(latest['id'] == row['id'], 'Owned report changed during dialogue read')
            snapshot = db.execute('SELECT sha256 FROM dependency_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()
            require(snapshot['sha256'] == saved['sha256'], 'Dependency snapshot changed during dialogue read')
        searches = [(i, r['search']) for i, r in enumerate(report.get('recommendations', []))
                    if r.get('type') == 'EXECUTABLE_DIRECTION_SEARCH']
        candidates = []
        route_map = {r['candidate']: r for r in contract.get('advisor_policy', {}).get('routes', [])}
        for index, search in searches:
            entries = [(key, i, candidate) for key in ('candidates', 'blocked_candidates')
                       for i, candidate in enumerate(search.get(key, []))]
            for key, candidate_index, candidate in entries:
                action = candidate.get('action', {})
                route = route_map.get(action.get('id', candidate.get('action_id')))
                locator = '/recommendations/' + str(index) + '/search/' + key + '/' + str(candidate_index)
                candidates.append({'candidate': candidate.get('id', candidate.get('rule_id')),
                    'run_id': route['manifest']['id'] if route else None,
                    'status': candidate.get('status', 'UNKNOWN'),
                    'is_recorded_selection': bool(route and route['manifest']['id'] == report.get('selected_run')),
                    'description': action.get('description', 'UNKNOWN'),
                    'explanations': action.get('competing_explanations', 'UNKNOWN'),
                    'observation_to_next_decision': action.get('outcomes', 'UNKNOWN'),
                    'predictions': _detail(candidate.get('discrimination', 'UNKNOWN'), ref,
                        locator + '/discrimination'),
                    'cost': candidate.get('incremental_cost', 'UNKNOWN'),
                    'resource_estimates': route['manifest']['resource_estimates'] if route else 'UNKNOWN',
                    'pending': candidate.get('pending', []), 'dominated_by': candidate.get('dominated_by', []),
                    'evidence_locator': {'report': ref, 'locator': locator}})
        candidates.sort(key=lambda c: not c['is_recorded_selection'])
        # Display one declared decision-distinct alternative early when present.
        # This is presentation only: the owned selector and candidate eligibility
        # retain their original status and order in the saved report.
        if candidates and candidates[0]['is_recorded_selection']:
            outcomes = candidates[0]['observation_to_next_decision']
            decisions = sorted((canonical({'observation': o.get('observation'), 'next_decision': o.get('next_decision')})
                                for o in outcomes)) if isinstance(outcomes, list) else []
            for index, candidate in enumerate(candidates[1:], 1):
                alternative = candidate['observation_to_next_decision']
                other = sorted((canonical({'observation': o.get('observation'), 'next_decision': o.get('next_decision')})
                                for o in alternative)) if isinstance(alternative, list) else []
                if other and other != decisions:
                    candidates.insert(1, candidates.pop(index))
                    break
        result.update(status='COMPATIBLE_RECORDED_ADVICE', selected_run=report.get('selected_run'),
            recommendation='RECORDED_SELECTION' if report.get('selected_run') else 'UNKNOWN',
            selection_basis=report.get('selection_basis', 'UNKNOWN'), next_move=_detail(report.get('next_move'), ref, '/next_move'),
            candidates=_section(candidates, ref, '/recommendations'),
            warnings=_section(report.get('warnings', []), ref, '/warnings'),
            coverage=_detail(report.get('coverage', 'UNKNOWN'), ref, '/coverage'),
            search_scope=_section([{'truncation': search.get('truncation', 'UNKNOWN'),
                'evidence_locator': {'report': ref, 'locator': '/recommendations/' + str(i) + '/search'},
                'selection_review': _detail(search.get('selection_review', {}), ref,
                    '/recommendations/' + str(i) + '/search/selection_review')}
                for i, search in searches], ref, '/recommendations'),
            evidence=_section(report['evidence_files'], ref, '/evidence_files'))
    except (ValueError, KeyError, TypeError, OSError, UnicodeError, sqlite3.Error) as exc:
        result.update(status='STALE_OR_UNAVAILABLE', reason=str(exc)[:512],
                      next_move='Read original report and current steering; request project next to refresh owned advice before a consequential choice.')
    return result
