"""Small CLI adapters over the existing runner and checkpoint ledger.

Completion supplies file identities and operational fields, never scientific facts.
"""
import ast
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

from rds_project import ProjectStore, canonical, digest, execution_route, file_sha, number, require

MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_FILES = 128
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def cas_json(root, value):
    return cas_bytes(root, canonical(value).encode('utf-8'), 'json')


def cas_bytes(root, raw, suffix='bin'):
    require(isinstance(raw, bytes) and re.fullmatch(r'[a-z0-9]{1,12}', suffix), 'Invalid CAS bytes or suffix')
    sha = hashlib.sha256(raw).hexdigest()
    directory = Path(root).resolve() / '.rds' / 'cas'
    require(directory.resolve().is_relative_to(Path(root).resolve()), 'CAS escapes project root')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (sha + '.' + suffix)
    try:
        with path.open('xb') as handle:
            handle.write(raw)
    except FileExistsError:
        require(file_sha(path) == sha, 'CAS integrity failure')
    return {'sha256': sha, 'path': str(path), 'bytes': len(raw)}


def latest_decision(root, checkpoint_id=None):
    """Reuse recovery's integrity checks; do not infer a question from directory names."""
    from rds_checkpoints import restore_checkpoint
    store = ProjectStore(root)
    snapshot = store.snapshot(check_bindings=True)
    with store._db(True) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone():
            raise ValueError('No decision context: record a scoped choice with advise --choose --record first')
        row = (db.execute('SELECT id FROM checkpoints WHERE id=?', (checkpoint_id,)).fetchone() if checkpoint_id
               else db.execute('SELECT id FROM checkpoints ORDER BY rowid DESC LIMIT 1').fetchone())
    require(row is not None, 'No recorded decision context')
    restored = restore_checkpoint(root, row['id'], snapshot, kind='project')
    require(restored['status'] != 'CONFLICT', 'Checkpoint conflicts with live project bindings')
    decision = restored['decision']
    require(all(k in decision for k in ('question_id', 'goal_revision', 'scope', 'candidate', 'evidence')),
            'Latest checkpoint has no scoped route; supply advise --context --choose --record')
    return decision, restored


def _discarded_choice_hint(records):
    def bounded(value, limit):
        value = value if isinstance(value, str) else 'UNKNOWN'
        escaped = json.dumps(value[:limit], ensure_ascii=True)[1:-1]
        return escaped[:limit] + ('...' if len(value) > limit or len(escaped) > limit else '')

    hints = ['"' + bounded(c.get('id', c.get('rule_id')), 96) + '": ' + bounded(c.get('reason'), 200)
             for c in records[:3]]
    if len(records) > 3:
        hints.append(str(len(records) - 3) + ' more discarded actions')
    return '; '.join(hints) + '. Repair the configured action; inspect full advice for original reasons'


def choice(advice, context, candidate_id=None):
    from rds_advisor import _scope, _text, _loop_route
    decision = context.get('decision', {})
    require(isinstance(decision, dict) and _text(decision.get('id')) and _text(decision.get('goal_revision')),
            'Recording a choice needs decision.id and goal_revision in the context')
    _scope(decision.get('scope'))
    searches = [r['search'] for r in advice.get('recommendations', []) if r.get('type') == 'EXECUTABLE_DIRECTION_SEARCH']
    available = [c for search in searches for c in search['candidates']]
    discarded = [c for search in searches for c in search.get('discarded_candidates', [])]
    matches = ([c for c in available if c.get('status') == 'READY'] if candidate_id is None else
               [c for c in available if c.get('id') == candidate_id or c.get('action', {}).get('id') == candidate_id])
    discarded_matches = ([] if candidate_id is None else
                         [c for c in discarded if c.get('id') == candidate_id or c.get('action_id') == candidate_id])
    if not matches and candidate_id is None:
        unresolved = [c for c in available if c.get('method_review', {}).get('status') == 'UNKNOWN']
        require(not unresolved, 'Method scope is unresolved; inspect method_review questions and clarify only the affected work')
        if not available and discarded and not any(search.get('blocked_candidates') for search in searches):
            raise ValueError('No READY candidate; configured actions were discarded: ' + _discarded_choice_hint(discarded))
    if not matches and len(discarded_matches) == 1:
        raise ValueError('Selected candidate was discarded: ' + _discarded_choice_hint(discarded_matches))
    require(len(matches) == 1, 'Choose one returned candidate ID; missing, pruned or ambiguous candidate')
    candidate = deepcopy(matches[0])
    from rds_methods import review_candidate
    method_review = review_candidate(context, candidate)
    require(method_review is None or method_review['status'] in {'COMPATIBLE', 'UNCONSTRAINED'},
            'Method scope is unresolved or conflicting; describe missing steps or ask the user to clarify the affected restriction')
    require(candidate.get('status') == 'READY', 'Candidate still needs evidence; inspect its pending prerequisites')
    require(type(context.get('require_goal_link', False)) is bool, 'require_goal_link must be boolean')
    goal_guard = None
    if context.get('require_goal_link'):
        from rds_advisor_search import _dependency_review, _goal_contribution
        guarded_context = deepcopy(context)
        if advice.get('objective_binding') is not None:
            require(guarded_context.get('objective_binding') == advice['objective_binding'],
                    'Goal-link guard: advice objective binding differs from the current checked context')
        dependency = _dependency_review(guarded_context, audit_receipts=True, audit_files=True)
        contribution = _goal_contribution(candidate.get('action', {}), guarded_context, dependency)
        mapped = (contribution or {}).get('graph_path', {})
        if mapped.get('status') != 'DECLARED_CONNECTED_PATH' and mapped.get('blocked_bindings'):
            binding = mapped['blocked_bindings'][0]
            require(False, 'Goal-link guard: this route relies on a binding whose checked evidence is not grounded; '
                           'repair ' + binding['token'] + ' (' + binding['reason'] + ') before dispatch')
        require(dependency is not None and mapped.get('status') == 'DECLARED_CONNECTED_PATH'
                and mapped.get('goal_review', {}).get('blocker_sets_complete') is True,
                'Goal-link guard needs a complete actual dependency map and a connected original-goal path')
        require(mapped['goal_review']['status'] != 'DECLARED_SUPPORTED',
                'Goal-link guard: the declared goal is already closed; inspect retained evidence before another job')
        require(not set(contribution['path'][1:-1]).intersection(dependency['declared_supported_closure']),
                'Goal-link guard: the path crosses an already closed intermediate obligation; inspect retained evidence')
        token = mapped['start_token']
        require(token in {row['token'] for row in dependency['ready_obligations']},
                'Goal-link guard: this target is not a current ready obligation; inspect existing evidence or earlier premises')
        goal_guard = {'dependency_map': deepcopy(guarded_context['dependency_map']),
                      'input_sha256': dependency['input_sha256'], 'ready_obligation': token,
                      'contribution': deepcopy(contribution)}
    history_errors = [str(f.get('reason', '')) for r in advice.get('recommendations', [])
                      for f in r.get('review', {}).get('flags', []) if f.get('kind') == 'LOOP_HISTORY_REVIEW_ERROR']
    require(not history_errors, 'Repair checkpoint integrity before executing a candidate'
            + (': ' + history_errors[0] if history_errors and history_errors[0] else ''))
    require(_loop_route(candidate) is not None, 'Candidate has no structured route identity')
    record = {'question_id': decision['id'], 'goal_revision': decision['goal_revision'],
              'scope': deepcopy(decision['scope']), 'candidate': deepcopy(candidate),
              'outcome': 'plan_locked', 'evidence': deepcopy(context.get('facts', {})),
              'scientific_support': 'UNKNOWN'}
    if goal_guard is not None:
        record['goal_guard'] = goal_guard
    for recommendation in advice.get('recommendations', []):
        search = recommendation.get('search', {})
        if any(c.get('id') == candidate['id'] for c in search.get('candidates', [])) and 'selection_review' in search:
            record['selection_review'] = deepcopy(search['selection_review'])
            break
    if 'goal_conditions' in decision:
        record['goal_conditions'] = deepcopy(decision['goal_conditions'])
    if 'method_constraints' in context:
        record['method_constraints'] = deepcopy(context['method_constraints'])
    if 'require_goal_link' in context:
        record['require_goal_link'] = context['require_goal_link']
    return record


