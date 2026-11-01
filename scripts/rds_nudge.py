"""Recompute measurement-oriented reminders from research notes."""
from bisect import bisect_left
from collections import defaultdict
import rds_notes

THRESHOLDS = {'repeat': 3, 'flat': 3, 'proxy': 2, 'endpoint': 3, 'reshow_after': 5}
ASSURANCE = 'RECORDED_INPUT_NOT_SCIENTIFIC_VERIFICATION'


def _evaluate(notes):
    candidates, unknown = [], []
    by_id = {n['id']: n for n in notes}
    observes = [n for n in notes if n['type'] == 'observe']
    expects = [n for n in notes if n['type'] == 'expect']
    goals = [n for n in notes if n['type'] == 'goal']
    attempts = [n for n in notes if n['type'] == 'attempt']
    positions = {n['id']: i for i, n in enumerate(notes)}

    keys = set()
    def add(rule, anchor, anchors, question):
        key = rule + ':' + anchor['id']
        if key in keys:
            return
        keys.add(key)
        candidates.append({'key': rule + ':' + anchor['id'], 'rule': rule,
                           'question': '“' + anchor.get('text', '') + '” — ' + question + ' Answer with a later observation or dismiss with a reason.',
                           'anchors': [n['id'] for n in anchors], 'status': 'OPEN'})

    latest_expects = {}
    for n in notes:
        metric = n.get('metric')
        if n['type'] == 'expect':
            if metric:
                latest_expects[metric] = n
            if n.get('value') is None or n.get('tolerance') is None:
                unknown.append({'rule': 'expect_mismatch', 'anchor': n['id'], 'reason': 'missing value or tolerance'})
        elif n['type'] == 'observe' and metric and metric in latest_expects:
            expect = latest_expects[metric]
            if n.get('value') is None:
                unknown.append({'rule': 'expect_mismatch', 'anchor': n['id'], 'reason': 'missing value'})
            elif expect.get('value') is not None and expect.get('tolerance') is not None and abs(n['value'] - expect['value']) > expect['tolerance']:
                add('expect_mismatch', n, [expect, n], 'Which premise does this deviation shake, and what measurement would test it?')
    if attempts and attempts[-1].get('route') and attempts[-1].get('outcome') == 'fail':
        route = attempts[-1]['route']
        start = len(attempts) - 1
        while start and attempts[start - 1].get('route') == route and attempts[start - 1].get('outcome') == 'fail':
            start -= 1
        run = attempts[start:]
        if len(run) >= THRESHOLDS['repeat']:
            add('repeat_route', run[0], run[:THRESHOLDS['repeat']], 'What have these attempts kept fixed, and what measurement changes it?')
    for n in notes:
        if n['type'] == 'claim' and not any(by_id.get(ref, {}).get('type') in ('observe', 'attempt') for ref in n.get('ref', [])):
            add('unbound_claim', n, [n], 'Which measurement could refute this claim?')
    metric_goal = next((g for g in reversed(goals) if g.get('metric')), None)
    if metric_goal:
        run = []
        for obs in observes:
            if positions[obs['id']] <= positions[metric_goal['id']]:
                continue
            if obs.get('metric') and obs['metric'] != metric_goal['metric']:
                run.append(obs)
            else:
                run = []
        if len(run) >= THRESHOLDS['proxy']:
            add('proxy_metric', run[0], [metric_goal, *run], 'Is this optimum still optimal on the goal metric?')
    scale_goal = next((g for g in reversed(goals) if g.get('scale') is not None), None)
    if scale_goal:
        for obs in observes:
            if positions[obs['id']] > positions[scale_goal['id']] and obs.get('scale') is not None and obs['scale'] != scale_goal['scale']:
                add('scale_gap', obs, [scale_goal, obs], 'Does the ranking hold at the goal scale?')
    metric_observes, metric_expects = defaultdict(list), defaultdict(list)
    for n in observes:
        if n.get('metric'):
            metric_observes[n['metric']].append(n)
    for n in expects:
        if n.get('metric'):
            metric_expects[n['metric']].append(n)
    for metric in sorted(metric_observes):
        recent = metric_observes[metric][-THRESHOLDS['flat']:]
        rows = metric_expects[metric]
        index = bisect_left([positions[n['id']] for n in rows], positions[recent[0]['id']])
        matching = rows[:index]
        if len(recent) < THRESHOLDS['flat'] or not matching:
            continue
        expect = matching[-1]
        if expect.get('tolerance') is None or any(n.get('value') is None for n in recent):
            unknown.append({'rule': 'flat_reading', 'anchor': recent[0]['id'], 'reason': 'missing value or tolerance'})
        elif max(n['value'] for n in recent) - min(n['value'] for n in recent) <= expect['tolerance']:
            first = len(metric_observes[metric]) - THRESHOLDS['flat']
            expect_positions = [positions[n['id']] for n in rows]
            while first:
                window = metric_observes[metric][first - 1:first - 1 + THRESHOLDS['flat']]
                prior = bisect_left(expect_positions, positions[window[0]['id']])
                if not prior or rows[prior - 1].get('tolerance') is None or any(n.get('value') is None for n in window):
                    break
                if max(n['value'] for n in window) - min(n['value'] for n in window) > rows[prior - 1]['tolerance']:
                    break
                first -= 1
            add('flat_reading', metric_observes[metric][first], [expect, *recent], 'What measurement can this reading still distinguish?')
    for n in observes:
        if n.get('aggregate'):
            add('aggregate', n, [n], 'What did the aggregation cancel, and what measurement reveals it?')
    goal_observes = [n for n in observes if goals and positions[n['id']] > positions[goals[-1]['id']]]
    if goals and len(goal_observes) >= THRESHOLDS['endpoint'] and not any(n.get('endpoint') for n in goal_observes):
        add('endpoint_unchecked', goals[-1], [goals[-1], *goal_observes], 'What does the system finally output when measured?')
    for n in notes:
        if n['type'] == 'constraint' and n.get('source') not in ('user', 'doc'):
            add('unsourced_constraint', n, [n], 'Who required it, and what measurement changes without it?')
    if not goals and len(attempts) >= 3:
        add('missing_goal', attempts[0], attempts[:3], 'What is the goal and expected result? Record them with note goal and note expect.')
    closures = {n.get('key'): n['type'] for n in notes if n['type'] in ('answer', 'dismiss')}
    for candidate in candidates:
        closure = closures.get(candidate['key'])
        if closure:
            candidate['status'] = 'ANSWERED' if closure == 'answer' else 'DISMISSED'
    return candidates, unknown


