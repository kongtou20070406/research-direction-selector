"""One program-generated future-sample challenge after original MLP weights settle.

The existing ledger owns the challenge, attempt and receipt. This is a finite
CPU check; it makes no hidden-data or distribution-wide generalization claim.
"""
from dataclasses import dataclass
from rds_admission_read import PreparedAdmission, StaleAdmission, check_files, check_locked, prepare
from rds_contract_read import contract_history
from copy import deepcopy
import hashlib
import math
import re
import secrets

from rds_artifacts import strict_json
from rds_project_values import canonical, digest, require
from rds_confirmation_read import KIND, EVENT, enabled, validate_claim, _candidate, records, check_run


def samples(claim, seed):
    validate_claim(claim)
    require(isinstance(seed, str) and re.fullmatch('[a-f0-9]{64}', seed), 'Invalid retained challenge seed')
    x = []
    for i in range(claim['sample_count']):
        row = []
        for j in range(claim['layers'][0]):
            raw = hashlib.sha256(bytes.fromhex(seed) + i.to_bytes(4, 'big') + j.to_bytes(4, 'big')).digest()
            row.append(int.from_bytes(raw[:7], 'big') / 2**56 * 2 - 1)
        x.append(row)
    y = [sum(.5*v*v*v - .2*v + .1 for v in row) / len(row) for row in x]
    return {'x': x, 'y': y}


def metric(claim, weights, data):
    """Evaluate hash-acquired JSON weights, never import candidate source."""
    validate_claim(claim)
    layers = claim['layers']
    finite = lambda x: type(x) in {int, float} and abs(x) <= 1e6 and math.isfinite(x)
    require(isinstance(weights, dict) and set(weights) == {'layers'} and
            isinstance(weights['layers'], list) and len(weights['layers']) == len(layers)-1,
            'Invalid original MLP weights')
    for w, n, m in zip(weights['layers'], layers[:-1], layers[1:]):
        require(isinstance(w, dict) and set(w) == {'weight', 'bias'} and
                isinstance(w['weight'], list) and len(w['weight']) == m and
                all(isinstance(row, list) and len(row) == n and all(finite(v) for v in row) for row in w['weight']) and
                isinstance(w['bias'], list) and len(w['bias']) == m and all(finite(v) for v in w['bias']),
                'MLP weight architecture or finite values differ')
    import torch
    with torch.no_grad():
        pred = torch.tensor(data['x'], dtype=torch.float32, device='cpu')
        for index, w in enumerate(weights['layers']):
            pred = pred @ torch.tensor(w['weight'], dtype=torch.float32, device='cpu').T + torch.tensor(w['bias'], dtype=torch.float32, device='cpu')
            if index < len(weights['layers'])-1:
                pred = torch.tanh(pred)
        loss = float(torch.mean((pred.reshape(-1) - torch.tensor(data['y'], dtype=torch.float32, device='cpu'))**2).item())
    require(math.isfinite(loss), 'Post-commit CPU metric is non-finite')
    return loss


def bind_run(store, db, contract, run):
    if not enabled(contract):
        return
    value = contract['advisor_policy']['confirmation']
    if run['id'] != value['confirmation_runs'][0]:
        return
    from rds_cas import cas_bytes
    from rds_method_revision import contract_history
    previous = records(store, db, contract)
    if previous:
        event = previous[0]
    else:
        receipt, _, weights = _candidate(store, db, contract, value)
        challenge = {'schema':1, 'seed':secrets.token_hex(32), 'claim_sha256':value['claim']['sha256']}
        event = {'kind':EVENT, 'run_id':run['id'], 'candidate_run_id':receipt['run_id'],
                 'candidate_receipt_sha256':receipt['sha256'], 'weights':weights,
                 'genesis_contract_sha256':contract_history(db)[0]['sha256'],
                 'effective_contract_sha256':digest(contract), 'declaration_sha256':digest(value),
                 'manifest_sha256':run['manifest_sha256'],
                 'challenge':cas_bytes(store.root,canonical(challenge).encode('utf-8'))}
        event['sha256'] = digest(event)
        db.execute('INSERT INTO events(body) VALUES (?)', (canonical(event),))
    run['confirmation_challenge'] = deepcopy(event['challenge'])



@dataclass(frozen=True, slots=True)
class PreparedConfirmation:
    schema_version: int
    run_id: str
    contract_sha256: str
    existing: bool
    event_json: str
    fence: PreparedAdmission


def _applies(contract, run):
    return enabled(contract) and run['id'] == contract['advisor_policy']['confirmation']['confirmation_runs'][0]


