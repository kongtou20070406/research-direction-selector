"""Explicit workflow modes, scoped root discovery and same-ledger Advisor adoption.

Discovery does not infer that arbitrary sibling directories share a research goal.
An activation adds only a policy to the existing verified contract lineage. It
never replaces the genesis contract or grants commands, resources or execution.
"""
from copy import deepcopy
from collections import deque
from contextlib import ExitStack
import json
import os
from pathlib import Path
import re
import sqlite3
import time

from rds_project import ProjectStore, canonical, digest, load_json, require, _shell_argument

ACTIVATED = 'ADVISOR_POLICY_ENABLED'
POLICY_FIELDS = {'schema', 'context', 'graph', 'routes', 'observations'}
MAX_RETAINED_JOBS = 128
MAX_RETAINED_EVENTS = 512
MAX_RETAINED_RUNS = 4096
MAX_RETAINED_ENTRIES = 512


def _command(root, suffix):
    return 'python -B scripts/rds_cli.py --root ' + _shell_argument(str(root)) + ' ' + suffix


def discover(root):
    """Prefer the nearest project contract; otherwise retain the nearest native scope."""
    requested = Path(root).resolve()
    require(requested.is_dir(), 'Project root must exist')
    from rds_campaign import binding
    bound = binding(requested)
    if bound is not None:
        info = describe(bound['project_root'])
        return {'status': 'EXISTING_PROJECT', 'requested_root': str(requested),
                'project_root': bound['project_root'], 'relation': 'WORKSPACE_BINDING',
                'workflow': info, 'next_action': info['next_action'], 'execution_started': False,
                'search_scope': 'BOUND_CAMPAIGN', 'sibling_projects_searched': False}
    native_scope = None
    for candidate in (requested, *requested.parents):
        state = candidate / '.rds'
        # A native objective/CAS is also an existing research workspace, even
        # before its external-execution contract has been initialized.
        if not state.exists():
            continue
        require(state.resolve().is_relative_to(candidate), 'State directory escapes project root')
        if not any((state / name).exists() for name in ('project.sqlite3', 'state.sqlite3')):
            continue
        reference = state / 'state.sqlite3'
        if reference.exists():
            require(reference.is_file() and reference.resolve().is_relative_to(candidate),
                    'Reference ledger escapes project root')
            # A native reference ledger is a legitimate local scope, but not
            # an external-execution project contract. Verify it before looking
            # farther up; damaged data must not disappear behind an ancestor.
            from rds_cli import RDSState
            with RDSState(candidate).snapshot() as (reference_db, reference_state):
                if reference_state:
                    RDSState.invariants(reference_state)
        if (state / 'project.sqlite3').exists() and not (state / 'state.sqlite3').exists():
            # QUICK may create an empty same-DB lock anchor for admission. It
            # has no research records; malformed/nonempty databases still fail
            # or participate in discovery instead of being silently ignored.
            with ProjectStore(candidate)._db(True) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1").fetchone() is None:
                    continue
        info = describe(candidate)
        found = {'status': 'EXISTING_PROJECT', 'requested_root': str(requested),
                'project_root': str(candidate), 'relation': 'CURRENT' if candidate == requested else 'ANCESTOR',
                'workflow': info, 'next_action': info['next_action'], 'execution_started': False,
                'search_scope': 'REQUESTED_ROOT_AND_ANCESTORS', 'sibling_projects_searched': False}
        if info['mode'] != 'UNINITIALIZED':
            return found
        if native_scope is None:
            native_scope = found
    if native_scope is not None:
        return native_scope
    return {'status': 'NO_PROJECT_FOUND', 'requested_root': str(requested), 'project_root': None,
            'workflow': describe(requested), 'next_action': _command(requested, 'project plan'),
            'execution_started': False, 'search_scope': 'REQUESTED_ROOT_AND_ANCESTORS',
            'sibling_projects_searched': False}


