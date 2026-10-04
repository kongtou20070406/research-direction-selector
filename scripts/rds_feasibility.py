"""Receipt-backed conditional forecasts for complete owned research plans.

Reservations are hard allowances, never runtime predictions. Scaling is a
declared model whose assumptions must be reviewed; a pilot does not prove that
an arbitrary algorithm scales this way or that a scientific goal is attainable.
"""
from copy import deepcopy
import math
import platform
import time

from rds_project import IDENTITY, digest, load_json, number, require


def runtime_fingerprint():
    return digest({'system': platform.system(), 'release': platform.release(),
                   'machine': platform.machine(), 'processor': platform.processor(),
                   'python': platform.python_version()})


def validate(store, contract, policy):
    config = policy.get('feasibility')
    if config is None:
        return
    require(isinstance(config, dict) and set(config) ==
            {'schema', 'pilots', 'max_pilot_wall_seconds', 'models', 'plans'},
            'feasibility needs schema, pilots, max_pilot_wall_seconds, models and plans')
    require(type(config['schema']) is int and config['schema'] == 1, 'feasibility schema must be 1')
    routes = {r['manifest']['id']: r['manifest'] for r in policy['routes']}
    pilots = config['pilots']
    require(isinstance(pilots, list) and len(pilots) <= 16 and len(set(pilots)) == len(pilots)
            and all(p in routes for p in pilots), 'Invalid bounded pilot routes')
    cap = number(config['max_pilot_wall_seconds'], 'pilot wall cap', True)
    require(cap <= contract['budget']['wall_seconds'] / 10, 'Pilot cap must be at most 10% of total wall allowance')
    require(all(routes[p]['resource_estimates']['wall_seconds'] <= cap for p in pilots),
            'Pilot reservation exceeds its separate bounded allowance')
    models = config['models']
    require(isinstance(models, dict) and len(models) <= 64 and set(models) <= routes.keys(), 'Invalid forecast models')
    for run_id, model in models.items():
        require(isinstance(model, dict) and set(model) ==
                {'pilot_runs', 'exponent', 'safety_factor', 'max_scale', 'assumptions'}, 'Invalid scaling model')
        sources = model['pilot_runs']
        require(isinstance(sources, list) and 1 <= len(sources) <= 8 and len(set(sources)) == len(sources)
                and all(p in pilots and p != run_id for p in sources), 'Forecast needs distinct bounded pilot sources')
        require(0 <= number(model['exponent'], 'scaling exponent') <= 4, 'Scaling exponent outside 0..4')
        require(1 <= number(model['safety_factor'], 'forecast safety factor') <= 100, 'Invalid safety factor')
        require(1 <= number(model['max_scale'], 'maximum extrapolation') <= 1000000, 'Invalid extrapolation bound')
        assumptions = model['assumptions']
        require(isinstance(assumptions, list) and 1 <= len(assumptions) <= 16 and
                all(isinstance(a, str) and a.strip() and len(a) <= 1024 for a in assumptions),
                'Scaling must state its conditional assumptions')
    plans = config['plans']
    require(isinstance(plans, list) and 1 <= len(plans) <= 32, 'Need 1..32 complete plans')
    names = set()
    goals = {g['fact'] for g in policy['context']['decision']['goal_conditions']}
    observations = {o['fact']: o['run_id'] for o in policy['observations']}
    for plan in plans:
        require(isinstance(plan, dict) and set(plan) ==
                {'id', 'steps', 'goal_facts', 'recovery_wall_seconds', 'delivery_wall_seconds'}, 'Invalid completion plan')
        require(isinstance(plan['id'], str) and plan['id'] and plan['id'] not in names, 'Duplicate or invalid plan ID')
        names.add(plan['id'])
        steps = plan['steps']
        require(isinstance(steps, list) and 1 <= len(steps) <= 64 and len(set(steps)) == len(steps)
                and all(s in routes and s not in pilots for s in steps), 'Plan must name distinct non-pilot routes')
        require(isinstance(plan['goal_facts'], list) and set(plan['goal_facts']) == goals,
                'Completion plan must deliver every unchanged goal predicate')
        require(all(observations.get(g) in steps or any(g == f'run.{s}.{k}' for s in steps
                for k in ('status', 'completed', 'succeeded', 'failed', 'timed_out')) for g in goals),
                'Plan lacks an executable producer for a final obligation')
        number(plan['recovery_wall_seconds'], 'recovery overhead')
        number(plan['delivery_wall_seconds'], 'delivery overhead')
    planned = {s for p in plans for s in p['steps']}
    require(set(routes) <= planned | set(pilots), 'Every route must belong to a completion plan or bounded pilot')


