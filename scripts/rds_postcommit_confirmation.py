"""One program-generated future-sample challenge after original MLP weights settle.

The existing ledger owns the challenge, attempt and receipt. This is a finite
CPU check; it makes no hidden-data or distribution-wide generalization claim.
"""
from copy import deepcopy
import hashlib
import math
import re
import secrets

from rds_artifacts import strict_json
from rds_project import canonical, digest, require

KIND = 'torch_postcommit_mlp'
EVENT = 'DOMAIN_CHALLENGE_COMMITTED'


def enabled(contract):
    return contract.get('advisor_policy', {}).get('confirmation', {}).get('rules', {}).get('kind') == KIND


def validate_claim(claim):
    require(isinstance(claim, dict) and set(claim) == {'schema', 'kind', 'layers', 'activation',
            'sampler', 'label', 'sample_count', 'max_mse'} and type(claim['schema']) is int and
            claim['schema'] == 1 and claim['kind'] == KIND, 'Invalid post-commit MLP claim')
    layers = claim['layers']
    require(isinstance(layers, list) and len(layers) == 4 and
            all(type(n) is int and 1 <= n <= 16 for n in layers) and layers[-1] == 1 and
            claim['activation'] == 'tanh' and claim['sampler'] == 'sha256_uniform_v1' and
            claim['label'] == 'cubic_mean_v1' and type(claim['sample_count']) is int and
            1 <= claim['sample_count'] <= 256 and type(claim['max_mse']) in {int, float} and
            0 <= claim['max_mse'] <= 1e6 and math.isfinite(claim['max_mse']),
            'Unsupported bounded CPU MLP architecture, sampler or threshold')
    return claim


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


def _candidate(store, db, contract, value):
    from rds_domain_confirmation import _output
    rid = value['candidate_runs'][0]
    row = db.execute('SELECT run_id,sha256,body FROM receipts WHERE run_id=?', (rid,)).fetchone()
    require(row is not None, 'Post-commit challenge requires an original candidate receipt')
    receipt = store._receipt(row)
    run = store._run(db, rid)
    require(store._observe(db, run) == receipt and receipt['run_status'] == 'SUCCEEDED' and
            receipt.get('process_started') is True and receipt['bindings_before'] == receipt['bindings_after'] == contract['bindings'],
            'Post-commit challenge requires successful settled current weights')
    weights, artifact = _output(store, receipt, value['rules']['candidate_output'])
    return receipt, weights, artifact


def records(store, db, contract):
    if not enabled(contract):
        return []
    from rds_domain_confirmation import _json, _ref
    from rds_math import blob
    from rds_method_revision import contract_history
    value = contract['advisor_policy']['confirmation']
    claim = validate_claim(_json(_ref(store, contract, value['claim'], 'config'), value['claim']['sha256']))
    rows = db.execute("SELECT id,body FROM events WHERE json_extract(body,'$.kind')=? ORDER BY id", (EVENT,)).fetchall()
    require(len(rows) <= 1, 'Duplicate post-commit challenge; reseeding is forbidden')
    result = []
    for row in rows:
        event = strict_json(row['body'])
        require(set(event) == {'kind', 'run_id', 'candidate_run_id', 'candidate_receipt_sha256', 'weights',
                'genesis_contract_sha256', 'effective_contract_sha256', 'declaration_sha256',
                'manifest_sha256', 'challenge', 'sha256'} and
                event['sha256'] == digest({k:v for k,v in event.items() if k != 'sha256'}),
                'Post-commit challenge event integrity failure')
        receipt, _, weights = _candidate(store, db, contract, value)
        history = contract_history(db)
        require(event['candidate_receipt_sha256'] == receipt['sha256'] and event['weights'] == weights and
                event['candidate_run_id'] == value['candidate_runs'][0] and event['run_id'] == value['confirmation_runs'][0] and
                event['genesis_contract_sha256'] == history[0]['sha256'] and
                event['effective_contract_sha256'] == digest(contract) and
                event['declaration_sha256'] == digest(value), 'Post-commit challenge identity differs')
        completed = db.execute("SELECT id FROM events WHERE json_extract(body,'$.kind')='ATTEMPT_FINISHED' AND "
                               "json_extract(body,'$.run_id')=? AND json_extract(body,'$.sha256')=?",
                               (receipt['run_id'], receipt['sha256'])).fetchall()
        require(len(completed) == 1 and completed[0]['id'] < row['id'], 'Challenge was not committed after the candidate')
        route = next(r['manifest'] for r in contract['advisor_policy']['routes'] if r['manifest']['id'] == event['run_id'])
        require(event['manifest_sha256'] == digest(route), 'Challenge confirmation route differs')
        challenge = strict_json(blob(store.root, event['challenge']).decode('utf-8'))
        require(isinstance(challenge, dict) and set(challenge) == {'schema','seed','claim_sha256'} and
                type(challenge['schema']) is int and challenge['schema'] == 1 and
                challenge['claim_sha256'] == value['claim']['sha256'] and
                isinstance(challenge['seed'], str) and re.fullmatch('[a-f0-9]{64}', challenge['seed']),
                'Invalid original challenge declaration')
        validate_claim(claim)
        result.append(event)
    return result


def bind_run(store, db, contract, run):
    if not enabled(contract):
        return
    value = contract['advisor_policy']['confirmation']
    if run['id'] != value['confirmation_runs'][0]:
        return
    from rds_quick import cas_bytes
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


def check_run(store, db, contract, run):
    if enabled(contract) and run['id'] == contract['advisor_policy']['confirmation']['confirmation_runs'][0]:
        events = records(store, db, contract)
        require(len(events) == 1 and run.get('confirmation_challenge') == events[0]['challenge'] and
                run['manifest_sha256'] == events[0]['manifest_sha256'], 'Run is missing its original post-commit challenge')


def inspect(store, contract, candidate, confirmation, weights, checked):
    from rds_domain_confirmation import _json, _ref
    from rds_math import blob
    value = contract['advisor_policy']['confirmation']
    claim = validate_claim(_json(_ref(store, contract, value['claim'], 'config'), value['claim']['sha256']))
    with store._db(True) as db:
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
