"""Explicit workflow modes, scoped root discovery and same-ledger Advisor adoption.

Discovery does not infer that arbitrary sibling directories share a research goal.
An activation adds only a policy to the existing verified contract lineage. It
never replaces the genesis contract or grants commands, resources or execution.
"""
from copy import deepcopy
import json
from pathlib import Path
import time

from rds_project import ProjectStore, canonical, digest, load_json, require, _shell_argument

ACTIVATED = 'ADVISOR_POLICY_ENABLED'
POLICY_FIELDS = {'schema', 'context', 'graph', 'routes', 'observations'}


def _command(root, suffix):
    return 'python -B scripts/rds_cli.py --root ' + _shell_argument(str(root)) + ' ' + suffix


def discover(root):
    """Inspect only the requested root and its ancestors, without creating state."""
    requested = Path(root).resolve()
    require(requested.is_dir(), 'Project root must exist')
    for candidate in (requested, *requested.parents):
        state = candidate / '.rds'
        # A native objective/CAS is also an existing research workspace, even
        # before its external-execution contract has been initialized.
        if not state.exists():
            continue
        require(state.resolve().is_relative_to(candidate), 'State directory escapes project root')
        if not any((state / name).exists() for name in ('project.sqlite3', 'state.sqlite3')):
            continue
        info = describe(candidate)
        return {'status': 'EXISTING_PROJECT', 'requested_root': str(requested),
                'project_root': str(candidate), 'relation': 'CURRENT' if candidate == requested else 'ANCESTOR',
                'workflow': info, 'next_action': info['next_action'], 'execution_started': False,
                'search_scope': 'REQUESTED_ROOT_AND_ANCESTORS', 'sibling_projects_searched': False}
    return {'status': 'NO_PROJECT_FOUND', 'requested_root': str(requested), 'project_root': None,
            'workflow': describe(requested), 'next_action': _command(requested, 'project plan'),
            'execution_started': False, 'search_scope': 'REQUESTED_ROOT_AND_ANCESTORS',
            'sibling_projects_searched': False}


def describe(root, *, quick=False):
    root = Path(root).resolve()
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
    return info


def check_root(root, *, separate_reason=None, supersedes=None):
    if separate_reason is not None:
        require(isinstance(separate_reason, str) and 1 <= len(separate_reason.strip()) <= 2048,
                '--separate-project needs a nonempty reason of at most 2048 characters')
    found = discover(root)
    if found.get('relation') == 'ANCESTOR' and separate_reason is None and supersedes is None:
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


def enable_advisor(store, policy, *, apply=False, expected_snapshot=None):
    """Preview, then atomically append the exact reviewed transition; no dispatch."""
    from rds_method_revision import pending_revision, contract_history
    from rds_owned_advisor import validate_policy
    policy = deepcopy(policy)
    require(isinstance(policy, dict) and set(policy) == POLICY_FIELDS,
            'Activation accepts schema/context/graph/routes/observations only')
    policy_sha = digest(policy)
    # A read-only preview and a single write transaction share the same checks.
    with store._db(readonly=not apply) as db:
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
