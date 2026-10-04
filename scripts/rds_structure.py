"""Experimental problem-model branches over the existing ledger/CAS/TMS.

This adapter owns no second execution budget, theorem checker or scheduler.
Definition, admission, observation and logical support remain separate.
"""
from copy import deepcopy
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

from rds_project import ProjectStore, canonical, digest, require, TERMINAL, _alive
from rds_quick import cas_json
from rds_tms_store import current, save
from rds_hypergraph import _validate, review_hypergraph
from rds_frontier import discover_frontier
from rds_frontier_proposals import review_proposals

PREFIX = 'STRUCTURE_'
MAX_BYTES = 128 * 1024


def _read_ref(store, ref):
    path = Path(ref['path']).resolve()
    require(path.is_relative_to((store.state_dir / 'cas').resolve()), 'Structure CAS escapes project scope')
    require(path.stat().st_size <= MAX_BYTES, 'Structure record exceeds byte limit')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == ref['sha256'], 'Structure CAS hash mismatch')
    from rds_artifacts import strict_json
    return strict_json(raw.decode('utf-8'))


def _events(store, db=None):
    if db is None:
        with store._db(True) as connection:
            return _events(store, connection)
    rows = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind') LIKE 'STRUCTURE_%' ORDER BY id LIMIT 1025").fetchall()
    require(len(rows) <= 1024, 'Structure event limit reached')
    events = [json.loads(row['body']) for row in rows]
    for event in events:
        if 'record' in event:
            require(event['record']['sha256'] == event['sha256'], 'Structure event hash mismatch')
    return events


def _find(store, kind, ident):
    found = [e for e in _events(store) if e['kind'] == PREFIX + kind and e.get('id') == ident]
    require(len(found) <= 1, 'Duplicate structure identity')
    if not found:
        return None
    value = _read_ref(store, found[0]['record'])
    require(value.get('id') == ident, 'Structure record/event identity mismatch')
    if kind == 'REQUEST':
        require(digest(value['scope']) == value['scope_sha256'], 'Request scope hash mismatch')
    if kind == 'PROPOSAL':
        require(value['proposal_sha256'] == digest(value['proposal']), 'Retained proposal hash mismatch')
    return value