def record_choice(root, advice, context, candidate_id, checkpoint_id):
    from rds_checkpoints import save_checkpoint
    record = choice(advice, context, candidate_id)
    # Read the contract before writing advice, so a rejected record leaves no blob.
    snapshot = ProjectStore(root).snapshot(check_bindings=True)
    record['advice'] = cas_json(root, advice)
    saved = save_checkpoint(root, checkpoint_id, snapshot, kind='project', decision=record)
    saved['candidate_id'] = record['candidate']['id']
    return saved


def reject_route(args):
    from rds_checkpoints import save_checkpoint
    decision, previous = latest_decision(args.root)
    require(isinstance(args.reason, str) and args.reason.strip() and len(args.reason) <= 512, 'Rejection reason must contain 1–512 characters')
    candidate = decision['candidate']
    names = {candidate.get('id'), candidate.get('action', {}).get('id')}
    route = args.route or candidate.get('id') or candidate.get('action', {}).get('id')
    require(route in names, 'Route does not match the current recorded candidate; select it with advise --choose --record')
    domain = None
    if getattr(args, 'domain', None):
        from rds_guard import read, validate_domain
        domain, _ = read(args.domain)
        validate_domain(domain, candidate)
    path = Path(args.evidence).resolve()
    require(path.is_file(), 'Falsifying evidence must be an existing file')
    # Read bounded original bytes; a label or exit status is not a proof.
    with path.open('rb') as handle:
        raw = handle.read(MAX_INPUT_BYTES + 1)
    require(len(raw) <= MAX_INPUT_BYTES, 'Evidence exceeds 2 MiB; supply a bounded witness or certificate')
    witness = {'source_path': str(path), 'sha256': hashlib.sha256(raw).hexdigest(),
               'size': len(raw), 'assurance': 'RECORDED_INPUT_NOT_SCIENTIFIC_VERIFICATION'}
    directory = Path(args.root).resolve() / '.rds' / 'cas'
    require(directory.resolve().is_relative_to(Path(args.root).resolve()), 'CAS escapes project root')
    directory.mkdir(parents=True, exist_ok=True)
    copy = directory / (witness['sha256'] + '.bin')
    try:
        with copy.open('xb') as handle:
            handle.write(raw)
    except FileExistsError:
        require(file_sha(copy) == witness['sha256'], 'Witness CAS integrity failure')
    witness['path'] = str(copy)
    decision = {**decision, 'outcome': 'rejected', 'reason': args.reason,
                'falsification': witness, 'previous_checkpoint': previous['id']}
    if domain is not None:
        decision['rejected_domain'] = domain
    checkpoint_id = args.id or 'reject-' + str(time.time_ns())
    saved = save_checkpoint(args.root, checkpoint_id, ProjectStore(args.root).snapshot(check_bindings=True),
                            kind='project', decision=decision)
    return {'status': 'RECORDED_REJECTION', 'route': route, 'checkpoint': saved,
            'evidence': witness, 'scientific_support': 'UNKNOWN', 'execution_started': False}


def record_falsification(root, *, witness, reason, route=None, domain=None):
    """Python entry for a declared counterexample, using the same checkpoint gates."""
    from types import SimpleNamespace
    raw = canonical(witness).encode('utf-8')
    require(isinstance(witness, dict) and len(raw) <= MAX_INPUT_BYTES, 'Witness must be a bounded JSON object')
    if domain is not None:
        from rds_guard import validate_domain
        decision, _ = latest_decision(root)
        validate_domain(domain, decision['candidate'])
    ref = cas_json(root, witness)
    return reject_route(SimpleNamespace(root=root, route=route, reason=reason, evidence=ref['path'], id=None,
                                       domain=cas_json(root, domain)['path'] if domain is not None else None))


