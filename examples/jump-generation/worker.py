"""Finite public adapter: diagnose, retrieve scoped material, then synthesize.

Frozen trusted worker, ordinary RDS project command. No paid model or network.
The evaluator's held-out table is never opened by this worker.
"""
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

from rds_jump_search import diagnose, synthesize, evaluate
from instrument import measure


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def original(ref):
    raw = Path(ref['path']).read_bytes()
    if hashlib.sha256(raw).hexdigest() != ref['sha256']:
        raise ValueError('Original source changed')
    return json.loads(raw.decode('utf-8-sig'))


def context(kind):
    with sqlite3.connect('.rds/project.sqlite3') as db:
        events = [json.loads(r[0]) for r in db.execute('SELECT body FROM events ORDER BY id')]
        started = next(e for e in reversed(events) if e.get('kind') == 'STRUCTURE_JUMP')
        started = original(started['record'])
        request = next(e for e in events if e.get('kind') == 'STRUCTURE_REQUEST' and e.get('id') == started['request_id'])
        req = original(request['record'])
        prior, docs = [], []
        for stage in read('jump-generation.json')['stages']:
            if stage['kind'] == kind:
                break
            row = db.execute('SELECT sha256,body FROM receipts WHERE run_id=?', (stage['run']['id'],)).fetchone()
            if row is None:
                raise ValueError('Original receipt missing')
            receipt = json.loads(row[1])
            body = {k: v for k, v in receipt.items() if k != 'sha256'}
            if (receipt.get('run_status') != 'SUCCEEDED' or receipt.get('sha256') != row[0]
                    or hashlib.sha256(canonical(body).encode('utf-8')).hexdigest() != row[0]):
                raise ValueError('Original receipt changed or did not succeed')
            item = next(a for a in receipt['artifacts'] if a.get('kind') == 'project_output' and a['path'] == stage['output'])
            doc = original(item)
            prior.append({'run_id': receipt['run_id'], 'receipt_sha256': receipt['sha256'],
                          'path': item['path'], 'sha256': item['sha256']})
            docs.append(doc['result'])
    return req, prior, docs


def proposal(req, result, template):
    contract = read('contract.json')
    if hashlib.sha256(canonical(contract).encode('utf-8')).hexdigest() != req['scope']['contract_sha256']:
        raise ValueError('Frozen contract copy changed')
    template['conditions'] = [{k: b[k] for k in ('path', 'sha256')} for b in contract['bindings'] if b['role'] in ('config', 'data')]
    for run in template['experiment']['runs']:
        protocol_path = run.pop('protocol_path')
        binding = next(b for b in contract['bindings'] if b['path'] == protocol_path and b['role'] == 'protocol')
        run['protocol'] = {k: binding[k] for k in ('path', 'sha256')}
    candidate, rival = result['candidate'], result['rival']
    identity = {'request': req, 'candidate': candidate}
    name = 'generated-' + hashlib.sha256(canonical(identity).encode('utf-8')).hexdigest()[:16]
    explanation = 'Exact finite integer relation: ' + canonical(candidate)
    node = {'id': name, 'kind': 'model', 'label': explanation,
            'source': {'locator': 'out/synthesize.json#/result/search/candidate'}}
    p = {'id': name, 'request_id': req['id'], 'gap_id': req['gap_id'], 'action_kind': 'knowledge_expansion',
         'new_nodes': [node], 'relations': [],
         'assumptions': ['The generated integer expression applies to the unmeasured declared inputs'],
         'prediction': {'observable': name, 'if_proposal': str(evaluate(candidate, result['probe'])),
                        'if_rival': str(evaluate(rival, result['probe']))},
         'test': {'protocol': 'Precommit separating input; independent frozen finite evaluator',
                  'measurement': 'independent-probe-value', 'stop_condition': 'One candidate run and one verifier'},
         'discriminator': {'schema': 1, 'hypothesis_id': name, 'conditions': template['conditions'],
             'measurement': {'name': 'independent-probe-value', 'path': 'out/verdict.json', 'pointer': '/probe_value'},
             'proposal': {'op': 'eq', 'value': evaluate(candidate, result['probe'])},
             'rival': {'op': 'eq', 'value': evaluate(rival, result['probe'])}},
         'next_if_positive': 'Retain the generated relation under this finite scope; seek new independent inputs',
         'next_if_negative': 'Keep the counterexample and reconsider the grammar or measured premises',
         'exploration': {'limitation': 'The initial relation leaves measured residuals',
             'change': explanation, 'rationale': 'Bounded grammar search fits the acquired observations',
             'sources': [{'kind': 'local_observation', 'source': 'out/probe.json'},
                         {'kind': 'literature_claim', 'source': 'out/refresh.json (synthetic scoped corpus; applicability unproven)'}],
             'unknown_premises': ['Applicability outside the observed finite inputs'],
             'search_directions': ['Test the precommitted prediction against the retained rival'],
             'cost': {'wall_seconds': 20}},
         'topology': {'retire_hyperedges': [], 'hyperedges': [{'id': name + '-route',
             'premises': [name], 'conclusion': req['goal'], 'source': node['source']}]},
         'experiment': template['experiment']}
    if req['prior_feedback']:
        feedback = req['prior_feedback'][-1]
        p['trigger'] = {'proposal_id': feedback['id'], 'feedback_sha256': feedback['feedback_sha256'],
                        'observation': feedback['observation'], 'purpose': 'ALTERNATIVE'}
    return p


