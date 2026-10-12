"""Verified read-only effective contract lineage and CAS identity checks.

This leaf has no Store or writer dependency; write-side method and activation
operations retain their original compatibility facades.
"""
import hashlib
import json
from pathlib import Path
import re

from rds_project_values import digest, require

PREPARED = 'METHOD_REVISION_PREPARED'
ADOPTED = 'METHOD_REVISION_ADOPTED'
ACTIVATED = 'ADVISOR_POLICY_ENABLED'
POLICY_FIELDS = {'schema', 'context', 'graph', 'routes', 'observations'}


def activation_transition(old, new):
    """The only permitted activation delta; also checked while reading lineage."""
    require('advisor_policy' not in old and set(new) == set(old) | {'advisor_policy'},
            'Advisor activation must add exactly one policy to a non-owned contract')
    require(all(new[key] == value for key, value in old.items()),
            'Advisor activation cannot change the original contract or authority')
    require(isinstance(new['advisor_policy'], dict) and set(new['advisor_policy']) == POLICY_FIELDS,
            'Activation accepts basic owned policy only; no new autonomy, confirmation, tools or execution authority')


def _event_hash(event):
    return digest({k: v for k, v in event.items() if k != 'sha256'})

def _cas_dir(db):
    filename = db.execute('PRAGMA database_list').fetchone()[2]
    require(filename, 'Method revision needs a file-backed ledger')
    state = Path(filename).parent.resolve()
    directory = state / 'cas'
    require(directory.resolve().is_relative_to(state), 'Method CAS directory escapes operational state')
    return directory

def _blob(db, sha):
    require(isinstance(sha, str) and re.fullmatch('[0-9a-f]{64}', sha), 'Invalid method CAS identity')
    path = _cas_dir(db) / (sha + '.method-bytes')
    require(path.resolve().is_relative_to(_cas_dir(db).resolve()), 'Method CAS file escapes its directory')
    data = path.read_bytes()
    require(hashlib.sha256(data).hexdigest() == sha, 'Method CAS integrity failure')
    return data

def _genesis(db):
    row = db.execute('SELECT body,sha256 FROM contract WHERE id=1').fetchone()
    require(row is not None, 'Project contract is missing')
    value = json.loads(row['body'])
    require(digest(value) == row['sha256'], 'Contract integrity failure')
    return value

def _transition(old, new, changes):
    require(set(old) == set(new), 'Method revision cannot change contract fields')
    for key in set(old) - {'advisor_policy', 'bindings'}:
        require(old[key] == new[key], 'Method revision changes immutable contract field: ' + key)
    require(old.get('method_evolution'), 'Method evolution was not authorized at genesis')
    require(len(old['bindings']) == len(new['bindings']), 'Revision cannot add or remove bindings')
    authorized = set(old['method_evolution']['code_paths'])
    changed_paths = {c['path'] for c in changes}
    require(len(changed_paths) == len(changes), 'Duplicate revision targets')
    for before, after in zip(old['bindings'], new['bindings']):
        require(set(before) == set(after) == {'path', 'role', 'sha256'}
                and before['path'] == after['path'] and before['role'] == after['role'],
                'Revision cannot reorder or change binding identities')
        if before['sha256'] != after['sha256']:
            require(before['path'] in changed_paths and (before['role'] == 'protocol'
                    or (before['role'] == 'code' and before['path'] in authorized)),
                    'Revision cannot change frozen input/evaluator/config bindings')
    require(old.get('advisor_policy', {}).get('context') == new.get('advisor_policy', {}).get('context'),
            'Revision cannot change decision, goals or scope')
    for protected in ('autonomy', 'confirmation'):
        require(old.get('advisor_policy', {}).get(protected) == new.get('advisor_policy', {}).get(protected),
                'Revision cannot change protected controller/confirmation declaration: ' + protected)
    forecast = old.get('advisor_policy', {}).get('feasibility')
    if forecast is not None:
        updated = new.get('advisor_policy', {}).get('feasibility')
        require(isinstance(updated, dict), 'Revision cannot remove the predictive feasibility gate')
        require(updated['max_pilot_wall_seconds'] <= forecast['max_pilot_wall_seconds'],
                'Revision cannot increase the original cumulative pilot allowance')
    for change in changes:
        before = [b for b in old['bindings'] if b['path'] == change['path']]
        after = [b for b in new['bindings'] if b['path'] == change['path']]
        require(len(before) == len(after) == 1 and before[0]['sha256'] == change['before_sha256']
                and after[0]['sha256'] == change['after_sha256'], 'Revision file changes disagree with bindings')

