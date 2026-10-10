"""Rolling, evidence-bound shadow plans over the existing owned review.

This module projects current declarations and checked observations. It has no
database, provider, admission gate or executor. Its priority is an explicit
heuristic, never a calibrated research utility or a scientific acceptance test.
"""
from collections import Counter, deque
from copy import deepcopy

from rds_project import canonical, digest, require
from rds_advisor_search import evaluate_condition

ITEM_LIMIT = 8
ROUTE_LIMIT = 4
DETAIL_BYTES = 4096
CONE_NODES = 32
CONE_EDGES = 64


def _section(rows, locator, limit=ITEM_LIMIT):
    """Keep exact predicates or an explicit original locator, never text slices."""
    items = []
    for row in rows[:limit]:
        if len(canonical(row).encode('utf-8')) <= DETAIL_BYTES:
            items.append(deepcopy(row))
        else:
            items.append({**{k: row[k] for k in ('id', 'run_id', 'status', 'fact', 'kind') if k in row},
                          'details_omitted': True, 'sha256': digest(row),
                          'source': deepcopy(row.get('source', {'locator': locator,
                                      'id': row.get('id', row.get('run_id', row.get('fact')))}))})
    return {'items': items, 'total': len(rows), 'omitted': max(0, len(rows) - limit),
            'source': {'locator': locator}, 'sha256': digest(rows)}


def _cone(spec, roots):
    """Traverse only incoming AND/OR dependencies; retain omitted-scope counts."""
    nodes = {n['id']: n for n in spec.get('nodes', [])}
    edges = spec.get('hyperedges', [])
    incoming = {}
    for edge in edges:
        incoming.setdefault(edge['conclusion'], []).append(edge)
    queue = deque(roots)
    seen, included, links = set(), [], {}
    truncated = False
    while queue:
        ident = queue.popleft()
        if ident in seen:
            continue
        seen.add(ident)
        if ident not in nodes:
            continue
        if len(included) == CONE_NODES:
            truncated = True
            break
        node = nodes[ident]
        included.append({**{k: deepcopy(node[k]) for k in ('id', 'status', 'kind') if k in node},
                         'source': {'locator': 'context.dependency_map.nodes', 'id': ident,
                                    'evidence_source': deepcopy(node.get('source'))}})
        for edge in incoming.get(ident, []):
            if edge['id'] not in links:
                if len(links) == CONE_EDGES:
                    truncated = True
                    continue
                links[edge['id']] = {**{k: deepcopy(edge[k]) for k in
                                     ('id', 'premises', 'conclusion', 'status') if k in edge},
                                     'source': {'locator': 'context.dependency_map.hyperedges', 'id': edge['id']}}
                queue.extend(edge['premises'])
    return {'scope': 'INCOMING_GOAL_DEPENDENCIES_ONLY', 'roots': roots,
            'input_nodes': len(nodes), 'input_hyperedges': len(edges),
            'included_nodes': len(included), 'included_hyperedges': len(links),
            'truncated': truncated, 'complete_global_search': False,
            'nodes': _section(included, 'context.dependency_map.nodes', CONE_NODES),
            'hyperedges': _section(list(links.values()), 'context.dependency_map.hyperedges', CONE_EDGES)}


