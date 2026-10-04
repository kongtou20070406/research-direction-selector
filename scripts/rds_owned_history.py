"""Committed owned choices and receipts in the existing checkpoint history.

Operational failures are evidence for review, never scientific rejections. All
writers use the caller's ledger transaction and grant no execution authority.
"""
from copy import deepcopy
import json
import sqlite3

from rds_checkpoints import MAX_BYTES, _checkpoint_record, append_checkpoint, read_checkpoint
from rds_method_revision import contract_history
from rds_project import TERMINAL, ProjectStore, canonical, digest, execution_route, file_sha, require

MAX_CHECKPOINTS = 512
MAX_RUNS = 128
COMPLETION_HEADROOM = 128 * 1024


def method_identity(store, contract, manifest, *, _executor_sha256=None):
    return execution_route(manifest['argv'], contract['bindings'], manifest['outpaths'], store.root,
                           contract.get('objective_sha256'), arm=manifest['arm'],
                           executor_sha256=(_executor_sha256 if _executor_sha256 is not None
                                            else file_sha(store._command(manifest['argv']))))


def bind_graph(store, contract, graph, *, _executor_hashes=None):
    """Derive identity on a graph copy; never change frozen policy declarations."""
    result = deepcopy(graph)
    routes = {r['candidate']: r['manifest'] for r in contract['advisor_policy']['routes']}
    executors = {} if _executor_hashes is None else _executor_hashes
    def bind(action):
        if isinstance(action, dict) and action.get('id') in routes:
            manifest = routes[action['id']]
            executor = store._command(manifest['argv'])
            if executor not in executors:
                executors[executor] = file_sha(executor)
            action['owned_execution_identity'] = method_identity(
                store, contract, manifest, _executor_sha256=executors[executor])
    for node in result['nodes']:
        config = node.get('executable', {})
        bind(config.get('action'))
        for key in ('preconditions', 'satisfied_when'):
            for condition in config.get(key, []):
                if isinstance(condition, dict):
                    bind(condition.get('on_false'))
    return result


def history_cut(store, db, *, pending_choice=None, pending_completion=None):
    """Verified local decision-history identities; snapshots cannot invent ancestors."""
    marked = [run for run in store._runs(db) if run.get('owned_history_recorded') is True]
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone():
        require(not any(run['id'] != pending_choice for run in marked),
                'Owned history is missing a committed decision checkpoint')
        return []
    require(db.execute('SELECT count(*) FROM checkpoints').fetchone()[0] <= MAX_CHECKPOINTS,
            'Owned decision history exceeds its bounded coverage')
    lineage = {h['sha256']: h['contract'] for h in contract_history(db)}
    cut, records = [], {}
    for row in db.execute('SELECT id FROM checkpoints ORDER BY rowid').fetchall():
        retained = read_checkpoint(db, row[0], root=store.root)
        record = retained['record']
        require(record['kind'] == 'project' and record['contract_sha256'] in lineage
                and record['snapshot']['contract'] == lineage[record['contract_sha256']],
                'Owned history checkpoint contract is outside verified lineage')
        cut.append({'id': row[0], 'sha256': retained['sha256']})
        records[row[0]] = record
    for run in marked:
        stages = [('before', pending_choice)]
        if run['status'] in TERMINAL:
            stages.append(('after', pending_completion))
        for stage, pending in stages:
            record = records.get(checkpoint_id(stage, run))
            if record is None and run['id'] == pending:
                continue  # Only this caller's not-yet-written atomic record is exempt.
            require(record is not None, 'Owned history is missing a committed ' + stage + ' checkpoint')
            identity = record['decision'].get('owned_history', {})
            require(all(identity.get(k) == run[k] for k in
                        ('manifest_sha256', 'effective_contract_sha256'))
                    and identity.get('run_id') == run['id'],
                    'Owned history checkpoint differs from its marked run')
            if stage == 'after':
                receipt_row = db.execute('SELECT run_id,sha256,body FROM receipts WHERE run_id=?', (run['id'],)).fetchone()
                require(receipt_row is not None, 'Owned history completion has no retained receipt')
                receipt = store._receipt(receipt_row)
                require(record['decision'].get('previous_checkpoint') == checkpoint_id('before', run)
                        and record['decision'].get('execution') == {
                            'run_id': run['id'], 'attempt_id': run['attempt_id'],
                            'receipt_sha256': receipt['sha256'], 'run_status': receipt['run_status']},
                        'Owned history completion differs from its retained receipt')
    return cut


