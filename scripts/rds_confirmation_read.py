"""Read and validate original confirmation evidence; never select, reseed or write."""
import math
import re

from rds_cas_read import blob
from rds_contract_read import contract_history
from rds_original_read import _read_original
from rds_project_read_services import ProjectReadServices
from rds_project_values import digest, require
from rds_source_documents import strict_json

MAX_BYTES = 2 * 1024 * 1024
KIND = 'torch_postcommit_mlp'
EVENT = 'DOMAIN_CHALLENGE_COMMITTED'


def _ref(store, contract, ref, role):
    require(isinstance(ref, dict) and set(ref) == {'path', 'sha256'} and
            isinstance(ref['sha256'], str) and re.fullmatch('[a-f0-9]{64}', ref['sha256']),
            'Invalid domain confirmation file reference')
    path = store._path(ref['path'])
    matches = [b for b in contract['bindings'] if store._path(b['path']) == path]
    require(matches and {b['role'] for b in matches} == {role} and
            all(b['sha256'] == ref['sha256'] for b in matches),
            'Domain confirmation file must be exclusively frozen as ' + role)
    return path

def _json(path, expected, *, max_bytes=MAX_BYTES):
    require(path.is_file() and not path.is_symlink() and path.stat().st_size <= max_bytes,
            'Domain confirmation input missing, linked, or too large')
    raw = path.read_bytes()
    import hashlib
    require(hashlib.sha256(raw).hexdigest() == expected, 'Domain confirmation input changed')
    return strict_json(raw.decode('utf-8-sig'))

def _output(store, receipt, path, *, max_bytes=MAX_BYTES):
    artifacts = [a for a in receipt['artifacts'] if a['kind'] == 'project_output' and a['path'] == path]
    require(len(artifacts) == 1, 'Domain original output missing or ambiguous')
    require(type(artifacts[0].get('size')) is int and artifacts[0]['size'] <= max_bytes,
            'Domain original exceeds bounded JSON size')
    raw = _read_original(store, artifacts[0], keep=True)
    require(raw is not None, 'Domain original exceeds bounded JSON size')
    return strict_json(raw.decode('utf-8-sig')), artifacts[0]

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

def _candidate(store, db, contract, value):
    rid = value['candidate_runs'][0]
    row = ProjectReadServices(db).receipt_row(rid)
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
    value = contract['advisor_policy']['confirmation']
    claim = validate_claim(_json(_ref(store, contract, value['claim'], 'config'), value['claim']['sha256']))
    rows = ProjectReadServices(db).events((EVENT,), maximum=1,
                                          limit_message='Duplicate post-commit challenge; reseeding is forbidden')
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
        completed = ProjectReadServices(db).completion_ids(receipt['run_id'], receipt['sha256'])
        require(len(completed) == 1 and completed[0] < row['id'], 'Challenge was not committed after the candidate')
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

def check_run(store, db, contract, run):
    if enabled(contract) and run['id'] == contract['advisor_policy']['confirmation']['confirmation_runs'][0]:
        events = records(store, db, contract)
        require(len(events) == 1 and run.get('confirmation_challenge') == events[0]['challenge'] and
                run['manifest_sha256'] == events[0]['manifest_sha256'], 'Run is missing its original post-commit challenge')
