"""Small CLI adapters over the existing runner and checkpoint ledger.

Completion supplies file identities and operational fields, never scientific facts.
"""
import ast
from collections import Counter
from contextlib import contextmanager, ExitStack, nullcontext
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import uuid

from rds_project import ProjectStore, canonical, digest, execution_route, file_sha, number, require
from rds_mutation import mutation

MAX_INPUT_BYTES = 2 * 1024 * 1024
MAX_FILES = 128
MAX_TOTAL_BYTES = 64 * 1024 * 1024


def cas_json(root, value):
    return cas_bytes(root, canonical(value).encode('utf-8'), 'json')


@mutation()
def cas_bytes(root, raw, suffix='bin'):
    from rds_campaign import enforce
    enforce(root)
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
    from rds_advisor_coverage import require_complete
    require_complete(searches)
    require(all(search.get('context_sha256') == digest(context) for search in searches),
            'Advisor context changed after complete graph analysis; review the current graph and facts again')
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


def record_choice(root, advice, context, candidate_id, checkpoint_id, *,
                  _expected_contract_sha256=None, _parent_db=None):
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
    context = project_context(root, context)
    record = choice(advice, context, candidate_id)
    if _parent_db is not None:
        head = None
        if _parent_db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dependency_snapshots'").fetchone():
            head = _parent_db.execute('SELECT sha256 FROM dependency_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()
        require((head['sha256'] if head else None) == context.get('dependency_snapshot_sha256'),
                'Dependency snapshot changed before checkpoint publication; reanalyze the current map')
    # Analysis stays outside the gate; its original snapshot guards are checked
    # again while publishing the CAS and checkpoint as one bounded operation.
    with mutation():
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


def _policy_route(request, route):
    guard = request.get('guard')
    if guard is None:
        return route
    return {'advisor_route': route, 'guard': {key: guard.get(key) for key in ('engine_sha256', 'verifier_sha256')},
            'native_executable_sha256': guard.get('native_binding', {}).get('sha256')}


def _charge_ledger(root, workspace, request, seconds, route=None, source_root=None, dispatch=True, executor_sha256=None, existing_only=False, *, _parent_db=None):
    """Conservatively charge external controller work to the existing budget.

    This decreases allowance; it never extends the contract or grants execution
    authority. The child still has its own frozen command and run admission.
    """
    store = ProjectStore(root)
    with (store._db() if _parent_db is None else nullcontext(_parent_db)) as db:
        if _parent_db is None:
            db.execute('BEGIN IMMEDIATE')
        else:
            require(db.in_transaction and Path(db.execute('PRAGMA database_list').fetchone()[2]).resolve() == store.path,
                    'Allowance publication requires its owning parent transaction')
        if dispatch:
            from rds_campaign import detached_admission
            detached_admission(root, db)
        contract = store._contract(db)
        if dispatch and request.get('research_context'):
            # Bind allowance admission to the graph revision reviewed by the
            # caller. The final launch path also rechecks the current snapshot.
            reviewed = request['research_context'].get('dependency_snapshot_sha256')
            snapshot = None
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='dependency_snapshots'").fetchone():
                snapshot = db.execute('SELECT sha256 FROM dependency_snapshots ORDER BY rowid DESC LIMIT 1').fetchone()
            require((snapshot['sha256'] if snapshot else None) == reviewed,
                    'Dependency snapshot changed before execution allowance; review the complete graph again')
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
            else:
                same_job = [prior for prior in rows if Path(prior['job_root']).resolve() == workspace.resolve()]
                if same_job:
                    require(len(same_job) == 1, 'Execution policy: child allowance is ambiguous; inspect retained state')
                    prior = same_job[0]
                    require(prior.get('contract_sha256') == digest(contract)
                            and prior.get('execution_policy_sha256') == digest(contract['execution_policy']),
                            'Execution policy: retained parent allowance binding differs')
                    if not Path(workspace).exists() or _pending_partial_child(db, workspace):
                        _is_unmaterialized_allowance(db, workspace, request, prior)
                        return {'status': 'PENDING_MATERIALIZATION_RECOVERY', 'execution_started': False,
                                'job_root': str(workspace), 'ledger_root': str(store.root)}
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
        elif dispatch:
            rows = [json.loads(row['body']) for row in db.execute(
                "SELECT body FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE' "
                "AND json_extract(body,'$.job_root')=? ORDER BY id", (str(workspace),))]
            if rows:
                require(len(rows) == 1, 'External job allowance is ambiguous; inspect retained state')
                prior = rows[0]
                require(prior.get('request_sha256') == digest(request)
                        and prior.get('contract_sha256') == digest(contract),
                        'External job allowance differs from the original request or contract')
                if not Path(workspace).exists() or _pending_partial_child(db, workspace):
                    _is_unmaterialized_allowance(db, workspace, request, prior)
                    return {'status': 'PENDING_MATERIALIZATION_RECOVERY', 'execution_started': False,
                            'job_root': str(workspace), 'ledger_root': str(store.root)}
                raise ValueError('External job allowance already consumed; inspect its preserved state')
        from rds_steering import current
        require(current(db) is None,
                'Human steering requires project create/execute; new quick child allowances cannot bypass it')
        amount = number(seconds, 'external wall allowance', True)
        row = db.execute("SELECT * FROM budget WHERE resource='wall_seconds'").fetchone()
        require(row['spent'] + row['charged'] + row['reserved'] + amount <= row['cap'] + 1e-9,
                'Insufficient parent ledger wall_seconds budget')
        event = {'kind': 'EXTERNAL_RUN_ALLOWANCE', 'job_root': str(workspace), 'request_sha256': digest(request),
                 'contract_sha256': digest(contract),
                 'resource': 'wall_seconds', 'amount': amount, 'accounting': 'CONSERVATIVE_ALLOWANCE',
                 'execution_authority': 'UNCHANGED'}
        if dispatch:
            event['materialization_state'] = 'PENDING'
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


def _validate_recorded_choice(db, root, checkpoint_id, candidate_id, expected_contract_sha256):
    """Validate, but never recompute, the original QUICK choice during recovery."""
    from rds_checkpoints import read_checkpoint
    saved = read_checkpoint(db, checkpoint_id, root=root)
    require(saved is not None and saved['record']['kind'] == 'project',
            'Original prospective before checkpoint is missing; inspect the charged allowance')
    require(saved['record']['contract_sha256'] == expected_contract_sha256,
            'Original prospective choice contract changed; inspect the charged allowance')
    decision = saved['record']['decision']
    require(decision.get('outcome') == 'plan_locked'
            and isinstance(decision.get('candidate'), dict)
            and decision['candidate'].get('id') == candidate_id,
            'Original prospective choice differs from the charged request')
    return {'schema': 'rds-checkpoint-v1', 'status': 'ALREADY_SAVED', 'id': checkpoint_id,
            'kind': 'project', 'sha256': saved['sha256'], 'contract_sha256': expected_contract_sha256,
            'candidate_id': candidate_id}


def _pending_quick_choices(root, owner, requested_name=None):
    """Find only explicitly pending, not-yet-materialized prospective QUICK jobs."""
    if owner is None:
        return []
    root = Path(root).resolve()
    store = ProjectStore(owner)
    exec_root = (root / '.rds' / 'exec').resolve()
    require(exec_root.is_relative_to(root), 'Exec workspace escapes root')
    from rds_checkpoints import read_checkpoint
    from rds_campaign import QUICK_JOB_KIND
    with store._db(True) as db:
        db.execute('BEGIN')
        contract_sha256 = digest(store._contract(db))
        rows = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE' ORDER BY id").fetchall()
        pending = []
        for row in rows:
            prior = json.loads(row['body'])
            raw_job = prior.get('job_root')
            if not isinstance(raw_job, str):
                continue
            job = Path(raw_job)
            resolved_job = job.resolve()
            if os.path.normcase(str(resolved_job.parent)) != os.path.normcase(str(exec_root)):
                continue
            name = resolved_job.name
            if requested_name is not None and name != requested_name:
                continue
            if job.exists() and not _pending_partial_child(db, job):
                continue
            require(not job.is_symlink(), 'Retained QUICK workspace is a dangling symlink; inspect the original state')
            if prior.get('materialization_state') != 'PENDING':
                if requested_name is not None:
                    raise ValueError('Charged QUICK child state is unavailable; inspect retained allowance, no refund or new launch')
                continue
            marker = db.execute("SELECT 1 FROM events WHERE json_extract(body,'$.kind')=? "
                                "AND json_extract(body,'$.job_root')=? LIMIT 1", (QUICK_JOB_KIND, raw_job)).fetchone()
            require(marker is None,
                    'Charged QUICK child was previously materialized but is missing; inspect retained state, no new launch')
            require(isinstance(prior.get('request_sha256'), str)
                    and re.fullmatch('[0-9a-f]{64}', prior['request_sha256']),
                    'Pending QUICK allowance identity is invalid')
            require(prior.get('contract_sha256') == contract_sha256,
                    'Pending QUICK allowance contract changed; inspect the original request')
            checkpoint_id = _checkpoint_name('before', name)
            saved = read_checkpoint(db, checkpoint_id, root=owner)
            require(saved is not None and saved['record']['kind'] == 'project'
                    and saved['record']['contract_sha256'] == contract_sha256,
                    'Pending QUICK allowance has no original prospective checkpoint')
            decision = saved['record']['decision']
            candidate = decision.get('candidate') if isinstance(decision, dict) else None
            require(decision.get('outcome') == 'plan_locked' and isinstance(candidate, dict)
                    and isinstance(candidate.get('id'), str),
                    'Pending QUICK allowance has no unambiguous original choice')
            pending.append({'name': name, 'candidate_id': candidate['id'], 'candidate': candidate,
                            'request_sha256': prior['request_sha256'], 'job_root': raw_job,
                            'checkpoint_id': checkpoint_id})
        return pending


def _publish_quick_request(workspace, request):
    """A process interruption leaves either no identity or its complete bytes."""
    target = workspace / 'rds-exec-request.json'
    temporary = workspace / ('.request-' + uuid.uuid4().hex + '.tmp')
    with temporary.open('xb') as handle:
        handle.write(canonical(request).encode('utf-8'))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


def _pending_partial_child(db, workspace):
    """An owning allowance, without a durable publication marker, is intent only."""
    from rds_campaign import QUICK_JOB_KIND
    raw_job = str(workspace)
    marker = db.execute("SELECT 1 FROM events WHERE json_extract(body,'$.kind')=? "
                        "AND json_extract(body,'$.job_root')=? LIMIT 1", (QUICK_JOB_KIND, raw_job)).fetchone()
    if marker is not None:
        return False
    rows = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='EXTERNAL_RUN_ALLOWANCE' "
                      "AND json_extract(body,'$.job_root')=?", (raw_job,)).fetchall()
    return len(rows) == 1 and json.loads(rows[0]['body']).get('materialization_state') == 'PENDING'