def evaluate(notes):
    return _evaluate(notes)[0]


def status(notes):
    reminders, unknown = _evaluate(notes)
    return {'open': [n for n in reminders if n['status'] == 'OPEN'],
            'closed': [n for n in reminders if n['status'] != 'OPEN'], 'unknown': unknown, 'assurance': ASSURANCE}


def next_for_host(notes):
    count = 0
    shown = {}
    for n in notes:
        if n['type'] == 'shown':
            shown[n.get('key')] = count
        else:
            count += 1
    for reminder in status(notes)['open']:
        last = shown.get(reminder['key'])
        if last is None or count - last >= THRESHOLDS['reshow_after']:
            return reminder
    return None


def _open(notes, key):
    reminder = next((n for n in evaluate(notes) if n['key'] == key and n['status'] == 'OPEN'), None)
    if reminder is None:
        raise ValueError('Reminder is not open')
    return reminder


def answer(root, key, ref):
    notes = rds_notes.read(root)
    reminder = _open(notes, key)
    refs = [ref] if isinstance(ref, str) else ref
    positions = {n['id']: i for i, n in enumerate(notes)}
    by_id = {n['id']: n for n in notes}
    if not refs or any(r not in by_id or by_id[r]['type'] != 'observe' or positions[r] <= max(positions[a] for a in reminder['anchors']) for r in refs):
        raise ValueError('Answer requires a later observation')
    return rds_notes.append(root, 'answer', key=key, ref=refs)


def dismiss(root, key, reason):
    _open(rds_notes.read(root), key)
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError('Dismissal requires a reason')
    return rds_notes.append(root, 'dismiss', key=key, reason=reason)
