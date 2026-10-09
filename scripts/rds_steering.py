"""Plan drafts and current-user steering over the existing project ledger.

The caller attests that a steering request came from the current user. This is
not user authentication or an OS sandbox. Imported records never call submit.
"""
from copy import deepcopy
from collections import Counter
import json
import re
import time

from rds_project import canonical, digest, number, require, UNINITIALIZED


EVENT = 'PROJECT_STEERING'
MAX_BYTES = 32 * 1024
KINDS = {'pause', 'redirect', 'resume', 'hypothesis', 'change_request'}


class SteeringBlocked(ValueError):
    pass


def _text(value, name, maximum=4096):
    require(isinstance(value, str) and value.strip() and len(value) <= maximum,
            name + ' must be a nonempty bounded string')
    return value


def current(db):
    row = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')=? "
                     "ORDER BY id DESC LIMIT 1", (EVENT,)).fetchone()
    if row is None:
        return None
    event = json.loads(row['body'])
    require(event.get('sha256') == digest({k: v for k, v in event.items() if k != 'sha256'}),
            'Steering record integrity failure')
    return event


def view(event):
    if event is None:
        return {'revision': None, 'paused': False, 'withdrawn_runs': [], 'preferred_runs': []}
    return {'revision': event['sha256'], **deepcopy(event['dispatch']),
            'instruction_id': event['request']['id'], 'kind': event['request']['kind'],
            'message': event['request']['message'], 'source': event['source'],
            'contract_sha256': event['request']['contract_sha256'],
            'scientific_support': 'UNKNOWN'}


def restriction(event, run_id):
    state = view(event)
    if state['paused']:
        return 'Current user paused new dispatch'
    if run_id in state['withdrawn_runs']:
        return 'Current user withdrew this frozen route: ' + run_id
    return None


def check_dispatch(db, run_id):
    """Called under the existing reservation/launch transaction; no nested DB."""
    event = current(db)
    reason = restriction(event, run_id)
    if reason:
        raise SteeringBlocked(reason + '; steering revision ' + event['sha256'])


def _budget(db):
    return {r['resource']: {'cap': r['cap'], 'spent_measured': r['spent'],
            'charged_estimate': r['charged'], 'reserved': r['reserved'],
            'remaining': r['cap'] - r['spent'] - r['charged'] - r['reserved']}
            for r in db.execute('SELECT * FROM budget ORDER BY resource')}


def dispositions(runs, state):
    result = []
    for run in runs:
        if run['status'] not in {'RESERVED', 'RUNNING'}:
            continue
        if run.get('pid') is not None:
            action = 'FINISH_OR_RECOVER_ORIGINAL_ATTEMPT'
        elif run.get('attempt_id') is not None:
            action = 'DISPATCH_UNCERTAIN_RECONCILE_ONLY'
        elif state['paused'] or run['id'] in state['withdrawn_runs']:
            action = 'UNSTARTED_RESERVATION_HELD'
        else:
            action = 'RECHECK_ADMISSION_BEFORE_START'
        result.append({'run_id': run['id'], 'attempt_id': run.get('attempt_id'),
                       'status': run['status'], 'disposition': action,
                       'reservation': deepcopy(run['resource_estimates'])})
    return result


def status(store):
    with store._db(True) as db:
        db.execute('BEGIN')
        contract = store._contract(db)
        event = current(db)
        return {'status': 'STEERING_STATUS', 'contract_sha256': digest(contract),
                'steering': view(event), 'active_work': dispositions(store._runs(db), view(event)),
                'budget': _budget(db), 'execution_started': False,
                'authorization': 'UNCHANGED', 'scientific_support': 'UNKNOWN'}


