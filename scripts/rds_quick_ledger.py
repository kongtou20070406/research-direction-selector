"""Existing QUICK allowances and recovery; no route selection or CLI import."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import re
import sys
import uuid

from rds_project import ProjectStore, execution_route, file_sha
from rds_project_values import canonical, digest, number, require
from rds_mutation import mutation
from rds_project_hooks import register as register_project_hooks


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
    from rds_workspace import check_admission
    check_admission(root)
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
                check_admission(child.root)
                require(child.path.is_file(), 'Execution policy: charged child state is unavailable; inspect retained allowance, no refund or new launch')
                from rds_project import load_json
                stored = load_json(child.root / 'rds-exec-request.json')
                require(digest(stored) == prior['request_sha256'] and stored.get('execution_policy_owner') == request['execution_policy_owner'],
                        'Execution policy: retained child request binding differs')
                with child.readonly() as child_db:
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
        ProjectStore.record_event(db, event)



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
    with store.readonly() as db:
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
            with child.readonly() as child_db:
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
        with ProjectStore(ledger).readonly() as db:
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
        with store.readonly() as db:
            rows = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')='QUICK_EXEC_REGRESSION_REVIEW' LIMIT 2").fetchall()
        require(len(rows) == 1, 'Guard review is unfinished or ambiguous; inspect the retained job and allowance')
        ref = json.loads(rows[0]['body'])['report']
        report_path = Path(ref['path']).resolve()
        require(report_path.is_relative_to((store.root / '.rds/cas').resolve()) and file_sha(report_path) == ref['sha256'],
                'Guard report CAS integrity failure')
        from rds_guard import read
        regression = read(report_path)[0]
    _complete_prospective(store.root, request, receipt, regression, ref)



register_project_hooks("quick", sys.modules[__name__])
