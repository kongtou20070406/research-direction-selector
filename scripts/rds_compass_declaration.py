"""Compile one Compass research declaration into the existing kernel contract.

The declaration names the goal, rival rulebooks, the experiment command and its
bound files. The compiler fills hashes, the frozen protocol and one exact argv
per candidate experiment. It never invents research semantics: a protocol
identity field the declaration does not state is frozen as the literal string
``UNKNOWN`` and listed in ``unknown``; controller tuning that falls back to a
documented default is listed in ``defaulted``.
"""
from copy import deepcopy
import hashlib
import os
import sys
from pathlib import Path

import rds_compass_model as model
from rds_artifacts import strict_json
from rds_project import IDENTITY, ProjectStore, canonical, digest, file_sha, require

SCHEMA = 'rds-compass-declaration-v1'
PLAN_SCHEMA = 'rds-compass-plan-v1'
MAX_BYTES = 1024 * 1024
MAX_CANDIDATES = 64
MAX_HYPOTHESES = 64
OUTPUT_ROOT = 'outputs/compass'
PROTOCOL_PATH = 'compass-protocol.json'
GOALS = ('IDENTIFY_LAW', 'IMPROVE')
FIELDS = {'schema', 'goal', 'inputs', 'observables', 'experiment', 'candidates', 'hypotheses',
          'grammar', 'synthesis', 'escape', 'budget', 'protocol', 'agent', 'library', 'rsi',
          'profile', 'description', 'verification'}
DEFAULTS = {
    'synthesis': {'max_nodes': 7, 'max_candidates': 20000},
    'escape': {'refuted_streak': 2, 'stagnation_rounds': 2, 'max_jumps': 6, 'probe_points': 2},
    'rsi': {'enabled': True, 'max_tools': 3, 'max_mutants': 24},
    'verification': {'confirmations': 2},
}
PROTOCOL_FIELDS = tuple(k for k in IDENTITY if not k.endswith('_sha256'))


