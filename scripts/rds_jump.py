"""Frozen three-source generation routes over the existing project ledger.

The adapter owns no executor, provider, scientific verdict, or retry budget.
Domain workers generate explanations; structure retains and tests them.
"""
from copy import deepcopy
import hashlib
import re

from rds_artifacts import strict_json
from rds_project import ProjectStore, digest, require, TERMINAL
import rds_structure as structure

POLICY_PATH = 'jump-generation.json'
KINDS = ('probe', 'refresh', 'synthesize')
PACKET_SCHEMA = 'rds-jump-packet-v1'
PACKET_BYTES = 32 * 1024


def _seal(value):
    return {**value, 'sha256': digest(value)}


def _origin(store, state):
    """Locate one original generation through verified effective ancestors."""
    from rds_method_revision import contract_history
    with store._db(True) as db:
        history = contract_history(db)
    require(history[-1]['sha256'] == state['contract_sha256'], 'Jump effective ancestry changed')
    events = [e for e in structure._events(store) if e['kind'] == structure.PREFIX + 'JUMP']
    require(len(events) <= 1, 'Multiple jump generations require an explicit new plan/workspace')
    if not events:
        return None
    _, errors = store._bindings(state['contract'])
    require(not errors, 'Jump current frozen bindings changed: ' + '; '.join(errors))
    started = structure._find(store, 'JUMP', events[0]['id'])
    req = structure._find(store, 'REQUEST', started['request_id'])
    require(req is not None and digest(req) == started['request_sha256'], 'Jump request changed')
    ancestor = next((h for h in history if h['sha256'] == req['scope']['contract_sha256']), None)
    require(ancestor is not None, 'Jump original contract is not a verified ancestor')
    origin = {**state, 'contract': ancestor['contract'], 'contract_sha256': ancestor['sha256']}
    plan = load_plan(store, origin)
    require(plan is not None and started['plan_sha256'] == digest(plan)
            and started['id'] == 'jump-' + digest({'contract': ancestor['sha256'], 'plan': plan})[:24],
            'Jump original plan identity changed')
    current = {b['path']: b for b in state['contract']['bindings']}
    for binding in ancestor['contract']['bindings']:
        if binding['role'] in {'config', 'data', 'evaluator'}:
            require(current.get(binding['path']) == binding, 'Jump immutable source compatibility changed')
        if binding['role'] == 'code' and any(binding['path'] in stage['run']['argv'] for stage in plan['stages']):
            require(current.get(binding['path']) == binding, 'Jump generator code compatibility changed')
    return started, req, plan, origin, history


def _origin_live(store, req, origin):
    # Reuse the unchanged structure ancestry/scope checks against the verified
    # origin contract. This view cannot admit or execute a historical proposal.
    class HistoricalView:
        def __getattr__(self, name):
            return getattr(store, name)

        def snapshot(self):
            return origin
    return structure._live(HistoricalView(), req, allow_owned_updates=True)