def _lineage(db, genesis=None):
    initial = _genesis(db)
    require(genesis is None or initial == genesis, 'Genesis contract differs')
    history = [{'sha256': digest(initial), 'contract': initial}]
    pending, seen_ids, revisions = None, set(), 0
    rows = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind') IN (?,?,?) ORDER BY id",
                      (PREPARED, ADOPTED, ACTIVATED)).fetchall()
    require(len(rows) <= 17, 'Contract transition event limit exceeded')
    for row in rows:
        event = json.loads(row['body'])
        require(event.get('sha256') == _event_hash(event), 'Method revision event integrity failure')
        require(event.get('genesis_sha256') == history[0]['sha256'], 'Method revision genesis mismatch')
        if event['kind'] == ACTIVATED:
            require(pending is None and type(event.get('schema')) is int and event['schema'] == 1,
                    'Advisor activation overlaps a pending revision or has an invalid schema')
            require(event.get('parent_sha256') == history[-1]['sha256'] and
                    event.get('contract_sha256') == digest(event['contract']) and
                    event.get('policy_sha256') == digest(event['contract'].get('advisor_policy')),
                    'Advisor activation lineage mismatch')
            activation_transition(history[-1]['contract'], event['contract'])
            history.append({'sha256': event['contract_sha256'], 'contract': event['contract']})
        elif event['kind'] == PREPARED:
            require(pending is None and event['id'] not in seen_ids, 'Duplicate or overlapping method revision')
            require(event['parent_sha256'] == history[-1]['sha256'], 'Method revision parent mismatch or cycle')
            require(revisions < initial.get('method_evolution', {}).get('max_revisions', 0),
                    'Method revision maximum reached')
            require(digest(event['contract']) == event['contract_sha256'], 'Revised contract integrity failure')
            require(event['contract_sha256'] not in {h['sha256'] for h in history}, 'Method revision cycle')
            _transition(history[-1]['contract'], event['contract'], event['changes'])
            for change in event['changes']:
                old_bytes, new_bytes = _blob(db, change['before_sha256']), _blob(db, change['after_sha256'])
                binding = next(b for b in event['contract']['bindings'] if b['path'] == change['path'])
                if binding['role'] == 'protocol':
                    before, after = json.loads(old_bytes.decode('utf-8-sig')), json.loads(new_bytes.decode('utf-8-sig'))
                    require(after == {**before, 'code_sha256': _code_sha(event['contract'])},
                            'Revision must preserve all protocol fields except derived code identity')
            pending = event
            seen_ids.add(event['id'])
        else:
            require(pending is not None and event['id'] == pending['id']
                    and event['prepared_sha256'] == pending['sha256']
                    and event['parent_sha256'] == history[-1]['sha256']
                    and event['contract_sha256'] == pending['contract_sha256']
                    and event['proposal_sha256'] == pending['proposal_sha256'], 'Method adoption lineage mismatch')
            history.append({'sha256': pending['contract_sha256'], 'contract': pending['contract']})
            revisions += 1
            pending = None
    return history, pending

def contract_history(db, genesis=None):
    """Verified genesis and effective ancestors; never trust snapshot claims alone."""
    return _lineage(db, genesis)[0]

def effective_contract(db):
    return contract_history(db)[-1]['contract']

def pending_revision(db):
    return _lineage(db)[1]

def _code_sha(contract):
    rows = [{'path': b['path'], 'sha256': b['sha256']} for b in contract['bindings'] if b['role'] == 'code']
    return rows[0]['sha256'] if len(rows) == 1 else digest(sorted(rows, key=lambda b: b['path']))
