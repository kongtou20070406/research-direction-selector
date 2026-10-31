"""Recompute measurement-oriented reminders from research notes."""
from bisect import bisect_left, bisect_right
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

    # Prefix extrema locate the earliest preceding expectation outside tolerance.
    prefixes = defaultdict(lambda: ([], [], [], []))
    mismatches = []
    for n in notes:
        metric = n.get('metric')
        if n['type'] == 'expect':
            if n.get('value') is None or n.get('tolerance') is None:
                unknown.append({'rule': 'expect_mismatch', 'anchor': n['id'], 'reason': 'missing value or tolerance'})
            elif metric:
                rows, lows, highs, neg_highs = prefixes[metric]
                rows.append(n)
                lower, upper = n['value'] - n['tolerance'], n['value'] + n['tolerance']
                lows.append(max(lows[-1], lower) if lows else lower)
                highs.append(min(highs[-1], upper) if highs else upper)
                neg_highs.append(-highs[-1])
        elif n['type'] == 'observe' and metric:
            rows, lows, highs, neg_highs = prefixes[metric]
            if n.get('value') is None:
                if rows:
                    unknown.append({'rule': 'expect_mismatch', 'anchor': n['id'], 'reason': 'missing value'})
            elif rows:
                value = n['value']
                index = min(bisect_right(lows, value), bisect_right(neg_highs, -value))
                if index < len(rows):
                    mismatches.append((positions[rows[index]['id']], positions[n['id']], rows[index], n))
    for _, _, expect, obs in sorted(mismatches, key=lambda item: item[:2]):
        add('expect_mismatch', obs, [expect, obs], 'Which premise does this deviation shake, and what measurement would test it?')
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
    for goal in goals:
        recent = observes[-THRESHOLDS['proxy']:]
        if goal.get('metric') and len(recent) == THRESHOLDS['proxy'] and all(n.get('metric') and n['metric'] != goal['metric'] for n in recent):
            add('proxy_metric', goal, [goal, *recent], 'Is this optimum still optimal on the goal metric?')
    scaled_goals = [g for g in goals if g.get('scale') is not None]
    first_goal = scaled_goals[0] if scaled_goals else None
    other_goal = next((g for g in scaled_goals if g['scale'] != first_goal['scale']), None) if first_goal else None
    gaps = []
    for obs in observes:
        if obs.get('scale') is not None and first_goal:
            goal = first_goal if first_goal['scale'] != obs['scale'] else other_goal
            if goal:
                gaps.append((positions[goal['id']], positions[obs['id']], goal, obs))
    for _, _, goal, obs in sorted(gaps, key=lambda item: item[:2]):
        add('scale_gap', obs, [goal, obs], 'Does the ranking hold at the goal scale?')
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
    if goals and len(observes) >= THRESHOLDS['endpoint'] and not any(n.get('endpoint') for n in observes):
        add('endpoint_unchecked', goals[0], [goals[0], *observes], 'What does the system finally output when measured?')
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