def packet(store):
    """Project verified current jump evidence for an AI, without claiming use.

    A projection writes only an oversized original to the existing CAS. It does
    not reserve resources, dispatch, record acknowledgement, or change verdicts.
    All predicates are delivered intact or the entire payload requires an original.
    """
    from rds_project import canonical
    from rds_quick import cas_json
    from rds_tms_store import current
    value = {'schema': PACKET_SCHEMA, 'status': 'UNAVAILABLE', 'items': [],
             'omissions': {'items': 0, 'generation_results': False},
             'original_scope': None, 'sources': [], 'scientific_support': 'UNKNOWN',
             'use_assurance': 'NOT_RECORDED', 'authorization': 'UNCHANGED'}
    try:
        state = store.snapshot()
        found = _origin(store, state)
        if found is None:
            load_plan(store, state)  # A corrupt configured plan still stops visibly.
            return None
        started, req, plan, origin, history = found
        ident = started['id']
        event_sha = digest(structure._events(store))
        value.update(id=ident, original_scope=deepcopy(req['scope']),
                     scope_sha256=req['scope_sha256'], request_id=req['id'],
                     request_sha256=digest(req), goal=req['goal'],
                     origin_contract_sha256=origin['contract_sha256'],
                     current_contract_sha256=state['contract_sha256'],
                     evidence_scope='CURRENT' if origin['contract_sha256'] == state['contract_sha256'] else 'HISTORICAL',
                     admission_authorized=False)
        _, saved = _origin_live(store, req, origin)
        finished = structure._find(store, 'JUMP_FINISHED', ident)
        require(finished is not None, 'Jump generation has not finished; recover its original attempts')
        require(finished['status'] in {'JUMP_PROPOSED', 'NO_CANDIDATE'}, 'Unsupported jump completion')
        prior, results = [], {}
        runs = {r['id']: r for r in state['runs']}
        for stage in plan['stages']:
            retained = runs.get(stage['run']['id'])
            require(retained is not None and retained['manifest_sha256'] == digest(stage['run']),
                    'Jump source manifest identity changed')
            result, ref = _read_stage(store, stage, req, prior)
            require(isinstance(result, dict), 'Jump result must be an object')
            # Proposals are represented once, as verified retained items below.
            results[stage['kind']] = deepcopy({k: v for k, v in result.items() if k != 'proposals'})
            prior.append(ref)
        require(prior == finished['sources'], 'Finished jump source identity changed')
        source_result = result  # Reuse the already hash-checked synthesis original.
        raw_proposals = source_result.get('proposals')
        require(isinstance(raw_proposals, list) and len(raw_proposals) <= 4,
                'Invalid original generated proposal inventory')
        require([p.get('id') for p in raw_proposals] == finished['proposal_ids'],
                'Retained jump proposal inventory changed')
        require(source_result.get('status') == ('PROPOSED' if raw_proposals else 'NO_CANDIDATE')
                and finished['status'] == ('JUMP_PROPOSED' if raw_proposals else 'NO_CANDIDATE'),
                'Jump completion and original synthesis disagree')
        observed = {r['id']: r for r in structure._verified_feedback(store, req['scope_sha256'])}
        items = []
        for raw in raw_proposals:
            row = structure._find(store, 'PROPOSAL', raw['id'])
            require(row is not None and row['scope_sha256'] == req['scope_sha256']
                    and row['proposal_sha256'] == digest(row['proposal']), 'Jump proposal identity changed')
            proposal = row['proposal']
            admitted = structure._find(store, 'REQUEST', row['request_id'])
            require(admitted is not None and admitted['scope_sha256'] == req['scope_sha256']
                    and admitted['goal'] == req['goal'], 'Jump proposal admission scope changed')
            # Admission may bind a later receipt-current search request. The
            # original explanatory content must still match the generator bytes.
            expected = deepcopy(raw)
            require(expected.get('request_id') == req['id'], 'Original generated request changed')
            expected['request_id'] = proposal['request_id']
            if 'search_allocation' in row:
                expected['search'] = {'slot': row['search_allocation']['slot']}
            for ref in prior:
                expected['exploration']['sources'].append({'kind': 'local_observation', 'source': deepcopy(ref)})
            require(expected == proposal, 'Retained explanation differs from original synthesis')
            feedback = structure._feedback_view(row, observed[raw['id']]) if raw['id'] in observed else None
            exploration = proposal['exploration']
            items.append({'id': row['id'], 'kind': 'hypothesis', 'proposal_sha256': row['proposal_sha256'],
                'explanation': '\n'.join(n['label'] for n in proposal.get('new_nodes', [])),
                'new_nodes': deepcopy(proposal.get('new_nodes', [])),
                'assumptions': deepcopy(proposal.get('assumptions', [])),
                'action_kind': proposal.get('action_kind', 'path_repair'),
                'change': exploration.get('change'), 'rationale': exploration.get('rationale'),
                'limitation': exploration.get('limitation'),
                'unknown_premises': deepcopy(exploration.get('unknown_premises', [])),
                'prediction': deepcopy(proposal.get('prediction')), 'discriminator': deepcopy(row.get('discriminator')),
                'topology': deepcopy(proposal.get('topology')), 'test': deepcopy(proposal.get('test')),
                'sources': deepcopy(exploration['sources']),
                'observation': feedback['observation'] if feedback else 'UNKNOWN',
                'status': feedback['status'] if feedback else 'HYPOTHESIS_PENDING',
                'feedback': deepcopy(feedback), 'scientific_support': 'UNKNOWN'})
            items[-1]['evidence_scope'] = value['evidence_scope']
        if not items:
            items = [{'id': ident, 'kind': 'no_candidate', 'status': 'NO_CANDIDATE',
                      'observation': 'UNKNOWN', 'reason': source_result.get('reason'),
                      'next_step': finished['next_move'], 'sources': deepcopy(prior),
                      'scientific_support': 'UNKNOWN'}]
        # Check the same cut after hash/feedback replay. No partial packet is
        # presented as current when a concurrent execution or topology edit moved it.
        latest = store.snapshot()
        require(latest['contract_sha256'] == state['contract_sha256']
                and [r['sha256'] for r in latest['receipts']] == [r['sha256'] for r in state['receipts']]
                and digest(structure._events(store)) == event_sha
                and current(store.root)['sha256'] == saved['sha256'], 'Jump evidence changed during projection')
        value.update(status='CURRENT', items=items, sources=prior, generation_status=finished['status'],
                     generation_results=results)
        if len(history) > 1:
            with store._db(True) as db:
                from rds_artifacts import strict_json
                revisions = [strict_json(r['body']) for r in db.execute(
                    "SELECT body FROM events WHERE json_extract(body,'$.kind')='METHOD_REVISION_ADOPTED' ORDER BY id")]
            value['method_context'] = {
                'verified_contract_lineage': [h['sha256'] for h in history],
                'revisions': revisions,
                'executions': [{k: deepcopy(r[k]) for k in ('run_id', 'sha256', 'run_status',
                    'effective_contract_sha256', 'artifacts') if k in r} for r in state['receipts']],
                'current_scope_feedback': structure._verified_feedback(store,
                    digest(structure._binding_scope(state, saved))),
                'historical_feedback_is_current_support': False}
        latest = store.snapshot()
        require(latest['contract_sha256'] == state['contract_sha256']
                and [r['sha256'] for r in latest['receipts']] == [r['sha256'] for r in state['receipts']]
                and digest(structure._events(store)) == event_sha
                and current(store.root)['sha256'] == saved['sha256'], 'Jump lineage changed during projection')
        sealed = _seal(value)
        if len(canonical(sealed).encode('utf-8')) <= PACKET_BYTES:
            return sealed
        original = cas_json(store.root, sealed)
        summary = {k: deepcopy(v) for k, v in value.items() if k not in {'items', 'generation_results', 'sources', 'method_context'}}
        summary.update(status='NEEDS_ORIGINAL', items=[], sources=[], original=original,
                       omissions={'items': len(items), 'generation_results': True, 'sources': len(prior)})
        if 'method_context' in value:
            summary['omissions']['method_context'] = True
        sealed = _seal(summary)
        require(len(canonical(sealed).encode('utf-8')) <= PACKET_BYTES, 'Jump scope exceeds packet limit')
        return sealed
    except (ValueError, KeyError, TypeError, OSError, UnicodeError) as exc:
        # Never expose a partly verified item as an available model premise.
        value.update(status='UNAVAILABLE', items=[], sources=[], diagnostic=str(exc)[:512])
        value.pop('generation_results', None)
        sealed = _seal(value)
        if len(canonical(sealed).encode('utf-8')) > PACKET_BYTES:
            value['original_scope'] = {'original': cas_json(store.root, value['original_scope']),
                                       'details_omitted': True}
            value['omissions'] = {**value['omissions'], 'original_scope': True}
            sealed = _seal(value)
        return sealed