def _is_unmaterialized_allowance(db, workspace, request, prior):
    """Permit the one charged request to resume only before its child marker exists."""
    path = Path(workspace)
    require(not path.is_symlink(), 'Retained QUICK child is a symlink; inspect its original state')
    require(prior.get('materialization_state') == 'PENDING'
            and prior.get('request_sha256') == digest(request),
            'Charged QUICK child state is unavailable; inspect retained allowance, no refund or new launch')
    require(prior.get('contract_sha256') is not None,
            'Charged QUICK allowance lacks its original contract binding')
    from rds_campaign import QUICK_JOB_KIND
    raw_job = str(path)
    marker = db.execute("SELECT 1 FROM events WHERE json_extract(body,'$.kind')=? "
                        "AND json_extract(body,'$.job_root')=? LIMIT 1", (QUICK_JOB_KIND, raw_job)).fetchone()
    require(marker is None,
            'Charged QUICK child was previously materialized but is missing; inspect retained state, no new launch')
    if path.exists():
        require(path.is_dir(), 'Retained QUICK child is not a directory')
        request_path = path / 'rds-exec-request.json'
        if request_path.exists() or request_path.is_symlink():
            from rds_project import load_json
            require(not request_path.is_symlink() and load_json(request_path) == request,
                    'Job identity is frozen; changed inputs need a new --name')
        child = ProjectStore(path)
        if child.path.exists() or child.path.is_symlink():
            # Never recreate a launched child, even if its parent marker is lost.
            # Read incomplete initialization without requiring a contract table.
            with child._db(True) as child_db:
                tables = {r[0] for r in child_db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                for table in ('receipts', 'exposures'):
                    if table in tables:
                        require(child_db.execute('SELECT count(*) FROM ' + table).fetchone()[0] == 0,
                                'Retained QUICK child has execution evidence; no new launch')
                if 'runs' in tables:
                    for row in child_db.execute('SELECT body FROM runs'):
                        run = json.loads(row['body'])
                        require(run.get('status') == 'RESERVED' and run.get('attempt_id') is None
                                and run.get('started_at') is None,
                                'Retained QUICK child has an attempt; no new launch')
    return True


def _prospective_completion(workspace, request, receipt, regression=None, guard_ref=None, *, parent_db=None):
    """Read the original decision and exact completion; never choose a new route."""
    context = request.get('research_context')
    if context is None or receipt is None:
        return None
    from rds_checkpoints import read_checkpoint
    workspace = Path(workspace).resolve()
    ledger = Path(context['ledger'])
    require(ledger.is_absolute(), 'Prospective completion ledger must be absolute')
    ledger = ledger.resolve()
    require(receipt.get('run_id') == workspace.name and isinstance(receipt.get('sha256'), str),
            'Prospective completion requires the original terminal receipt')
    if parent_db is None:
        with ProjectStore(ledger)._db(True) as db:
            db.execute('BEGIN')
            return _prospective_completion(workspace, request, receipt, regression, guard_ref, parent_db=db)
    filename = parent_db.execute('PRAGMA database_list').fetchone()[2]
    require(Path(filename).resolve() == ProjectStore(ledger).path,
            'Prospective completion transaction belongs to another ledger')
    before = read_checkpoint(parent_db, _checkpoint_name('before', workspace.name), root=ledger)
    require(before is not None and before['record']['kind'] == 'project', 'Original prospective before checkpoint is missing')
    original = before['record']['decision']
    require(original.get('outcome') == 'plan_locked' and original.get('candidate', {}).get('id') == context['candidate'],
            'Original prospective decision differs from the frozen request')
    decision = {**original, 'execution': {'job_root': str(workspace), 'receipt_sha256': receipt['sha256'],
                'run_status': receipt.get('run_status', 'UNKNOWN')},
                'pending_evidence': ['Assess the original output; completion alone does not reject or prove a hypothesis']}
    if request.get('guard') is not None:
        require(regression is not None and guard_ref is not None, 'Prospective guard completion is unfinished')
        decision['regression_review'] = {'status': regression['status'], 'report': guard_ref, 'scientific_support': 'UNKNOWN'}
    else:
        require(regression is None and guard_ref is None, 'Unexpected prospective guard completion')
    identity = _checkpoint_name('after', workspace.name)
    after = read_checkpoint(parent_db, identity, root=ledger)
    if after is not None:
        require(after['record']['kind'] == 'project' and after['record']['decision'] == decision
                and after['record']['contract_sha256'] == before['record']['contract_sha256'],
                'Original prospective after checkpoint conflicts with the retained completion')
    return {'ledger_root': str(ledger), 'contract_sha256': before['record']['contract_sha256'],
            'decision': decision, 'id': identity, 'after_checkpoint': after}


@mutation()
def _complete_prospective(workspace, request, receipt, regression=None, guard_ref=None):
    if receipt is None or receipt.get('sha256') is None:
        return  # A still-live background attempt has no completion to record.
    tail = _prospective_completion(workspace, request, receipt, regression, guard_ref)
    if tail is not None and tail['after_checkpoint'] is None:
        from rds_checkpoints import save_checkpoint
        save_checkpoint(tail['ledger_root'], tail['id'], ProjectStore(tail['ledger_root']).snapshot(),
                        kind='project', decision=tail['decision'],
                        _expected_contract_sha256=tail['contract_sha256'])


def _recover_prospective(store, contract, receipt):
    """Finish a frozen QUICK tail through the existing public recover boundary."""
    if receipt is None or receipt.get('sha256') is None:
        return
    entries = [entry for entry in contract['bindings']
               if entry['path'] == 'rds-exec-request.json' and entry['role'] == 'config']
    if not entries:
        return
    require(len(entries) == 1, 'Retained QUICK request binding is ambiguous')
    from rds_project import load_json
    path = store.root / entries[0]['path']
    require(path.resolve().is_relative_to(store.root) and file_sha(path) == entries[0]['sha256'],
            'Retained QUICK request differs from its original binding')
    request = load_json(path)
    if request.get('research_context') is None:
        return
    regression, ref = None, None
    if request.get('guard') is not None:
        with store._db(True) as db:
            rows = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='QUICK_EXEC_REGRESSION_REVIEW' LIMIT 2").fetchall()
        require(len(rows) == 1, 'Guard review is unfinished or ambiguous; inspect the retained job and allowance')
        ref = json.loads(rows[0]['body'])['report']
        report_path = Path(ref['path']).resolve()
        require(report_path.is_relative_to((store.root / '.rds/cas').resolve()) and file_sha(report_path) == ref['sha256'],
                'Guard report CAS integrity failure')
        from rds_guard import read
        regression = read(report_path)[0]
    _complete_prospective(store.root, request, receipt, regression, ref)


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
    with parent._db(True) as db:
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
        with parent._db(True) as parent_db:
            if not _preparation_contract(parent, parent_db):
                rows = parent_db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='TOOL_PREPARATION_STARTED' "
                                         "AND json_extract(body,'$.request.token')=? LIMIT 2", (token,)).fetchall()
                require(len(rows) == 1, 'Native preparation requires its original project intent')
                intent = json.loads(rows[0]['body'])
                require(intent['request_sha256'] == digest(intent['request']) and
                        intent['request']['job_root'] == (root / '.rds/exec/tool-check').relative_to(parent.root).as_posix(),
                        'Native preparation intent or workspace changed')
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
    execution_policy, executor_sha256 = None, None
    if owner is not None:
        parent = ProjectStore(owner)
        with parent._db(True) as db:
            parent_contract = parent._contract(db)
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

    @contextmanager
    def admission_context():
        # The original allowance path acquires parent before child. Keep that
        # order during preparation and through the child's attempt commit.
        with mutation(), ExitStack() as locks:
            try:
                from rds_campaign import binding as campaign_binding
                for scope in (root, getattr(args, 'ledger', None), _native_preparation_root):
                    if scope is not None:
                        require(campaign_binding(scope) is None, 'Bound campaign refuses detached QUICK preparation')
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
            from rds_advisor_coverage import project_context
            choice(review[0], project_context(args.ledger, review[1]), args.choose)
    def admit_quick(_db, _run):
        check_quick_parents(validate_choice=not resume_materialization)

    args.name = args.name or 'exec-' + digest(request)[:20]
    require(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', args.name), 'Job name must contain 1–64 safe identifier characters')
    workspace = root / '.rds' / 'exec' / args.name
    require(workspace.resolve().is_relative_to(root), 'Exec workspace escapes root')
    partial_child = False
    if workspace.exists() and owner is not None:
        with ProjectStore(owner)._db(True) as parent_db:
            partial_child = _pending_partial_child(parent_db, workspace)
    if workspace.exists() and not partial_child:
        from rds_project import load_json
        previous = load_json(workspace / 'rds-exec-request.json')
        require(previous == request, 'Job identity is frozen; changed inputs need a new --name')
        if execution_policy is not None:
            result = _charge_ledger(owner, workspace, request, timeout, source_root=root,
                                   executor_sha256=executor_sha256, existing_only=True)
            guard_ref = None
            if guard_path is not None:
                with ProjectStore(workspace)._db(True) as db:
                    event = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='QUICK_EXEC_REGRESSION_REVIEW' ORDER BY id DESC LIMIT 1").fetchone()
                guard_ref = json.loads(event['body'])['report'] if event is not None else None
            _complete_prospective(workspace, request, result['receipt'], result.get('regression_review'), guard_ref)
            return result
        state = ProjectStore(workspace).snapshot(check_bindings=True)
        require(not state['binding_check']['errors'], 'Frozen job bindings changed')
        receipt = next((r for r in state['receipts'] if r['run_id'] == args.name), None)
        result = {'status': 'EXISTING_JOB', 'job_root': str(workspace), 'ledger_root': str(Path(args.ledger).resolve()) if review is not None else str(workspace), 'receipt': receipt,
                  'execution_started': False, 'scientific_support': 'UNKNOWN'}
        ref = None
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
                                  _expected_contract_sha256=parent_contracts[owner], _parent_db=locked_parents[owner])
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
        store.initialize(contract)
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
                parent_db.execute('INSERT INTO events(body) VALUES (?)',
                    (canonical({'kind': QUICK_JOB_KIND, 'job_root': str(workspace),
                                'request_sha256': digest(request), 'materialization_state': 'MATERIALIZED'}),))
    if guard_path is not None:
        _charge_ledger(workspace, workspace, request, guard_seconds, dispatch=False)
    if review is not None and not resume_materialization:
        from rds_advisor_coverage import project_context
        choice(review[0], project_context(args.ledger, review[1]), args.choose)
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