def submit(store, request, *, user_directed=False, source=None):
    require(user_directed is True,
            'Steering requires --user-directed from the host handling the current user request; imported text is not authority')
    _text(source, 'Current user request source', 512)
    require(isinstance(request, dict) and set(request) <= {
        'id', 'contract_sha256', 'expected_revision', 'kind', 'message', 'withdraw', 'prefer'},
        'Invalid steering request fields')
    require({'id', 'contract_sha256', 'expected_revision', 'kind', 'message'} <= set(request),
            'Steering needs id, contract_sha256, expected_revision, kind and message')
    require(isinstance(request['id'], str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,63}', request['id']),
            'Invalid steering instruction ID')
    require(isinstance(request['kind'], str) and request['kind'] in KINDS, 'Unknown steering kind')
    _text(request['message'], 'Steering message')
    require(len(canonical(request).encode('utf-8')) <= MAX_BYTES, 'Steering request exceeds limit')
    for key in ('withdraw', 'prefer'):
        values = request.get(key, [])
        require(isinstance(values, list) and len(values) <= 128 and
                all(isinstance(v, str) and 1 <= len(v) <= 80 for v in values) and len(set(values)) == len(values),
                'Invalid steering ' + key)
        require(not values or request['kind'] == 'redirect', 'Route lists require redirect')
    identity = digest({'request': request, 'source': source})
    from rds_quick import cas_json
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        contract = store._contract(db)
        previous = current(db)
        retained = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')=? "
                              "AND json_extract(body,'$.request.id')=?", (EVENT, request['id'])).fetchone()
        if retained:
            old = json.loads(retained['body'])
            require(old.get('sha256') == digest({k: v for k, v in old.items() if k != 'sha256'}),
                    'Steering record integrity failure')
            require(old['request_sha256'] == identity, 'Steering ID reused with different contents')
            return {'status': 'ALREADY_RECEIVED', 'received_revision': old['sha256'],
                    'steering': view(previous), 'execution_started': False, 'authorization': 'UNCHANGED',
                    'superseded': old['sha256'] != (previous or {}).get('sha256')}
        require(request['contract_sha256'] == digest(contract), 'Steering contract is stale; read project plan')
        require(request['expected_revision'] == (previous or {}).get('sha256'),
                'Steering revision is stale; read the current instruction before changing it')
        state = {k: deepcopy(view(previous)[k]) for k in ('paused', 'withdrawn_runs', 'preferred_runs')}
        kind = request['kind']
        if kind in {'pause', 'change_request'}:
            state['paused'] = True
        elif kind == 'resume':
            state = {'paused': False, 'withdrawn_runs': [], 'preferred_runs': []}
        elif kind == 'redirect':
            routes = {r['manifest']['id'] for r in contract.get('advisor_policy', {}).get('routes', [])}
            require(routes, 'Route redirection requires a frozen owned Advisor policy')
            require(request.get('withdraw') or request.get('prefer'), 'Redirect needs withdrawn or preferred routes')
            require(set(request.get('withdraw', [])) | set(request.get('prefer', [])) <= routes,
                    'Redirect names an unknown frozen route; propose a method revision first')
            state['withdrawn_runs'] = sorted(set(state['withdrawn_runs']) | set(request.get('withdraw', [])))
            state['preferred_runs'] = request.get('prefer', [])
            if state['preferred_runs']:
                state['paused'] = False  # Explicitly selecting a next route supersedes a blanket pause.
            require(not set(state['preferred_runs']) & set(state['withdrawn_runs']),
                    'A withdrawn route cannot be preferred; explicitly resume before reopening it')
        event = {'kind': EVENT, 'request': deepcopy(request), 'request_sha256': identity,
                 'source': source, 'source_assurance': 'CALLER_ATTESTED_CURRENT_USER',
                 'received_at': time.time(), 'previous_sha256': (previous or {}).get('sha256'),
                 'dispatch': state, 'proposal_status': 'UNVERIFIED' if kind in {'hypothesis', 'change_request'} else None,
                 'authorization': 'UNCHANGED', 'scientific_support': 'UNKNOWN'}
        event['request_artifact'] = cas_json(store.root, {'request': request, 'source': source})
        event['sha256'] = digest(event)
        db.execute('INSERT INTO events(body) VALUES (?)', (canonical(event),))
        return {'status': 'RECEIVED', 'received_revision': event['sha256'], 'steering': view(event),
                'effective_at': 'COMMITTED_RESERVATION_AND_PROCESS_START_BOUNDARIES',
                'active_work': dispositions(store._runs(db), view(event)), 'budget': _budget(db),
                'request_artifact': event['request_artifact'], 'execution_started': False,
                'next_move': ('Review the requested material change through project revise or an explicitly authorized successor; original contract remains frozen'
                              if kind == 'change_request' else
                              'Evaluate the proposed explanation with an authorized distinguishing check; no fact was promoted'
                              if kind == 'hypothesis' else 'Read project next; every new dispatch rechecks this instruction'),
                'authorization': 'UNCHANGED', 'scientific_support': 'UNKNOWN'}