def prepare_binding(store, contract, run):
    if not _applies(contract, run):
        return None
    with store.readonly() as db:
        db.execute('BEGIN')
        if digest(store._contract(db)) != digest(contract):
            raise StaleAdmission('Confirmation contract changed during preparation; STALE')
        value = contract['advisor_policy']['confirmation']
        previous = records(store, db, contract)
        receipt, _, weights = _candidate(store, db, contract, value)
        event = previous[0] if previous else {
            'kind': EVENT, 'run_id': run['id'], 'candidate_run_id': receipt['run_id'],
            'candidate_receipt_sha256': receipt['sha256'], 'weights': weights,
            'genesis_contract_sha256': contract_history(db)[0]['sha256'],
            'effective_contract_sha256': digest(contract), 'declaration_sha256': digest(value),
            'manifest_sha256': run['manifest_sha256']}
        files = []
        for item in contract['bindings']:
            path = store._path(item['path'])
            files.append({'path': str(path.relative_to(store.root)),
                          'sha256': item['sha256'], 'size': path.stat().st_size})
        # _observe validates all retained outputs, not only the selected weights.
        files.extend(item for item in receipt['artifacts'] if item['kind'] == 'project_output')
        refs = [event['challenge']] if previous else []
        if 'objective_sha256' in contract:
            from rds_math import objective
            goal = objective(store.root)
            require(goal is not None, 'Confirmation original objective is missing')
            refs.append(goal['asset'])
        for ref in refs:
            path = (store.root / ref['path']).resolve()
            files.append({'path': str(path.relative_to(store.root)),
                          'sha256': ref['sha256'], 'size': ref['bytes']})
        return PreparedConfirmation(1, run['id'], digest(contract), bool(previous),
                                    canonical(event), prepare(db, store.path, run['manifest'], files, None))


def _identity(contract, run, packet):
    require(type(packet) is PreparedConfirmation and packet.schema_version == 1,
            'Native confirmation preparation is missing; user JSON is not admission')
    if packet.contract_sha256 != digest(contract):
        raise StaleAdmission('Confirmation contract changed after preparation; STALE')
    require(packet.run_id == run['id'] and packet.fence.manifest_sha256 == run['manifest_sha256']
            == digest(run['manifest']), 'Confirmation registration binding mismatch')


def check_binding(store, db, contract, run, packet):
    """Call before any registration writes, on the caller's original transaction."""
    if not _applies(contract, run):
        require(packet is None, 'Unexpected confirmation preparation')
        return
    _identity(contract, run, packet)
    check_locked(db, store.path, store.root, run['manifest'], packet.fence)


def bind_prepared(store, db, contract, run, packet):
    """Publish only at the original post-admission site, after check_binding.

    The caller has already rechecked the fence before its own budget/output
    writes. Do not open another read-side policy computation in this function.
    """
    if not _applies(contract, run):
        require(packet is None, 'Unexpected confirmation preparation')
        return
    _identity(contract, run, packet)
    require(db.in_transaction, 'Confirmation publication needs the caller transaction')
    check_files(store.root, packet.fence.files)
    event = strict_json(packet.event_json)
    if not packet.existing:
        from rds_cas import cas_bytes
        challenge = {'schema': 1, 'seed': secrets.token_hex(32),
                     'claim_sha256': contract['advisor_policy']['confirmation']['claim']['sha256']}
        event['challenge'] = cas_bytes(store.root, canonical(challenge).encode('utf-8'))
        event['sha256'] = digest(event)
        db.execute('INSERT INTO events(body) VALUES (?)', (canonical(event),))
    run['confirmation_challenge'] = event['challenge']


def inspect(store, contract, candidate, confirmation, weights, checked):
    from rds_domain_confirmation import _json, _ref
    from rds_math import blob
    value = contract['advisor_policy']['confirmation']
    claim = validate_claim(_json(_ref(store, contract, value['claim'], 'config'), value['claim']['sha256']))
    with store.readonly() as db:
        db.execute('BEGIN')
        events = records(store, db, contract)
        require(len(events) == 1, 'Missing post-commit challenge')
        event = events[0]
        run = store._run(db, confirmation['run_id'])
        require(store._observe(db, run) == confirmation and confirmation.get('confirmation_challenge') == run.get('confirmation_challenge') == event['challenge'] and
                candidate['sha256'] == event['candidate_receipt_sha256'], 'Confirmation did not consume the original challenge')
    challenge = strict_json(blob(store.root, event['challenge']).decode('utf-8'))
    data = samples(claim, challenge['seed'])
    require(checked.get('challenge_sha256') == event['challenge']['sha256'] and
            checked.get('weights_sha256') == event['weights']['sha256'] and checked.get('samples') == data,
            'Confirmation sample or weight identity differs from its original challenge')
    loss = metric(claim, weights, data)
    return {'task_confirmation':'PASS' if loss <= claim['max_mse'] else 'FAIL',
            'finite_evaluation':'PASS' if loss <= claim['max_mse'] else 'FAIL',
            'assurance':'RECOMPUTED_CPU_FP32_POSTCOMMIT_MLP', 'measured_mse':loss,
            'confirmation_independence':'GENERATED_AFTER_WEIGHT_COMMIT',
            'external_secrecy':'UNKNOWN', 'population_generalization':'UNKNOWN',
            'challenge':event['challenge'], 'sample_count':len(data['x'])}
