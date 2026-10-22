"""Verified autonomy history from the original ledger; no controller or writes.

The caller owns the snapshot transaction. Original event, CAS, run and receipt
checks are retained; missing or damaged evidence never authorizes execution.
"""
from rds_project_values import digest, require
from rds_project_read_services import ProjectReadServices
from rds_source_documents import strict_json
from rds_cas_read import blob

REQUESTED = 'AUTONOMY_MODEL_REQUESTED'
PROCESSED = 'AUTONOMY_MODEL_PROCESSED'
MAX_BYTES = 2 * 1024 * 1024


def _hash_event(body):
    return digest({k: v for k, v in body.items() if k != 'sha256'})

def _events(db, kinds):
    rows = ProjectReadServices(db).events(tuple(kinds), maximum=256,
                                          limit_message='Autonomy event bound exceeded')
    result = [strict_json(r['body']) for r in rows]
    require(all(e.get('sha256') == _hash_event(e) for e in result), 'Autonomy event integrity failure')
    return result

def records(store, db, contract):
    """Coherent immutable event inputs for ordinary Advisor fingerprints."""
    config = contract.get('advisor_policy', {}).get('autonomy')
    if config is None:
        return []
    declared = {s['run_id']: s for s in config['repair_slots']}
    result, seen, processed = [], set(), set()
    reads = ProjectReadServices(db)
    for event in _events(db, (REQUESTED, PROCESSED)):
        rid = event.get('run_id')
        require(rid in declared, 'Autonomy event names an undeclared repair route')
        if event['kind'] == REQUESTED:
            require(rid not in seen, 'Duplicate model request')
            seen.add(rid)
            raw = blob(store.root, event['request'])
            require(len(raw) <= MAX_BYTES, 'Model request exceeds bound')
            request = strict_json(raw.decode('utf-8'))
            require(request['run_id'] == rid and request['parent_sha256'] == event['parent_sha256'] and
                    request['provider'] == config['provider'] and request['response_path'] == declared[rid]['response_path'],
                    'Model request differs from frozen provider/parent/route')
        else:
            require(rid in seen and rid not in processed, 'Duplicate or orphan processed model result')
            processed.add(rid)
            row = reads.receipt_row(rid)
            require(row is not None and store._receipt(row)['sha256'] == event['receipt_sha256'],
                    'Processed model result has no original receipt')
            if event['outcome'] == 'ADOPTED':
                adopted = reads.revision_adoption(event['revision_id'])
                require(adopted is not None, 'Model adoption has no revision event')
        result.append(event)
    for run in store._runs(db):
        if run['id'] in declared:
            request = next((e['request'] for e in result if e['kind'] == REQUESTED and e['run_id'] == run['id']), None)
            require(request is not None and run.get('autonomy_request') == request, 'Repair run lacks its request binding')
            row = reads.receipt_row(run['id'])
            if row:
                require(store._receipt(row).get('autonomy_request') == request, 'Repair receipt request binding differs')
    return result