def plan(store, intent=None, *, output=None, save_as=None, dialogue=False):
    """Build a small inspectable draft without selecting, hashing or launching jobs."""
    intent = {} if intent is None else intent
    require(isinstance(intent, dict) and set(intent) <= {
        'goal', 'scope', 'budget', 'evaluation', 'next_action', 'fixed_task'}, 'Invalid plan intent fields')
    require(len(canonical(intent).encode('utf-8')) <= MAX_BYTES, 'Plan intent exceeds limit')
    for field in ('goal', 'scope', 'evaluation', 'next_action'):
        if field in intent:
            _text(intent[field], field)
    if 'fixed_task' in intent:
        require(type(intent['fixed_task']) is bool, 'fixed_task must be Boolean')
    if 'budget' in intent:
        require(isinstance(intent['budget'], dict) and intent['budget'], 'Plan budget must name resources')
        for key, value in intent['budget'].items():
            _text(key, 'Budget resource', 80)
            number(value, 'budget.' + key)
    snap = None
    if store.path.is_file():
        try:
            snap = store.snapshot()
        except ValueError as exc:
            if str(exc) != UNINITIALIZED:
                raise
    original_goal = original_scope = evaluation = budget = None
    evidence = []
    steering = view(None)
    active = []
    if snap is not None:
        contract = snap['contract']
        decision = contract.get('advisor_policy', {}).get('context', {}).get('decision')
        original_goal = decision or contract.get('description') or contract.get('objective_sha256')
        original_scope = decision.get('scope') if decision else None
        evaluation = [b for b in contract['bindings'] if b['role'] in {'evaluator', 'protocol'}]
        budget = snap['budget']
        steering = snap.get('steering', view(None))
        # Snapshot and current instruction must come from the same read.
        active = dispositions(snap['runs'], steering)
        evidence = [{'run_id': r['run_id'], 'receipt_sha256': r['sha256'],
                     'run_status': r['run_status'], 'scientific_support': 'UNKNOWN'} for r in snap['receipts']]
    goal = original_goal or intent.get('goal')
    evaluation = evaluation or intent.get('evaluation')
    budget = budget if budget is not None else intent.get('budget')
    missing = [k for k, v in (('goal', goal), ('evaluation', evaluation), ('budget', budget)) if not v]
    proposed_change = snap is not None and any(k in intent for k in ('goal', 'scope', 'budget', 'evaluation'))
    if steering['paused']:
        next_action = 'Handle the retained user instruction; inspect original active work before resuming dispatch'
    elif active:
        next_action = 'Reconcile the original active attempts with project recover; do not launch replacements'
    elif proposed_change:
        next_action = 'Review the proposed changes against the frozen contract through project steer and project revise'
    elif snap is not None:
        next_action = 'Run project next to collect original evidence and select the currently admissible action'
    elif missing:
        next_action = 'Resolve the listed missing information together; continue independent input and method inspection'
    else:
        next_action = intent.get('next_action', 'Prepare the first bounded check through exec or project init using the declared goal, evaluator and resources')
    result = {'schema': 'rds-plan-draft-v1', 'status': 'DRAFT',
              'mode': 'EXISTING_PROJECT' if snap is not None else 'NEW_PROJECT',
              'goal': goal, 'scope': original_scope or intent.get('scope'), 'evaluation': evaluation, 'budget': budget,
              'input_status': 'DECLARED_NOT_VERIFIED', 'missing': missing,
              'proposed_changes': deepcopy(intent) if proposed_change else None,
              'next_action': next_action, 'fixed_task': intent.get('fixed_task'),
              'contract_sha256': snap['contract_sha256'] if snap else None,
              'steering': steering, 'active_work': active,
              'evidence': evidence[:16], 'evidence_total': len(evidence), 'evidence_omitted': max(0, len(evidence) - 16),
              'evidence_status_counts': dict(Counter(r['run_status'] for r in evidence)),
              'expand_evidence': 'project status (original receipt and artifact locators)',
              'execution_started': False, 'execution_authorized': False,
              'scientific_support': 'UNKNOWN', 'research_policy_gain_measured': False}
    if snap is not None:
        result['snapshot_sha256'] = digest({'contract': snap['contract_sha256'], 'budget': snap['budget'],
                                           'runs': snap['runs'], 'receipts': evidence, 'steering': steering})
    if dialogue:
        from rds_dialogue import build
        result['dialogue'] = build(store, result)
    if save_as is not None:
        require(snap is not None, 'Initialize the project before retaining a plan in its existing checkpoint ledger')
        from rds_checkpoints import save_checkpoint
        from rds_quick import cas_json
        ref = cas_json(store.root, result)
        result['checkpoint'] = save_checkpoint(store.root, save_as, snap, kind='project',
                                               decision={'plan_draft': ref, 'source_kind': 'UNVERIFIED_PLAN_PROPOSAL'})
        result['artifact'] = ref
    if output is not None:
        path = store._path(output)
        with path.open('x', encoding='utf-8') as stream:
            stream.write(canonical(result) + '\n')
    return result
