"""Kernel autonomy binding and launch checks; no decision/controller callbacks.

Registration freezes validated original evidence on a read transaction. The
writer checks that exact ledger and CAS before attaching the existing request.
These values do not authorize a command or replace the ordinary budget checks.
"""
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from rds_admission_read import PreparedAdmission, check_locked, prepare
from rds_autonomy_read import REQUESTED, records
from rds_cas_read import blob
from rds_feasibility_values import file_sha
from rds_project_values import canonical, digest, require
from rds_source_documents import strict_json


@dataclass(frozen=True, slots=True)
class AutonomyBinding:
    schema_version: int
    run_id: str
    contract_sha256: str
    request_json: str | None
    fence: PreparedAdmission


def _check_worker_adapter(store, contract, rid, *, hash_file=None):
    hash_file = file_sha if hash_file is None else hash_file
    slot = next(s for s in contract['advisor_policy']['autonomy']['repair_slots'] if s['run_id'] == rid)
    path = store._path(slot['worker_path'])
    bound = next(b for b in contract['bindings'] if store._path(b['path']) == path)
    require(hash_file(path) == bound['sha256'] == hash_file(Path(__file__).with_name('rds_autonomy_worker.py')),
            'Unsupported adapter for a new model attempt; retain and inspect original settled evidence')


def _request_binding(store, db, contract, run, *, hash_file=None):
    config = contract.get('advisor_policy', {}).get('autonomy')
    if config:
        require(len(store._runs(db)) < config['max_steps'], 'AUTONOMY_TOTAL_STEP_LIMIT')
    if config and run['id'] in {s['run_id'] for s in config['repair_slots']}:
        _check_worker_adapter(store, contract, run['id'], hash_file=hash_file)
        history = records(store, db, contract)
        event = next((e for e in history if e['kind'] == REQUESTED and e['run_id'] == run['id']), None)
        require(event is not None and event['parent_sha256'] == run['effective_contract_sha256'], 'Repair request parent is stale')
        return event['request'], history
    return None, ()


def bind_run(store, db, contract, run, *, hash_file=None):
    """Compatibility entry; production registration uses the prepared path."""
    request, _ = _request_binding(store, db, contract, run, hash_file=hash_file)
    if request is not None:
        run['autonomy_request'] = deepcopy(request)


def prepare_binding(store, contract, run):
    if 'autonomy' not in contract.get('advisor_policy', {}):
        return None
    with store._db(True) as db:
        db.execute('BEGIN')
        require(digest(store._contract(db)) == run['effective_contract_sha256'] == digest(contract),
                'Method revision changed during run registration')
        request, history = _request_binding(store, db, contract, run)
        files = []
        for event in history:
            if event['kind'] == REQUESTED:
                ref = event['request']
                path = (store.root / ref['path']).resolve()
                files.append({'path': str(path.relative_to(store.root)),
                              'sha256': ref['sha256'], 'size': ref['bytes']})
        fence = prepare(db, store.path, run['manifest'], files, None)
        return AutonomyBinding(1, run['id'], digest(contract),
                               canonical(request) if request is not None else None, fence)


def bind_locked(store, db, contract, run, packet):
    require(type(packet) is AutonomyBinding and packet.schema_version == 1,
            'Native autonomy binding is missing; user JSON is not admission')
    require(packet.run_id == run['id'] and packet.contract_sha256 == digest(contract)
            == run['effective_contract_sha256'], 'Autonomy registration binding mismatch')
    check_locked(db, store.path, store.root, run['manifest'], packet.fence)
    if packet.request_json is not None:
        _check_worker_adapter(store, contract, run['id'])
        run['autonomy_request'] = strict_json(packet.request_json)


def check_run(store, db, contract, run, *, hash_file=None):
    hash_file = file_sha if hash_file is None else hash_file
    if 'autonomy_request' in run:
        _check_worker_adapter(store, contract, run['id'], hash_file=hash_file)
        request = strict_json(blob(store.root, run['autonomy_request']).decode('utf-8'))
        require(request['run_id'] == run['id'] and request['parent_sha256'] == run['effective_contract_sha256'],
                'Repair request identity changed before launch')
        require(hash_file(request['provider']['argv'][0]) == request['provider']['executable_sha256'],
                'Model provider executable changed before launch')
