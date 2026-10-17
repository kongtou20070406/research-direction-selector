"""Selected-route policy and checkpoint data, shared by Advisor and QUICK."""
from copy import deepcopy
import json

from rds_project_values import digest, require


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
    from rds_checkpoint_decision import _scope, _text, _loop_route
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