def validate_use(context, value):
    """Validate returned evidence references, not inner reasoning or benefit."""
    from rds_project import canonical
    require(isinstance(context, dict) and context.get('schema') == PACKET_SCHEMA
            and context.get('status') == 'CURRENT', 'Jump use requires complete current delivered evidence')
    require(context.get('sha256') == digest({k: v for k, v in context.items() if k != 'sha256'}),
            'Jump packet hash changed')
    require(len(canonical(context).encode('utf-8')) <= PACKET_BYTES
            and context.get('omissions') == {'items': 0, 'generation_results': False},
            'Jump use cannot claim omitted evidence')
    require(isinstance(value, dict) and set(value) == {'schema', 'packet_sha256', 'decisions'}
            and type(value['schema']) is int and value['schema'] == 1
            and value['packet_sha256'] == context['sha256'], 'Jump use packet/schema mismatch')
    ids = [item['id'] for item in context['items']]
    require(ids and len(ids) == len(set(ids)), 'Jump packet needs distinct delivered item IDs')
    decisions = value['decisions']
    require(isinstance(decisions, list) and len(decisions) == len(ids), 'Jump use must address every delivered item once')
    checked = {}
    for decision in decisions:
        require(isinstance(decision, dict) and set(decision) == {'id', 'disposition', 'reason', 'next_step'},
                'Invalid jump decision fields')
        ident, disposition = decision['id'], decision['disposition']
        require(isinstance(ident, str) and ident in ids and ident not in checked, 'Invented or repeated jump item')
        require(isinstance(disposition, str) and disposition in {'adopt', 'adapt', 'reject', 'defer'},
                'Invalid jump disposition')
        cleaned = {'id': ident, 'disposition': disposition}
        for field in ('reason', 'next_step'):
            text = decision[field]
            require(isinstance(text, str) and 8 <= len(text.strip()) and len(text.encode('utf-8')) <= 2048,
                    'Jump use needs bounded substantive ' + field)
            tokens = set(re.findall(r'\w+', text.casefold()))
            require(tokens and not tokens <= {'ack', 'acknowledged', 'ok', 'okay', 'received', 'noted', '收到', '已收到', '已阅'},
                    'Acknowledgement alone is not jump use')
            cleaned[field] = text.strip()
        checked[ident] = cleaned
    return {'schema': 1, 'packet_sha256': context['sha256'], 'decisions': [checked[ident] for ident in ids]}


