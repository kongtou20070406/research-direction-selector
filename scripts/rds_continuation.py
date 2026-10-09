"""Bounded presentation of a drive stop, using the existing owned working set.

No selector, new authority, model call, or persistent continuation state. The
report is an evidence snapshot; the next execution always rechecks admission.
"""
from rds_project import digest


def resources(db):
    return {r['resource']: {'cap': r['cap'], 'spent_measured': r['spent'],
            'charged_estimate': r['charged'], 'reserved': r['reserved'],
            'remaining': r['cap'] - r['spent'] - r['charged'] - r['reserved'],
            'unit': 'seconds' if r['resource'].endswith('_seconds') else r['resource']}
            for r in db.execute('SELECT * FROM budget ORDER BY resource')}


def response(status):
    if status == 'GOAL_CONFIRMED':
        return 'REPORT_SCOPED_CONFIRMATION', 'Report the original domain confirmation and its declared scope.'
    if status.startswith('GOAL_PREDICATES_MET_CONFIRMATION_') or status == 'DOMAIN_CONFIRMATION_UNKNOWN':
        return 'INDEPENDENT_CONFIRMATION', 'Inspect the declared confirmation; predicate truth alone does not establish scientific success.'
    if status == 'HUMAN_STEERING_REQUIRED':
        return 'HUMAN_STEERING', 'Respect the retained user instruction; inspect project steering before any continuation.'
    if status == 'WAITING_FOR_ORIGINAL_ATTEMPT':
        return 'WAIT_FOR_ORIGINAL_ATTEMPT', 'Observe or recover the original attempt; do not dispatch a replacement.'
    if status == 'RECONCILE_MODEL_DELIVERY_REQUIRED':
        return 'RECONCILE_ORIGINAL_DELIVERY', 'Inspect the original paid delivery and receipt; do not purchase another call to resolve uncertainty.'
    if status == 'EVIDENCE_REPAIR_REQUIRED':
        return 'REPAIR_ORIGINAL_EVIDENCE', 'Repair collection from original artifacts, then run project next; do not rerun completed work.'
    if status in {'STEP_LIMIT', 'CONTROL_TRANSITION_LIMIT'}:
        return 'BOUNDED_CONTINUATION', 'A later bounded pass may continue under the same goal and remaining budget; admission is rechecked.'
    if status in {'JUDGMENT_REQUIRED', 'MODEL_REQUEST_READY'}:
        return 'RESEARCH_JUDGMENT', 'Inspect the exact goal, blockers and originals before proposing a new hypothesis or method through existing structure or method-revision paths. Any expansion of authority needs authorization.'
    return 'INSPECT_ADMISSION_OR_RECOVERY', 'Inspect the original stop reason and remaining resources. Restore evidence or obtain any needed scope/budget authorization; do not bypass the stop.'


def attach(store, contract, result, *, project):
    """An optional observer must not mask failure or prevent budget settlement."""
    kind, instruction = response(result['status'])
    handoff = {'schema': 1, 'stop_reason': result['status'], 'response_class': kind,
               'allowed_response': instruction, 'authorization': 'UNCHANGED',
               'scientific_support': 'UNKNOWN', 'research_benefit': 'UNMEASURED',
               'original_goal': {'entry_contract_sha256': digest(contract),
                                 'pointer': '/advisor_policy/context/decision'},
               'evidence_status': 'UNAVAILABLE', 'goal_status': 'UNKNOWN',
               'admission_token': False}
    result['handoff'] = handoff
    try:
        from rds_advisor_workset import build, _compact
        report = result.get('advisor')
        if project and report is not None:
            workset = build(store, report)
            handoff['source_report'] = workset['source_report']
            # The controller release changes budget/fingerprint after this
            # snapshot. Its exact settled resources are filled by that transaction.
            if workset['status'] == 'CURRENT':
                workset['status'] = 'AT_STOP_BEFORE_CONTROLLER_SETTLEMENT'
                workset.pop('budget', None)
                handoff.update(evidence_status='VERIFIED_STOP_SNAPSHOT', evidence=workset)
                handoff['goal_status'] = next((r['search']['selection_review'].get('goal', {}).get('status', 'UNKNOWN')
                    for r in report.get('recommendations', []) if r.get('type') == 'EXECUTABLE_DIRECTION_SEARCH'), 'UNKNOWN')
            else:
                handoff['diagnostic'] = workset.get('diagnostic', 'No coherent owned working set')
        else:
            handoff['diagnostic'] = 'No current report within the controller allowance; inspect project next before a consequential choice'
        if report is not None:
            result['advisor'] = _compact(store, report)
        if 'unresolved_obstacle' in result:
            result['unresolved_obstacle'] = _compact(store, result['unresolved_obstacle'])
    except Exception as exc:
        # Observer failure is visible, while original execution/receipt/cost and
        # the primary drive stop survive. No partial view is called verified.
        handoff.pop('evidence', None)
        handoff.update(evidence_status='UNAVAILABLE', goal_status='UNKNOWN',
                       diagnostic=f'{type(exc).__name__}: {exc}'[:512])
        if 'advisor' in result:
            result['advisor'] = {'status': result['advisor'].get('status'), 'details_unavailable': True}
        if 'unresolved_obstacle' in result:
            result['unresolved_obstacle'] = {'details_unavailable': True,
                                             'recovery': 'Inspect the original project ledger and Advisor report'}