def describe(root, *, quick=False):
    root = Path(root).resolve()
    from rds_campaign import binding
    bound = binding(root)
    store = ProjectStore(root)
    contract = None
    if store.path.is_file():
        with store._db(True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone() \
                    and db.execute('SELECT 1 FROM contract WHERE id=1').fetchone():
                contract = store._contract(db)
    owned = contract is not None and 'advisor_policy' in contract
    mode = 'QUICK' if quick or contract is not None and not owned else 'FULL' if owned else 'UNINITIALIZED'
    info = {'mode': mode, 'advisor': 'PROGRAM_OWNED' if owned and not quick else 'NOT_PROGRAM_OWNED',
            'project_root': str(root), 'ledger': str(store.path) if store.path.is_file() else None,
            'default_new_project_mode': 'FULL', 'full_means': 'PROGRAM_OWNED_RESEARCH_WORKFLOW',
            'optional_capabilities_are_not_automatically_enabled': True}
    if owned and not quick:
        policy = contract['advisor_policy']
        capabilities = ['project next --brief', 'project advance --brief', 'advise --brief']
        if 'autonomy' in policy:
            capabilities.append('project drive --max-steps 1')
        info['next_action'] = _command(root, 'project next --brief')
    elif contract is not None:
        capabilities = ['project next --brief', 'project create --manifest <run.json>',
                        'project enable-advisor --policy <policy.json>']
        info['next_action'] = _command(root, 'project next --brief')
        info['upgrade'] = _command(root, 'project enable-advisor --policy <policy.json>')
    else:
        capabilities = ['project plan', 'project init --recipe <recipe.json>', 'exec -- <command...>']
        info['next_action'] = _command(root, 'project plan')
    info['capabilities'] = capabilities + ['hypergraph --help', 'rsi list', 'checkpoint save --help']
    info['continuity'] = ({'status': 'BOUND', 'project_root': bound['project_root'],
                           'workspace_root': bound['workspace_root'], 'binding_path': bound['binding_path'],
                           'binding_id': bound['binding_id']}
                          if bound is not None else {'status': 'UNBOUND'})
    if contract is not None and bound is None:
        info['capabilities'].append('project bind-workspace --workspace-root <research-workspace>')
    return info


def check_root(root, *, separate_reason=None, supersedes=None):
    from rds_campaign import enforce
    enforce(root)
    if separate_reason is not None:
        require(isinstance(separate_reason, str) and 1 <= len(separate_reason.strip()) <= 2048,
                '--separate-project needs a nonempty reason of at most 2048 characters')
    found = discover(root)
    inherited = supersedes is not None and Path(supersedes).resolve() == Path(found['project_root']) \
        if found.get('relation') == 'ANCESTOR' else False
    if found.get('relation') == 'ANCESTOR' and separate_reason is None and not inherited:
        raise ValueError('Existing research project at ' + found['project_root'] +
                         '; add the experiment as a run there. Use: ' + found['next_action'] +
                         '; a deliberately independent project needs --separate-project <reason>')
    return found


def initialize(store, contract, *, mode=None, supersedes=None, separate_reason=None):
    """Public entry: FULL by default for new contracts, unchanged legacy retries."""
    check_root(store.root, separate_reason=separate_reason, supersedes=supersedes)
    existing = describe(store.root)['mode']
    selected = mode or (existing.lower() if existing != 'UNINITIALIZED' else 'full')
    if mode is None and store.path.is_file():
        with store._db(True) as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='contract'").fetchone():
                genesis = db.execute('SELECT sha256 FROM contract WHERE id=1').fetchone()
                if genesis and genesis['sha256'] == digest(contract):
                    selected = 'full' if 'advisor_policy' in contract else 'quick'
    require(selected in {'full', 'quick'}, 'Unknown project mode')
    require(('advisor_policy' in contract) == (selected == 'full'),
            'FULL requires an explicit valid advisor_policy or --recipe; QUICK requires no advisor_policy. '
            'Use --mode quick only for an intentionally limited workflow; do not invent missing research declarations')
    declaration = None
    if separate_reason is not None:
        ancestor = discover(store.root.parent) if store.root.parent != store.root else {}
        declaration = {'kind': 'PROJECT_SCOPE_DECLARED', 'reason': separate_reason,
                       'ancestor_root': ancestor.get('project_root'), 'contract_sha256': digest(contract)}
    return store.initialize(contract, supersedes=supersedes, scope_declaration=declaration)


def activation_transition(old, new):
    """The only permitted activation delta; also checked while reading lineage."""
    require('advisor_policy' not in old and set(new) == set(old) | {'advisor_policy'},
            'Advisor activation must add exactly one policy to a non-owned contract')
    require(all(new[key] == value for key, value in old.items()),
            'Advisor activation cannot change the original contract or authority')
    require(isinstance(new['advisor_policy'], dict) and set(new['advisor_policy']) == POLICY_FIELDS,
            'Activation accepts basic owned policy only; no new autonomy, confirmation, tools or execution authority')


def _state(store, db):
    contract = store._contract(db)
    runs = store._runs(db)
    receipts = [store._receipt(r) for r in db.execute('SELECT run_id,sha256,body FROM receipts ORDER BY run_id')]
    from rds_owned_advisor import MAX_RUNS
    require(len(runs) <= MAX_RUNS and len(receipts) <= MAX_RUNS, 'Existing work exceeds owned evidence coverage bounds')
    from rds_owned_history import history_cut
    history_cut(store, db)
    budget = [dict(r) for r in db.execute('SELECT * FROM budget ORDER BY resource')]
    last = db.execute('SELECT id,body FROM events ORDER BY id DESC LIMIT 1').fetchone()
    checkpoints = []
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='checkpoints'").fetchone():
        checkpoints = [dict(r) for r in db.execute('SELECT * FROM checkpoints ORDER BY id')]
    state = {'contract_sha256': digest(contract), 'runs': runs, 'receipts': receipts, 'budget': budget,
             'last_event': dict(last) if last else None, 'checkpoints': checkpoints}
    return contract, state, digest(state)


def _activation_children(store, db, locks, *, apply=False):
    """Verify retained QUICK jobs in the parent admission transaction.

    The parent lock serializes supported QUICK attempt admission and adoption.
    Applying also holds bounded child writer locks until adoption commits, so
    another child's supported native writer cannot change a checked snapshot.
    Child snapshots remain open until the adoption transaction ends. No child
    state, cost, receipt or contract is rewritten by this read-only inventory.
    """
    from rds_source_documents import strict_json
    from rds_project import TERMINAL
    pending, visited, snapshots = deque([(store.root, db)]), set(), []
    counts = {'events': 0, 'runs': 0, 'entries': 0}
    while pending:
        root, child_db = pending.popleft()
        root = Path(root).resolve()
        if root in visited:
            continue
        visited.add(root)
        require(len(visited) <= MAX_RETAINED_JOBS + 1,
                'Retained child inventory exceeds the job bound; activation is incomplete')
        if child_db is None:
            path = root / '.rds/project.sqlite3'
            require(root.is_dir() and path.is_file() and path.resolve().is_relative_to(root),
                    'Retained child ledger is missing or escapes its original root')
            try:
                child_db = sqlite3.connect(path.as_uri() + ('?mode=rw' if apply else '?mode=ro'),
                                           uri=True, timeout=2)
                locks.callback(child_db.close)
                child_db.row_factory = sqlite3.Row
                child_db.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
                # Acquire the writer reservation first; query_only rejects
                # BEGIN IMMEDIATE itself when enabled before that reservation.
                child_db.execute('PRAGMA query_only=ON')
            except sqlite3.Error as exc:
                raise ValueError('Retained child lock is unavailable; activation inventory is incomplete: '
                                 + str(root)) from exc
            contract = ProjectStore._contract(child_db)
            # An initialized but empty child is still an unfinished preparation,
            # not proof that an allowance's command completed.
            amount = child_db.execute('SELECT count(*) FROM runs').fetchone()[0]
            counts['runs'] += amount
            require(0 < amount and counts['runs'] <= MAX_RETAINED_RUNS,
                    'Retained child preparation is empty or exceeds the run bound')
            runs = ProjectStore._runs(child_db)
            require(all(run['status'] in TERMINAL for run in runs),
                    'Retained child has reserved/running work; finish it before Advisor activation')
            receipts = []
            for run in runs:
                row = child_db.execute('SELECT run_id,sha256,body FROM receipts WHERE run_id=?',
                                       (run['id'],)).fetchone()
                receipt = ProjectStore._receipt(row) if row is not None else None
                require(receipt is not None and receipt.get('attempt_id') == run['attempt_id']
                        and receipt.get('manifest_sha256') == run['manifest_sha256']
                        and receipt.get('process_status') == run['status'],
                        'Retained child has no matching original terminal receipt')
                receipts.append(receipt)
            budget = [dict(row) for row in child_db.execute('SELECT * FROM budget ORDER BY resource')]
            require(all(row['reserved'] == 0 for row in budget), 'Retained child has outstanding reservations')
            last = child_db.execute('SELECT id,body FROM events ORDER BY id DESC LIMIT 1').fetchone()
            snapshots.append({'root': str(root), 'contract_sha256': digest(contract), 'runs': runs,
                              'receipts': receipts, 'budget': budget, 'last_event': dict(last) if last else None})
        rows = child_db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind') IN "
                                "('EXTERNAL_RUN_ALLOWANCE','QUICK_JOB_ADMITTED') ORDER BY id LIMIT ?",
                                (MAX_RETAINED_EVENTS - counts['events'] + 1,)).fetchall()
        counts['events'] += len(rows)
        require(counts['events'] <= MAX_RETAINED_EVENTS,
                'Retained child event bound exceeded; activation inventory is incomplete')
        for row in rows:
            require(isinstance(row['body'], str) and len(row['body'].encode('utf-8')) <= 16384,
                    'Retained child event exceeds its bounded encoding')
            event = strict_json(row['body'])
            request = event.get('request', {}) if isinstance(event, dict) else None
            require(isinstance(request, dict), 'Retained child event has an invalid request')
            job = event.get('job_root') or request.get('job_root')
            require(isinstance(job, str) and 0 < len(job) <= 4096, 'Retained child pointer is missing')
            target = (root / job).resolve()
            token = target.name
            if (event['kind'] == 'EXTERNAL_RUN_ALLOWANCE' and re.fullmatch('[0-9a-f]{32}', token)
                    and target.parent.name == 'tool-checks' and target.parent.parent.name == 'rsi'
                    and target.parent.parent.parent.name == '.rds'
                    and event.get('request_sha256') == digest({'tool_validation': token})):
                target = target / '.rds/exec/tool-check'
            pending.append((target, None))
        directory = root / '.rds/exec'
        if directory.exists() or directory.is_symlink():
            require(directory.is_dir() and directory.resolve().is_relative_to(root),
                    'Retained QUICK directory escapes its original project')
            with os.scandir(directory) as entries:
                for entry in entries:
                    counts['entries'] += 1
                    require(counts['entries'] <= MAX_RETAINED_ENTRIES,
                            'Retained QUICK entry bound exceeded; activation inventory is incomplete')
                    target = Path(entry.path).resolve()
                    require(entry.is_dir() and target.is_relative_to(root),
                            'Retained QUICK job is unavailable or escapes its original project')
                    pending.append((target, None))
    return digest(sorted(snapshots, key=lambda value: value['root']))


