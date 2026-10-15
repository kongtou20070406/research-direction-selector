"""Unattended Compass loop over the existing ledger.

``init -> ingest receipts -> Advisor decision -> experiment | jump | RSI ->
exact/Lean verification -> auditable report``. Every step is an append-only
ledger write, so an outer kill loses at most the step in flight: the next
``drive`` recovers original attempts and resumes from the recorded head.
A controller deadline or SIGTERM ends the loop cleanly with an INTERRUPTED /
CONTROLLER_DEADLINE handoff that names the partial evidence (#215).
"""
import time

import rds_compass as compass
import rds_compass_store as cstore
from rds_interrupt import OuterInterrupt, install, restore
from rds_project import digest


def _recover(store, state, runs):
    """Finish original attempts; never start a second attempt of the same run."""
    for run_id in runs:
        with store._db(True) as db:
            run = store._run(db, run_id)
        if run['status'] == 'RESERVED' and run['attempt_id'] is None:
            store.execute(run_id)  # Registered but never started: start the original reservation.
            continue
        result = store.recover(run_id)
        if 'recovery' in result:
            return 'WAITING'
    return 'RECOVERED'


def _partial(store, steps, in_flight, signal_name):
    state = compass.fold(store)
    return {'signal': signal_name, 'in_flight_run': in_flight, 'head': state['head'],
            'observations': len(state['observations']),
            'receipts': sorted(rid for rid in state['runs'] if rid.startswith('compass-')),
            'steps': steps[-12:], 'resume': 'Run compass drive again: active originals are recovered, never rerun; '
                                                 'an INTERRUPTED attempt keeps its receipt and its candidate uses the declared retry slot'}


def drive(root, *, max_steps=200, wall_seconds=None, publish=True):
    store = cstore.store_for(root)
    deadline = None if wall_seconds is None else time.monotonic() + wall_seconds
    previous = install(('SIGTERM', 'SIGINT'))
    steps, in_flight = [], None

    def finish(reason, detail=None, **extra):
        value = compass.handoff(store, reason, detail)
        if publish:
            extra['tms_snapshot'] = compass.publish_tms(store, compass.fold(store))
        return {'status': reason, 'workset': value, 'steps': steps, **extra}
    try:
        while True:
            state, fresh = compass.ingest(store)
            if publish:
                compass.publish_tms(store, state)
            if len(steps) >= max_steps:
                return finish('STEP_LIMIT', {'max_steps': max_steps})
            if deadline is not None and time.monotonic() >= deadline:
                return finish('CONTROLLER_DEADLINE', _partial(store, steps, None, None))
            step = compass.decide(state)
            action = step['action']
            steps.append({'action': action, 'candidate': step.get('candidate'), 'head': state['head'][:16],
                          'trigger': (step.get('basin') or {}).get('trigger')})
            if action == 'RECOVER':
                if _recover(store, state, step['runs']) == 'WAITING':
                    return finish('WAITING_FOR_ORIGINAL_ATTEMPT', {'runs': step['runs']})
            elif action in ('EXPERIMENT', 'PROBE', 'CONFIRM'):
                compass.record(store, state, step)
                manifest = compass.manifest(state, step['candidate'])
                in_flight = manifest['id']
                store.register(manifest)
                store.execute(manifest['id'])
                in_flight = None
            elif action == 'RIVALS':
                compass.generate_rivals(store, state)
            elif action == 'ESCAPE':
                compass.record(store, state, step)
                result = compass.escape(store, compass.fold(store), step['basin'])
                steps[-1]['escape'] = {k: result.get(k) for k in ('status', 'jump', 'adopted', 'disposer')}
            elif action == 'VERIFY':
                verification = compass.verify(store, state)
                body = compass.report(store)
                cstore.append(store, 'REPORT', body['sha256'][:32], body)
                if verification['goal'] == 'IMPROVE':
                    improvement = verification['improvement']
                    # An undeclared threshold, an unmeasured baseline, or an arm that is unmeasured or
                    # measured on other samples than the baseline is not a settled evaluation.
                    judgment = [a['arm'] for a in improvement['arms']
                                if a['verdict'] in ('JUDGMENT_REQUIRED', 'UNMEASURED_OR_SAMPLE_MISMATCH')]
                    settled = (verification['receipts_rechecked'] and improvement['status'] == 'EVALUATED'
                               and not judgment)
                    detail = None if settled else {
                        'stale': verification['stale'][:8], 'improvement_status': improvement['status'],
                        'judgment_required': judgment[:8],
                        'unknown_thresholds': [k for k in ('min_gain', 'max_degradation')
                                               if k not in state['plan']['profile']]}
                    return finish('IMPROVEMENT_EVALUATED' if settled else 'JUDGMENT_REQUIRED', detail,
                                  verification=verification, report_sha256=body['sha256'])
                rival = (verification.get('polynomial') or {}).get('status') == 'POLYNOMIAL_RIVAL_DISAGREES'
                unique = (verification['claim'] == 'CONSISTENT_WITH_ALL_RECEIPTED_MEASUREMENTS' and not rival
                          and verification['distinct_live_classes'] == 1 and verification['receipts_rechecked'])
                return finish('GOAL_IDENTIFIED' if unique else 'JUDGMENT_REQUIRED',
                              None if unique else {'distinct_live_classes': verification['distinct_live_classes'],
                                                   'polynomial': verification.get('polynomial')},
                              verification=verification, report_sha256=body['sha256'])
            elif action == 'HANDOFF':
                return finish(step['reason'], step.get('detail') or step.get('basin'))
            else:
                raise RuntimeError('Unknown Compass action: ' + action)
    except (OuterInterrupt, KeyboardInterrupt) as exc:
        partial = _partial(store, steps, in_flight, str(exc) or type(exc).__name__)
        cstore.append(store, 'INTERRUPTED', 'i' + digest(partial)[:15], partial)
        return finish('INTERRUPTED', partial)
    finally:
        restore(previous)