def _brief_move(summary, move):
    """Retain a bounded explanation while keeping the historical next_move string."""
    if not isinstance(move, dict):
        summary['next_move'] = move
        summary.pop('next_move_detail', None)
        return
    summary['next_move'] = move.get('kind', move)
    detail = {}
    for key in ('reason', 'basis', 'source'):
        if isinstance(move.get(key), str):
            limit = 512 if key == 'reason' else 128
            detail[key] = move[key][:limit]
            if len(move[key]) > limit:
                detail[key + '_truncated'] = True
    if detail:
        summary['next_move_detail'] = detail
    else:
        summary.pop('next_move_detail', None)


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
                _brief_move(summary, selection['next_move'])
            if 'goal' in selection:
                summary['goal_input_status'] = selection['goal']['status']
        advisory_moves = {'GOAL_CONTRIBUTION_UNDECLARED': 'REVIEW_GOAL_LINK',
                          'GOAL_CONTRIBUTION_INVALID': 'REVIEW_GOAL_LINK'}
        relevant = [kind for kind in flags if kind in advisory_moves and advisory_moves[kind] == summary.get('next_move')]
        flags = relevant + [kind for kind in flags if kind not in advisory_moves]
        summary['flags'] = list(dict.fromkeys(flags))[:3]
    owned = value.get('advisor') or value
    graph_reviews = [r['search'].get('analysis_coverage') for r in owned.get('recommendations', [])
                     if 'search' in r]
    analysis = owned.get('analysis_coverage') or next((r for r in graph_reviews if r), None)
    if analysis:
        # Per-graph identities and diagnostics remain in the hashed full record.
        # Keep coverage visible without repeating hashes alongside decision detail.
        summary['analysis_coverage'] = {key: analysis[key] for key in ('status', 'full')}
        summary['analysis_coverage']['graph_count'] = len(analysis['graphs'])
        for key in ('node_count', 'edge_count'):
            counts = [graph.get(key) for graph in analysis['graphs']]
            summary['analysis_coverage'][key] = sum(counts) if all(type(n) is int for n in counts) else None
        if analysis['reasons']:
            summary['analysis_coverage']['reasons'] = analysis['reasons'][:3]
            summary['analysis_coverage']['omitted_reasons'] = max(0, len(analysis['reasons']) - 3)
    if 'steering' in owned:
        state = owned['steering']
        summary['steering'] = {key: state.get(key) for key in ('revision', 'paused', 'kind', 'instruction_id')}
        for key in ('withdrawn_runs', 'preferred_runs'):
            summary['steering'][key] = state.get(key, [])[:3]
            summary['steering']['omitted_' + key] = max(0, len(state.get(key, [])) - 3)
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
            _brief_move(summary, move)
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