def _seconds(value):
    """A JSON number of seconds; booleans are not numbers here."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _object(value, required, optional, label):
    require(isinstance(value, dict) and set(required) <= set(value) <= set(required) | set(optional),
            f'Invalid {label} fields: expected {sorted(required)} plus optional {sorted(optional)}')


def _ident(value, label):
    require(isinstance(value, str) and 0 < len(value) <= 60
            and all(c.isalnum() or c in '-_' for c in value), 'Invalid ' + label + ': ' + repr(value))
    return value


def _tuned(declaration, key, defaulted):
    supplied = declaration.get(key, {})
    require(isinstance(supplied, dict) and set(supplied) <= set(DEFAULTS[key]), 'Invalid ' + key + ' fields')
    for name in sorted(set(DEFAULTS[key]) - set(supplied)):
        defaulted.append(key + '.' + name)
    out = {**DEFAULTS[key], **supplied}
    for name, value in out.items():
        if name == 'enabled':
            require(type(value) is bool, key + '.enabled must be boolean')
        else:
            require(type(value) is int and 0 <= value <= model.MAX_SYNTH_CANDIDATES, f'{key}.{name} out of range')
    return out


def _argv(template, candidate, output):
    out = []
    for part in template:
        require(isinstance(part, str) and part and '\x00' not in part, 'Experiment argv must be strings')
        if part == '{python}':
            out.append(sys.executable)
            continue
        value = part.replace('{id}', candidate['id']).replace('{output}', output)
        for name, number in candidate['inputs'].items():
            value = value.replace('{' + name + '}', number)
        require('{' not in value or '}' not in value, 'Unresolved experiment argv placeholder: ' + part)
        out.append(value)
    return out


def compile_plan(store, declaration, source_sha256, source=None):
    """Return (contract, protocol bytes, plan) without writing anything."""
    _object(declaration, {'schema', 'goal', 'observables', 'experiment', 'candidates', 'budget'},
            FIELDS - {'schema', 'goal', 'observables', 'experiment', 'candidates', 'budget'}, 'Compass declaration')
    require(declaration['schema'] == SCHEMA, 'Compass declaration schema must be ' + SCHEMA)
    unknown, defaulted = [], []
    goal = declaration['goal']
    _object(goal, {'kind', 'statement'}, {'observable', 'success'}, 'goal')
    require(goal['kind'] in GOALS, 'Goal kind must be one of ' + ', '.join(GOALS))
    require(isinstance(goal['statement'], str) and 8 <= len(goal['statement'].strip()) <= 2048,
            'Goal needs a substantive statement')
    if 'success' not in goal:
        unknown.append('goal.success')
    observables = declaration['observables']
    require(isinstance(observables, dict) and 1 <= len(observables) <= 8, 'Declare 1..8 observables')
    tolerances = {}
    for name, spec in sorted(observables.items()):
        model._name(name)
        _object(spec, set(), {'tolerance', 'higher_is_better'}, 'observable ' + name)
        # A direction is a JSON boolean; truthiness would read "false" as higher-is-better.
        require(isinstance(spec.get('higher_is_better', True), bool),
                f'observables.{name}.higher_is_better must be true or false')
        if 'tolerance' not in spec:
            # Refutation needs a stated tolerance; a missing one is not exact.
            unknown.append(f'observables.{name}.tolerance')
            tolerances[name] = None
            continue
        absolute, relative = model.tolerance(spec['tolerance'])
        tolerances[name] = {'abs': model.text(absolute), 'rel': model.text(relative)}
    inputs = {}
    if goal['kind'] == 'IDENTIFY_LAW':
        require(goal.get('observable') in observables, 'IDENTIFY_LAW names one declared observable')
        require(isinstance(declaration.get('inputs'), dict) and 1 <= len(declaration['inputs']) <= 6,
                'IDENTIFY_LAW declares 1..6 numeric inputs')
        for name, spec in sorted(declaration['inputs'].items()):
            model._name(name)
            _object(spec, set(), {'min', 'max', 'integer'}, 'input ' + name)
            require(isinstance(spec.get('integer', False), bool), f'inputs.{name}.integer must be true or false')
            inputs[name] = {k: (model.text(v) if k != 'integer' else v) for k, v in spec.items()}
    else:
        require('inputs' not in declaration and 'hypotheses' not in declaration,
                'IMPROVE compares declared arms; it has no law inputs or rival rulebooks')
        profile = declaration.get('profile')
        _object(profile, {'baseline', 'metric'}, {'max_degradation', 'min_gain'}, 'profile')
        require(profile['metric'] in observables, 'Profile metric must be a declared observable')
        for key in ('max_degradation', 'min_gain'):
            if key not in profile:
                unknown.append('profile.' + key)
    experiment = declaration['experiment']
    _object(experiment, {'argv', 'files', 'timeout_seconds', 'wall_seconds'}, set(), 'experiment')
    require(isinstance(experiment['argv'], list) and 1 <= len(experiment['argv']) <= 64, 'Experiment argv required')
    require('{output}' in ''.join(experiment['argv']), 'Experiment argv must write {output}')
    timeout, wall = experiment['timeout_seconds'], experiment['wall_seconds']
    require(_seconds(timeout) and 0 < timeout <= 3600 and _seconds(wall)
            and wall >= timeout, 'Experiment wall_seconds must cover timeout_seconds')
    candidates, seen_inputs = [], set()
    rows = declaration['candidates']
    require(isinstance(rows, list) and 1 <= len(rows) <= MAX_CANDIDATES, f'Declare 1..{MAX_CANDIDATES} candidates')
    for row in rows:
        _object(row, {'id'}, {'inputs', 'cost', 'description'}, 'candidate')
        ident = _ident(row['id'], 'candidate id')
        values = row.get('inputs', {})
        require(isinstance(values, dict) and set(values) == set(inputs), 'Candidate inputs must match declared inputs')
        exact = {k: model.text(v) for k, v in sorted(values.items())}
        for name, value in exact.items():
            spec, number = inputs[name], model.exact(value)
            require(('min' not in spec or number >= model.exact(spec['min']))
                    and ('max' not in spec or number <= model.exact(spec['max']))
                    and (not spec.get('integer') or number.denominator == 1), 'Candidate outside input domain: ' + ident)
        key = canonical(exact)
        require(not inputs or key not in seen_inputs, 'Duplicate candidate inputs: ' + ident)
        seen_inputs.add(key)
        output = f'{OUTPUT_ROOT}/{ident}.json'
        candidate = {'id': ident, 'run_id': 'compass-' + ident, 'inputs': exact,
                     'cost': model.text(row.get('cost', 1)), 'output': output}
        require(model.exact(candidate['cost']) > 0, 'Candidate cost must be positive')
        candidate['argv'] = _argv(experiment['argv'], candidate, output)
        # One retry slot, used only after an outer interruption left no measurement (#215).
        retry = f'{OUTPUT_ROOT}/{ident}-a2.json'
        candidate['retry'] = {'run_id': candidate['run_id'] + '-a2', 'output': retry,
                              'argv': _argv(experiment['argv'], candidate, retry)}
        candidates.append(candidate)
    require(len({c['id'] for c in candidates}) == len(candidates), 'Duplicate candidate ids')
    slots = [run for c in candidates for run in (c['run_id'], c['retry']['run_id'])]
    require(len(set(slots)) == len(slots), 'Candidate ids collide with retry slots (avoid an -a2 suffix)')
    hypotheses = []
    for value in declaration.get('hypotheses', []):
        require(len(hypotheses) < MAX_HYPOTHESES, 'Too many declared hypotheses')
        hypotheses.append(model.compile_hypothesis({**value, 'origin': 'declared'}, list(inputs), list(observables),
                                                   goal=goal.get('observable') if goal['kind'] == 'IDENTIFY_LAW' else None))
    require(len({h['id'] for h in hypotheses}) == len(hypotheses), 'Duplicate hypothesis ids')
    if goal['kind'] == 'IDENTIFY_LAW' and not hypotheses:
        unknown.append('hypotheses')  # The first escape synthesizes rivals from evidence.
    grammar = declaration.get('grammar')
    if grammar is not None and inputs:
        model._grammar(grammar, list(inputs))
    elif inputs:
        defaulted.append('grammar')
    budget = declaration['budget']
    _object(budget, {'wall_seconds'}, {'experiments'}, 'budget')
    require(_seconds(budget['wall_seconds']) and budget['wall_seconds'] >= wall,
            'Budget wall_seconds must cover one experiment')
    if 'experiments' not in budget:
        defaulted.append('budget.experiments')
    else:
        count = budget['experiments']
        # Every attempt, retries included, is one experiment: the cap never exceeds the declared slots.
        require(type(count) is int and 0 <= count <= len(slots),
                f'budget.experiments must be an integer from 0 to {len(slots)}')
    protocol = declaration.get('protocol', {})
    require(isinstance(protocol, dict) and set(protocol) <= set(PROTOCOL_FIELDS) | {'path', 'metrics'},
            'Protocol declares identity fields only: ' + ', '.join(PROTOCOL_FIELDS))
    metadata = {}
    for field in PROTOCOL_FIELDS:
        if field in protocol:
            metadata[field] = deepcopy(protocol[field])
        else:
            metadata[field] = 'UNKNOWN'
            unknown.append('protocol.' + field)
    if 'metrics' in protocol:
        metadata['metrics'] = deepcopy(protocol['metrics'])
    protocol_path = protocol.get('path', PROTOCOL_PATH)
    agent = declaration.get('agent')
    if agent is not None:
        _object(agent, {'argv', 'timeout_seconds'}, set(), 'agent')
        require(isinstance(agent['argv'], list) and agent['argv'] and all(isinstance(a, str) and a for a in agent['argv']),
                'Agent argv must be a nonempty string list')
        require(_seconds(agent['timeout_seconds']) and 0 < agent['timeout_seconds'] <= 600,
                'Agent timeout must be in (0, 600] seconds')
        agent = {'argv': [sys.executable if a == '{python}' else a for a in agent['argv']],
                 'timeout_seconds': agent['timeout_seconds']}
    library = declaration.get('library')
    require(library is None or (isinstance(library, str) and library), 'Library must be a path')
    if library is None and os.environ.get('RDS_COMPASS_LIBRARY'):
        library = os.environ['RDS_COMPASS_LIBRARY']  # Recorded as defaulted: the plan names its source.
        defaulted.append('library:RDS_COMPASS_LIBRARY')
    files = experiment['files']
    require(isinstance(files, dict) and set(files) <= {'code', 'config', 'data', 'evaluator'},
            'Experiment files map code/config/data/evaluator to path lists')
    bindings, seen = [], set()
    protocol_resolved = store._path(protocol_path)
    if source is not None and Path(source).resolve().is_relative_to(store.root.resolve()):
        # The declaration is the campaign configuration; freeze it with the contract.
        relative = Path(source).resolve().relative_to(store.root.resolve()).as_posix()
        files = {**files, 'config': sorted(set(files.get('config', [])) | {relative})}
    for role in ('code', 'config', 'data', 'evaluator'):
        paths = files.get(role, [])
        require(isinstance(paths, list) and len(paths) <= 64, 'Experiment files must be bounded lists')
        require(paths, f'Experiment declares no {role} file; RDS will not invent one (state it explicitly)')
        for path in paths:
            resolved = store._path(path)
            require(resolved.is_file() and resolved != protocol_resolved, 'Missing experiment file: ' + path)
            require((resolved, role) not in seen, 'Duplicate experiment binding: ' + path)
            seen.add((resolved, role))
            bindings.append({'role': role, 'path': path, 'sha256': file_sha(resolved)})
    bindings.sort(key=lambda b: (b['role'], b['path']))
    contract = {'schema': 1, 'output_roots': [OUTPUT_ROOT], 'budget': {'wall_seconds': budget['wall_seconds']},
                'description': 'Compass campaign: ' + goal['statement'][:400]}
    for role in ('code', 'config', 'data'):
        metadata[role + '_sha256'] = ProjectStore._role_sha({'bindings': bindings}, role) if any(
            b['role'] == role for b in bindings) else 'UNKNOWN'
    metadata['compass_declaration_sha256'] = source_sha256
    protocol_raw = (canonical(metadata) + '\n').encode('utf-8')
    protocol_ref = {'path': protocol_path, 'sha256': hashlib.sha256(protocol_raw).hexdigest()}
    contract['bindings'] = bindings + [{'role': 'protocol', **protocol_ref}]
    commands = []
    for candidate in candidates:
        for argv in (candidate['argv'], candidate['retry']['argv']):
            if argv not in commands:
                commands.append(argv)
    contract['allowed_commands'] = commands
    plan = {'schema': PLAN_SCHEMA, 'goal': deepcopy(goal), 'inputs': inputs, 'tolerances': tolerances,
            'observables': sorted(observables), 'candidates': candidates, 'hypotheses': hypotheses,
            'grammar': deepcopy(grammar), 'synthesis': _tuned(declaration, 'synthesis', defaulted),
            'escape': _tuned(declaration, 'escape', defaulted), 'rsi': _tuned(declaration, 'rsi', defaulted),
            'verification': _tuned(declaration, 'verification', defaulted),
            'budget': {'wall_seconds': budget['wall_seconds'],
                       'experiments': budget.get('experiments', len(candidates))},
            'experiment': {'timeout_seconds': timeout, 'wall_seconds': wall}, 'protocol': protocol_ref,
            'agent': agent, 'library': library, 'unknown': sorted(set(unknown)),
            'defaulted': sorted(set(defaulted)), 'declaration_sha256': source_sha256,
            'higher_is_better': {k: v.get('higher_is_better', True) for k, v in observables.items()}}
    if goal['kind'] == 'IMPROVE':
        plan['profile'] = {k: (model.text(v) if k in ('max_degradation', 'min_gain') else v)
                           for k, v in declaration['profile'].items()}
        require(plan['profile']['baseline'] in {c['id'] for c in candidates}, 'Profile baseline must be a candidate')
    plan['contract_sha256'] = digest(contract)
    return contract, protocol_raw, plan


def initialize(root, declaration_path):
    """Freeze the protocol and contract, then append the COMPASS_DECLARED plan."""
    import rds_compass_store as cstore
    from rds_project_lifecycle import check_root, initialize as initialize_project
    store = ProjectStore(root)
    source = Path(declaration_path).resolve()
    with source.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    require(len(raw) <= MAX_BYTES, 'Compass declaration exceeds 1 MiB')
    declaration = strict_json(raw.decode('utf-8-sig'))
    contract, protocol_raw, plan = compile_plan(store, declaration, hashlib.sha256(raw).hexdigest(), source)
    if store.path.is_file():
        with store._db(True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone():
                row = db.execute('SELECT sha256 FROM contract WHERE id=1').fetchone()
                if row is not None:
                    require(row['sha256'] == digest(contract),
                            'Declaration differs from the initialized contract; start a successor campaign')
                    declared = [e for e in cstore.events(store, db, load=False) if e['kind'] == 'COMPASS_DECLARED']
                    require(declared and declared[0]['id'] == plan['declaration_sha256'][:32],
                            'Initialized ledger holds no matching Compass declaration')
                    return {'status': 'ALREADY_INITIALIZED', 'plan': plan, 'contract_sha256': row['sha256']}
    check_root(store.root)
    path = store._path(plan['protocol']['path'])
    require(path != source, 'Generated protocol would overwrite the declaration')
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('xb') as stream:
            stream.write(protocol_raw)
    except FileExistsError:
        require(path.read_bytes() == protocol_raw, 'Generated protocol would overwrite different content')
    initialize_project(store, contract, mode='quick')
    cstore.append(store, 'DECLARED', plan['declaration_sha256'][:32], plan)
    return {'status': 'INITIALIZED', 'plan': plan, 'contract_sha256': digest(contract),
            'execution_started': False, 'scientific_support': 'UNKNOWN'}