def _put(store, kind, ident, value, *, expected=None, check_snapshot=False):
    require(len(canonical(value).encode('utf-8')) <= MAX_BYTES, 'Structure record exceeds 128 KiB')
    ref = cas_json(store.root, value)
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        events = _events(store, db)
        old = next((e for e in events if e['kind'] == PREFIX + kind and e.get('id') == ident), None)
        if old:
            require(old['sha256'] == ref['sha256'], 'Structure ID reused with changed content')
            return value
        if check_snapshot:
            row = db.execute('SELECT sha256 FROM dependency_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()
            require(row and row['sha256'] == expected, 'Stale structure snapshot; request current evidence')
        db.execute('INSERT INTO events(body) VALUES (?)',
                   (canonical({'kind': PREFIX + kind, 'id': ident, 'sha256': ref['sha256'], 'record': ref}),))
    return value


@contextmanager
def _meter(store, operation):
    """Reserve bounded controller work in the original budget, even on failure.

    Interrupted reservations are reconciled explicitly; they cannot disappear
    when an invocation or branch changes. Worker time is accounted separately.
    """
    ident, cap = uuid.uuid4().hex, 2.0
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        events = _events(store, db)
        settled = {e['id'] for e in events if e['kind'] == PREFIX + 'CONTROL_FINISHED'}
        require(all(e['id'] in settled for e in events if e['kind'] == PREFIX + 'CONTROL_STARTED'),
                'Interrupted structure controller reservation; use structure recover')
        row = db.execute("SELECT * FROM budget WHERE resource='wall_seconds'").fetchone()
        require(row and row['cap'] - row['spent'] - row['charged'] - row['reserved'] >= cap,
                'Insufficient wall_seconds for bounded structure control work')
        store._campaign_deadline(db, store._contract(db), admit=True)
        db.execute("UPDATE budget SET reserved=reserved+? WHERE resource='wall_seconds'", (cap,))
        db.execute('INSERT INTO events(body) VALUES (?)', (canonical({'kind': PREFIX + 'CONTROL_STARTED',
                   'id': ident, 'operation': operation, 'cap': cap, 'pid': os.getpid()}),))
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        with store._db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE budget SET reserved=reserved-?,spent=spent+? WHERE resource='wall_seconds'", (cap, elapsed))
            db.execute('INSERT INTO events(body) VALUES (?)', (canonical({'kind': PREFIX + 'CONTROL_FINISHED',
                       'id': ident, 'wall_seconds': elapsed, 'over_cap': elapsed > cap}),))


def recover_control(root):
    store = ProjectStore(root)
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        events = _events(store, db)
        settled = {e['id'] for e in events if e['kind'] == PREFIX + 'CONTROL_FINISHED'}
        pending = [e for e in events if e['kind'] == PREFIX + 'CONTROL_STARTED' and e['id'] not in settled]
        for event in pending:
            require(event.get('pid') is not None and _alive(event['pid']) is False,
                    'Controller may still be active; retain reservation for host reconciliation')
            db.execute("UPDATE budget SET reserved=reserved-?,charged=charged+? WHERE resource='wall_seconds'", (event['cap'], event['cap']))
            db.execute('INSERT INTO events(body) VALUES (?)', (canonical({'kind': PREFIX + 'CONTROL_FINISHED',
                       'id': event['id'], 'wall_seconds': None, 'charged_estimate': event['cap'], 'status': 'UNKNOWN'}),))
    return {'status': 'RECOVERED', 'reconciled_controls': len(pending), 'execution_started': False}


def _binding_scope(state, saved):
    contract = state['contract']
    return {'contract_sha256': state['contract_sha256'],
            'goal': deepcopy(contract.get('advisor_policy', {}).get('context', {}).get('decision',
                                    contract.get('description', 'Frozen project goal'))),
            'objective_sha256': contract.get('objective_sha256'),
            'evaluators': [deepcopy(b) for b in contract['bindings'] if b['role'] == 'evaluator'],
            'goals': list(saved['dependency_map']['goals']), 'source_base_dir': saved['source_base_dir']}


def _frontier(spec, goal):
    # Projecting an AND edge into pairwise relations is question generation
    # only: mark it PROPOSED so it cannot incorrectly close the AND obligation.
    nodes = [{'id': n['id'], 'kind': n.get('kind', 'concept'), 'label': n.get('label', n['id']),
              'source': n['source']} for n in spec['nodes']]
    edges = [{'from': p, 'to': e['conclusion'], 'relation': 'requires', 'status': 'PROPOSED',
              'source': e['source']} for e in spec['hyperedges'] for p in e['premises']]
    anchors = list(dict.fromkeys(p for e in spec['hyperedges'] if e['conclusion'] == goal for p in e['premises'])) or [goal]
    data = {'schema_version': 1, 'nodes': nodes, 'edges': edges,
            'goals': [{'id': goal, 'target': goal, 'anchors': anchors, 'decision': 'Resolve original goal ' + goal,
                       'source': 'original TMS goal'}]}
    result = discover_frontier(data)
    gap = {'id': 'structure-gap:' + digest(goal)[:24], 'kind': 'PROBLEM_MODEL_LIMITATION',
           'target': goal, 'anchors': anchors, 'decision': 'Resolve original goal ' + goal,
           'evidence_refs': [{'id': n['id'], 'source': n['source']} for n in spec['nodes'] if n['id'] in anchors + [goal]],
           'why': 'Current dependency analysis leaves the original goal unresolved; its decomposition is open to review',
           'question': 'What representation, missing concept or rival decomposition yields a bounded distinguishing test?'}
    result['gaps'].append(gap)
    return data, result, gap


def request(root, limit=3):
    require(type(limit) is int and 1 <= limit <= 8, 'Task limit must be 1..8')
    store = ProjectStore(root)
    with _meter(store, 'request'):
        if 'advisor_policy' in store.snapshot()['contract']:
            from rds_owned_advisor import review
            review(store)  # Collect program-owned goals before freezing the task scope.
        state, saved = store.snapshot(), current(root)
        require(saved is not None, 'Declare dependencies before structure exploration')
        spec = saved['dependency_map']
        require(len(spec['nodes']) <= 128 and len(spec['hyperedges']) <= 512,
                'Structure exploration map exceeds bounded task scope; retain a scoped dependency model first')
        analysis = review_hypergraph(spec)
        scope = _binding_scope(state, saved)
        tasks = []
        for goal in spec['goals']:
            if analysis['goals'][goal]['status'] == 'DECLARED_SUPPORTED':
                continue  # Healthy OR alternatives do not justify false dead-end claims.
            data, frontier, gap = _frontier(spec, goal)
            identity = {'snapshot_sha256': saved['sha256'], 'scope': scope, 'goal': goal,
                        'receipts': [r['sha256'] for r in state['receipts']]}
            rid = 'request-' + digest(identity)[:24]
            old = _find(store, 'REQUEST', rid)
            if old:
                tasks.append(old)
                if len(tasks) >= limit:
                    break
                continue
            value = {'id': rid, **identity, 'scope_sha256': digest(scope), 'gap_id': gap['id'],
                     'goal_status': analysis['goals'][goal], 'frontier_spec': data, 'frontier': frontier, 'original_map': spec,
                     'sources': gap['evidence_refs'], 'unknown_premises': gap['anchors'],
                     'external_search': ['Find current primary sources for missing concepts and applicability conditions',
                                         'Find competing formulations; the initial decomposition may be wrong'],
                     'experiment_suggestion': 'Compare different predictions or computational routes with a frozen independent evaluator',
                     'budget': state['budget'], 'actions': ['path_repair', 'knowledge_expansion', 'structural_reconstruction'],
                     'proposal_commands': ['structure propose --proposal FILE', 'structure next', 'structure advance --id ID'],
                     'instruction': 'Propose directions outside the listed nodes. No complete goal path is required for bounded exploration. '
                                    'Keep original acceptance, sources, unknown premises, cost, stopping and outcome decisions.'}
            _put(store, 'REQUEST', rid, value, expected=saved['sha256'], check_snapshot=True)
            tasks.append(value)
            if len(tasks) >= limit:
                break
        return {'status': 'EXPLORATION_REQUESTED' if tasks else 'NO_UNRESOLVED_GOAL', 'tasks': tasks,
                'authorization': 'UNCHANGED', 'scientific_support': 'UNKNOWN',
                'selection': 'UNRESOLVED_GOAL_DECLARATION_ORDER', 'omitted_goals': max(0, len(spec['goals']) - len(tasks))}


def _live(store, req, allow_owned_updates=False):
    state, saved = store.snapshot(), current(store.root)
    require(saved is not None, 'Missing structure snapshot')
    if saved['sha256'] != req['snapshot_sha256']:
        def declared(spec):
            return {k: [r for r in spec[k] if not r['id'].startswith('owned:')] for k in ('nodes', 'hyperedges')}
        require(allow_owned_updates, 'Stale structure snapshot')
        from rds_math import blob
        seen = saved
        with store._db(True) as db:
            for _ in range(64):
                require(declared(seen['dependency_map']) == declared(req['original_map'])
                        and seen['dependency_map']['goals'] == req['original_map']['goals']
                        and seen['source_base_dir'] == req['scope']['source_base_dir'], 'Stale structure snapshot')
                if seen['sha256'] == req['snapshot_sha256']:
                    break
                record = db.execute('SELECT body FROM dependency_snapshots WHERE sha256=?', (seen['parent'],)).fetchone()
                require(record is not None, 'Stale structure snapshot lineage')
                previous = json.loads(record['body'])
                require(digest(previous) == seen['parent'], 'Structure snapshot ancestry hash mismatch')
                seen = {**previous, 'sha256': seen['parent'], 'dependency_map': json.loads(blob(store.root, previous['map']))}
            else:
                raise ValueError('Structure snapshot ancestry exceeds bounded recovery scope')
    require(_binding_scope(state, saved) == req['scope'], 'Structure goal/contract/scope changed')
    return state, saved


def propose(root, proposal):
    store = ProjectStore(root)
    require(isinstance(proposal, dict) and len(canonical(proposal).encode('utf-8')) <= MAX_BYTES, 'Invalid structure proposal')
    require(isinstance(proposal.get('id'), str) and 1 <= len(proposal['id']) <= 80, 'Invalid structure proposal ID')
    previous = _find(store, 'PROPOSAL', proposal['id'])
    if previous:
        require(previous['proposal_sha256'] == digest(proposal), 'Proposal ID reused with changed content')
        return previous
    with _meter(store, 'propose'):
        req = _find(store, 'REQUEST', proposal.get('request_id'))
        require(req is not None, 'Unknown exploration request')
        state, saved = _live(store, req)
        reviewed = review_proposals(req['frontier'], req['frontier_spec'], {'schema_version': 1, 'proposals': [proposal]})['proposals'][0]
        require(not reviewed['definition_errors'], '; '.join(reviewed['definition_errors']))
        require(proposal.get('gap_id') == req['gap_id'], 'Structure proposal must bind the problem-model gap')
        topology = proposal.get('topology', {})
        require(isinstance(topology, dict) and set(topology) == {'retire_hyperedges', 'hyperedges'}, 'Declare exact topology edits')
        removed, added = topology['retire_hyperedges'], topology['hyperedges']
        require(isinstance(removed, list) and len(removed) <= 32 and len(set(removed)) == len(removed)
                and all(isinstance(v, str) for v in removed), 'Invalid retired hyperedges')
        require(isinstance(added, list) and len(added) <= 32, 'Invalid new hyperedges')
        original = saved['dependency_map']
        old_edges = {e['id']: e for e in original['hyperedges']}
        require(all(e in old_edges and not e.startswith('owned:') for e in removed), 'Cannot retire missing or program-owned edges')
        if proposal.get('action_kind', 'path_repair') != 'structural_reconstruction':
            require(not removed, 'Only structural reconstruction may retire dependencies')
        if proposal.get('action_kind') == 'structural_reconstruction':
            require(removed or added, 'Reconstruction must change dependency topology')
        candidate = deepcopy(original)
        for node in proposal.get('new_nodes', []):
            require(not node['id'].startswith('owned:'), 'Program-owned ID is protected')
            candidate['nodes'].append({**{k: deepcopy(node[k]) for k in ('id', 'kind', 'label', 'source')}, 'status': 'UNKNOWN'})
        candidate['hyperedges'] = [e for e in candidate['hyperedges'] if e['id'] not in removed]
        for edge in added:
            require(isinstance(edge, dict) and set(edge) == {'id', 'premises', 'conclusion', 'source'},
                    'New hyperedges require ID, AND premises, conclusion and source; no support flag')
            require(edge['id'] not in old_edges and not edge['id'].startswith('owned:'), 'New edge ID must be fresh')
            candidate['hyperedges'].append({**deepcopy(edge), 'status': 'PROPOSED'})
        _validate(candidate)
        require(candidate != original, 'Proposal must change the problem model, not merely describe it')
        require(candidate['goals'] == original['goals'], 'Original goals cannot be replaced or weakened')
        experiment = proposal.get('experiment')
        require(isinstance(experiment, dict) and set(experiment) == {'runs', 'candidate_output', 'verdict_output'}, 'Declare experiment and independent result files')
        runs = experiment['runs']
        require(isinstance(runs, list) and len(runs) == 2 and runs[0]['id'] != runs[1]['id'], 'Experiment needs distinct candidate/verifier manifests')
        contract = state['contract']
        evaluator_paths = [b['path'] for b in contract['bindings'] if b['role'] == 'evaluator']
        verifier_entry = next((v for v in runs[1]['argv'][1:] if not v.startswith('-')), None)
        require(verifier_entry in evaluator_paths, 'Verifier must directly invoke a frozen evaluator binding')
        require(not any(b['path'] == verifier_entry and b['role'] == 'code' for b in contract['bindings']), 'Verifier cannot share candidate code binding')
        require(not any(p in runs[0]['argv'] for p in evaluator_paths), 'Candidate cannot serve as its own evaluator')
        require(experiment['candidate_output'] in runs[0]['outpaths'] and experiment['verdict_output'] in runs[1]['outpaths'], 'Result files must be declared by their runs')
        require(experiment['candidate_output'] in runs[1]['argv'], 'Verifier must consume the candidate original result')
        for run in runs:
            require(run['argv'] in contract['allowed_commands'], 'Experiment command is not authorized; use existing revision path')
            require(set(run['resource_estimates']) == set(contract['budget']), 'Experiment cost dimensions differ')
        caps = proposal.get('exploration', {}).get('cost', {})
        from rds_frontier import _finite
        require(caps and set(caps) == set(contract['budget']), 'Declare total exploration cost in project budget dimensions')
        require(all(_finite(v) and v >= 0 for v in caps.values()) and caps['wall_seconds'] > 0, 'Invalid exploration cost')
        require(all(sum(r['resource_estimates'][k] for r in runs) <= caps[k] for k in caps), 'Experiment exceeds exploration cap')
        reviewed['exploration'] = deepcopy(proposal['exploration'])
        value = {'id': proposal['id'], 'request_id': req['id'], 'proposal_sha256': digest(proposal),
                 'scope_sha256': req['scope_sha256'], 'snapshot_sha256': req['snapshot_sha256'],
                 'proposal': deepcopy(proposal), 'review': reviewed, 'candidate_map': candidate,
                 'status': 'HYPOTHESIS_PENDING', 'execution_authorized': False, 'scientific_support': 'UNKNOWN'}
        return _put(store, 'PROPOSAL', value['id'], value, expected=saved['sha256'], check_snapshot=True)


def next_step(root):
    store = ProjectStore(root)
    with _meter(store, 'next'):
        state, saved = store.snapshot(), current(root)
        events = _events(store)
        feedback = {e['id'] for e in events if e['kind'] == PREFIX + 'FEEDBACK'}
        goal_checks = []
        for event in events:
            if event['kind'] == PREFIX + 'FEEDBACK':
                observed = _read_ref(store, event['record'])
                proposal_row = _find(store, 'PROPOSAL', event['id'])
                if observed['goal_status'] == 'PASS':
                    req = _find(store, 'REQUEST', proposal_row['request_id'])
                    if _binding_scope(state, saved) == req['scope']:
                        _check_feedback(store, proposal_row, observed)
                        goal_checks.append(event['id'])
        proposals = [_read_ref(store, e['record']) for e in events if e['kind'] == PREFIX + 'PROPOSAL' and e['id'] not in feedback]
        eligible, blocked = [], []
        for row in proposals:
            caps = row['review'].get('exploration', {}).get('cost', {})
            reason = None
            try:
                _live(store, _find(store, 'REQUEST', row['request_id']), allow_owned_updates=True)
            except ValueError:
                reason = 'STALE_SNAPSHOT'
            existing = {r['id'] for r in state['runs']}
            needed = {k: sum(r['resource_estimates'][k] for r in row['proposal']['experiment']['runs'] if r['id'] not in existing) for k in caps}
            if not reason and any(v > state['budget'][k]['remaining'] for k, v in needed.items()):
                reason = 'BUDGET_EXHAUSTED'
            if reason:
                blocked.append({'id': row['id'], 'reason': reason})
            else:
                eligible.append(row)
        eligible.sort(key=lambda r: (r['review']['exploration']['cost']['wall_seconds'], r['id']))
        choice = eligible[0] if eligible else None
        if goal_checks:
            choice = None
        result = {'status': 'TEST_CANDIDATE' if choice else 'REQUEST_OPEN_EXPLORATION',
                  'selected': choice['id'] if choice else None, 'blocked': blocked,
                  'basis': 'SMALLEST_DECLARED_DISTINGUISHING_TEST_CAP_THEN_STABLE_ID',
                  'probability_model': None, 'scientific_support': 'UNKNOWN', 'authorization': 'UNCHANGED',
                  'feedback_consumed': sorted(feedback), 'next_move': 'structure advance --id ' + choice['id'] if choice else 'structure request'}
        if goal_checks:
            result.update(status='ORIGINAL_EVALUATOR_PASSED', goal_checks=goal_checks,
                          next_move='Inspect original independently checked goal scope; activate supported structure if useful')
        _put(store, 'DECISION', 'decision-' + digest(result)[:24], result)
        return result


def advance(root, ident):
    """One candidate and verifier through original admission, no rerun on resume."""
    store = ProjectStore(root)
    row = _find(store, 'PROPOSAL', ident)
    require(row is not None, 'Unknown structure proposal')
    old = _find(store, 'FEEDBACK', ident)
    if old:
        _check_feedback(store, row, old)
        return old
    req = _find(store, 'REQUEST', row['request_id'])
    _live(store, req, allow_owned_updates=True)
    for manifest in row['proposal']['experiment']['runs']:
        state = store.snapshot()
        _live(store, _find(store, 'REQUEST', row['request_id']), allow_owned_updates=True)
        run = next((r for r in state['runs'] if r['id'] == manifest['id']), None)
        if run is None:
            # Includes current owned Advisor, immutable bindings, deadline,
            # resources and command authority. This adapter adds no bypass.
            store.register(manifest)
        else:
            require(run['manifest_sha256'] == digest(manifest), 'Experiment run ID is bound to another manifest')
            if run['status'] in TERMINAL:
                if run['status'] != 'COMPLETED':
                    return feedback(root, ident)
                continue
            if run['attempt_id'] is not None:
                recovered = store.recover(run['id'])
                if recovered.get('run_status') not in {'SUCCEEDED', 'FAILED', 'INTERRUPTED'}:
                    return {'status': 'RECOVERY_REQUIRED', 'run_id': run['id'], 'execution_started': False}
                if recovered.get('run_status') != 'SUCCEEDED':
                    return feedback(root, ident)
                continue
        receipt = store.execute(manifest['id'])
        if receipt.get('run_status') != 'SUCCEEDED':
            return feedback(root, ident)
    return feedback(root, ident)


def _artifact(store, receipt, relative):
    item = next((a for a in receipt['artifacts'] if a['kind'] == 'project_output' and a['path'] == relative), None)
    require(item is not None, 'Experiment receipt omitted declared result')
    path = store._path(relative, True, store.snapshot()['contract'])
    require(path.stat().st_size <= MAX_BYTES, 'Experiment JSON exceeds byte cap')
    raw = path.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == item['sha256'], 'Experiment output hash mismatch')
    from rds_artifacts import strict_json
    return strict_json(raw.decode('utf-8-sig')), item['sha256']