def _policy_route(request, route):
    guard = request.get('guard')
    if guard is None:
        return route
    return {'advisor_route': route, 'guard': {key: guard.get(key) for key in ('engine_sha256', 'verifier_sha256')},
            'native_executable_sha256': guard.get('native_binding', {}).get('sha256')}


def _charge_ledger(root, workspace, request, seconds, route=None, source_root=None, dispatch=True, executor_sha256=None, existing_only=False):
    """Conservatively charge external controller work to the existing budget.

    This decreases allowance; it never extends the contract or grants execution
    authority. The child still has its own frozen command and run admission.
    """
    store = ProjectStore(root)
    with store._db() as db:
        db.execute('BEGIN IMMEDIATE')
        contract = store._contract(db)
        require('advisor_policy' not in contract,
                'Program-owned Advisor requires project advance/create/execute; quick child allowance cannot bypass it')
        require('stop_policy' not in contract and 'maintenance_allowance' not in contract,
                'Configured stop/maintenance policies require project create/execute; quick child allowance cannot bypass them')
        require(set(contract['budget']) == {'wall_seconds'}, 'Quick exec supports a wall-only parent ledger; use a full project manifest for other resources')
        _, errors = store._bindings(contract)
        require(not errors, '; '.join(errors))
        require(not existing_only or dispatch and 'execution_policy' in contract,
                'Execution policy: observation requires its configured parent ledger')
        if dispatch and 'execution_policy' in contract:
            if executor_sha256 is not None:
                require(file_sha(request['argv'][0]) == executor_sha256, 'Execution policy: command executable changed before charge')
            key = execution_route(request['argv'], request['inputs'], request['outputs'], source_root or workspace,
                                  contract.get('objective_sha256', request.get('objective_sha256')), route=_policy_route(request, None),
                                  arm='tool', executor_sha256=executor_sha256)
            request['execution_policy_owner'] = {'root': str(store.root), 'contract_sha256': digest(contract),
                                                'policy_sha256': digest(contract['execution_policy']), 'route_sha256': key}
            rows = [json.loads(row['body']) for row in db.execute(
                "SELECT body FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE' "
                "AND json_extract(body,'$.execution_route_sha256')=? ORDER BY id", (key,))]
            if existing_only:
                rows = [prior for prior in rows if Path(prior['job_root']).resolve() == workspace.resolve()]
                require(len(rows) == 1 and rows[0]['request_sha256'] == digest(request),
                        'Execution policy: existing child has no matching parent allowance for this job and request')
            for prior in rows:
                require(prior.get('contract_sha256') == digest(contract)
                        and prior.get('execution_policy_sha256') == digest(contract['execution_policy']),
                        'Execution policy: retained parent allowance binding differs')
                require(Path(prior['job_root']).is_dir(),
                        'Execution policy: charged child state is unavailable; inspect retained allowance, no refund or new launch')
                child = ProjectStore(prior['job_root'])
                require(child.path.is_file(), 'Execution policy: charged child state is unavailable; inspect retained allowance, no refund or new launch')
                from rds_project import load_json
                stored = load_json(child.root / 'rds-exec-request.json')
                require(digest(stored) == prior['request_sha256'] and stored.get('execution_policy_owner') == request['execution_policy_owner'],
                        'Execution policy: retained child request binding differs')
                with child._db(True) as child_db:
                    runs = list(child_db.execute('SELECT id FROM runs'))
                    require(len(runs) == 1, 'Execution policy: charged child has an incomplete or ambiguous run; recover it without a new allowance')
                    run = child._run(child_db, runs[0]['id'])
                    observed = child._observe(child_db, run)
                    reviewed = stored.get('guard') is not None and (run['status'] == 'COMPLETED'
                               or existing_only and run['status'] in {'FAILED', 'INTERRUPTED'})
                    guard_event = (child_db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='QUICK_EXEC_REGRESSION_REVIEW' ORDER BY id DESC LIMIT 1").fetchone()
                                   if reviewed else None)
                if existing_only or run['status'] in {'RESERVED', 'RUNNING', 'COMPLETED'}:
                    result = {'status': 'EXISTING_JOB', 'job_root': str(child.root), 'ledger_root': str(store.root),
                            'receipt': observed if 'sha256' in observed else None, 'execution_started': False,
                            'policy_observation': 'Retained attempt; inspect or recover it', 'scientific_support': 'UNKNOWN'}
                    if reviewed:
                        require(guard_event is not None, 'Guard review is unfinished; inspect retained job and allowance')
                        ref = json.loads(guard_event['body'])['report']
                        report_path = Path(ref['path']).resolve()
                        require(report_path.is_relative_to((child.root / '.rds/cas').resolve()) and file_sha(report_path) == ref['sha256'],
                                'Guard report CAS integrity failure')
                        from rds_guard import read
                        result['regression_review'] = read(report_path)[0]
                    return result
            require(len(rows) < contract['execution_policy']['max_attempts'],
                    'Execution policy: unchanged route reached max_attempts; failures do not establish scientific impossibility')
        amount = number(seconds, 'external wall allowance', True)
        row = db.execute("SELECT * FROM budget WHERE resource='wall_seconds'").fetchone()
        require(row['spent'] + row['charged'] + row['reserved'] + amount <= row['cap'] + 1e-9,
                'Insufficient parent ledger wall_seconds budget')
        event = {'kind': 'EXTERNAL_RUN_ALLOWANCE', 'job_root': str(workspace), 'request_sha256': digest(request),
                 'resource': 'wall_seconds', 'amount': amount, 'accounting': 'CONSERVATIVE_ALLOWANCE',
                 'execution_authority': 'UNCHANGED'}
        if dispatch and 'execution_policy' in contract:
            event.update(execution_route_sha256=key, contract_sha256=digest(contract),
                         execution_policy_sha256=digest(contract['execution_policy']), advisor_route_sha256=route)
        require(db.execute("SELECT 1 FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE' AND json_extract(body,'$.job_root')=? LIMIT 1",
                           (str(workspace),)).fetchone() is None, 'External job allowance already consumed; inspect its preserved state')
        db.execute("UPDATE budget SET charged=charged+? WHERE resource='wall_seconds'", (amount,))
        db.execute('INSERT INTO events(body) VALUES (?)', (canonical(event),))