def prepare_decision(store, report, contract):
    """Retain the actual selected candidate and its decision-time evidence."""
    from rds_quick import choice
    manifest = report.get('selected_manifest')
    require(report.get('status') == 'REVIEWED' and isinstance(manifest, dict),
            'Owned history needs a reviewed selected manifest')
    route = next((r for r in contract['advisor_policy']['routes'] if r['manifest'] == manifest), None)
    require(route is not None, 'Owned history manifest is not an authorized policy route')
    record = choice(report['advice'], report['context'], route['candidate'])
    identity = method_identity(store, contract, manifest)
    require(record['candidate']['action'].get('owned_execution_identity') == identity,
            'Owned history candidate lacks its program-derived method identity')
    record['owned_history'] = {'run_id': manifest['id'], 'manifest_sha256': digest(manifest),
                             'effective_contract_sha256': digest(contract), 'method_sha256': identity,
                             'dependency_snapshot_sha256': report.get('snapshot_sha256')}
    return record


def checkpoint_id(stage, run):
    return 'owned-' + stage + '-' + digest({'run_id': run['id'], 'manifest_sha256': run['manifest_sha256'],
                                          'contract_sha256': run['effective_contract_sha256']})[:48]


def snapshot(store, db):
    """Compact identities over live state; full originals stay in the ledger."""
    budget = {}
    for row in db.execute('SELECT * FROM budget ORDER BY resource'):
        budget[row['resource']] = {'cap': row['cap'], 'spent_measured': row['spent'],
            'charged_estimate': row['charged'], 'reserved': row['reserved'],
            'remaining': row['cap'] - row['spent'] - row['charged'] - row['reserved'],
            'unit': 'seconds' if row['resource'].endswith('_seconds') else row['resource']}
    contract = contract_history(db)[-1]['contract']
    runs = [{'id': run['id'], 'run_id': run['id'], 'status': run['status'],
             'run_status': 'SUCCEEDED' if run['status'] == 'COMPLETED' else run['status'],
             'manifest_sha256': run['manifest_sha256'], 'attempt_id': run['attempt_id']}
            for run in store._runs(db)]
    return {'schema': 1, 'contract': contract, 'contract_sha256': digest(contract), 'budget': budget,
            'runs': runs, 'exposures': [json.loads(r[0]) for r in db.execute('SELECT body FROM exposures ORDER BY id')],
            'receipts': [{'run_id': receipt['run_id'], 'sha256': receipt['sha256']}
                         for receipt in (store._receipt(r) for r in
                                         db.execute('SELECT run_id,sha256,body FROM receipts ORDER BY run_id'))]}


def _outstanding_slots(runs, recorded):
    return {checkpoint_id(stage, run): run['id']
            for run in runs if run.get('owned_history_recorded') is True
            for stage in ('before', 'after') if checkpoint_id(stage, run) not in recorded}


def check_append_slots(root, db, checkpoint_id, *, owned_run_id=None):
    """Protect owned completion capacity inside any checkpoint append lock."""
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runs'").fetchone():
        return
    marked = False
    for row in db.execute('SELECT body FROM runs'):
        try:
            value = json.loads(row[0])
            marked = marked or (isinstance(value, dict) and value.get('owned_history_recorded') is True)
        except (ValueError, TypeError):
            continue  # Generic legacy checkpoint behavior is unchanged.
    if not marked:
        return
    previous_factory = db.row_factory
    try:
        db.row_factory = sqlite3.Row
        runs = ProjectStore(root)._runs(db)
    finally:
        db.row_factory = previous_factory
    recorded = {row[0] for row in db.execute('SELECT id FROM checkpoints')} if db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone() else set()
    outstanding = _outstanding_slots(runs, recorded)
    if checkpoint_id in outstanding:
        require(owned_run_id == outstanding[checkpoint_id],
                'Checkpoint identity is reserved for the owned lifecycle')
    require(len(recorded) + 1 + len(set(outstanding) - {checkpoint_id}) <= MAX_CHECKPOINTS,
            'Owned history needs checkpoint slots for outstanding completions before appending a checkpoint')