def load_plan(store, state):
    bindings = [b for b in state['contract']['bindings'] if b['path'] == POLICY_PATH]
    if not bindings:
        return None
    require(len(bindings) == 1 and bindings[0]['role'] == 'config',
            'jump-generation.json requires one frozen config binding')
    path = store._path(POLICY_PATH)
    require(path.stat().st_size <= 32768, 'Jump plan exceeds 32 KiB')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == bindings[0]['sha256'], 'Jump plan binding changed')
    plan = strict_json(raw.decode('utf-8-sig'))
    require(isinstance(plan, dict) and set(plan) == {'schema', 'stages'} and type(plan['schema']) is int
            and plan['schema'] == 1, 'Unsupported jump plan')
    stages = plan['stages']
    require(isinstance(stages, list) and len(stages) == 3, 'Declare exactly probe, refresh and synthesize stages')
    ids, outputs = set(), set()
    for kind, stage in zip(KINDS, stages):
        require(isinstance(stage, dict) and set(stage) == {'kind', 'run', 'output'} and stage['kind'] == kind,
                'Jump stages must be ordered probe, refresh, synthesize')
        run = stage['run']
        require(isinstance(run, dict) and 'protocol' not in run and isinstance(run.get('protocol_path'), str),
                'Jump route declares a frozen protocol_path; its hash is resolved from the contract')
        protocols = [b for b in state['contract']['bindings'] if b['path'] == run['protocol_path'] and b['role'] == 'protocol']
        require(len(protocols) == 1, 'Jump protocol is not frozen')
        run['protocol'] = {k: protocols[0][k] for k in ('path', 'sha256')}
        del run['protocol_path']
        require(isinstance(run, dict) and run.get('arm') == 'tool'
                and isinstance(run.get('id'), str) and run['id'] not in ids,
                'Jump routes need distinct tool run IDs')
        require(run.get('argv') in state['contract']['allowed_commands'], 'Jump command is not authorized')
        require(isinstance(run.get('outpaths'), list) and isinstance(stage['output'], str)
                and stage['output'] in run['outpaths'], 'Jump output must be declared by its run')
        require(not outputs.intersection(run['outpaths']), 'Jump routes must not overwrite prior outputs')
        require(set(run.get('resource_estimates', {})) == set(state['budget']), 'Jump route resource dimensions differ')
        ids.add(run['id'])
        outputs.update(run['outpaths'])
    return plan


def _read_stage(store, stage, req, prior):
    state = store.snapshot()
    receipt = next((r for r in state['receipts'] if r['run_id'] == stage['run']['id']), None)
    require(receipt is not None and receipt['run_status'] == 'SUCCEEDED', 'Jump source did not succeed')
    doc, sha = structure._artifact(store, receipt, stage['output'])
    require(isinstance(doc, dict) and set(doc) == {'schema', 'kind', 'request_sha256', 'inputs', 'result'}
            and type(doc['schema']) is int and doc['schema'] == 1 and doc['kind'] == stage['kind'],
            'Invalid jump result envelope')
    require(doc['request_sha256'] == digest(req), 'Jump result belongs to another exploration request')
    require(doc['inputs'] == prior, 'Jump result must consume exact preceding original receipts and outputs')
    return doc['result'], {'run_id': receipt['run_id'], 'receipt_sha256': receipt['sha256'],
                           'path': stage['output'], 'sha256': sha}