def _protocol(store, manifest):
    path = store._path(manifest['protocol']['path'])
    from rds_project import file_sha
    require(file_sha(path) == manifest['protocol']['sha256'], 'Forecast protocol binding changed')
    return load_json(path)


def _estimate(store, state, run_id, manifest, model):
    unknown = {'status': 'UNKNOWN', 'run_id': run_id, 'lower_wall_seconds': 0,
               'upper_wall_seconds': None, 'reason': 'No applicable measured pilot; timeout is not a forecast'}
    if model is None:
        return unknown
    receipts = {r['run_id']: r for r in state['receipts']}
    try:
        target = _protocol(store, manifest)
        scope = target.get('runtime_model_identity')
        require(isinstance(scope, dict) and set(scope) == {'algorithm', 'precision', 'hardware'}
                and all(isinstance(v, (str, int)) and not isinstance(v, bool) for v in scope.values()),
                'Forecast needs protocol-bound algorithm, precision and hardware identity')
        require(scope['hardware'] == 'local-cpu',
                'Unsupported device/backend forecast: only local-cpu host identity is measured; accelerator forecasts remain UNKNOWN')
        units = number(target['sample_work']['units'], 'target work units', True)
        measured, provenance = [], []
        for pilot_id in model['pilot_runs']:
            receipt = receipts.get(pilot_id)
            require(receipt and receipt['run_status'] == 'SUCCEEDED' and not receipt['timeout'], 'Pilot is not successful')
            require(receipt.get('runtime_fingerprint') == runtime_fingerprint(), 'Pilot runtime/hardware identity is stale or unknown')
            require(receipt['bindings_before'] == receipt['bindings_after'] == state['contract']['bindings'],
                    'Pilot code/config/data/evaluator identity is stale')
            protocol = receipt['protocol']
            require(protocol.get('runtime_model_identity') == scope and all(protocol.get(k) == target.get(k)
                    for k in IDENTITY if k != 'sample_work'), 'Pilot algorithm/precision/protocol does not apply')
            require({k:v for k,v in protocol['sample_work'].items() if k != 'units'} ==
                    {k:v for k,v in target['sample_work'].items() if k != 'units'},
                    'Pilot work domain differs beyond the declared scale')
            scale = units / number(protocol['sample_work']['units'], 'pilot work units', True)
            require(1 <= scale <= model['max_scale'], 'Work scale lies outside the declared extrapolation domain')
            wall = number(receipt['resources']['wall_seconds']['measured'], 'measured pilot wall', True)
            value = wall * scale ** model['exponent']
            require(math.isfinite(value), 'Scaling overflow')
            measured.append(value)
            provenance.append({'run_id': pilot_id, 'receipt_sha256': receipt['sha256'],
                               'measured_wall_seconds': wall, 'work_scale': scale})
        return {'status': 'CONDITIONAL_FORECAST', 'run_id': run_id,
                'lower_wall_seconds': min(measured), 'upper_wall_seconds': max(measured) * model['safety_factor'],
                'basis': 'MEASURED_PILOT_WITH_DECLARED_SCALING_NOT_A_GUARANTEE',
                'model_sha256': digest(model), 'assumptions': deepcopy(model['assumptions']), 'sources': provenance}
    except (KeyError, TypeError, ValueError, OSError, OverflowError) as exc:
        return {**unknown, 'reason': str(exc)}