def enable_advisor(store, policy, *, apply=False, expected_snapshot=None):
    """Preview, then atomically append the exact reviewed transition; no dispatch."""
    from rds_method_revision import pending_revision, contract_history
    from rds_owned_advisor import validate_policy
    policy = deepcopy(policy)
    require(isinstance(policy, dict) and set(policy) == POLICY_FIELDS,
            'Activation accepts schema/context/graph/routes/observations only')
    policy_sha = digest(policy)
    # A read-only preview and a single write transaction share the same checks.
    with ExitStack() as locks, store._db(readonly=not apply) as db:
        db.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
        old, state, state_sha = _state(store, db)
        # Bind the proposed policy as well as live state: a preview of policy A
        # cannot authorize applying policy B to an otherwise unchanged ledger.
        snapshot_sha = digest({'state_sha256': state_sha, 'policy_sha256': policy_sha})
        prior = db.execute("SELECT body FROM events WHERE json_extract(body,'$.kind')=? LIMIT 1", (ACTIVATED,)).fetchone()
        if prior:
            event = json.loads(prior['body'])
            contract_history(db)  # Verify the transition before accepting a retry.
            require(event['policy_sha256'] == policy_sha, 'Advisor policy is already enabled and frozen')
            if apply:
                require(expected_snapshot == event['snapshot_sha256'], 'Retry must bind the original reviewed snapshot')
            return {'status': 'ALREADY_ENABLED', 'activation': event, 'execution_started': False,
                    'workflow': describe(store.root)}
        require('advisor_policy' not in old, 'Advisor is already enabled; use the existing project')
        require(pending_revision(db) is None, 'Finish the existing prepared method revision first')
        require(not any(r['status'] in {'RESERVED', 'RUNNING'} for r in state['runs']) and
                all(r['reserved'] == 0 for r in state['budget']),
                'Advisor activation requires no reserved/running work or outstanding resource reservations')
        retained_sha = _activation_children(store, db, locks, apply=apply)
        snapshot_sha = digest({'state_sha256': state_sha, 'policy_sha256': policy_sha,
                               'retained_children_sha256': retained_sha})
        new = {**deepcopy(old), 'advisor_policy': policy}
        activation_transition(old, new)
        validate_policy(store, new)
        _, errors = store._bindings(old)
        require(not errors, '; '.join(errors))
        routes = {r['manifest']['id']: r['manifest'] for r in policy['routes']}
        for run in state['runs']:
            require(run['id'] not in routes or routes[run['id']] == run['manifest'],
                    'Policy changes an existing run manifest: ' + run['id'])
        for route in routes.values():
            protocol = load_json(store._path(route['protocol']['path']))
            error = store._protocol_error(old, protocol)
            require(not error, 'Frozen route protocol cannot register: ' + str(error))
        claims = store._output_claims(db)
        for rid, route in routes.items():
            for output in route['outpaths']:
                key = store._output_key(store._path(output, True, old))
                require(key not in claims or claims[key] == {rid}, 'Policy output belongs to another existing run')
        proposal = {'status': 'ADVISOR_ACTIVATION_PREVIEW', 'parent_contract_sha256': digest(old),
                    'contract_sha256': digest(new), 'policy_sha256': policy_sha,
                    'snapshot_sha256': snapshot_sha, 'existing_runs': len(state['runs']),
                    'existing_receipts': len(state['receipts']), 'budget': state['budget'],
                    'observations': deepcopy(policy['observations']), 'evidence_validation': 'ON_NEXT_OWNED_REVIEW',
                    'execution_started': False, 'authorization': 'UNCHANGED',
                    'next_action': _command(store.root, 'project enable-advisor --policy <same-policy.json> --apply '
                                            '--expected-snapshot ' + snapshot_sha)}
        if not apply:
            return proposal
        require(expected_snapshot == snapshot_sha, 'Project changed since preview; inspect a fresh activation preview')
        event = {'kind': ACTIVATED, 'schema': 1, 'genesis_sha256': contract_history(db)[0]['sha256'],
                 'parent_sha256': digest(old), 'contract': new, 'contract_sha256': digest(new),
                 'policy_sha256': policy_sha, 'snapshot_sha256': snapshot_sha, 'created_ns': time.time_ns()}
        event['sha256'] = digest(event)
        db.execute('INSERT INTO events(body) VALUES (?)', (canonical(event),))
    return {**proposal, 'status': 'ADVISOR_ENABLED', 'activation': event,
            'next_action': _command(store.root, 'project next --brief')}