def _checkpoint_name(stage, name):
    value = 'exec-' + stage + '-' + name
    return value if len(value) <= 64 else 'exec-' + stage + '-' + digest(name)[:48]


def _inputs(root, argv, binds):
    """Bound existing argv files and static local Python imports, once per file.

    Dynamic imports, environment reads and remote files require explicit --bind.
    """
    files, pending, raw_by_path = {}, [], {}
    total = 0

    def add(value, role):
        path = (root / value).resolve()
        require(path.is_relative_to(root) and not path.is_relative_to((root / '.rds').resolve()), 'Input escapes source root or addresses operational state')
        require(path.is_file(), 'Input file unavailable: ' + value)
        if path not in raw_by_path:
            with path.open('rb') as handle:
                raw = handle.read(MAX_INPUT_BYTES + 1)
            require(len(raw) <= MAX_INPUT_BYTES, 'Input exceeds 2 MiB: ' + value)
            raw_by_path[path] = raw
            pending.append(path)
        files.setdefault(path, set()).add(role)
        if path.suffix == '.py':
            parent = path.parent
            while parent != root:
                init = parent / '__init__.py'
                if init.is_file() and init not in raw_by_path:
                    add(init.relative_to(root).as_posix(), 'code')
                parent = parent.parent

    for value in argv[1:]:
        if not value.startswith('-') and (root / value).is_file():
            add(value, 'code' if Path(value).suffix == '.py' else 'data')
    for binding in binds:
        role, sep, value = binding.partition('=')
        require(sep and role in {'code', 'config', 'data', 'evaluator'}, '--bind expects code|config|data|evaluator=relative/path')
        add(value, role)
    while pending:
        path = pending.pop()
        raw = raw_by_path[path]
        total += len(raw)
        require(len(files) <= MAX_FILES and total <= MAX_TOTAL_BYTES, 'Input discovery exceeds 128 files or 64 MiB; use an explicit project contract')
        if path.suffix != '.py':
            continue
        try:
            tree = ast.parse(raw)
        except (SyntaxError, ValueError) as exc:
            raise ValueError('Cannot inspect Python input: ' + str(path)) from exc
        for node in ast.walk(tree):
            names = [n.name for n in node.names] if isinstance(node, ast.Import) else [node.module or ''] if isinstance(node, ast.ImportFrom) else []
            for name in names:
                parts = name.split('.') if name else []
                bases = [path.parent, root]
                if isinstance(node, ast.ImportFrom) and node.level:
                    base = path.parent
                    for _ in range(node.level - 1):
                        base = base.parent
                    bases = [base]
                for base in bases:
                    stem = base.joinpath(*parts)
                    candidates = [stem.with_suffix('.py'), stem / '__init__.py'] if parts else []
                    if isinstance(node, ast.ImportFrom):
                        candidates += [stem / (alias.name + '.py') for alias in node.names]
                    for local in candidates:
                        if local.is_file():
                            add(str(local.relative_to(root)), 'code')
    require(any('code' in roles for roles in files.values()), 'Bind at least one code file; inline commands need --bind code=...')
    return files, raw_by_path