def assess(store, state, *, now=None):
    policy = state['contract'].get('advisor_policy', {})
    config = policy.get('feasibility')
    if config is None:
        return None
    now = time.time() if now is None else now
    routes = {r['manifest']['id']: r['manifest'] for r in policy['routes']}
    runs = {r['id']: r for r in state['runs']}
    receipts = {r['run_id']: r for r in state['receipts']}
    historical_configs = [h['contract'].get('advisor_policy', {}).get('feasibility')
                          for h in state.get('contract_history', [])]
    historical_configs = [c for c in historical_configs if c is not None] or [config]
    all_pilots = {p for c in historical_configs for p in c['pilots']}
    pilot_cap = min(c['max_pilot_wall_seconds'] for c in historical_configs)
    pilot_spent = sum(max(r.get('resources', {}).get('wall_seconds', {}).get('measured') or 0,
                          r.get('resources', {}).get('wall_seconds', {}).get('charged_estimate') or 0,
                          r.get('resources', {}).get('wall_seconds', {}).get('observed_lower_bound') or 0)
                      for r in state['receipts'] if r['run_id'] in all_pilots)
    pilot_reserved = sum(r['resource_estimates']['wall_seconds'] for r in state['runs']
                         if r['id'] in all_pilots and r['status'] in {'RESERVED', 'RUNNING'})
    wall = next(b for b in state['budget'] if b['resource'] == 'wall_seconds')
    unreserved = max(0, wall['cap'] - wall['spent'] - wall['charged'] - wall['reserved'])
    deadline = None
    campaign = state.get('campaign_started')
    if campaign is not None:
        deadline = campaign['started_at'] + state['contract']['stop_policy']['wall_seconds']
    plans, admitted, needed_pilots = [], set(), set()
    for plan in config['plans']:
        remaining = [s for s in plan['steps'] if receipts.get(s, {}).get('run_status') != 'SUCCEEDED']
        # Failed steps cannot be renamed or granted more timeout by this model.
        failed = [s for s in remaining if s in receipts]
        recovered_reservations = sum(runs[s]['resource_estimates']['wall_seconds'] for s in remaining
                                     if runs.get(s, {}).get('status') in {'RESERVED', 'RUNNING'})
        accounting_available = unreserved + recovered_reservations
        available = accounting_available
        if deadline is not None:
            available = min(available, max(0, deadline - now))
        estimates = [_estimate(store, state, s, routes[s], config['models'].get(s)) for s in remaining if s not in failed]
        accounting = []
        for resource in state['budget']:
            if resource['resource'] == 'wall_seconds':
                continue
            name = resource['resource']
            required = sum(routes[s]['resource_estimates'].get(name, 0) for s in remaining)
            owned = sum(runs[s]['resource_estimates'].get(name, 0) for s in remaining
                        if runs.get(s, {}).get('status') in {'RESERVED', 'RUNNING'})
            available_other = max(0, resource['cap'] - resource['spent'] - resource['charged'] - resource['reserved']) + owned
            accounting.append({'resource':name, 'required_allowance':required, 'available_allowance':available_other,
                               'basis':'DECLARED_ACCOUNTING_ALLOWANCE_NOT_RUNTIME_PREDICTION'})
        overhead = plan['recovery_wall_seconds'] + plan['delivery_wall_seconds']
        wall_requirements = []
        previous_upper = 0
        index = {e['run_id']:e for e in estimates}
        for s in remaining:
            wall_requirements.append({'run_id':s,'required_allowance':routes[s]['resource_estimates']['wall_seconds'],
                'available_before_conditional_previous_steps':max(0, accounting_available - previous_upper),
                'basis':'HARD_RESERVATION_SEPARATE_FROM_CONDITIONAL_RUNTIME'})
            previous_upper += index.get(s, {}).get('upper_wall_seconds') or 0
        lower = overhead + sum(e['lower_wall_seconds'] for e in estimates)
        upper = None if any(e['upper_wall_seconds'] is None for e in estimates) else overhead + sum(e['upper_wall_seconds'] for e in estimates)
        if failed:
            status, reason = 'REPAIR_REQUIRED', 'A preserved failed step needs a new method identity and evidence'
        elif any(r['required_allowance'] > r['available_allowance'] for r in accounting):
            status, reason = 'INFEASIBLE', 'Complete plan lacks a declared resource allowance for execution and final verification'
        elif any(r['required_allowance'] > r['available_before_conditional_previous_steps'] for r in wall_requirements):
            status, reason = 'INFEASIBLE', 'A required solve/verification step cannot obtain its hard wall reservation'
        elif any(e['upper_wall_seconds'] is not None and e['upper_wall_seconds'] > routes[e['run_id']]['timeout_seconds'] for e in estimates):
            status, reason = 'INFEASIBLE', 'Conditional completion forecast exceeds a required step hard timeout'
        elif lower > available:
            status, reason = 'INFEASIBLE', 'Even the conditional lower forecast exceeds remaining complete-plan allowance'
        elif upper is not None and upper <= available:
            status, reason = 'FEASIBLE', 'Conditional complete-plan upper forecast fits current allowance and deadline'
            if remaining:
                admitted.add(remaining[0])
        elif upper is not None:
            status, reason = 'INFEASIBLE', 'Conservative conditional forecast exceeds available allowance; do not start this plan'
        else:
            status, reason = 'UNKNOWN', 'Missing applicable forecast for at least one final execution/verification obligation'
        for e in estimates if status == 'UNKNOWN' else []:
            if e['status'] == 'UNKNOWN':
                needed_pilots.update(config['models'].get(e['run_id'], {}).get('pilot_runs', []))
        plans.append({'id': plan['id'], 'status': status, 'reason': reason, 'remaining_steps': remaining,
                      'lower_wall_seconds': lower, 'upper_wall_seconds': upper, 'available_wall_seconds': available,
                      'recovery_wall_seconds': plan['recovery_wall_seconds'], 'delivery_wall_seconds': plan['delivery_wall_seconds'],
                      'accounting_requirements':accounting, 'wall_reservation_requirements':wall_requirements, 'steps': estimates})
    pilots = []
    for p in config['pilots']:
        reservation = routes[p]['resource_estimates']['wall_seconds']
        own = reservation if runs.get(p, {}).get('status') in {'RESERVED', 'RUNNING'} else 0
        if (p in needed_pilots and p not in receipts and reservation <= unreserved + own
                and pilot_spent + pilot_reserved - own + reservation <= pilot_cap
                and (deadline is None or reservation <= max(0, deadline - now))):
            pilots.append(p)
    admitted.update(pilots)
    return {'assurance': 'CONDITIONAL_RUNTIME_MODEL_NOT_SCIENTIFIC_PROOF', 'snapshot_at': now,
            'deadline': deadline, 'plans': plans, 'admitted_runs': sorted(admitted), 'bounded_pilots': pilots,
            'pilot_budget':{'cap':pilot_cap, 'spent_or_charged':pilot_spent, 'reserved':pilot_reserved,
                            'remaining':max(0, pilot_cap - pilot_spent - pilot_reserved)},
            'runtime_identity_scope':'LOCAL_OS_CPU_PYTHON_HOST_FINGERPRINT_PLUS_FROZEN_DECLARED_PROTOCOL; accelerator/library drift requires updated bound evidence',
            'non_wall_costs': 'Hard complete-plan accounting allowances checked; physical CPU/GPU predictions remain UNKNOWN',
            'next_action': 'EXECUTE_FEASIBLE_PLAN_OR_BOUNDED_PILOT' if admitted else 'REVISE_METHOD_WITH_NEW_EVIDENCE',
            'repair_request': None if admitted else {
                'kind':'IMPROVE_TOOL',
                'goal_conditions': deepcopy(policy['context']['decision']['goal_conditions']),
                'blocked_plans': [p['id'] for p in plans if p['status'] != 'FEASIBLE'],
                'required_changes': ['measured pilot or new complexity/scaling justification',
                                     'create or improve an executable tool; inspect dominant cost and failure diagnostics',
                                     'preserve final verification and delivery obligations',
                                     'change the method, not just timeout, precision or name'],
                'command': 'python -B scripts/rds_cli.py --root ' + __import__('rds_project')._shell_argument(str(store.root)) + ' project revise --proposal <proposal.json>',
                'tool_workbench_command':'python -B scripts/rds_cli.py --root ' + __import__('rds_project')._shell_argument(str(store.root)) + ' project improve --code-path <authorized-code-path> --id <revision-id>',
                'remaining_wall_seconds': unreserved, 'authorization': 'EXISTING_METHOD_EVOLUTION_ENVELOPE_ONLY'}}


def check_start(store, db, run_id):
    from rds_owned_advisor import _state
    report = assess(store, _state(store, db))
    if report is not None:
        require(run_id in report['admitted_runs'], 'Predictive feasibility gate rejected this route before child launch; inspect project next')
    return report
