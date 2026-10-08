"""Frozen three-source generation routes over the existing project ledger.

The adapter owns no executor, provider, scientific verdict, or retry budget.
Domain workers generate explanations; structure retains and tests them.
"""
from copy import deepcopy
import hashlib

from rds_artifacts import strict_json
from rds_project import ProjectStore, digest, require, TERMINAL
import rds_structure as structure

POLICY_PATH = 'jump-generation.json'
KINDS = ('probe', 'refresh', 'synthesize')


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


def generate(root, steps=1):
    """Advance at most three already-authorized routes; never redispatch attempts.

    One frozen plan permits one generation per contract. A new evidence round
    requires an explicit revision, preserving earlier attempts and costs.
    """
    require(type(steps) is int and 1 <= steps <= 3, 'Jump steps must be 1..3')
    store = ProjectStore(root)
    state = store.snapshot()
    plan = load_plan(store, state)
    if plan is None:
        return {'status': 'JUMP_NOT_CONFIGURED', 'execution_started': False, 'scientific_support': 'UNKNOWN'}
    ident = 'jump-' + digest({'contract': state['contract_sha256'], 'plan': plan})[:24]
    started = structure._find(store, 'JUMP', ident)
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
    structure._live(store, req, allow_owned_updates=True)
    finished = structure._find(store, 'JUMP_FINISHED', ident)
    if finished is not None:
        originals = []
        for stage in plan['stages']:
            _, ref = _read_stage(store, stage, req, originals)
            originals.append(ref)
        require(originals == finished['sources'], 'Finished jump source identity changed')
        for proposal_id in finished['proposal_ids']:
            require(structure._find(store, 'PROPOSAL', proposal_id) is not None, 'Finished jump proposal missing')
        return finished  # Read-only reuse requires no new budget or campaign admission.
    prior, dispatched, result = [], 0, None
    for stage in plan['stages']:
        state = store.snapshot()
        run = next((r for r in state['runs'] if r['id'] == stage['run']['id']), None)
        if run:
            require(run['manifest_sha256'] == digest(stage['run']), 'Jump run ID already belongs to another manifest')
            if run['status'] not in TERMINAL and run.get('attempt_id') is not None:
                recovered = store.recover(run['id'])
                if recovered.get('run_status') not in {'SUCCEEDED', 'FAILED', 'INTERRUPTED'}:
                    return {'status': 'RECOVERY_REQUIRED', 'run_id': run['id'], 'execution_started': False}
                state = store.snapshot()
                run = next(r for r in state['runs'] if r['id'] == run['id'])
            if run['status'] in TERMINAL and run['status'] != 'COMPLETED':
                return {'status': 'JUMP_STOPPED', 'run_id': run['id'], 'reason': run['status'],
                        'scientific_support': 'UNKNOWN', 'retry_authorized': False}
        if run is None or run['status'] not in TERMINAL:
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
    if proposals and 'search_allocation' in req:
        # Generation adds receipts. Bind adoption to a fresh allocation over
        # those originals while retaining the earlier generation request chain.
        admission = next((r for r in structure.request(root, 8)['tasks']
                          if r['goal'] == req['goal'] and r['scope_sha256'] == req['scope_sha256']), None)
        require(admission is not None, 'Original jump goal no longer has an open admission request')
        slots = [s for s in admission['search_allocation']['slots'] if s['kind'] == 'explore']
        require(len(slots) >= len(proposals), 'Generated proposals exceed allocated exploration slots')
    for index, proposal in enumerate(proposals):
        require(isinstance(proposal, dict) and proposal.get('request_id') == req['id'],
                'Generated proposal must retain the original request')
        proposal = deepcopy(proposal)
        if admission is not req:
            proposal['request_id'] = admission['id']
            proposal['search'] = {'slot': slots[index]['slot']}
        sources = proposal.get('exploration', {}).get('sources')
        require(isinstance(sources, list), 'Generated explanation must declare its sources')
        for ref in prior:
            sources.append({'kind': 'local_observation', 'source': deepcopy(ref)})
        retained.append(structure.propose(root, proposal)['id'])
    with structure._meter(store, 'jump-finish'):
        return structure._put(store, 'JUMP_FINISHED', ident,
            {'id': ident, 'status': 'JUMP_PROPOSED' if retained else 'NO_CANDIDATE', 'proposal_ids': retained,
             'sources': prior, 'reason': result.get('reason'), 'scientific_support': 'UNKNOWN',
             'next_move': 'structure next' if retained else 'Inspect retained evidence and revise the bounded plan',
             'execution_authorized': False})