def feedback(root, ident):
    store = ProjectStore(root)
    row = _find(store, 'PROPOSAL', ident)
    require(row is not None, 'Unknown structure proposal')
    existing = _find(store, 'FEEDBACK', ident)
    if existing:
        _check_feedback(store, row, existing)
        return existing  # Original consumed receipt IDs, no extra billing.
    with _meter(store, 'feedback'):
        state = store.snapshot()
        _live(store, _find(store, 'REQUEST', row['request_id']), allow_owned_updates=True)
        experiment = row['proposal']['experiment']
        receipts = {r['run_id']: r for r in state['receipts']}
        runs = experiment['runs']
        result = {'id': ident, 'proposal_sha256': row['proposal_sha256'], 'scope_sha256': row['scope_sha256'],
                  'status': 'UNKNOWN', 'goal_status': 'UNKNOWN', 'scientific_support': 'UNKNOWN',
                  'receipts': [], 'observation': None, 'next_decision': 'Retain branch; repair missing evidence', 'reason': None}
        selected = [receipts.get(r['id']) for r in runs]
        recorded_runs = {r['id']: r for r in state['runs']}
        require(all(r is None or recorded_runs[m['id']]['manifest_sha256'] == digest(m)
                    for m, r in zip(runs, selected)), 'Feedback run manifest differs from proposal')
        result['receipts'] = [r['sha256'] for r in selected if r]
        require(selected[0] is not None, 'Candidate has not settled; observe or recover the original run')
        if any(r is None or r['run_status'] != 'SUCCEEDED' for r in selected):
            require(selected[0]['run_status'] != 'SUCCEEDED' or selected[1] is not None,
                    'Independent verifier has not settled; continue original experiment')
            result['reason'] = 'FAILED_TIMEOUT_OR_INTERRUPTED_EXPERIMENT'
        else:
            _, candidate_sha = _artifact(store, selected[0], experiment['candidate_output'])
            verdict, verdict_sha = _artifact(store, selected[1], experiment['verdict_output'])
            require(isinstance(verdict, dict) and verdict.get('scope_sha256') == row['scope_sha256']
                    and verdict.get('proposal_sha256') == row['proposal_sha256']
                    and verdict.get('candidate_run_id') == runs[0]['id']
                    and verdict.get('candidate_receipt_sha256') == selected[0]['sha256']
                    and verdict.get('output_sha256') == candidate_sha, 'Verifier scope/proposal/run/hash mismatch')
            require(verdict.get('observation') in {'SUPPORT', 'REFUTE', 'UNKNOWN'}
                    and verdict.get('goal_status') in {'PASS', 'FAIL', 'UNKNOWN'}, 'Invalid independent observation')
            result.update(status={'SUPPORT': 'TEST_SUPPORTED', 'REFUTE': 'TEST_REFUTED', 'UNKNOWN': 'UNKNOWN'}[verdict['observation']],
                          goal_status=verdict['goal_status'], observation=verdict['observation'],
                          output_sha256=candidate_sha, verdict_sha256=verdict_sha,
                          next_decision=row['review']['next_if_positive'] if verdict['observation'] == 'SUPPORT'
                          else row['review']['next_if_negative'] if verdict['observation'] == 'REFUTE'
                          else 'Retain branch; obtain distinguishing evidence')
        return _put(store, 'FEEDBACK', ident, result)