def execute(args, review=None):
    """Create one frozen normal ProjectStore per named job, without JSON boilerplate."""
    root = Path(args.root).resolve()
    require(root.is_dir(), 'Source root must exist')
    owner = Path(args.ledger).resolve() if review is not None else None
    source_store = ProjectStore(root)
    if source_store.path.is_file():
        with source_store._db(True) as db:
            # Native research records can share this database before project init.
            has_contract = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone()
            source_contract = source_store._contract(db) if has_contract else {}
        require('advisor_policy' not in source_contract,
                'Program-owned Advisor requires project advance/create/execute; quick exec cannot bypass it')
        require('stop_policy' not in source_contract and 'maintenance_allowance' not in source_contract,
                'Configured stop/maintenance policies require project create/execute; quick exec cannot bypass them')
        if 'execution_policy' in source_contract:
            require(owner is None or owner == root, 'Frozen source execution policy cannot be replaced by another ledger')
            owner = root
    timeout = number(args.timeout, 'timeout', True)
    require(timeout <= 3600, 'Quick exec is bounded to 3600 seconds; use project execute --background for longer jobs')
    argv = list(args.argv)
    if argv[:1] == ['--']:
        argv.pop(0)
    require(argv, 'Supply the command after --')
    if argv[0].endswith('.py') and (root / argv[0]).is_file():
        argv = [sys.executable, '-B'] + argv
    if review is not None:
        if review[1].get('require_goal_link'):
            from rds_math import check_context
            binding = check_context(args.ledger, review[1])
            if binding is not None:
                context = {**deepcopy(review[1]), 'objective_binding': binding}
                review = (review[0], context)
        selected = choice(review[0], review[1], args.choose)  # Reject ambiguity before creating a job.
        args.choose = selected['candidate']['id']
    argv[0] = ProjectStore._command(argv)
    from rds_math import bind_objective, blob, objective, objective_spec, read_bytes
    goal = objective(root)
    goal_raw = blob(root, goal['asset']) if goal else None
    if getattr(args, 'objective', None):
        supplied = read_bytes(root / args.objective)
        objective_spec(supplied)
        require(goal_raw is None or goal_raw == supplied, 'Objective is frozen; changed goals need an explicit new project')
        goal_raw = supplied
    guard_path, guard_seconds = None, 0
    binds = list(args.bind)
    if getattr(args, 'guard', None):
        from rds_guard import policy_inputs, exact
        require(not args.background, 'Synchronous regression review cannot run with --background; review the completed background receipt separately')
        guard_path = (root / args.guard).resolve()
        policy, dependencies = policy_inputs(guard_path, root)
        guard_seconds = float(exact(policy.get('wall_seconds', 30)))
        require(guard_seconds < timeout, 'Exec timeout must cover both the command and the guard wall cap')
        require(all(row['candidate'] in args.output for row in policy.get('metrics', [])), 'Declare each guarded candidate using --output')
        binds += ['data=' + p.relative_to(root).as_posix() for p in dependencies]
    files, raw_by_path = _inputs(root, argv, binds)
    # Validate all output declarations before creating a child workspace or charging
    # an owning project ledger. A rejected binding must be safe to retry unchanged.
    if args.output:
        preflight = ProjectStore(root)
        output_roots = sorted({Path(p).parts[0] for p in args.output if len(Path(p).parts) > 1}) or ['outputs']
        output_files = sorted({Path(p).as_posix() for p in args.output if len(Path(p).parts) == 1})
        preflight_contract = {'output_roots': output_roots, 'output_files': output_files,
                              'bindings': [{'path': p.relative_to(root).as_posix()} for p in files]}
        seen_outputs = set()
        bound_paths = [(root / p.relative_to(root)).resolve() for p in files]
        for output in args.output:
            output_path = preflight._path(output, output=True, contract=preflight_contract)
            output_key = preflight._output_key(output_path)
            require(output_key not in seen_outputs, 'Output paths must be unique')
            seen_outputs.add(output_key)
            require(not any(output_path == bound or output_path in bound.parents or bound in output_path.parents
                            for bound in bound_paths), 'Output path overlaps a bound input')
    request = {'argv': argv, 'timeout': timeout, 'outputs': args.output,
               'inputs': [{'path': p.relative_to(root).as_posix(), 'roles': sorted(roles),
                           'sha256': hashlib.sha256(raw_by_path[p]).hexdigest()} for p, roles in sorted(files.items())]}
    if goal_raw is not None:
        request['objective_sha256'] = hashlib.sha256(goal_raw).hexdigest()
    if guard_path is not None:
        request['guard'] = {'path': guard_path.relative_to(root).as_posix(), 'wall_seconds': guard_seconds,
                            'engine_sha256': file_sha(Path(__file__).with_name('rds_guard.py'))}
        if policy.get('milestones'):
            from rds_verify import verifier_id
            request['guard']['verifier_sha256'] = verifier_id()
            request['guard']['native_executable'] = os.environ.get('RDS_LEAN_EXECUTABLE')
            if any(row['expected']['assurance'] == 'LEAN_KERNEL_CHECKED' for row in policy['milestones']):
                from rds_lean_verify import _executable
                try:
                    native, fingerprint = _executable()
                    request['guard']['native_binding'] = {'path': str(native), 'sha256': fingerprint}
                except (ValueError, OSError) as exc:
                    request['guard']['native_binding'] = {'status': 'UNKNOWN', 'reason': str(exc)}
    if review is not None:
        request['research_context'] = {'sha256': digest(review[1]), 'candidate': args.choose,
                                       'ledger': str(Path(args.ledger).resolve())}
    execution_policy, executor_sha256 = None, None
    if owner is not None:
        parent = ProjectStore(owner)
        with parent._db(True) as db:
            parent_contract = parent._contract(db)
            execution_policy = parent_contract.get('execution_policy')
            if execution_policy is not None:
                _, errors = parent._bindings(parent_contract)
                require(not errors, '; '.join(errors))
        if execution_policy is not None:
            executor_sha256 = file_sha(argv[0])
            request['execution_policy_owner'] = {'root': str(parent.root), 'contract_sha256': digest(parent_contract),
                'policy_sha256': digest(execution_policy), 'route_sha256': execution_route(request['argv'], request['inputs'],
                    request['outputs'], root, parent_contract.get('objective_sha256', request.get('objective_sha256')),
                    route=_policy_route(request, None), arm='tool', executor_sha256=executor_sha256)}
    args.name = args.name or 'exec-' + digest(request)[:20]
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', args.name), 'Job name must contain 1–64 safe identifier characters')
    workspace = root / '.rds' / 'exec' / args.name
    require(workspace.resolve().is_relative_to(root), 'Exec workspace escapes root')
    if workspace.exists():
        from rds_project import load_json
        previous = load_json(workspace / 'rds-exec-request.json')
        require(previous == request, 'Job identity is frozen; changed inputs need a new --name')
        if execution_policy is not None:
            return _charge_ledger(owner, workspace, request, timeout, source_root=root,
                                  executor_sha256=executor_sha256, existing_only=True)
        state = ProjectStore(workspace).snapshot(check_bindings=True)
        require(not state['binding_check']['errors'], 'Frozen job bindings changed')
        receipt = next((r for r in state['receipts'] if r['run_id'] == args.name), None)
        result = {'status': 'EXISTING_JOB', 'job_root': str(workspace), 'ledger_root': str(Path(args.ledger).resolve()) if review is not None else str(workspace), 'receipt': receipt,
                  'execution_started': False, 'scientific_support': 'UNKNOWN'}
        if guard_path is not None:
            store = ProjectStore(workspace)
            with store._db(True) as db:
                event = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='QUICK_EXEC_REGRESSION_REVIEW' ORDER BY rowid DESC LIMIT 1").fetchone()
            require(event is not None, 'Guard review is unfinished; inspect the retained job and allowance')
            ref = json.loads(event['body'])['report']
            report_path = Path(ref['path']).resolve()
            require(report_path.is_relative_to((workspace / '.rds' / 'cas').resolve()) and file_sha(report_path) == ref['sha256'], 'Guard report CAS integrity failure')
            from rds_guard import read
            result['regression_review'] = read(report_path)[0]
        return result
    if owner is not None:
        from rds_advisor import _loop_route
        observation = _charge_ledger(owner, workspace, request, timeout,
            route=_loop_route(selected['candidate']) if review is not None else None, source_root=root,
            executor_sha256=executor_sha256)
        if observation is not None:
            return observation
    workspace.mkdir(parents=True)
    bindings = []
    if goal_raw is not None:
        bind_objective(root, goal_raw)
        bind_objective(workspace, goal_raw)
        (workspace / 'rds-exec-objective.json').write_bytes(goal_raw)
        bindings.append({'path': 'rds-exec-objective.json', 'sha256': request['objective_sha256'], 'role': 'config'})
    for p, roles in files.items():
        target = workspace / p.relative_to(root)
        require(not target.name.startswith('rds-exec-'), 'Input uses a reserved rds-exec- filename')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw_by_path[p])
        for role in roles:
            bindings.append({'path': target.relative_to(workspace).as_posix(), 'sha256': file_sha(target), 'role': role})
    metadata = {'kind': 'OPERATIONAL_COMMAND_WRAPPER', 'request': request,
                'input_discovery': 'ARGV_FILES_AND_STATIC_LOCAL_PYTHON_IMPORTS',
                'hidden_inputs': 'UNKNOWN', 'evaluator': 'EXIT_CODE_AND_DECLARED_OUTPUT_EXISTENCE_ONLY',
                'scientific_support': 'UNKNOWN'}
    for name, value in [('rds-exec-request.json', request), ('rds-exec-metadata.json', metadata)]:
        (workspace / name).write_text(canonical(value), encoding='utf-8')
    bindings.append({'path': 'rds-exec-request.json', 'sha256': file_sha(workspace / 'rds-exec-request.json'), 'role': 'config'})
    for role in ('config', 'data', 'evaluator'):
        if not any(b['role'] == role for b in bindings):
            bindings.append({'path': 'rds-exec-metadata.json', 'sha256': file_sha(workspace / 'rds-exec-metadata.json'), 'role': role})
    # Missing scientific identity is explicit UNKNOWN, not guessed from filenames.
    protocol = {k: 'UNKNOWN' for k in ('data_split', 'init', 'seed', 'checkpoint', 'schedule', 'sample_work', 'numeric_protocol')}
    protocol.update({role + '_sha256': ProjectStore._role_sha({'bindings': bindings}, role) for role in ('code', 'config', 'data')})
    protocol['purpose'] = 'OPERATIONAL_COMMAND_WRAPPER'
    (workspace / 'rds-exec-protocol.json').write_text(canonical(protocol), encoding='utf-8')
    bindings.append({'path': 'rds-exec-protocol.json', 'sha256': file_sha(workspace / 'rds-exec-protocol.json'), 'role': 'protocol'})
    # Absolute source-file arguments must refer to their frozen copies.
    frozen_argv = [argv[0]] + [str(Path(v).resolve().relative_to(root)) if Path(v).is_absolute() and Path(v).resolve() in files else v for v in argv[1:]]
    store = ProjectStore(workspace)
    # Multi-component paths authorize a directory root (strict containment). A
    # single-component path is a root-level file: it cannot sit strictly below any
    # root, so authorize that exact file instead of conflating it with a directory.
    output_roots = sorted({Path(p).parts[0] for p in args.output if len(Path(p).parts) > 1}) or ['outputs']
    output_files = sorted({Path(p).as_posix() for p in args.output if len(Path(p).parts) == 1})
    contract = {'schema': 1, 'bindings': bindings, 'allowed_commands': [frozen_argv],
                'output_roots': output_roots, 'budget': {'wall_seconds': timeout}, 'description': 'Explicitly invoked frozen tool command; not an OS sandbox or science verdict'}
    if output_files:
        contract['output_files'] = output_files
    if goal_raw is not None:
        contract['objective_sha256'] = request['objective_sha256']
    if execution_policy is not None:
        contract['execution_policy'] = deepcopy(execution_policy)
    store.initialize(contract)
    if review is not None:
        require(args.ledger, '--context for exec needs an existing --ledger for prospective decisions')
        record_choice(args.ledger, review[0], review[1], args.choose, _checkpoint_name('before', args.name))
    manifest = {'schema': 1, 'id': args.name, 'arm': 'tool', 'control_id': None,
                'protocol': {'path': 'rds-exec-protocol.json', 'sha256': file_sha(workspace / 'rds-exec-protocol.json')},
                'argv': frozen_argv, 'outpaths': args.output, 'timeout_seconds': timeout - guard_seconds,
                'resource_estimates': {'wall_seconds': timeout - guard_seconds}, 'description': 'Frozen quick exec'}
    store.register(manifest, executor_sha256=executor_sha256)
    for output in args.output:
        store._path(output, output=True, contract=contract).parent.mkdir(parents=True, exist_ok=True)
    if guard_path is not None:
        _charge_ledger(workspace, workspace, request, guard_seconds, dispatch=False)
    receipt = store.execute(args.name, background=args.background)
    regression = None
    if guard_path is not None:
        from rds_guard import evaluate
        try:
            regression = evaluate(workspace / request['guard']['path'], workspace)
        except (OSError, ValueError, KeyError, TypeError, RecursionError) as exc:
            regression = {'status': 'UNKNOWN', 'promotion_eligible': False, 'reason': str(exc), 'scientific_support': 'UNKNOWN'}
        if receipt.get('run_status') != 'SUCCEEDED':
            regression = {**regression, 'status': 'UNKNOWN', 'promotion_eligible': False, 'reason': 'The command did not succeed'}
        ref = cas_json(workspace, regression)
        with store._db() as db:
            db.execute('INSERT INTO events(body) VALUES (?)', (canonical({'kind': 'QUICK_EXEC_REGRESSION_REVIEW', 'report': ref}),))
    if review is not None:
        from rds_checkpoints import save_checkpoint
        decision, _ = latest_decision(args.ledger, _checkpoint_name('before', args.name))
        decision = {**decision, 'execution': {'job_root': str(workspace), 'receipt_sha256': receipt.get('sha256'),
                                            'run_status': receipt.get('run_status', 'UNKNOWN')},
                    'pending_evidence': ['Assess the original output; completion alone does not reject or prove a hypothesis']}
        if regression is not None:
            decision['regression_review'] = {'status': regression['status'], 'report': ref, 'scientific_support': 'UNKNOWN'}
        save_checkpoint(args.ledger, _checkpoint_name('after', args.name), ProjectStore(args.ledger).snapshot(), kind='project', decision=decision)
    result = {'status': receipt.get('run_status', 'UNKNOWN'), 'job_root': str(workspace),
            'ledger_root': str(Path(args.ledger).resolve()) if review is not None else str(workspace),
            'receipt': receipt, 'execution_started': True, 'scientific_support': 'UNKNOWN'}
    if regression is not None:
        result['regression_review'] = regression
    return result


