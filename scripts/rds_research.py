"""One host research loop, projected from existing notes and original CAS bytes.

Proposals and selections are declarations. Only the existing checker adapters
may describe checked scope; this facade grants no experiment authorization.
"""
import hashlib
from pathlib import Path

import rds_notes as notes
from rds_cas import cas_json
from rds_project import require, _shell_argument
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
    if kind == 'proposal':
        previous = [n for n in notes.read(root) if n.get('source') == PREFIX + original['sha256']]
        if previous:
            return previous[-1]['id']
    note_type = {'goal': 'goal', 'proposal': 'constraint', 'check': 'observe',
                 'observation': 'observe', 'selection': 'claim'}[kind]
    return notes.append(root, note_type, payload.get('summary', kind),
                        source=PREFIX + original['sha256'], ref=list(refs))['id']


def _get(entries, ident, kind):
    row = entries.get(ident)
    require(row is not None and row['kind'] == kind, 'Expected ' + kind + ' reference: ' + str(ident))
    return row


def _active_goal(entries):
    return next((r for r in reversed(entries.values()) if r['kind'] == 'goal'), None)


def _workflow(root):
    from rds_project_lifecycle import describe
    value = describe(root)
    if value['mode'] == 'UNINITIALIZED':
        return {'mode': value['mode'], 'advisor': value['advisor'], 'execution_contract': 'NOT_INITIALIZED'}
    prefix = 'python -B scripts/rds_cli.py'
    actual = 'python -B ' + _shell_argument(str(Path(__file__).resolve().with_name('rds_cli.py')))

    def rebind(item):
        if isinstance(item, dict):
            return {key: rebind(part) for key, part in item.items()}
        if isinstance(item, list):
            return [rebind(part) for part in item]
        if isinstance(item, str):
            return item.replace(prefix, actual)
        return item

    return rebind(value)


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
    require(_active_goal(rows)['id'] == spec['goal'], 'Proposal belongs to an older goal')
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
    goal = _active_goal(rows)
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
        routes.append({'id': row['id'], 'source': row['source'], 'refs': row['refs'],
                       **row['payload'], 'obligations': obligations, 'next_action': next_action})
    selections = [r for r in rows.values() if r['kind'] == 'selection' and any(r['refs'] == [p['id']] for p in routes)]
    observations = [r for r in rows.values() if r['kind'] == 'observation' and any(r['refs'] == [p['id']] for p in routes)]
    focus = routes[-1] if routes else None
    if selections and int(selections[-1]['id'][1:]) > int(focus['id'][1:]):
        focus = next(r for r in routes if r['id'] == selections[-1]['refs'][0])
    from rds_math import objective
    reported = [n for n in notes.read(root) if n['id'] not in rows]
    return {'schema': 'rds-research-context-v1', 'project_root': str(root), 'goal': goal,
            'routes': routes, 'observations': observations,
            'selected': selections[-1] if selections else None,
            'focus_route': focus['id'] if focus else None,
            'next_action': focus['next_action'] if focus else 'PROPOSE' if goal else 'STATE_GOAL',
            'retained_context': {'native_objective': objective(root), 'workflow': _workflow(root),
                                 'reported_notes': reported[-20:], 'omitted_notes': max(0, len(reported) - 20)},
            'scientific_support': 'UNKNOWN', 'research_benefit': 'UNMEASURED',
            'authorization': 'UNCHANGED', 'execution_started': False}


def select(root, proposal, reason):
    root = project_root(root)
    row = _get(_entries(root), proposal, 'proposal')
    current = context(root)
    require(row['payload']['goal'] == current['goal']['id'], 'Selection belongs to an older goal')
    ident = _save(root, 'selection', {'summary': _text(reason, 'reason')}, [proposal])
    return {'record_id': ident, 'context': context(root)}


def check(root, proposal, name, evidence=None):
    from rds_research_checks import obligation, run, replay
    root = project_root(root)
    rows = _entries(root)
    row = _get(rows, proposal, 'proposal')
    item = obligation(row['payload'], name)
    existing = [r for r in rows.values() if r['kind'] == 'check' and r['refs'] == [proposal]
                and r['payload']['obligation'] == name]
    if existing:
        if evidence is not None:
            from rds_math import read_bytes
            require(item['tool'] == 'observation' and
                    hashlib.sha256(read_bytes(evidence)).hexdigest() == existing[-1]['payload']['result']['original']['sha256'],
                    'Different evidence requires an explicit revised proposal')
        checked = replay(root, row['payload'], existing[-1]['payload'])
        return {'record_id': existing[-1]['id'], 'reused': True, 'check': checked, 'context': context(root)}
    require(_active_goal(rows)['id'] == row['payload']['goal'], 'New check belongs to an older goal')
    result = run(root, item, evidence)
    saved = {'obligation': name, 'request': item, 'result': result}
    ident = _save(root, 'check', saved, [proposal])
    return {'record_id': ident, 'reused': False, 'check': replay(root, row['payload'], saved), 'context': context(root)}


def advance(root, proposal, name):
    from rds_research_checks import obligation
    from rds_structure import advance as advance_structure
    root = project_root(root)
    rows = _entries(root)
    row = _get(rows, proposal, 'proposal')
    require(_active_goal(rows)['id'] == row['payload']['goal'], 'Execution belongs to an older goal')
    item = obligation(row['payload'], name)
    require(item['tool'] == 'structure' and set(item['spec']) == {'proposal_id'},
            'Advance requires an existing authorized structure proposal')
    result = advance_structure(root, item['spec']['proposal_id'])
    if result.get('status') == 'RECOVERY_REQUIRED':
        return {'execution': result, 'context': context(root)}
    return {'execution': result, **check(root, proposal, name)}


def command(args):
    if args.action == 'start':
        return start(args.root, args.goal)
    if args.action == 'status':
        return context(args.root)
    if args.action == 'show':
        rows = _entries(project_root(args.root))
        require(args.id in rows, 'Unknown research record')
        return rows[args.id]
    if args.action == 'propose':
        from rds_math import read_bytes
        return propose(args.root, strict_json(read_bytes(args.file).decode('utf-8-sig')))
    if args.action == 'select':
        return select(args.root, args.proposal, args.reason)
    if args.action == 'advance':
        return advance(args.root, args.proposal, args.obligation)
    return check(args.root, args.proposal, args.obligation, args.evidence)


def add_parser(commands):
    entry = commands.add_parser('research', help='Continue one goal through changed routes, checks and feedback')
    actions = entry.add_subparsers(dest='action', required=True)
    actions.add_parser('start').add_argument('--goal', required=True)
    actions.add_parser('status')
    actions.add_parser('show').add_argument('--id', required=True)
    actions.add_parser('propose').add_argument('--file', required=True)
    choice = actions.add_parser('select')
    choice.add_argument('--proposal', required=True)
    choice.add_argument('--reason', required=True)
    check_parser = actions.add_parser('check')
    check_parser.add_argument('--proposal', required=True)
    check_parser.add_argument('--obligation', required=True)
    check_parser.add_argument('--evidence', help='Original external output; remains INPUT_REPORTED')
    advance_parser = actions.add_parser('advance', help='Execute an already authorized structure experiment and consume its feedback')
    advance_parser.add_argument('--proposal', required=True)
    advance_parser.add_argument('--obligation', required=True)