def build(state, report, *, admitted_runs=(), goal=None):
    """Derive a new proposal from this evidence cut; never adopt a previous plan."""
    policy = state['contract'].get('advisor_policy')
    result = {'schema': 'rds-shadow-plan-v1', 'status': 'UNAVAILABLE',
              'mode': 'SHADOW', 'authorization': 'UNCHANGED', 'execution_authorized': False,
              'execution_started': False, 'scientific_support': 'UNKNOWN',
              'research_policy_gain_measured': False,
              'selection_applied': False,
              'admission_scope': 'ELIGIBLE_FILTERED_ALTERNATIVES_NOT_SELECTED_DISPATCH_AUTHORITY',
              'limits': {'items': ITEM_LIMIT, 'routes': ROUTE_LIMIT, 'detail_bytes': DETAIL_BYTES,
                         'cone_nodes': CONE_NODES, 'cone_hyperedges': CONE_EDGES},
              'expand': 'project next and project status; compare source identities before using omitted originals'}
    if not policy or report.get('status') != 'REVIEWED':
        return {**result, 'reason': 'A coherent program-owned evidence review is required',
                'owned_status': report.get('status'), 'current_advisor_run': report.get('selected_run'),
                'source': {'contract_sha256': digest(state['contract']),
                           'fingerprint': report.get('fingerprint'),
                           'snapshot_sha256': report.get('snapshot_sha256')},
                'admitted_runs': [], 'next_small_check': None,
                'current_next_move': _section([report['next_move']], 'owned review.next_move')['items'][0]
                    if report.get('next_move') else None,
                'diagnostics': {
                    'coverage_errors': _section(report.get('coverage', {}).get('errors', []), 'owned review.coverage.errors'),
                    'coverage_gaps': _section(report.get('coverage', {}).get('gaps', []), 'owned review.coverage.gaps'),
                    'warnings': _section(report.get('warnings', []), 'owned review.warnings')}}
    context = report['context']
    conditions = context['decision']['goal_conditions']
    require(goal is None or any(c['fact'] == goal for c in conditions), 'Shadow goal must name a frozen goal fact')
    facts = context['facts']
    searches = [r['search'] for r in report['recommendations'] if r.get('type') == 'EXECUTABLE_DIRECTION_SEARCH']
    candidates = {c['action']['id']: c for s in searches
                  for c in s.get('candidates', []) + s.get('blocked_candidates', []) if c.get('action', {}).get('id')}
    selection = searches[0].get('selection_review', {}) if searches else {}
    contributions = {c['id']: c.get('goal_contribution') for c in selection.get('candidates', [])}
    runs = {r['id']: r for r in state['runs']}
    receipts = {r['run_id']: r for r in state['receipts']}
    routes = {r['manifest']['id']: r for r in policy['routes']}
    actions = {n.get('executable', {}).get('action', {}).get('id'): n.get('executable', {})
               for n in policy['graph']['nodes']}
    producers = {}
    for observation in policy['observations']:
        producers.setdefault(observation['fact'], []).append(observation['run_id'])
    milestones = []
    for index, condition in enumerate(conditions):
        if goal is not None and condition['fact'] != goal:
            continue
        evaluation = evaluate_condition(condition, facts)
        fid = condition['fact']
        producer_ids = producers.get(fid, [])
        # Lifecycle predicates are operational declarations, not scientific goals.
        if fid.startswith('run.'):
            producer_ids = [rid for rid in routes if fid.startswith('run.' + rid + '.')]
        milestones.append({'id': 'goal:' + str(index), 'fact': fid, 'condition': deepcopy(condition),
            'status': evaluation['truth'], 'evaluation': evaluation,
            'producers': [{'run_id': rid, 'process_status': receipts.get(rid, {}).get('run_status',
                            runs.get(rid, {}).get('status', 'NOT_STARTED'))} for rid in producer_ids],
            'source': {'locator': f'advisor_policy.context.decision.goal_conditions[{index}]'},
            'closure': 'Only the current goal predicate comparison closes this milestone; a producer receipt alone does not'})
    open_facts = {m['fact'] for m in milestones if m['status'] != 'TRUE'}
    admitted = set(admitted_runs)
    declared = policy.get('feasibility', {}).get('plans', [])
    forecasts = {p['id']: p for p in report.get('feasibility', {}).get('plans', [])}
    plan_links = {}
    for plan in declared:
        remaining = forecasts.get(plan['id'], {}).get('remaining_steps', plan['steps'])
        if remaining:
            plan_links.setdefault(remaining[0], []).append({'plan_id': plan['id'],
                'goal_facts': plan['goal_facts'], 'basis': 'DECLARED_COMPLETE_PLAN_NOT_VERIFIED_COMPLETION'})
        for step in remaining:
            for pilot in policy.get('feasibility', {}).get('models', {}).get(step, {}).get('pilot_runs', []):
                if pilot in report.get('feasibility', {}).get('bounded_pilots', []):
                    plan_links.setdefault(pilot, []).append({'plan_id': plan['id'], 'forecast_run': step,
                        'goal_facts': plan['goal_facts'], 'basis': 'BOUNDED_PILOT_TO_RESOLVE_CONDITIONAL_PLAN_FORECAST'})
    route_views = []
    for index, route in enumerate(policy['routes']):
        manifest, action_id = route['manifest'], route['candidate']
        rid = manifest['id']
        config = actions.get(action_id, {})
        candidate = candidates.get(action_id, {})
        contribution = contributions.get(candidate.get('id'))
        direct = {m['fact'] for m in milestones if any(p['run_id'] == rid for p in m['producers'])}
        linked = set(direct)
        if contribution and contribution.get('status') == 'DECLARED_PATH':
            linked.add(contribution['target'])
        planning_links = plan_links.get(rid, [])
        for link in planning_links:
            linked.update(link['goal_facts'])
        process = receipts.get(rid, {}).get('run_status', runs.get(rid, {}).get('status', 'NOT_STARTED'))
        prerequisites = [evaluate_condition(c, facts) for c in config.get('preconditions', [])]
        disc = candidate.get('discrimination') or {}
        route_views.append({'id': rid, 'candidate': action_id, 'scope': 'LOCAL_ROUTE_NOT_COMPLETE_STRATEGY',
            'process_status': process, 'candidate_status': candidate.get('status', 'NOT_IN_SEARCH'),
            'admitted_now': rid in admitted, 'declaration_order': index,
            'goal_links': sorted(linked), 'open_goal_links': sorted(linked & open_facts),
            'planning_links': deepcopy(planning_links),
            'remaining_goal_bridge': sorted(open_facts - linked),
            'goal_contribution': deepcopy(contribution), 'unknown_prerequisites': [p for p in prerequisites if p['truth'] == 'UNKNOWN'],
            'false_prerequisites': [p for p in prerequisites if p['truth'] == 'FALSE'],
            'conditional_distinguishing_pairs': len(disc.get('conditional_distinguishing_pairs', []))
                if disc.get('valid_prediction_support') else 0,
            'discriminator': deepcopy(config.get('action', {})),
            'resource_allowance': deepcopy(manifest['resource_estimates']),
            'cost_basis': 'HARD_ALLOWANCE_NOT_EXPECTED_COMPLETION_COST',
            'source': {'locator': f'advisor_policy.routes[{index}]', 'manifest_sha256': digest(manifest)}})
    by_id = {r['id']: r for r in route_views}
    alternatives = []
    for index, plan in enumerate(declared):
        forecast = forecasts.get(plan['id'], {})
        remaining = forecast.get('remaining_steps', plan['steps'])
        alternatives.append({'id': plan['id'], 'scope': 'DECLARED_COMPLETE_EXECUTION_PLAN_NOT_SCIENTIFIC_PROOF',
            'status': forecast.get('status', 'UNKNOWN'), 'goal_facts': deepcopy(plan['goal_facts']),
            'first_remaining_run': remaining[0] if remaining else None,
            'first_step': deepcopy(by_id.get(remaining[0])) if remaining else None,
            'milestones': _section([{'run_id': rid, 'process_status': by_id[rid]['process_status'],
                                    'goal_links': by_id[rid]['goal_links']} for rid in plan['steps']],
                                   f'advisor_policy.feasibility.plans[{index}].steps'),
            'forecast': deepcopy(forecast),
            'source': {'locator': f'advisor_policy.feasibility.plans[{index}]', 'sha256': digest(plan)}})
    admitted_views = [r for r in route_views if r['admitted_now']]
    # This deliberate global heuristic may disagree with declaration order.
    # Unknown links do not receive coverage credit; reservations remain separate
    # from runtime forecasts. No part of this ranking feeds execution admission.
    ranked = sorted(admitted_views, key=lambda r: (-len(r['open_goal_links']),
                    -r['conditional_distinguishing_pairs'], r['resource_allowance'].get('wall_seconds', 0),
                    r['declaration_order']))
    selected = report.get('selected_run')
    steering = state.get('steering') or {}
    active = [r['id'] for r in state['runs'] if r['status'] in {'RESERVED', 'RUNNING'}]
    precedence = ('CURRENT_USER_INSTRUCTION' if steering.get('paused') or steering.get('preferred_runs')
                  else 'ACTIVE_ORIGINAL_WORK' if active else None)
    suggestion = by_id.get(selected) if precedence else next((r for r in ranked if r['open_goal_links']), None)
    if not open_facts:
        suggestion = None
    flags = [f for s in searches for f in s.get('selection_review', {}).get('flags', [])
             + s.get('loop_review', {}).get('flags', [])]
    requests = []
    def request(kind, reason, discriminator, source):
        requests.append({'kind': kind, 'reason': reason, 'next_small_check': discriminator,
                         'source': {'locator': source}, 'authorization': 'UNCHANGED'})
    if open_facts:
        if not declared:
            request('DESIGN_COMPLETE_ROUTE', 'Only local routes are declared; the final AND goal needs an explicit bridge.',
                    'Propose a completion plan covering every open goal predicate, state unknown premises and the first bounded test that can reject it.',
                    'advisor_policy.context.decision.goal_conditions and advisor_policy.routes')
        if len(declared) < 2:
            request('PROPOSE_STRATEGIC_ALTERNATIVE', 'Fewer than two complete strategies are declared; local action count does not establish strategic diversity.',
                    'Propose a materially different goal-reaching method and a same-scope observation on which its predictions differ. Preserve the evaluator and total budget.',
                    'advisor_policy.feasibility.plans')
        for milestone in milestones:
            if milestone['status'] != 'TRUE' and (not milestone['producers'] or all(
                    p['process_status'] in {'SUCCEEDED', 'FAILED', 'INTERRUPTED', 'TIMED_OUT'} for p in milestone['producers'])):
                request('REPAIR_GOAL_BRIDGE', 'The goal predicate remains open without an unexecuted declared producer: ' + milestone['fact'],
                        'Inspect the original output and predicate. Propose a missing verifier, evidence repair or changed method; do not repeat a terminal manifest or treat UNKNOWN as refutation.',
                        milestone['source']['locator'])
        if selected and not by_id[selected]['open_goal_links']:
            request('REVIEW_LOCAL_GLOBAL_LINK', 'The current action has no declared link to a remaining goal predicate.',
                    'Explain which final premise or decision this action can change, and test that link before investing in longer local refinement.',
                    by_id[selected]['source']['locator'])
        if not admitted:
            request('RESOLVE_CURRENT_BLOCKER', 'No action is admitted at this evidence cut; this does not prove the goal impossible.',
                    'Consume current Advisor next_move and its original diagnostics. Resolve evidence, method or resource prerequisites before proposing a new route.',
                    'owned review.next_move')
        for flag in flags:
            if flag.get('kind') in {'REPEAT_REJECTED_ROUTE', 'REPEAT_DECLARED_REJECTED_DOMAIN', 'DECISION_OSCILLATION', 'GOAL_ROUTES_REJECTED'}:
                request('DESIGN_OFF_ROUTE_DISCRIMINATOR', 'Existing scoped loop review reports ' + flag['kind'],
                        'Use the retained rejection and unchanged premises to propose an off-route explanation and a cheap falsifying check. A new label or more precision is not changed evidence.',
                        'recommendations.search.loop_review.flags')
                break
    spec = context.get('dependency_map', {})
    cone = _cone(spec, ['owned:' + m['id'] for m in milestones])
    search_truncated = any(s.get('truncation', {}).get('truncated') for s in searches)
    dependency_incomplete = selection.get('dependency_review', {}).get('status') in {'UNKNOWN', 'INCOMPLETE'}
    if cone['truncated'] or search_truncated or dependency_incomplete:
        request('INSPECT_OMITTED_SCOPE', 'The goal cone, original search or dependency analysis is incomplete.',
                'Expand the named original scope before claiming route exhaustion, a best strategy or a complete goal bridge.',
                'context.dependency_map and recommendations.search.truncation')
    focused = [r for r in ranked if r['open_goal_links']]
    alternatives = alternatives or focused + [r for r in route_views if r not in focused]
    summary = {'scope': 'ONE_FROZEN_GOAL' if goal is not None else 'ALL_DECLARED_GOALS',
               'open_goal_count': len(open_facts), 'goal_count': len(milestones),
               'global_goal_count': len(conditions),
               'global_open_goal_count': sum(evaluate_condition(c, facts)['truth'] != 'TRUE' for c in conditions),
               'declared_complete_plans': len(declared), 'admitted_local_routes': len(admitted_views),
               'strategic_diversity': 'UNVERIFIED', 'source_search_truncated': search_truncated,
               'dependency_analysis_incomplete': dependency_incomplete,
               'suggested_run': suggestion['id'] if suggestion else None,
               'current_advisor_run': selected, 'request_kinds': list(dict.fromkeys(r['kind'] for r in requests))}
    result.update(status='CURRENT', source={'contract_sha256': digest(state['contract']),
        'fingerprint': report['fingerprint'], 'snapshot_sha256': report.get('snapshot_sha256'),
        'dependency_map_sha256': digest(spec), 'decision_sha256': digest(context['decision']),
        'decision_id': context['decision']['id'], 'focused_goal': goal},
        admitted_runs=sorted(admitted),
        summary=summary, milestones=_section(milestones, 'advisor_policy.context.decision.goal_conditions'),
        critical_unknowns=_section([m for m in milestones if m['status'] != 'TRUE'], 'advisor_policy.context.decision.goal_conditions'),
        strategic_alternatives=_section(alternatives, 'advisor_policy.feasibility.plans' if declared else 'advisor_policy.routes', ROUTE_LIMIT),
        local_routes=_section(route_views, 'advisor_policy.routes', ROUTE_LIMIT),
        replan_requests=_section(requests, 'goal predicates and recommendations.search.loop_review.flags'), goal_cone=cone,
        comparison={'current_advisor_run': selected, 'shadow_suggested_run': suggestion['id'] if suggestion else None,
                    'different': suggestion is not None and suggestion['id'] != selected,
                    'precedence': precedence, 'selection_applied': False,
                    'basis': 'HEURISTIC_OPEN_GOAL_LINKS_THEN_DECLARED_DISCRIMINATION_THEN_ALLOWANCE_NOT_GLOBAL_OPTIMUM'},
        next_small_check={'run_id': suggestion['id'], 'action': deepcopy(suggestion['discriminator']),
                          'open_goal_links': suggestion['open_goal_links'], 'source': suggestion['source'],
                          'planning_links': deepcopy(suggestion['planning_links']),
                          'status': 'PROPOSED_ALREADY_ADMITTED_ACTION_NOT_DISPATCHED'} if suggestion else None,
        goal_status={'status': selection.get('goal', {}).get('status', 'UNKNOWN'),
                     'source': {'locator': 'recommendations.search.selection_review.goal'}},
        current_next_move=_section([report['next_move']], 'owned review.next_move')['items'][0] if report.get('next_move') else None,
        process_status_counts=dict(Counter(r['process_status'] for r in route_views)))
    if result['next_small_check'] is not None:
        result['next_small_check'] = _section([result['next_small_check']], 'advisor_policy.routes')['items'][0]
    return result


def inspect_plan(store, *, goal=None):
    """Read-only CLI path with a final concurrency/identity check."""
    from rds_owned_advisor import review, _state, _fingerprint
    from rds_tms_store import current
    report = review(store, persist=False)
    result = report['shadow_plan']
    with store._db(True) as db:
        db.execute('BEGIN')
        state = _state(store, db)
    saved = current(store.root)
    if (_fingerprint(state) != report['fingerprint'] or
            (saved['sha256'] if saved else None) != report['snapshot_sha256']):
        return {**result, 'status': 'UNAVAILABLE', 'reason': 'Evidence changed during planning; retry project plan --shadow',
                'summary': {**result.get('summary', {}), 'suggested_run': None},
                'comparison': {**result.get('comparison', {}), 'shadow_suggested_run': None, 'different': False},
                'admitted_runs': [], 'next_small_check': None}
    if goal is not None:
        result = build(state, report, admitted_runs=result.get('admitted_runs', []), goal=goal)
    return result