def brief(root, value, version, formal=False):
    """Persist full output and expose a bounded, truthful operational digest."""
    ref = cas_json(root, value)
    summary = {'status': value.get('status', value.get('run_status', 'RECORDED')), 'sha256': ref['sha256'], 'record': ref['path']}
    for key in ('id', 'name', 'source_sha256', 'module', 'assurance'):
        if key in value:
            summary[key] = value[key]
    if 'dependency_map' in value and 'input_review' in value:
        summary['authorization'] = 'UNCHANGED'
        if 'snapshot_sha256' in value:
            summary['snapshot_sha256'] = value['snapshot_sha256']
        goals = value.get('goals', {})
        summary['goals'] = {name: row['status'] for name, row in list(goals.items())[:3]}
        summary['omitted_goals'] = max(0, len(goals) - 3)
        review = value['input_review']
        summary['input_repairs'] = len(review['repairs']) + len(review.get('format_repairs', []))
        summary['input_warnings'] = len(review['warnings'])
        summary['input_issues'] = len(review['errors'])
        step = value.get('next_step', {})
        # Full field diagnostics and tables remain at the existing CAS locator.
        summary['next_step'] = {k: v for k, v in step.items() if k != 'fields'}
        if step.get('fields'):
            summary['next_step']['fields'] = step['fields'][:3]
            summary['next_step']['omitted_fields'] = max(0, len(step['fields']) - 3)
        if 'revision' in value:
            summary['lost_support_count'] = len(value['revision']['lost_support'])
            summary['gained_support_count'] = len(value['revision']['gained_support'])
            summary['lost_support'] = value['revision']['lost_support'][:3]
            summary['gained_support'] = value['revision']['gained_support'][:3]
        if 'support_cone' in value:
            cone = value['support_cone']
            summary['support_cone'] = {key: cone[key] for key in ('node_id', 'supported', 'derivation_rule')}
            for key in ('support_cone_nodes', 'support_cone_rules'):
                summary['support_cone'][key] = cone[key][:3]
                summary['support_cone']['omitted_' + key] = max(0, len(cone[key]) - 3)
    if value.get('status') == 'LOCAL_CATALOG' and isinstance(value.get('tools'), list):
        registered = {row['data']['name'] for row in value['tools'] if row['kind'] == 'tool-adoption'}
        candidates = sorted((row['data'] for row in value['tools'] if row['kind'] == 'tool'),
                            key=lambda item: item['name'])
        summary['tool_count'] = len(candidates)
        summary['omitted_tools'] = max(0, len(candidates) - 3)
        summary['reuse_checked'] = False
        summary['tools'] = []
        for item in candidates[:3]:
            entry = item['entry']
            row = {'name': item['name'], 'entry': entry if len(entry) <= 128 else None,
                   'recorded_registration': item['name'] in registered}
            if len(entry) > 128:
                row['entry_omitted'] = True
            summary['tools'].append(row)
    if 'affected' in value:
        summary['affected_count'] = len(value['affected'])
    if 'recommendations' in value:
        summary['gaps'] = sum(len(r.get('frontier', {}).get('gaps', [])) for r in value['recommendations'])
        summary['candidates'] = [c.get('id') for r in value['recommendations'] for c in r.get('search', {}).get('candidates', [])][:3]
        selection = next((r['search']['selection_review'] for r in value['recommendations']
                          if 'selection_review' in r.get('search', {})), None)
        flags = [f.get('kind') for r in value['recommendations'] for f in r.get('review', {}).get('flags', [])]
        if selection is not None:
            summary['selection_basis'] = selection['basis']
            flags += [f['kind'] for f in selection['flags']]
            if 'next_move' in selection:
                summary['next_move'] = selection['next_move']['kind']
            if 'goal' in selection:
                summary['goal_input_status'] = selection['goal']['status']
        advisory_moves = {'GOAL_CONTRIBUTION_UNDECLARED': 'REVIEW_GOAL_LINK',
                          'GOAL_CONTRIBUTION_INVALID': 'REVIEW_GOAL_LINK'}
        relevant = [kind for kind in flags if kind in advisory_moves and advisory_moves[kind] == summary.get('next_move')]
        flags = relevant + [kind for kind in flags if kind not in advisory_moves]
        summary['flags'] = list(dict.fromkeys(flags))[:3]
    owned = value.get('advisor') or value
    if 'working_set' in owned:
        summary['working_set'] = owned['working_set']
    if 'advisor' in value and 'receipt' in value:
        summary.update(status=value['receipt'].get('run_status', 'UNKNOWN'),
                       run_id=value['receipt'].get('run_id'), receipt_sha256=value['receipt'].get('sha256'),
                       advisor_status=owned.get('status'), scientific_support='UNKNOWN')
        if owned.get('reason'):
            summary['advisor_reason'] = str(owned['reason'])[:512]
    if owned.get('assurance') == 'PROGRAM_OWNED_EVIDENCE_NOT_SCIENTIFIC_PROOF':
        summary.update(selected_run=owned.get('selected_run'), snapshot_sha256=owned.get('snapshot_sha256'),
                       authorization='UNCHANGED', scientific_support='UNKNOWN', assurance=owned['assurance'])
        if owned.get('graph_ranker'):
            ranker = owned['graph_ranker']
            summary['graph_ranker'] = {key: ranker.get(key) for key in (
                'status', 'mode', 'selection_applied', 'precedence', 'reason')}
            summary['graph_ranker']['preferred'] = ranker.get('preferred', [])[:4]
            summary['graph_ranker']['scope_count'] = len(ranker.get('scope', []))
        if owned.get('next_move'):
            move = owned['next_move']
            summary['next_move'] = move.get('kind', move) if isinstance(move, dict) else move
        if owned.get('feasibility'):
            forecast = owned['feasibility']
            summary['feasibility'] = {'next_action':forecast['next_action'],
                'plans':[{'id':p['id'],'status':p['status']} for p in forecast['plans'][:4]],
                'pilot_budget':forecast['pilot_budget']}
            repair = forecast.get('repair_request')
            if repair:
                summary['tool_workbench_command'] = repair['tool_workbench_command']
        coverage = owned.get('coverage', {})
        summary['coverage'] = {key: coverage.get(key, 0) for key in ('runs', 'receipts', 'artifacts', 'parsed_observations')}
        summary['coverage']['unparsed_outputs'] = len(coverage.get('unparsed_outputs', []))
        summary['coverage']['errors'] = len(coverage.get('errors', []))
        summary['coverage']['gaps'] = len(coverage.get('gaps', []))
        summary['coverage']['declared_outputs'] = len(coverage.get('declared_outputs', []))
        summary['coverage']['missing_outputs'] = sum(row.get('status') == 'MISSING'
                                                     for row in coverage.get('declared_outputs', []))
        summary['coverage_errors'] = coverage.get('errors', [])[:3]
        summary['coverage_gaps'] = coverage.get('gaps', [])[:3]
        if 'tool_utilization' in owned:
            use = owned['tool_utilization']
            summary['tool_utilization'] = {key: use[key] for key in
                ('scope', 'counts', 'applicable_use_rate', 'applicable_consumption_rate')}
        warnings = [row['kind'] for row in owned.get('warnings', []) if 'kind' in row]
        summary['flags'] = list(dict.fromkeys(warnings + summary.get('flags', [])))[:5]
        summary['omitted_flags'] = max(0, len(set(warnings)) - len(summary['flags']))
    if 'job_root' in value:
        summary['job_root'] = value['job_root']
        summary['run_status'] = (value.get('receipt') or {}).get('run_status', 'UNKNOWN')
    if 'regression_review' in value:
        summary['regression_status'] = value['regression_review'].get('status', 'UNKNOWN')
        summary['promotion_eligible'] = value['regression_review'].get('promotion_eligible') is True
    if 'checkpoint' in value:
        summary['checkpoint'] = value['checkpoint'].get('id')
        summary['route'] = value['checkpoint'].get('candidate_id', value.get('route'))
    if isinstance(value.get('runs'), list):
        summary['runs'] = len(value['runs'])
        summary['receipts'] = len(value.get('receipts', []))
        summary['run_states'] = dict(Counter(run.get('status', run.get('run_status', 'UNKNOWN')) for run in value['runs']))
        latest = max((receipt for receipt in value.get('receipts', [])
                      if isinstance(receipt, dict) and type(receipt.get('ended_at')) in (int, float)
                      and isinstance(receipt.get('cwd'), str) and Path(receipt['cwd']).is_absolute()),
                     key=lambda receipt: receipt['ended_at'], default=None)
        if latest is not None:
            summary['latest_receipt'] = {key: latest.get(key) for key in ('run_id', 'run_status', 'exit_code')}
            if latest.get('errors'):
                error = latest['errors'][0]
                summary['latest_receipt']['error_count'] = len(latest['errors'])
                summary['latest_receipt']['errors'] = [error[:197] + '...' if len(error) > 200 else error]
            stderr = next((artifact for artifact in latest.get('artifacts', [])
                           if artifact.get('kind') == 'stderr.bin' and artifact.get('size', 0) > 0), None)
            if stderr is not None:
                summary['latest_receipt']['stderr_path'] = str((Path(latest['cwd']) / stderr['path']).resolve())
    ledger_root = Path(value.get('ledger_root', root)).resolve()
    ledger = ledger_root / '.rds' / 'project.sqlite3'
    if ledger.is_file() and isinstance(value.get('runs'), list) and isinstance(value.get('contract'), dict):
        try:
            summary['next_move'] = ({'next_move': 'inspect current program-owned evidence and selection',
                                     'command': 'python -B scripts/rds_cli.py project next'}
                                    if 'advisor_policy' in value['contract'] else ProjectStore(ledger_root).next_move())
        except (ValueError, OSError, sqlite3.Error):
            summary['next_move'] = {'next_move': 'inspect recorded project state', 'command': 'python -B scripts/rds_cli.py project status'}
    assurance = value.get('assurance') if formal else None
    formal_status = value.get('status', 'UNKNOWN') if formal and assurance in {'CERTIFICATE_CHECKED', 'LEAN_KERNEL_CHECKED'} else 'UNKNOWN'
    if formal:
        summary['formal_status'] = formal_status
        summary['formal_assurance'] = assurance or 'NONE'
        summary['application_status'] = value.get('application_status', 'UNKNOWN')
    if ledger.is_file():
        store = ProjectStore(ledger_root)
        with store._db(True) as db:
            summary['ledger_checkpoints'] = 0
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone():
                summary['ledger_checkpoints'] = db.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0]
    return summary