def _check_feedback(store, row, result):
    """Reusing a result cannot hide changed originals or a different run."""
    state = store.snapshot()
    selected = [r for r in state['receipts'] if r['run_id'] in {m['id'] for m in row['proposal']['experiment']['runs']}]
    require(sorted(r['sha256'] for r in selected) == sorted(result['receipts']), 'Feedback receipt identity changed')
    if 'output_sha256' in result:
        candidate, verifier = row['proposal']['experiment']['runs']
        index = {r['run_id']: r for r in selected}
        _, csha = _artifact(store, index[candidate['id']], row['proposal']['experiment']['candidate_output'])
        _, vsha = _artifact(store, index[verifier['id']], row['proposal']['experiment']['verdict_output'])
        require(csha == result['output_sha256'] and vsha == result['verdict_sha256'], 'Feedback original hashes changed')


def activate(root, ident):
    store = ProjectStore(root)
    prior = current(root)
    if prior and (prior.get('revision') or {}).get('kind') == PREFIX + 'ACTIVATION' and prior['revision']['id'] == ident:
        _check_feedback(store, _find(store, 'PROPOSAL', ident), _find(store, 'FEEDBACK', ident))
        return {'status': 'EXPERIMENTAL_STRUCTURE_ACTIVE', 'id': ident, 'snapshot_sha256': prior['sha256'], 'logical_relations_adopted': 0}
    with _meter(store, 'activate'):
        row, observed = _find(store, 'PROPOSAL', ident), _find(store, 'FEEDBACK', ident)
        require(row is not None and observed and observed['status'] == 'TEST_SUPPORTED', 'Candidate lacks supporting independent observation')
        _check_feedback(store, row, observed)
        req = _find(store, 'REQUEST', row['request_id'])
        _, saved = _live(store, req, allow_owned_updates=True)
        candidate = deepcopy(row['candidate_map'])
        for key in ('nodes', 'hyperedges'):
            candidate[key] = [r for r in candidate[key] if not r['id'].startswith('owned:')] + [r for r in saved['dependency_map'][key] if r['id'].startswith('owned:')]
        # Reuse the TMS snapshot transaction for the recoverable adoption
        # marker. A crash after save needs no second adoption event.
        revision = {'kind': PREFIX + 'ACTIVATION', 'id': ident, 'proposal_sha256': row['proposal_sha256'],
                    'feedback_sha256': digest(observed), 'previous_map': saved['map']}
        def check(db):
            require(digest(store._contract(db)) == req['scope']['contract_sha256'], 'Contract changed before topology publication')
            require(not any(r['status'] not in TERMINAL for r in store._runs(db)), 'Settle active runs before topology publication')
        snapshot = save(root, candidate, expected=saved['sha256'], revision=revision,
                        source_base=saved['source_base_dir'], validate_current=check)
        return {'status': 'EXPERIMENTAL_STRUCTURE_ACTIVE', 'id': ident, 'snapshot_sha256': snapshot,
                'scientific_support': 'UNKNOWN', 'authorization': 'UNCHANGED', 'logical_relations_adopted': 0}


