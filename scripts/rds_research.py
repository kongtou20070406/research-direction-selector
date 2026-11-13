"""One host research loop, projected from existing notes and original CAS bytes.

Proposals and selections are declarations. Only the existing checker adapters
may describe checked scope; this facade grants no experiment authorization.
"""
import hashlib
from pathlib import Path

import rds_notes as notes
from rds_cas import cas_json
from rds_project import canonical, require
from rds_project_lifecycle import discover
from rds_reference_values import strict_json

PREFIX = 'rds-research-v1:'
MAX_BYTES = 8 * 1024 * 1024


def project_root(root):
    found = discover(root)
    return Path(found['project_root'] or root).resolve()


def _text(value, name):
    require(isinstance(value, str) and 0 < len(value.strip()) <= 2000,
            name + ' must be nonempty text (at most 2000 characters)')
    return value


def _entries(root):
    result = {}
    for note in notes.read(root):
        source = note.get('source', '')
        if not isinstance(source, str) or not source.startswith(PREFIX):
            continue
        sha = source[len(PREFIX):]
        require(len(sha) == 64 and all(c in '0123456789abcdef' for c in sha), 'Invalid research source')
        path = root / '.rds' / 'cas' / (sha + '.json')
        require(path.resolve().is_relative_to(root), 'Research source escapes project root')
        with path.open('rb') as stream:
            raw = stream.read(MAX_BYTES + 1)
        require(len(raw) <= MAX_BYTES and hashlib.sha256(raw).hexdigest() == sha,
                'Research source integrity failure')
        value = strict_json(raw.decode('utf-8'))
        require(value['refs'] == note.get('ref', []), 'Research references differ from original')
        require(all(ref in result for ref in value['refs']), 'Missing or forward research reference')
        result[note['id']] = {**value, 'id': note['id'], 'source': source}
    return result


def _save(root, kind, payload, refs=()):
    value = {'kind': kind, 'payload': payload, 'refs': list(refs)}
    original = cas_json(root, value, max_bytes=MAX_BYTES)
    note_type = {'goal': 'goal', 'proposal': 'expect', 'check': 'observe',
                 'observation': 'observe', 'selection': 'claim'}[kind]
    return notes.append(root, note_type, payload.get('summary', kind),
                        source=PREFIX + original['sha256'], ref=list(refs))['id']


def _get(entries, ident, kind):
    row = entries.get(ident)
    require(row is not None and row['kind'] == kind, 'Expected ' + kind + ' reference: ' + str(ident))
    return row


def start(root, goal):
    root = project_root(root)
    goal = _text(goal, 'goal')
    rows = _entries(root)
    goals = [r for r in rows.values() if r['kind'] == 'goal']
    ident = goals[-1]['id'] if goals and goals[-1]['payload']['summary'] == goal else _save(root, 'goal', {'summary': goal})
    return {'record_id': ident, 'context': context(root)}


def propose(root, spec):
    root = project_root(root)
    rows = _entries(root)
    require(isinstance(spec, dict) and set(spec) == {'goal', 'parent', 'change', 'summary', 'discriminator', 'obligations'},
            'Proposal needs goal, parent, change, summary, discriminator and obligations')
    _get(rows, spec['goal'], 'goal')
    _text(spec['summary'], 'summary')
    refs = [spec['goal']]
    change = spec['change']
    require(isinstance(change, dict) and set(change) == {'kind', 'before', 'after'}, 'Declare the before/after change')
    require(change['kind'] in {'baseline', 'hypothesis', 'representation', 'decomposition'}, 'Unknown change kind')
    for key in ('before', 'after'):
        _text(change[key], key)
    if change['kind'] != 'baseline':
        require(change['before'].strip() != change['after'].strip(), 'A jump must describe a changed route')
        parent = _get(rows, spec['parent'], 'proposal')
        require(parent['payload']['goal'] == spec['goal'], 'Parent belongs to a different goal')
        refs.append(parent['id'])
    else:
        require(spec['parent'] is None, 'A baseline has no parent')
    discriminator = spec['discriminator']
    require(isinstance(discriminator, dict) and set(discriminator) == {'question', 'if_pass', 'if_fail'},
            'Discriminator needs question, if_pass and if_fail')
    for key, value in discriminator.items():
        _text(value, key)
    require(discriminator['if_pass'] != discriminator['if_fail'], 'Outcomes must change the research decision')
    obligations = spec['obligations']
    require(isinstance(obligations, list) and 1 <= len(obligations) <= 16, 'Declare 1..16 obligations')
    names = set()
    for item in obligations:
        require(isinstance(item, dict) and set(item) == {'id', 'meaning', 'tool', 'spec'}, 'Invalid obligation fields')
        _text(item['id'], 'obligation id')
        _text(item['meaning'], 'obligation meaning')
        require(item['id'] not in names, 'Duplicate obligation id')
        names.add(item['id'])
        require(item['tool'] in {'formal', 'math', 'structure', 'observation'} and isinstance(item['spec'], dict),
                'Use formal, math, structure or observation with an explicit spec')
    ident = _save(root, 'proposal', spec, refs)
    return {'record_id': ident, 'context': context(root)}


def context(root):
    root = project_root(root)
    rows = _entries(root)
    goals = [r for r in rows.values() if r['kind'] == 'goal']
    goal = goals[-1] if goals else None
    routes = []
    for row in rows.values():
        if row['kind'] != 'proposal' or not goal or row['payload']['goal'] != goal['id']:
            continue
        checked = []
        for event in rows.values():
            if event['kind'] == 'check' and event['refs'] == [row['id']]:
                from rds_research_checks import replay
                checked.append({'record_id': event['id'], 'obligation': event['payload']['obligation'],
                                **replay(root, row['payload'], event['payload'])})
        obligations = []
        for obligation in row['payload']['obligations']:
            found = [c for c in checked if c['obligation'] == obligation['id']]
            result = found[-1] if found else {'status': 'UNTESTED', 'assurance': 'NONE'}
            obligations.append({**obligation, 'result': result})
        statuses = [o['result']['status'] for o in obligations]
        next_action = ('REFORMULATE' if 'FAIL' in statuses else 'INTERPRET_CHECKED_SCOPE'
                       if all(s == 'PASS' for s in statuses) else 'CHECK_OR_COLLECT_EVIDENCE')
        routes.append({**row, 'obligations': obligations, 'next_action': next_action})
    selections = [r for r in rows.values() if r['kind'] == 'selection' and any(r['refs'] == [p['id']] for p in routes)]
    observations = [r for r in rows.values() if r['kind'] == 'observation' and any(r['refs'] == [p['id']] for p in routes)]
    return {'schema': 'rds-research-context-v1', 'project_root': str(root), 'goal': goal,
            'routes': routes, 'observations': observations,
            'selected': selections[-1] if selections else None,
            'next_action': routes[-1]['next_action'] if routes else 'PROPOSE' if goal else 'STATE_GOAL',
            'scientific_support': 'UNKNOWN', 'research_benefit': 'UNMEASURED',
            'authorization': 'UNCHANGED', 'execution_started': False}


def select(root, proposal, reason):
    root = project_root(root)
    row = _get(_entries(root), proposal, 'proposal')
    current = context(root)
    require(row['payload']['goal'] == current['goal']['id'], 'Selection belongs to an older goal')
    ident = _save(root, 'selection', {'summary': _text(reason, 'reason')}, [proposal])
    return {'record_id': ident, 'context': context(root)}