def prepare_owned(store, selected_manifest):
    """Prepare/collect frozen generators; the owning drive alone executes them."""
    before = digest(structure._events(store))
    try:
        state = store.snapshot()
        found = _origin(store, state)
        if found is not None:
            started, _, _, origin, _ = found
            finished = structure._find(store, 'JUMP_FINISHED', started['id'])
            if finished is not None:
                context = packet(store)
                require(context is not None and context['status'] in {'CURRENT', 'NEEDS_ORIGINAL'},
                        'Completed jump original evidence is unavailable')
                return None
            require(origin['contract_sha256'] == state['contract_sha256'],
                    'Unfinished jump cannot cross a method revision')
        else:
            plan = load_plan(store, state)
            if plan is None or selected_manifest != plan['stages'][0]['run']:
                return None
        result = generate(store.root, _prepare_only=True, _selected_manifest=selected_manifest)
        return {**result, 'changed': digest(structure._events(store)) != before,
                'execution_started': False}
    except (ValueError, KeyError, TypeError, OSError, UnicodeError) as exc:
        return {'status': 'JUMP_UNAVAILABLE', 'changed': digest(structure._events(store)) != before,
                'diagnostic': str(exc)[:512], 'execution_started': False, 'retry_authorized': False}


def generate(root, steps=1, *, _prepare_only=False, _selected_manifest=None):
    """Advance at most three already-authorized routes; never redispatch attempts.

    One frozen plan permits one generation per contract. A new evidence round
    requires an explicit revision, preserving earlier attempts and costs.
    """
    require(type(steps) is int and 1 <= steps <= 3, 'Jump steps must be 1..3')
    store = ProjectStore(root)
    state = store.snapshot()
    found = _origin(store, state)
    plan = found[2] if found is not None else load_plan(store, state)
    if plan is None:
        return {'status': 'JUMP_NOT_CONFIGURED', 'execution_started': False, 'scientific_support': 'UNKNOWN'}
    ident = found[0]['id'] if found else 'jump-' + digest({'contract': state['contract_sha256'], 'plan': plan})[:24]
    started = found[0] if found else None
    if started is None:
        requests = structure.request(root, 1)['tasks']
        if not requests:
            return {'status': 'NO_UNRESOLVED_GOAL', 'execution_started': False}
        req = requests[0]
        with structure._meter(store, 'jump-start'):
            # This stable identity makes concurrent or repeated starts share
            # one request and the same owning run IDs, not fresh attempts.
            started = structure._put(store, 'JUMP', ident,
                {'id': ident, 'request_id': req['id'], 'request_sha256': digest(req), 'plan_sha256': digest(plan)},
                expected=req['snapshot_sha256'], check_snapshot=True)
    req = structure._find(store, 'REQUEST', started['request_id'])
    require(req is not None and digest(req) == started['request_sha256'], 'Jump request changed')
    finished = structure._find(store, 'JUMP_FINISHED', ident)
    if finished is not None:
        originals = []
        for stage in plan['stages']:
            _, ref = _read_stage(store, stage, req, originals)
            originals.append(ref)
        require(originals == finished['sources'], 'Finished jump source identity changed')
        for proposal_id in finished['proposal_ids']:
            require(structure._find(store, 'PROPOSAL', proposal_id) is not None, 'Finished jump proposal missing')
        context = packet(store)
        require(context is not None and context['status'] in {'CURRENT', 'NEEDS_ORIGINAL'},
                'Finished jump evidence is unavailable')
        return {**finished, 'agent_context': context}  # No fresh budget or campaign admission.
    structure._live(store, req, allow_owned_updates=True)
    prior, dispatched, result = [], 0, None
    for stage in plan['stages']:
        state = store.snapshot()
        run = next((r for r in state['runs'] if r['id'] == stage['run']['id']), None)
        if run:
            require(run['manifest_sha256'] == digest(stage['run']), 'Jump run ID already belongs to another manifest')
            if run['status'] not in TERMINAL and run.get('attempt_id') is not None:
                if _prepare_only:
                    return {'status': 'RECOVERY_REQUIRED', 'id': ident, 'run_id': run['id'],
                            'execution_started': False}
                recovered = store.recover(run['id'])
                if recovered.get('run_status') not in {'SUCCEEDED', 'FAILED', 'INTERRUPTED'}:
                    return {'status': 'RECOVERY_REQUIRED', 'run_id': run['id'], 'execution_started': False}
                state = store.snapshot()
                run = next(r for r in state['runs'] if r['id'] == run['id'])
            if run['status'] in TERMINAL and run['status'] != 'COMPLETED':
                return {'status': 'JUMP_STOPPED', 'run_id': run['id'], 'reason': run['status'],
                        'scientific_support': 'UNKNOWN', 'retry_authorized': False}
        if run is None or run['status'] not in TERMINAL:
            if _prepare_only:
                return {'status': 'READY_TO_EXECUTE' if _selected_manifest == stage['run'] else 'JUMP_WAITING_ADMISSION',
                        'id': ident, 'next_stage': stage['kind'], 'selected_manifest': deepcopy(stage['run']),
                        'sources': prior, 'scientific_support': 'UNKNOWN'}
            if dispatched >= steps:
                return {'status': 'JUMP_STEP_LIMIT', 'id': ident, 'next_stage': stage['kind'],
                        'sources': prior, 'scientific_support': 'UNKNOWN'}
            structure._live(store, req, allow_owned_updates=True)
            if run is None:
                store.register(stage['run'])
            receipt = store.execute(stage['run']['id'])
            dispatched += 1
            if receipt.get('run_status') != 'SUCCEEDED':
                return {'status': 'JUMP_STOPPED', 'run_id': stage['run']['id'],
                        'reason': receipt.get('run_status', 'UNKNOWN'), 'scientific_support': 'UNKNOWN',
                        'retry_authorized': False}
        if _prepare_only:
            result, ref = _read_stage(store, stage, req, prior)
            prior.append(ref)
        else:
            with structure._meter(store, 'jump-consume'):
                result, ref = _read_stage(store, stage, req, prior)
                prior.append(ref)
    require(isinstance(result, dict) and result.get('status') in {'PROPOSED', 'NO_CANDIDATE'},
            'Synthesis must return PROPOSED or NO_CANDIDATE')
    proposals = result.get('proposals')
    require(isinstance(proposals, list) and len(proposals) <= 4 and
            bool(proposals) == (result['status'] == 'PROPOSED'), 'Invalid generated proposal count')
    retained = []
    admission = req
    if proposals:
        # Generation adds receipts. Bind adoption to a fresh allocation over
        # those originals while retaining the earlier generation request chain.
        admission = next((r for r in structure.request(root, 8)['tasks']
                          if r['goal'] == req['goal'] and r['scope_sha256'] == req['scope_sha256']), None)
        require(admission is not None, 'Original jump goal no longer has an open admission request')
        if 'search_allocation' in admission:
            slots = [s for s in admission['search_allocation']['slots'] if s['kind'] == 'explore']
            require(len(slots) >= len(proposals), 'Generated proposals exceed allocated exploration slots')
    for index, proposal in enumerate(proposals):
        require(isinstance(proposal, dict) and proposal.get('request_id') == req['id'],
                'Generated proposal must retain the original request')
        proposal = deepcopy(proposal)
        if admission is not req:
            proposal['request_id'] = admission['id']
            if 'search_allocation' in admission:
                proposal['search'] = {'slot': slots[index]['slot']}
        sources = proposal.get('exploration', {}).get('sources')
        require(isinstance(sources, list), 'Generated explanation must declare its sources')
        for ref in prior:
            sources.append({'kind': 'local_observation', 'source': deepcopy(ref)})
        retained.append(structure.propose(root, proposal)['id'])
    with structure._meter(store, 'jump-finish'):
        finished = structure._put(store, 'JUMP_FINISHED', ident,
            {'id': ident, 'status': 'JUMP_PROPOSED' if retained else 'NO_CANDIDATE', 'proposal_ids': retained,
             'sources': prior, 'reason': result.get('reason'), 'scientific_support': 'UNKNOWN',
             'next_move': 'structure next' if retained else 'Inspect retained evidence and revise the bounded plan',
             'execution_authorized': False})
    return {**finished, 'agent_context': packet(store)}
