"""Small CLI adapters over the existing runner and checkpoint ledger.

Completion supplies file identities and operational fields, never scientific facts.
"""
import ast
from contextlib import contextmanager, ExitStack, nullcontext
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
import uuid

from rds_project import ProjectStore, canonical, digest, execution_route, file_sha, number, require
from rds_mutation import mutation
from rds_advisor_choice import _discarded_choice_hint, choice
from rds_output_values import _brief_move, serialize_brief, compact_brief_planning
from rds_result_record import brief
from rds_quick_ledger import (
    _policy_route, _charge_ledger, _checkpoint_name, _validate_recorded_choice,
    _pending_quick_choices, _publish_quick_request, _pending_partial_child,
    _is_unmaterialized_allowance, _prospective_completion, _complete_prospective,
    _recover_prospective,
)
from rds_cas import (
    cas_json, cas_bytes
)

MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_FILES = 128
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def latest_decision(root, checkpoint_id=None):
    """Reuse recovery's integrity checks; do not infer a question from directory names."""
    from rds_checkpoints import restore_checkpoint
    store = ProjectStore(root)
    snapshot = store.snapshot(check_bindings=True)
    with store.readonly() as db:
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






def _prepare_choice(root, advice, context, candidate_id, request):
    from rds_admission_read import StaleAdmission, prepare
    from rds_advisor_coverage import project_context
    from rds_quick_admission import PreparedQuickChoice, evidence_fence
    store = ProjectStore(root)
    with store.readonly() as db:
        db.execute('BEGIN')
        context = project_context(root, context)
        files, receipts, source_bytes = (), (), 0
        if context.get('require_goal_link'):
            from rds_hypergraph_input import prepare_input
            spec, _ = prepare_input(context['dependency_map'])
            records = spec['nodes'] + spec['hyperedges']
            files = tuple(str(Path(row['source']['file']).absolute()) for row in records
                          if isinstance(row['source'], dict) and 'file' in row['source'])
            receipts = tuple(sorted({(row['evidence']['receipt']['project_root'],
                                     row['evidence']['receipt']['sha256'])
                                    for row in records if row.get('evidence') is not None}))
            source_bytes = spec.get('limits', {}).get('max_source_bytes', 8 * 1024 * 1024)
        before = evidence_fence(db, root, files, receipts, source_bytes)
        record = choice(advice, context, candidate_id)
        if evidence_fence(db, root, files, receipts, source_bytes) != before:
            raise StaleAdmission('QUICK evidence changed during selection; STALE')
        row = (db.execute('SELECT sha256,body FROM dependency_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()
               if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dependency_snapshots'").fetchone()
               else None)
        require((row['sha256'] if row else None) == context.get('dependency_snapshot_sha256'),
                'Dependency snapshot changed during QUICK preparation')
        record['advice'] = cas_json(root, advice)
        ref = record['advice']
        originals = [{'path': Path(ref['path']).relative_to(store.root).as_posix(),
                      'sha256': ref['sha256'], 'size': ref['bytes']}]
        admission = prepare(db, store.path, request, originals, json.loads(row['body']) if row else None)
    return PreparedQuickChoice(admission, canonical(record), digest(context), record['candidate']['id'],
                               context.get('dependency_snapshot_sha256'), files, receipts, source_bytes, before)


def record_choice(root, advice, context, candidate_id, checkpoint_id, *,
                  _expected_contract_sha256=None, _parent_db=None, _prepared_choice=None):
    from rds_checkpoints import save_checkpoint, append_checkpoint
    from rds_advisor_coverage import project_context
    # QUICK contracts remain frozen; native activation changes ownership.
    # Reject caller choices in owned ledgers, and pin the checked QUICK
    # contract through the original checkpoint publication transaction.
    store = ProjectStore(root)
    if _parent_db is None:
        snapshot = store.snapshot(check_bindings=True)
    else:
        require(_parent_db.in_transaction
                and Path(_parent_db.execute('PRAGMA database_list').fetchone()[2]).resolve() == store.path,
                'Choice publication requires its owning parent transaction')
        from rds_owned_history import snapshot as transaction_snapshot
        snapshot = transaction_snapshot(store, _parent_db)
    require('advisor_policy' not in snapshot['contract'],
            'Program-owned Advisor owns route choices; use project next/advance')
    require(_expected_contract_sha256 is None or
            _expected_contract_sha256 == snapshot['contract_sha256'],
            'Expected choice contract differs from the current QUICK contract; inspect the original decision')
    expected_contract = (snapshot['contract_sha256'] if _expected_contract_sha256 is None
                         else _expected_contract_sha256)
    if _parent_db is None:
        context = project_context(root, context)
        record = choice(advice, context, candidate_id)
    else:
        from rds_quick_admission import PreparedQuickChoice
        require(type(_prepared_choice) is PreparedQuickChoice
                and _prepared_choice.admission.ledger_path == str(store.path.resolve())
                and _prepared_choice.context_sha256 == digest(context)
                and _prepared_choice.candidate_id == candidate_id,
                'Locked choice publication needs its original native preparation')
        record = json.loads(_prepared_choice.decision_json)
    if _parent_db is not None:
        head = None
        if _parent_db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dependency_snapshots'").fetchone():
            head = _parent_db.execute('SELECT sha256 FROM dependency_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()
        require((head['sha256'] if head else None) == context.get('dependency_snapshot_sha256'),
                'Dependency snapshot changed before checkpoint publication; reanalyze the current map')
    # Analysis stays outside the gate; its original snapshot guards are checked
    # again while publishing the CAS and checkpoint as one bounded operation.
    with mutation():
        if _parent_db is None:
            record['advice'] = cas_json(root, advice)
        if _parent_db is None:
            saved = save_checkpoint(root, checkpoint_id, snapshot, kind='project', decision=record,
                                    _expected_contract_sha256=expected_contract,
                                    _expected_dependency_snapshot_sha256=context.get('dependency_snapshot_sha256'))
        else:
            saved = append_checkpoint(_parent_db, root, checkpoint_id, snapshot, kind='project', decision=record)
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
    with mutation():
        # Use the original guarded CAS producer, including binding-first checks.
        witness['path'] = cas_bytes(args.root, raw)['path']
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
    with mutation():
        ref = cas_json(root, witness)
        return reject_route(SimpleNamespace(root=root, route=route, reason=reason, evidence=ref['path'], id=None,
                                           domain=cas_json(root, domain)['path'] if domain is not None else None))


def _inputs(root, argv, binds):
    """Bound existing argv files and static local Python imports, once per file.

    Dynamic imports, environment reads and remote files require explicit --bind.
    """
    files, pending, raw_by_path = {}, [], {}
    total = 0

    def add(value, role):
        path = (root / value).resolve()
        require(path.is_relative_to(root) and not path.is_relative_to((root / '.rds').resolve())
                and all(os.path.normcase(part) != os.path.normcase('.rds')
                        for part in path.relative_to(root).parts),
                'Input escapes source root or addresses operational state')
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


def _parent_controls(root, *, db=None):
    """Read effective contract and steering together without creating a ledger."""
    if db is not None:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone() \
                or not db.execute('SELECT 1 FROM contract WHERE id=1').fetchone():
            return None, None
        from rds_steering import current
        return ProjectStore._contract(db), current(db)
    parent = ProjectStore(root)
    if not parent.path.is_file():
        return None, None
    with parent.readonly() as db:
        db.execute('BEGIN')
        return _parent_controls(root, db=db)


def execute(args, review=None, *, _native_preparation_root=None):
    """Create one frozen normal ProjectStore per named job, without JSON boilerplate."""
    root = Path(args.root).resolve()
    from rds_campaign import binding, enforce
    enforce(root)
    # A detached child ledger has its own contract/accounting. Bound campaigns
    # use the original project's native run admission instead of minting one.
    for scope in (root, getattr(args, 'ledger', None), _native_preparation_root):
        if scope is not None:
            require(binding(scope) is None,
                    'Bound campaign refuses detached QUICK jobs; use the canonical project create/execute/advance')
    from rds_project_lifecycle import check_root
    def check_source_root():
        if _native_preparation_root is None:
            check_root(root)
            return
        # Native qualification already belongs to its research project's
        # preparation ledger. This private caller path is not a CLI escape.
        parent = ProjectStore(_native_preparation_root)
        check_root(parent.root)
        token = root.name
        require(re.fullmatch(r'[0-9a-f]{32}', token) and
                root == parent.root / '.rds' / 'rsi' / 'tool-checks' / token and args.name == 'tool-check',
                'Native preparation must use its original managed workspace')
        from rds_tools import _preparation_contract
        with parent.readonly() as parent_db:
            if not _preparation_contract(parent, parent_db):
                rows = parent_db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='TOOL_PREPARATION_STARTED' "
                                         "AND json_extract(body,'$.request.token')=? LIMIT 2", (token,)).fetchall()
                require(len(rows) == 1, 'Native preparation requires its original project intent')
                intent = json.loads(rows[0]['body'])
                require(intent['request_sha256'] == digest(intent['request']) and
                        intent['request']['job_root'] == (root / '.rds/exec/tool-check').relative_to(parent.root).as_posix(),
                        'Native preparation intent or workspace changed')
    from rds_workspace import check_admission, read_pointer
    check_admission(root)
    check_source_root()
    require(root.is_dir(), 'Source root must exist')
    owner = Path(args.ledger).resolve() if review is not None else None
    source_contract, source_steering = _parent_controls(root)
    parent_contracts = {root: digest(source_contract) if source_contract is not None else None}
    if source_contract is not None:
        require(source_steering is None,
                'Human steering requires project create/execute; use the original child root to inspect or recover an existing quick job')
        require('advisor_policy' not in source_contract,
                'Program-owned Advisor requires project advance/create/execute; quick exec cannot bypass it')
        require('stop_policy' not in source_contract and 'maintenance_allowance' not in source_contract,
                'Configured stop/maintenance policies require project create/execute; quick exec cannot bypass them')
        require(_native_preparation_root is not None or 'execution_policy' in source_contract,
                'Frozen source project requires project create/execute; QUICK needs its original execution_policy accounting')
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
    pending_choice_recoveries = []
    deferred_choice_recovery = False
    resume_materialization = False
    if review is not None:
        from rds_math import check_context
        binding = check_context(args.ledger, review[1])
        if binding is not None:
            context = {**deepcopy(review[1]), 'objective_binding': binding}
            review = (review[0], context)
        retained = (root / '.rds/exec' / args.name
                    if args.name and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', args.name) else None)
        pending_choice_recoveries = _pending_quick_choices(root, owner, args.name)
        if pending_choice_recoveries:
            deferred_choice_recovery = True
        elif retained is not None and retained.exists():
            require(retained.resolve().is_relative_to(root), 'Exec workspace escapes root')
            from rds_project import load_json
            frozen_context = load_json(retained / 'rds-exec-request.json').get('research_context')
            require(frozen_context is not None and args.choose in (None, frozen_context['candidate']),
                    'Job identity is frozen; changed choice needs a new --name')
            args.choose = frozen_context['candidate']
        else:
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
    # Check the complete frozen layout before writing a prospective checkpoint
    # or charging an owning ledger. OS failures remain visible retained failures.
    generated = ['rds-exec-request.json', 'rds-exec-metadata.json', 'rds-exec-protocol.json']
    if goal_raw is not None:
        generated.append('rds-exec-objective.json')
    preflight = ProjectStore(root)
    def layout_key(path):
        return Path(preflight._output_key(path))
    def overlaps(left, right):
        return left == right or left in right.parents or right in left.parents
    generated_paths = [layout_key(root / name) for name in generated]
    bound_paths = [layout_key(p) for p in files]
    for path, key in zip(files, bound_paths):
        require(not path.name.startswith('rds-exec-'), 'Input uses a reserved rds-exec- filename')
        require(not any(overlaps(key, generated_path) for generated_path in generated_paths),
                'Input path overlaps a generated exec file')
    # Validate all output declarations before creating a child workspace or charging
    # an owning project ledger. A rejected binding must be safe to retry unchanged.
    if args.output:
        output_roots = sorted({Path(p).parts[0] for p in args.output if len(Path(p).parts) > 1}) or ['outputs']
        output_files = sorted({Path(p).as_posix() for p in args.output if len(Path(p).parts) == 1})
        preflight_contract = {'output_roots': output_roots, 'output_files': output_files,
                              'bindings': [{'path': p.relative_to(root).as_posix()} for p in files]}
        seen_outputs = set()
        for output in args.output:
            output_path = preflight._path(output, output=True, contract=preflight_contract)
            output_key = preflight._output_key(output_path)
            require(output_key not in seen_outputs, 'Output paths must be unique')
            key = layout_key(output_path)
            require(not any(overlaps(key, Path(previous)) for previous in seen_outputs),
                    'Output paths overlap each other')
            seen_outputs.add(output_key)
            require(not any(overlaps(key, bound) for bound in bound_paths), 'Output path overlaps a bound input')
            require(not any(overlaps(key, generated_path) for generated_path in generated_paths),
                    'Output path overlaps a generated exec file')
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
    if review is not None and not deferred_choice_recovery:
        request['research_context'] = {'sha256': digest(review[1]), 'candidate': args.choose,
                                       'ledger': str(Path(args.ledger).resolve())}
        request['research_context']['dependency_snapshot_sha256'] = review[1].get('dependency_snapshot_sha256')
    if owner is not None:
        check_admission(owner)
    execution_policy, executor_sha256 = None, None
    if owner is not None:
        parent = ProjectStore(owner)
        parent_contract = parent.contract()
        parent_sha = digest(parent_contract)
        require(owner not in parent_contracts or parent_contracts[owner] == parent_sha,
                'Quick parent contract changed during preparation; use the original project')
        parent_contracts[owner] = parent_sha
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
    if deferred_choice_recovery:
        matches = []
        for prior in pending_choice_recoveries:
            candidate = prior['candidate']
            candidate_id = prior['candidate_id']
            accepted_names = {candidate_id}
            action_id = candidate.get('action', {}).get('id') if isinstance(candidate.get('action'), dict) else None
            if isinstance(action_id, str):
                accepted_names.add(action_id)
            if args.choose is not None and args.choose not in accepted_names:
                continue
            candidate_request = deepcopy(request)
            candidate_request['research_context'] = {'sha256': digest(review[1]), 'candidate': candidate_id,
                                                      'ledger': str(Path(args.ledger).resolve())}
            candidate_request['research_context']['dependency_snapshot_sha256'] = review[1].get('dependency_snapshot_sha256')
            expected_name = args.name or 'exec-' + digest(candidate_request)[:20]
            if (prior['name'] == expected_name
                    and prior['request_sha256'] == digest(candidate_request)):
                matches.append((prior, candidate_request))
        require(len(matches) == 1,
                'Original prospective QUICK request does not uniquely match the pending allowance; inspect retained state')
        prior, request = matches[0]
        args.choose = prior['candidate_id']
        selected = {'candidate': prior['candidate']}
        if args.name is None:
            args.name = prior['name']
        resume_materialization = True
    locked_parents = {}
    prepared_choice = None

    @contextmanager
    def admission_context():
        nonlocal prepared_choice
        prepared_choice = (_prepare_choice(args.ledger, review[0], review[1], args.choose, request)
                           if review is not None and not resume_materialization else None)
        # The original allowance path acquires parent before child. Keep that
        # order during preparation and through the child's attempt commit.
        with mutation(), ExitStack() as locks:
            try:
                from rds_campaign import binding as campaign_binding
                for scope in (root, getattr(args, 'ledger', None), _native_preparation_root):
                    if scope is not None:
                        require(campaign_binding(scope) is None, 'Bound campaign refuses detached QUICK preparation')
                for parent_root in parent_contracts:
                    check_admission(parent_root)
                for parent_root in sorted(parent_contracts, key=lambda p: os.path.normcase(str(p))):
                    parent = ProjectStore(parent_root)
                    create_anchor = not parent.path.is_file()
                    parent.state_dir.mkdir(exist_ok=True)
                    parent_db = locks.enter_context(parent._db())
                    if create_anchor:
                        # No contract/budget/event is created. The same SQLite
                        # file serializes a concurrent first project init.
                        parent_db.execute('PRAGMA journal_mode=WAL')
                    parent_db.execute('BEGIN IMMEDIATE')
                    locked_parents[parent_root] = parent_db
                yield
            finally:
                locked_parents.clear()

    def check_quick_parents(*, validate_choice=True):
        # Both materialization and attempt admission require these live checks.
        check_source_root()
        for parent_root, prepared_sha in parent_contracts.items():
            from rds_campaign import detached_admission
            detached_admission(parent_root, locked_parents[parent_root])
            current_contract, steering = _parent_controls(parent_root, db=locked_parents[parent_root])
            current_sha = digest(current_contract) if current_contract is not None else None
            require(current_sha == prepared_sha,
                    'Quick parent contract changed before admission; review the original project')
            if current_contract is not None:
                require('advisor_policy' not in current_contract,
                        'Program-owned Advisor was enabled before quick admission; use the original project')
                require('stop_policy' not in current_contract and 'maintenance_allowance' not in current_contract,
                        'Configured stop/maintenance policies require project create/execute; quick exec cannot bypass them')
                require(steering is None,
                        'Human steering changed before quick admission; use project create/execute')
        if review is not None and validate_choice:
            from rds_quick_admission import check_choice
            check_choice(locked_parents[owner], owner, request, prepared_choice)
    def admit_quick(_db, _run):
        check_quick_parents(validate_choice=not resume_materialization)

    args.name = args.name or 'exec-' + digest(request)[:20]
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', args.name), 'Job name must contain 1–64 safe identifier characters')
    workspace = root / '.rds' / 'exec' / args.name
    require(workspace.resolve().is_relative_to(root), 'Exec workspace escapes root')
    partial_child = False
    if workspace.exists() and owner is not None:
        partial_child = ProjectStore(owner).pending_partial_child(workspace)
    if workspace.exists() and not partial_child:
        from rds_project import load_json
        previous = load_json(workspace / 'rds-exec-request.json')
        require(previous == request, 'Job identity is frozen; changed inputs need a new --name')
        if execution_policy is not None:
            result = _charge_ledger(owner, workspace, request, timeout, source_root=root,
                                   executor_sha256=executor_sha256, existing_only=True)
            guard_ref = None
            if guard_path is not None:
                with ProjectStore(workspace).readonly() as db:
                    event = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='QUICK_EXEC_REGRESSION_REVIEW' ORDER BY id DESC LIMIT 1").fetchone()
                guard_ref = json.loads(event['body'])['report'] if event is not None else None
            if result.get('receipt') is None:
                return result  # e.g. PENDING_MATERIALIZATION_RECOVERY: retained state, nothing to complete.
            _complete_prospective(workspace, request, result['receipt'], result.get('regression_review'), guard_ref)
            return result
        check_admission(workspace)
        state = ProjectStore(workspace).snapshot(check_bindings=True)
        require(not state['binding_check']['errors'], 'Frozen job bindings changed')
        receipt = next((r for r in state['receipts'] if r['run_id'] == args.name), None)
        result = {'status': 'EXISTING_JOB', 'job_root': str(workspace), 'ledger_root': str(Path(args.ledger).resolve()) if review is not None else str(workspace), 'receipt': receipt,
                  'execution_started': False, 'scientific_support': 'UNKNOWN'}
        ref = None
        if guard_path is not None:
            store = ProjectStore(workspace)
            with store.readonly() as db:
                event = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='QUICK_EXEC_REGRESSION_REVIEW' ORDER BY rowid DESC LIMIT 1").fetchone()
            require(event is not None, 'Guard review is unfinished; inspect the retained job and allowance')
            ref = json.loads(event['body'])['report']
            report_path = Path(ref['path']).resolve()
            require(report_path.is_relative_to((workspace / '.rds' / 'cas').resolve()) and file_sha(report_path) == ref['sha256'], 'Guard report CAS integrity failure')
            from rds_guard import read
            result['regression_review'] = read(report_path)[0]
        _complete_prospective(workspace, request, receipt, result.get('regression_review'), ref)
        return result
    if owner is not None or review is not None:
        with admission_context():
            check_quick_parents(validate_choice=not resume_materialization)
            if owner is not None:
                from rds_advisor import _loop_route
                observation = _charge_ledger(owner, workspace, request, timeout,
                    route=_loop_route(selected['candidate']) if review is not None else None, source_root=root,
                    executor_sha256=executor_sha256, _parent_db=locked_parents[owner])
                if observation is not None:
                    if observation.get('status') == 'PENDING_MATERIALIZATION_RECOVERY':
                        if review is None:
                            # Plain QUICK has no Advisor checkpoint to reselect.
                            # The exact request hash and pending allowance were
                            # validated by _charge_ledger, so resume its one
                            # pre-materialization attempt without charging again.
                            resume_materialization = True
                        else:
                            require(resume_materialization,
                                    'Pending QUICK allowance was not matched to its original choice; inspect retained state')
                    else:
                        return observation
            if review is not None:
                require(args.ledger and owner == Path(args.ledger).resolve(),
                        'Prospective choice and allowance must share the original owning ledger')
                if resume_materialization:
                    _validate_recorded_choice(locked_parents[owner], args.ledger,
                                              _checkpoint_name('before', args.name), args.choose,
                                              parent_contracts[owner])
                else:
                    record_choice(args.ledger, review[0], review[1], args.choose, _checkpoint_name('before', args.name),
                                  _expected_contract_sha256=parent_contracts[owner], _parent_db=locked_parents[owner],
                                  _prepared_choice=prepared_choice)
    if goal_raw is not None:
        bind_objective(root, goal_raw)
    with admission_context():
        check_quick_parents(validate_choice=not resume_materialization)
        if workspace.exists():
            require(owner is not None and resume_materialization,
                    'Retained QUICK child is not an admitted materialization recovery')
            parent_db = locked_parents[owner]
            row = parent_db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE' "
                                    "AND json_extract(body,'$.job_root')=?", (str(workspace),)).fetchone()
            require(row is not None, 'Original QUICK allowance is missing')
            _is_unmaterialized_allowance(parent_db, workspace, request, json.loads(row['body']))
            # Preserve every partial byte instead of deleting/overwriting it.
            # Forensic copies are not materialized jobs. Keep them outside
            # .rds/exec, which campaign and Advisor inventory as child projects.
            retained_directory = root / '.rds' / 'quick-partials'
            require(retained_directory.resolve().is_relative_to(root)
                    and not retained_directory.resolve().is_relative_to((root / '.rds/exec').resolve()),
                    'Partial QUICK retention escapes root')
            retained_directory.mkdir(parents=True, exist_ok=True)
            retained_path = retained_directory / ('.partial-' + workspace.name + '-' + uuid.uuid4().hex)
            require(workspace.resolve().is_relative_to(root) and retained_path.resolve().is_relative_to(root),
                    'Partial QUICK retention escapes root')
            # Preserve the old operational bytes under an inactive namespace.
            # Campaign binding walks the entire workspace, so a relocated
            # .rds/project.sqlite3 would still be discovered as a live ledger.
            # Retire the namespace before moving: an interrupted move is safe
            # to retry and never leaves a live ledger in the forensic tree.
            partial_state = workspace / '.rds'
            if partial_state.exists() or partial_state.is_symlink():
                require(partial_state.is_dir() and partial_state.resolve() == partial_state,
                        'Partial QUICK state must be its original contained directory')
                # Reserve a fresh container atomically. Bound inputs may have
                # any inactive .rds.retained name; none may be overwritten or
                # make the original charged request impossible to recover.
                retired_container = Path(tempfile.mkdtemp(prefix='.rds.retained-', dir=workspace))
                retired_state = retired_container / 'state'
                partial_state.rename(retired_state)
            workspace.rename(retained_path)
        workspace.mkdir(parents=True)
        # Publish identity before any input copy or SQLite initialization.
        _publish_quick_request(workspace, request)
        bindings = []
        if goal_raw is not None:
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
        for name, value in [('rds-exec-metadata.json', metadata)]:
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
        bound_owner = (ProjectStore(owner) if owner is not None
                       and workspace.is_relative_to(owner / '.rds' / 'exec')
                       and read_pointer(owner) is not None else False)
        store.initialize(contract, fresh_workspace=bound_owner,
                         _fresh_workspace_admission=(locked_parents[owner], request) if bound_owner else None)
        if owner is not None:
            from rds_workspace import bind as bind_workspace
            check_admission(owner)
            bind_workspace(workspace)
        manifest = {'schema': 1, 'id': args.name, 'arm': 'tool', 'control_id': None,
                    'protocol': {'path': 'rds-exec-protocol.json', 'sha256': file_sha(workspace / 'rds-exec-protocol.json')},
                    'argv': frozen_argv, 'outpaths': args.output, 'timeout_seconds': timeout - guard_seconds,
                    'resource_estimates': {'wall_seconds': timeout - guard_seconds}, 'description': 'Frozen quick exec'}
        store.register(manifest, executor_sha256=executor_sha256)
        for output in args.output:
            store._path(output, output=True, contract=contract).parent.mkdir(parents=True, exist_ok=True)
        from rds_campaign import QUICK_JOB_KIND
        for parent_db in locked_parents.values():
            if parent_db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
                ProjectStore.record_event(parent_db, {'kind': QUICK_JOB_KIND, 'job_root': str(workspace),
                                                     'request_sha256': digest(request),
                                                     'materialization_state': 'MATERIALIZED'})
    if guard_path is not None:
        _charge_ledger(workspace, workspace, request, guard_seconds, dispatch=False)
    receipt = store.execute(args.name, background=args.background, admission_guard=admit_quick,
                            admission_context=admission_context)
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
    _complete_prospective(workspace, request, receipt, regression, ref if regression is not None else None)
    result = {'status': receipt.get('run_status', 'UNKNOWN'), 'job_root': str(workspace),
            'ledger_root': str(Path(args.ledger).resolve()) if review is not None else str(workspace),
            'receipt': receipt, 'execution_started': True, 'scientific_support': 'UNKNOWN'}
    if regression is not None:
        result['regression_review'] = regression
    return result