def rollback(root, ident):
    store = ProjectStore(root)
    prior = current(root)
    if prior and (prior.get('revision') or {}).get('kind') == PREFIX + 'ROLLBACK' and prior['revision']['id'] == ident:
        return {'status': 'ROLLED_BACK', 'id': ident, 'snapshot_sha256': prior['sha256'], 'branch_retained': True}
    with _meter(store, 'rollback'):
        saved = current(root)
        from rds_math import blob
        active = saved
        revision = active.get('revision') if active else None
        def declared(spec):
            return {k: [r for r in spec[k] if not r['id'].startswith('owned:')] for k in ('nodes', 'hyperedges')}
        with store._db(True) as db:
            for _ in range(64):
                if revision and revision.get('kind') == PREFIX + 'ACTIVATION' and revision.get('id') == ident:
                    break
                require(active and active.get('parent') and revision is None, 'Active snapshot differs; reconcile intervening evidence before rollback')
                record = db.execute('SELECT body FROM dependency_snapshots WHERE sha256=?', (active['parent'],)).fetchone()
                require(record is not None, 'Missing activation ancestor')
                body = json.loads(record['body'])
                require(digest(body) == active['parent'], 'Activation ancestry hash mismatch')
                active = {**body, 'sha256': active['parent'], 'dependency_map': json.loads(blob(store.root, body['map']))}
                require(declared(active['dependency_map']) == declared(saved['dependency_map']), 'Intervening model edits prevent rollback')
                revision = active.get('revision')
            else:
                raise ValueError('Activation ancestry exceeds bounded recovery scope')
        original = json.loads(blob(store.root, revision['previous_map']))
        for key in ('nodes', 'hyperedges'):
            original[key] = [r for r in original[key] if not r['id'].startswith('owned:')] + [r for r in saved['dependency_map'][key] if r['id'].startswith('owned:')]
        def check(db):
            require(not any(r['status'] not in TERMINAL for r in store._runs(db)), 'Settle active runs before rollback')
        snapshot = save(root, original, expected=saved['sha256'],
                        revision={'kind': PREFIX + 'ROLLBACK', 'id': ident, 'retained_candidate': saved['map']},
                        source_base=saved['source_base_dir'], validate_current=check)
        return {'status': 'ROLLED_BACK', 'id': ident, 'snapshot_sha256': snapshot, 'branch_retained': True}


