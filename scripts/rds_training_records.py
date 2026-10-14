"""Decision-time code packets to receipt-bound training rows; no IO or ledger.

Missing labels remain UNKNOWN. Comparison validity is distinct from science.
Callers supply original transition exports and original JSON output bytes.
"""
from copy import deepcopy
import hashlib

from rds_code_diff import encode_code_diff
from rds_core import object_digest
from rds_offline_contract_values import bounded, fields, number, require, text, token
from rds_source_documents import strict_json, _pointer


def freeze_packet(value, selected_id=None):
    packet = bounded(value)
    fields(packet, 'schema project_id decided_at base_code before_state candidates', 'training packet')
    require(type(packet['schema']) is int and packet['schema'] == 1, 'Unsupported training packet')
    text(packet['project_id'], 'project id', 256)
    number(packet['decided_at'], 'decision time', 0)
    text(packet['base_code'], 'base code')
    require(type(packet['before_state']) is dict, 'Missing before-state')
    number(packet['before_state'].get('score'), 'before score')
    require(type(packet['candidates']) is list and 1 <= len(packet['candidates']) <= 128, 'Candidate packet count')
    ids, hashes = set(), {}
    for c in packet['candidates']:
        require(type(c) is dict and {'id', 'code'} <= set(c) <= {'id', 'code', 'label'}, 'Candidate packet fields')
        token(c['id'], 'candidate id')
        require(c['id'] not in ids, 'Duplicate candidate packet')
        ids.add(c['id'])
        text(c['code'], 'candidate code')
        hashes[c['id']] = hashlib.sha256(c['code'].encode()).hexdigest()
        if 'label' in c:
            fields(c['label'], 'path score_pointer', 'label declaration')
            text(c['label']['path'], 'label path')
            text(c['label']['score_pointer'], 'label pointer')
    require(selected_id is None or selected_id in ids, 'Selected candidate missing from packet')
    return dict(value=packet, sha256=object_digest(packet), selected_id=selected_id,
                base_code_sha256=hashlib.sha256(packet['base_code'].encode()).hexdigest(), candidate_sha256s=hashes)


def split_for(packet, plan):
    plan = bounded(plan)
    if plan.get('kind') == 'project':
        fields(plan, 'kind train validation confirmation', 'project split')
        all_ids = [p for key in ('train', 'validation', 'confirmation') for p in plan[key]]
        require(len(all_ids) == len(set(all_ids)), 'Project split overlap')
        matches = [key for key in ('train', 'validation', 'confirmation') if packet['project_id'] in plan[key]]
        require(len(matches) == 1, 'Project split is not declared')
        return matches[0]
    fields(plan, 'kind train_before validation_before', 'time split')
    require(plan['kind'] == 'time', 'Unsupported split kind')
    number(plan['train_before'], 'train boundary', 0)
    number(plan['validation_before'], 'validation boundary', plan['train_before'])
    t = packet['decided_at']
    return 'train' if t < plan['train_before'] else ('validation' if t < plan['validation_before'] else 'confirmation')


def export_training_records(exported, label_documents, split_plan):
    """Use export_transitions' paired settled receipts, never future-state features.

label_documents: {run_id, receipt_sha256, path, body}; body is original bytes.
Only a declared pointer in a receipt-bound project_output may supply a label.
"""
    require(exported.get('schema') == 'rds-transition-v1', 'Expected transition export')
    require(type(label_documents) is list and len(label_documents) <= 128, 'Label document count')
    labels = {}
    for doc in label_documents:
        fields(doc, 'run_id receipt_sha256 path body', 'label document')
        key = (doc['run_id'], doc['path'])
        require(key not in labels and type(doc['body']) is bytes and len(doc['body']) <= 262144, 'Invalid label bytes')
        labels[key] = doc
    rows = []
    for item in exported['transitions']:
        decision = item['decision'].get('value', {})
        frozen = decision.get('training_packet')
        if frozen is None:
            continue  # Legacy transitions do not invent code from argv.
        packet = frozen['value']
        require(frozen == freeze_packet(packet, frozen['selected_id']), 'Frozen packet binding mismatch')
        c = next(c for c in packet['candidates'] if c['id'] == frozen['selected_id'])
        diff = encode_code_diff(packet['base_code'], c['code'])['obligation_signals']
        invalid = diff['control_modified'] or diff['signature_changed']
        row = dict(schema='rds-training-record-v1', project_id=packet['project_id'], decided_at=packet['decided_at'],
                   split=split_for(packet, split_plan), split_sha256=object_digest(split_plan),
                   base_code=packet['base_code'], candidate_code=c['code'], before_state=deepcopy(packet['before_state']),
                   outcome_score=None, cost=None, validity='INVALID' if invalid else 'UNKNOWN',
                   reason='CONTROL_OR_SIGNATURE_CHANGED' if invalid else 'MISSING_SETTLED_LABEL',
                   scientific_support='UNKNOWN', receipt_sha256=item['receipt_sha256'],
                   packet_sha256=frozen['sha256'], run_id=item['run_id'],
                   before_checkpoint=item['decision']['source'], after_checkpoint=item['after'].get('source'))
        receipt = item['observation']['value']
        label = c.get('label')
        doc = labels.get((item['run_id'], label['path'])) if label else None
        if not invalid and doc and item['after'].get('scope') == 'RETAINED_CHECKPOINT' and receipt['run_status'] == 'SUCCEEDED':
            require(doc['receipt_sha256'] == item['receipt_sha256'], 'Label receipt mismatch')
            sha = hashlib.sha256(doc['body']).hexdigest()
            require(any(a.get('kind') == 'project_output' and a.get('path') == doc['path'] and a.get('sha256') == sha
                        for a in receipt.get('artifacts', [])), 'Label is not original receipt-bound output')
            value = _pointer(strict_json(doc['body'].decode('utf-8-sig')), label['score_pointer'])
            number(value, 'outcome score')
            wall = receipt.get('resources', {}).get('wall_seconds', {})
            row.update(outcome_score=value, cost=wall.get('measured') if wall.get('unknown') is False else None,
                       validity='VALID', reason='RECEIPT_BOUND_OBSERVATION',
                       label_source=dict(path=doc['path'], sha256=sha, pointer=label['score_pointer']))
        rows.append(row)
    return rows