def _preflight(store, db, run, decision, live_snapshot, history):
    """Reserve bounded completion growth before committing any new worker."""
    require(len(live_snapshot['runs']) <= MAX_RUNS, 'Owned history exceeds 128 retained runs')
    recorded = {item['id'] for item in history}
    outstanding = _outstanding_slots(store._runs(db), recorded)
    require(len(recorded) + len(outstanding) <= MAX_CHECKPOINTS,
            'Owned history needs checkpoint slots for outstanding completions before launching a worker')
    data = [b for b in live_snapshot['contract']['bindings'] if b['role'] == 'data']
    pending_exposure_bytes = 0
    for pending in live_snapshot['runs']:
        if pending['status'] == 'RESERVED' and pending.get('attempt_id') is None:
            # Match the existing exposure writer. The margin bounds its time
            # encoding and enclosing list separator; all data bytes stay full.
            exposure = {'run_id': pending['id'], 'attempt_id': 'f' * 32, 'at': 1e20,
                        'kind': 'declared_input_access', 'data': data, 'eligibility_change': 'none'}
            pending_exposure_bytes += len(canonical(exposure).encode('utf-8')) + 64
    _, raw = _checkpoint_record(store.root, checkpoint_id('before', run), live_snapshot,
                                kind='project', decision=decision)
    require(len(raw.encode('utf-8')) <= MAX_BYTES - COMPLETION_HEADROOM - pending_exposure_bytes,
            'Owned history checkpoint needs completion headroom before launching a worker')


def _check_choice(store, db, run, decision, snapshot):
    contract = contract_history(db)[-1]['contract']
    require(snapshot['contract'] == contract and digest(contract) == run['effective_contract_sha256'],
            'Owned decision snapshot differs from the verified live method')
    executor = store._command(run['manifest']['argv'])
    executors = {executor: file_sha(executor)}
    expected = {'run_id': run['id'], 'manifest_sha256': run['manifest_sha256'],
                'effective_contract_sha256': run['effective_contract_sha256'],
                'method_sha256': method_identity(store, contract, run['manifest'],
                                                _executor_sha256=executors[executor])}
    require(isinstance(decision, dict) and decision.get('outcome') == 'plan_locked'
            and decision.get('scientific_support') == 'UNKNOWN'
            and isinstance(decision.get('owned_history'), dict)
            and all(decision['owned_history'].get(k) == v for k, v in expected.items()),
            'Owned decision differs from its committed run')
    scope = contract['advisor_policy']['context']['decision']
    require(decision['question_id'] == scope['id'] and decision['goal_revision'] == scope['goal_revision']
            and decision['scope'] == scope['scope'] and decision.get('goal_conditions') == scope['goal_conditions'],
            'Owned decision changes frozen goal or scope')
    route = next((r for r in contract['advisor_policy']['routes'] if r['manifest'] == run['manifest']), None)
    require(route is not None and decision['candidate']['action']['id'] == route['candidate']
            and decision['candidate']['action'].get('owned_execution_identity') == expected['method_sha256'],
            'Owned decision candidate differs from the frozen route')
    graph = bind_graph(store, contract, contract['advisor_policy']['graph'], _executor_hashes=executors)
    actions = [node.get('executable', {}).get('action') for node in graph['nodes']]
    for node in graph['nodes']:
        config = node.get('executable', {})
        for key in ('preconditions', 'satisfied_when'):
            actions.extend(condition.get('on_false') for condition in config.get(key, [])
                           if isinstance(condition, dict))
    require(decision['candidate']['action'] in actions,
            'Owned decision action differs from the bound policy action')