def inspect(root):
    store = ProjectStore(root)
    events = _events(store)
    return {'status': 'RETAINED', 'records': [{k: e[k] for k in ('kind', 'id', 'sha256', 'record') if k in e}
                                            for e in events if 'record' in e],
            'budget': store.snapshot()['budget'], 'scientific_support': 'UNKNOWN'}


def drive(root, steps=1):
    """Bounded host/model handoff over current proposals, without a model menu."""
    require(type(steps) is int and 1 <= steps <= 8, 'Drive steps must be 1..8')
    history = []
    for _ in range(steps):
        decision = next_step(root)
        if decision['selected'] is None:
            return {'status': decision['status'], 'decisions': history + [decision],
                    'agent_tasks': request(root)['tasks'] if decision['status'] == 'REQUEST_OPEN_EXPLORATION' else [],
                    'authorization': 'UNCHANGED', 'scientific_support': 'UNKNOWN'}
        observed = advance(root, decision['selected'])
        history.append({'decision': decision, 'feedback': observed})
        if observed['status'] == 'RECOVERY_REQUIRED':
            return {'status': 'RECOVERY_REQUIRED', 'decisions': history, 'execution_started': False}
    return {'status': 'STEP_LIMIT', 'decisions': history, 'next_move': 'structure drive', 'scientific_support': 'UNKNOWN'}