def main():
    kind, output = sys.argv[1:]
    if kind == 'candidate':
        # Read the exact synthesis bytes authenticated by the successful
        # original receipt, just like the preceding generation stages.
        _, _, docs = context('candidate')
        result = docs[-1]['search']
        Path(output).write_text(canonical({'expression': result['candidate'], 'probe': result['probe'],
            'predictions': [{'inputs': env, 'value': evaluate(result['candidate'], env)}
                            for env in read('domain.json')['probes']]}), encoding='utf-8')
        return
    req, inputs, docs = context(kind)
    domain = read('domain.json')
    if kind == 'probe':
        new_observations = [{'inputs': env, 'value': measure(env)} for env in domain['diagnostic_inputs']]
        observations = domain['observations'] + new_observations
        result = {'observations': observations, 'diagnostic': diagnose(observations, domain['baseline']),
                  'new_observations': new_observations,
                  'source': {'path': 'domain.json', 'sha256': sha('domain.json')},
                  'scope': 'FINITE_PUBLIC_MEASUREMENTS'}
    elif kind == 'refresh':
        # Content is data, never a command. Explicit typed fields select only
        # preauthorized grammar primitives; arbitrary prose is not executable.
        corpus = read('corpus.json')
        terms = set(domain['query'].lower().split())
        hits = [row for row in corpus if terms.intersection(row['text'].lower().split())]
        operators = sorted(set(domain['base_operators']).union(
            op for row in hits for op in row.get('operators', []) if op in domain['allowed_operators']))
        seeds = [ast for row in hits for ast in row.get('branch_seeds', [])]
        result = {'query': domain['query'], 'sources': hits, 'operators': operators, 'seeds': seeds,
                  'source': {'path': 'corpus.json', 'sha256': sha('corpus.json')},
                  'evidence_used': {'residuals': docs[0]['diagnostic'], 'new_observations': docs[0]['new_observations']},
                  'scope': 'FROZEN_SCOPED_CORPUS', 'source_truth': 'UNKNOWN'}
    elif kind == 'synthesize':
        search = synthesize(docs[0]['observations'], domain['variables'], domain['constants'], docs[1]['operators'],
                            seeds=docs[1]['seeds'], max_nodes=domain['max_nodes'],
                            max_candidates=domain['max_candidates'], probes=domain['probes'], baseline=domain['baseline'])
        ready = search['candidate'] is not None and search['probe'] is not None
        result = {'status': 'PROPOSED' if ready else 'NO_CANDIDATE', 'search': search,
                  'reason': search['status'], 'proposals': [proposal(req, search, read('template.json'))] if ready else [],
                  'consumption': {'observations': 'probe result -> fit constraints',
                                  'operators': 'refresh sources -> grammar', 'seeds': 'retained branches -> search start',
                                  'predictions': 'generated AST + declared probes -> discriminator'}}
    else:
        raise ValueError('Unknown stage')
    Path(output).write_text(canonical({'schema': 1, 'kind': kind,
        'request_sha256': hashlib.sha256(canonical(req).encode('utf-8')).hexdigest(),
        'inputs': inputs, 'result': result}), encoding='utf-8')


if __name__ == '__main__':
    main()