def capture_choice(store, db, run, decision, live_snapshot=None):
    """Call only at the successful reservation boundary in BEGIN IMMEDIATE."""
    retained = store._run(db, run['id'])
    require(retained == run and run['status'] == 'RESERVED' and run['attempt_id'] is None,
            'Owned choice must accompany its unstarted committed reservation')
    current_snapshot = snapshot(store, db)
    require(live_snapshot is None or live_snapshot == current_snapshot,
            'Owned choice snapshot differs from its owning transaction')
    live_snapshot = current_snapshot
    _check_choice(store, db, run, decision, live_snapshot)
    history = history_cut(store, db, pending_choice=run['id'])
    _preflight(store, db, run, decision, live_snapshot, history)
    return append_checkpoint(db, store.root, checkpoint_id('before', run), live_snapshot,
                             kind='project', decision=decision, idempotent=True, _owned_run_id=run['id'])


def capture_completion(store, db, run, receipt, live_snapshot=None):
    """Reconcile one settled receipt without launching or interpreting its science."""
    require(store._run(db, run['id']) == run, 'Owned completion run is not retained in this ledger')
    row = db.execute('SELECT run_id,sha256,body FROM receipts WHERE run_id=?', (run['id'],)).fetchone()
    require(row is not None and store._receipt(row) == receipt, 'Owned completion receipt is not retained in this ledger')
    require(receipt.get('run_id') == run['id'] and receipt.get('attempt_id') == run['attempt_id']
            and receipt.get('manifest_sha256') == run['manifest_sha256']
            and receipt.get('process_status') == run['status']
            and receipt.get('effective_contract_sha256') == run['effective_contract_sha256'],
            'Owned completion receipt/run identity differs')
    require(db.execute("SELECT 1 FROM events WHERE json_extract(body,'$.kind')='ATTEMPT_FINISHED' "
                       "AND json_extract(body,'$.run_id')=? AND json_extract(body,'$.sha256')=?",
                       (run['id'], receipt['sha256'])).fetchone() is not None,
            'Owned completion receipt has no settlement event')
    history_cut(store, db, pending_completion=run['id'])
    before = read_checkpoint(db, checkpoint_id('before', run), root=store.root)
    require(before is not None, 'Owned completion has no committed decision checkpoint')
    decision = deepcopy(before['record']['decision'])
    require(decision.get('owned_history', {}).get('run_id') == run['id']
            and decision['owned_history'].get('manifest_sha256') == run['manifest_sha256']
            and decision['owned_history'].get('effective_contract_sha256') == run['effective_contract_sha256'],
            'Owned completion original decision identity differs')
    decision['previous_checkpoint'] = before['record']['id']
    decision['execution'] = {'run_id': run['id'], 'attempt_id': run['attempt_id'],
                             'receipt_sha256': receipt['sha256'], 'run_status': receipt['run_status']}
    decision['pending_evidence'] = ['Assess original outputs; process completion does not prove or reject a scientific hypothesis']
    after_id = checkpoint_id('after', run)
    existing = read_checkpoint(db, after_id, root=store.root)
    if existing is not None:
        require(existing['record']['decision'] == decision, 'Owned completion checkpoint conflicts with its retained receipt')
        return {'status': 'ALREADY_SAVED', 'id': after_id, 'sha256': existing['sha256']}
    current_snapshot = snapshot(store, db)
    require(live_snapshot is None or live_snapshot == current_snapshot,
            'Owned completion snapshot differs from its owning transaction')
    live_snapshot = current_snapshot
    require(live_snapshot['contract'] == contract_history(db)[-1]['contract'],
            'Owned completion snapshot differs from the verified live method')
    return append_checkpoint(db, store.root, after_id, live_snapshot, kind='project', decision=decision,
                             idempotent=True, _owned_run_id=run['id'])
